"""
Pruebas con PostgreSQL de verdad (por ejemplo, Supabase).

Solo corren si existe la variable TEST_DATABASE_URL; si no, se saltan.
Usa una base de PRUEBAS (no la de producción). Cada prueba usa un
carrito_id único y al terminar borra sus filas de eventos y control.

    $env:TEST_DATABASE_URL = "postgresql://usuario:contraseña@host:5432/postgres"
    python -m pytest tests/test_postgres.py -v
"""

import json
import os
import uuid

import pytest

URL_PRUEBAS = os.getenv("TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not URL_PRUEBAS, reason="Define TEST_DATABASE_URL para probar PostgreSQL.")

import psycopg  # noqa: E402

from app.catalogo import ARCHIVO_COMPONENTES  # noqa: E402
from conftest import APP, CARRITO, EVENTO_VALIDO, LECTURA  # noqa: E402

API = "/api/v1"


@pytest.fixture
def carrito():
    """Un carrito_id que solo usa esta prueba; al final se borran sus datos."""
    ident = f"prueba-{uuid.uuid4().hex[:12]}"
    yield ident
    with psycopg.connect(URL_PRUEBAS, prepare_threshold=None) as conexion:
        conexion.execute("DELETE FROM eventos WHERE carrito_id = %s", (ident,))
        conexion.execute("DELETE FROM control WHERE carrito_id = %s", (ident,))


@pytest.fixture
def pg(crear_cliente):
    # modo_demo=True a propósito: con DATABASE_URL debe quedar apagado
    return crear_cliente(database_url=URL_PRUEBAS, modo_demo=True)


def _eventos(carrito, n=6):
    senales = [("alto", "detener", 0.93), ("avanzar", "avanzar", 0.88), ("izquierda", "girar_izq", 0.71),
               ("alto", "detener", 0.55), ("peaton", "detener", 0.97), ("ninguna", "avanzar", 0.40)]
    return [{**EVENTO_VALIDO, "carrito_id": carrito, "fecha": f"2026-12-04T10:15:{30 + i:02d}",
             "senal": s, "accion": a, "confianza": c} for i, (s, a, c) in enumerate(senales[:n])]


# --- Configuración y salud -------------------------------------------------
def test_health_dice_postgresql(pg):
    r = pg.get(f"{API}/health")
    assert r.status_code == 200
    datos = r.json()
    assert datos["base_datos"] == "postgresql"
    assert datos["conexion"] == "ok"
    assert datos["estado"] == "ok"
    assert datos["modo_demo"] is False


def test_demo_desactivado(pg):
    assert pg.post(f"{API}/demo/generar", headers=APP).status_code == 404


def test_health_con_base_caida(crear_cliente):
    cliente = crear_cliente(database_url="postgresql://nadie:nada@127.0.0.1:1/nada?connect_timeout=1")
    r = cliente.get(f"{API}/health")
    assert r.status_code == 503
    assert r.json()["base_datos"] == "postgresql"
    assert r.json()["conexion"] == "error"


# --- Esquema ---------------------------------------------------------------
def test_tablas_y_restricciones():
    with psycopg.connect(URL_PRUEBAS, prepare_threshold=None) as conexion:
        tablas = {f[0] for f in conexion.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")}
        assert {"eventos", "control", "componentes"} <= tablas
        indices = {f[0] for f in conexion.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'eventos'")}
        assert {"idx_eventos_carrito_fecha", "idx_eventos_fecha"} <= indices
        with pytest.raises(psycopg.errors.CheckViolation):
            conexion.execute("INSERT INTO eventos (carrito_id, fecha, senal, confianza, accion, modo) "
                             "VALUES ('x', now(), 'volar', 0.5, 'detener', 'autonomo')")


# --- Eventos ---------------------------------------------------------------
def test_guardar_y_consultar(pg, carrito):
    lote = _eventos(carrito)
    r = pg.post(f"{API}/eventos", json=lote, headers=CARRITO)
    assert r.status_code == 201
    ids = r.json()["ids"]
    assert r.json()["guardados"] == 6
    assert [int(i) for i in ids] == sorted(int(i) for i in ids)      # en el orden enviado

    historial = pg.get(f"{API}/eventos", params={"carrito_id": carrito}, headers=LECTURA).json()
    assert [e["fecha"] for e in historial] == [e["fecha"] for e in reversed(lote)]
    assert historial[0] == {**lote[-1], "id": ids[-1]}

    assert pg.get(f"{API}/eventos/ultimo", params={"carrito_id": carrito}, headers=APP).json()["id"] == ids[-1]
    solo_alto = pg.get(f"{API}/eventos", params={"carrito_id": carrito, "senal": "alto"}, headers=LECTURA).json()
    assert len(solo_alto) == 2
    rango = pg.get(f"{API}/eventos", headers=LECTURA, params={
        "carrito_id": carrito, "desde": "2026-12-04T10:15:31", "hasta": "2026-12-04T10:15:33"}).json()
    assert len(rango) == 3
    assert len(pg.get(f"{API}/eventos", params={"carrito_id": carrito, "limite": 2}, headers=LECTURA).json()) == 2


def test_lote_invalido_no_guarda_nada(pg, carrito):
    lote = _eventos(carrito, 2) + [{**EVENTO_VALIDO, "carrito_id": carrito, "senal": "volar"}]
    assert pg.post(f"{API}/eventos", json=lote, headers=CARRITO).status_code == 422
    assert pg.get(f"{API}/eventos", params={"carrito_id": carrito}, headers=LECTURA).json() == []
    assert pg.get(f"{API}/eventos/ultimo", params={"carrito_id": carrito}, headers=LECTURA).status_code == 404


def test_mismas_respuestas_que_memoria(pg, crear_cliente, carrito):
    """Lo que ve 4°A debe ser idéntico con memoria y con PostgreSQL (salvo los id)."""
    memoria = crear_cliente()
    for cliente in (pg, memoria):
        assert cliente.post(f"{API}/eventos", json=_eventos(carrito), headers=CARRITO).status_code == 201

    def sin_id(datos):
        if isinstance(datos, list):
            return [sin_id(d) for d in datos]
        if isinstance(datos, dict):
            return {k: sin_id(v) for k, v in datos.items() if k != "id"}
        return datos

    for ruta, params in [("/eventos", {"carrito_id": carrito}), ("/eventos/ultimo", {"carrito_id": carrito}),
                         ("/estadisticas", {"carrito_id": carrito})]:
        a = pg.get(f"{API}{ruta}", params=params, headers=LECTURA)
        b = memoria.get(f"{API}{ruta}", params=params, headers=LECTURA)
        assert a.status_code == b.status_code == 200
        assert sin_id(a.json()) == sin_id(b.json()), ruta


# --- Estadísticas -----------------------------------------------------------
def test_estadisticas(pg, carrito):
    pg.post(f"{API}/eventos", json=_eventos(carrito), headers=CARRITO)
    e = pg.get(f"{API}/estadisticas", params={"carrito_id": carrito}, headers=LECTURA).json()
    assert e["total_eventos"] == 6
    assert e["conteo_por_senal"]["alto"] == 2
    assert e["conteo_por_accion"]["detener"] == 3
    assert e["confianza_promedio_por_senal"]["alto"] == 0.74
    assert e["confianza_promedio_por_senal"]["derecha"] is None
    assert e["senal_mas_detectada"] == "alto"
    assert e["evento_menor_confianza"]["confianza"] == 0.40


# --- Control remoto ---------------------------------------------------------
def test_control(pg, carrito):
    url = f"{API}/control/{carrito}"
    assert pg.get(url, headers=CARRITO).json() == {"modo": "autonomo", "accion": "detener", "actualizado": None}
    r = pg.put(url, json={"modo": "manual", "accion": "avanzar"}, headers=APP)
    assert r.status_code == 200
    leido = pg.get(url, headers=CARRITO).json()
    assert leido["modo"] == "manual" and leido["accion"] == "avanzar"
    assert leido["actualizado"] == r.json()["actualizado"]
    pg.put(url, json={"modo": "autonomo"}, headers=APP)
    assert pg.get(url, headers=CARRITO).json()["modo"] == "autonomo"


def test_control_manual_vencido_da_detener(crear_cliente, carrito):
    pg = crear_cliente(database_url=URL_PRUEBAS, segundos_vigencia_manual=0)
    pg.put(f"{API}/control/{carrito}", json={"modo": "manual", "accion": "avanzar"}, headers=APP)
    assert pg.get(f"{API}/control/{carrito}", headers=CARRITO).json()["accion"] == "detener"


# --- Componentes ------------------------------------------------------------
def test_componentes_desde_la_tabla(pg):
    with open(ARCHIVO_COMPONENTES, encoding="utf-8") as archivo:
        esperado = json.load(archivo)
    assert pg.get(f"{API}/componentes", headers=LECTURA).json() == esperado
    energia = pg.get(f"{API}/componentes", params={"tipo": "energía"}, headers=APP).json()
    assert [c["id"] for c in energia] == [c["id"] for c in esperado if c["tipo"] == "energía"]
    assert pg.get(f"{API}/componentes/l298n", headers=LECTURA).json() == next(c for c in esperado if c["id"] == "l298n")
    assert pg.get(f"{API}/componentes/motor_x", headers=LECTURA).status_code == 404
