"""Control remoto (incluida la regla de los 2 s), estadísticas y modo demo."""

import time

from conftest import APP, CARRITO, EVENTO_VALIDO, LECTURA

URL_CONTROL = "/api/v1/control/celula-1"


# --- Control ----------------------------------------------------------------
def test_control_sin_configurar_es_autonomo(cliente):
    r = cliente.get(URL_CONTROL, headers=CARRITO).json()
    assert r == {"modo": "autonomo", "accion": "detener", "actualizado": None}


def test_app_pone_modo_manual(cliente):
    r = cliente.put(URL_CONTROL, json={"modo": "manual", "accion": "girar_izq"}, headers=APP)
    assert r.status_code == 200
    r = cliente.get(URL_CONTROL, headers=CARRITO).json()
    assert r["modo"] == "manual" and r["accion"] == "girar_izq"
    assert r["actualizado"] is not None


def test_manual_sin_renovar_responde_detener(crear_cliente):
    cliente = crear_cliente(segundos_vigencia_manual=0.3)
    cliente.put(URL_CONTROL, json={"modo": "manual", "accion": "avanzar"}, headers=APP)
    assert cliente.get(URL_CONTROL, headers=CARRITO).json()["accion"] == "avanzar"
    time.sleep(0.45)
    r = cliente.get(URL_CONTROL, headers=CARRITO).json()
    assert r["modo"] == "manual" and r["accion"] == "detener"
    # al renovar, vuelve la acción manual
    cliente.put(URL_CONTROL, json={"modo": "manual", "accion": "avanzar"}, headers=APP)
    assert cliente.get(URL_CONTROL, headers=CARRITO).json()["accion"] == "avanzar"


def test_regla_de_2_segundos_por_defecto(cliente):
    cliente.put(URL_CONTROL, json={"modo": "manual", "accion": "avanzar"}, headers=APP)
    time.sleep(2.2)
    assert cliente.get(URL_CONTROL, headers=CARRITO).json()["accion"] == "detener"


def test_volver_a_autonomo(cliente):
    cliente.put(URL_CONTROL, json={"modo": "manual", "accion": "avanzar"}, headers=APP)
    cliente.put(URL_CONTROL, json={"modo": "autonomo"}, headers=APP)
    assert cliente.get(URL_CONTROL, headers=CARRITO).json()["modo"] == "autonomo"


def test_control_invalido(cliente):
    for cuerpo in ({"modo": "turbo"}, {"modo": "manual", "accion": "volar"}, {"accion": "avanzar"}):
        assert cliente.put(URL_CONTROL, json=cuerpo, headers=APP).status_code == 422
    assert cliente.get("/api/v1/control/celula 1", headers=APP).status_code == 422


def test_control_es_por_carrito(cliente):
    cliente.put(URL_CONTROL, json={"modo": "manual", "accion": "avanzar"}, headers=APP)
    assert cliente.get("/api/v1/control/celula-2", headers=CARRITO).json()["modo"] == "autonomo"


# --- Estadísticas -----------------------------------------------------------
def test_estadisticas(cliente):
    datos = [("alto", 0.9, "detener"), ("alto", 0.7, "detener"), ("izquierda", 0.4, "girar_izq"),
             ("ninguna", 0.0, "avanzar"), ("ninguna", 0.0, "avanzar"), ("ninguna", 0.0, "avanzar")]
    lote = [{**EVENTO_VALIDO, "fecha": f"2026-12-04T10:00:0{i}", "senal": s, "confianza": c, "accion": a}
            for i, (s, c, a) in enumerate(datos)]
    lote.append({**EVENTO_VALIDO, "carrito_id": "otro", "senal": "peaton", "confianza": 0.01})
    cliente.post("/api/v1/eventos", json=lote, headers=CARRITO)

    r = cliente.get("/api/v1/estadisticas", params={"carrito_id": "celula-1"}, headers=LECTURA)
    assert r.status_code == 200
    e = r.json()
    assert e["total_eventos"] == 6
    assert e["conteo_por_senal"] == {"alto": 2, "avanzar": 0, "izquierda": 1, "derecha": 0,
                                     "ninguna": 3, "peaton": 0}
    assert e["conteo_por_accion"] == {"avanzar": 3, "detener": 2, "girar_izq": 1, "girar_der": 0}
    assert e["confianza_promedio_por_senal"]["alto"] == 0.8
    assert e["confianza_promedio_por_senal"]["derecha"] is None
    assert e["senal_mas_detectada"] == "alto"          # "ninguna" no cuenta
    # la menor es "ninguna" (0.0); entre empates, la más reciente
    assert e["evento_menor_confianza"]["confianza"] == 0.0
    assert e["evento_menor_confianza"]["fecha"] == "2026-12-04T10:00:05"

    todos = cliente.get("/api/v1/estadisticas", headers=LECTURA).json()
    assert todos["total_eventos"] == 7 and todos["carrito_id"] is None


def test_estadisticas_vacias(cliente):
    e = cliente.get("/api/v1/estadisticas", headers=LECTURA).json()
    assert e["total_eventos"] == 0
    assert e["senal_mas_detectada"] is None and e["evento_menor_confianza"] is None


# --- Modo demo --------------------------------------------------------------
def test_demo_genera_eventos_validos(cliente):
    r = cliente.post("/api/v1/demo/generar", json={"carrito_id": "celula-9", "cantidad": 120, "minutos": 5},
                     headers=APP)
    assert r.status_code == 201 and r.json()["generados"] == 120
    eventos = cliente.get("/api/v1/eventos", params={"carrito_id": "celula-9", "limite": 500},
                          headers=LECTURA).json()
    assert len(eventos) == 120
    for e in eventos:
        assert 0 <= e["confianza"] <= 1
        if e["modo"] == "autonomo" and e["senal"] in ("alto", "peaton"):
            assert e["accion"] == "detener"


def test_demo_limites(cliente):
    assert cliente.post("/api/v1/demo/generar", json={"cantidad": 501}, headers=APP).status_code == 422


def test_demo_crea_eventos_al_arrancar(crear_cliente):
    cliente = crear_cliente(demo_eventos_iniciales=30)
    assert cliente.get("/api/v1/estadisticas", headers=LECTURA).json()["total_eventos"] == 30


def test_sin_modo_demo_no_existe_generar(crear_cliente):
    # Sin MODO_DEMO la ruta no existe. Se usa una URI que nunca se contacta:
    # pymongo no se conecta hasta la primera operación.
    cliente = crear_cliente(modo_demo=False, mongodb_uri="mongodb://127.0.0.1:1/?serverSelectionTimeoutMS=200")
    assert cliente.post("/api/v1/demo/generar", headers=APP).status_code == 404
