"""Los datos válidos se guardan y los inválidos se rechazan con 422."""

import pytest

from conftest import APP, CARRITO, EVENTO_VALIDO, LECTURA

URL = "/api/v1/eventos"


# ---------------------------------------------------------------- válidos
def test_evento_valido_se_guarda(cliente):
    r = cliente.post(URL, json=EVENTO_VALIDO, headers=CARRITO)
    assert r.status_code == 201
    assert r.json()["guardados"] == 1

    historial = cliente.get(URL, headers=LECTURA).json()
    assert len(historial) == 1
    guardado = historial[0]
    assert guardado.pop("id") == r.json()["ids"][0]
    assert guardado == EVENTO_VALIDO          # mismos nombres y valores que el contrato


def test_lista_de_eventos_se_guarda(cliente):
    lote = [EVENTO_VALIDO,
            {**EVENTO_VALIDO, "fecha": "2026-12-04T10:15:40", "senal": "izquierda",
             "accion": "girar_izq", "confianza": 0.8},
            {**EVENTO_VALIDO, "fecha": "2026-12-04T10:15:45", "senal": "ninguna",
             "accion": "avanzar", "confianza": 0, "modo": "manual"}]
    r = cliente.post(URL, json=lote, headers=CARRITO)
    assert r.status_code == 201
    assert r.json()["guardados"] == 3
    assert len(cliente.get(URL, headers=LECTURA).json()) == 3


@pytest.mark.parametrize("confianza", [0, 1, 0.5])
def test_confianza_en_los_limites(cliente, confianza):
    r = cliente.post(URL, json={**EVENTO_VALIDO, "confianza": confianza}, headers=CARRITO)
    assert r.status_code == 201


def test_fecha_con_zona_se_convierte_a_hora_local(cliente):
    # 16:15:32 UTC = 10:15:32 en Ciudad de México (UTC-6)
    cliente.post(URL, json={**EVENTO_VALIDO, "fecha": "2026-12-04T16:15:32Z"}, headers=CARRITO)
    assert cliente.get(URL, headers=LECTURA).json()[0]["fecha"] == "2026-12-04T10:15:32"


# ---------------------------------------------------------------- inválidos
@pytest.mark.parametrize("cambio", [
    {"senal": "rojo"},
    {"senal": "ALTO"},
    {"accion": "retroceder"},
    {"modo": "remoto"},
    {"confianza": 1.01},
    {"confianza": -0.1},
    {"confianza": "0.9"},
    {"confianza": True},
    {"fecha": "ayer"},
    {"carrito_id": ""},
    {"carrito_id": "celula 1"},
    {"señal": "alto"},                 # campo extra
    {"motivo": "retroceso"},           # campo extra
])
def test_evento_invalido_se_rechaza(cliente, cambio):
    r = cliente.post(URL, json={**EVENTO_VALIDO, **cambio}, headers=CARRITO)
    assert r.status_code == 422, cambio
    assert cliente.get(URL, headers=LECTURA).json() == []


@pytest.mark.parametrize("campo", list(EVENTO_VALIDO))
def test_falta_un_campo(cliente, campo):
    evento = {k: v for k, v in EVENTO_VALIDO.items() if k != campo}
    assert cliente.post(URL, json=evento, headers=CARRITO).status_code == 422


def test_lista_con_un_invalido_no_guarda_nada(cliente):
    lote = [EVENTO_VALIDO, {**EVENTO_VALIDO, "senal": "rojo"}]
    assert cliente.post(URL, json=lote, headers=CARRITO).status_code == 422
    assert cliente.get(URL, headers=LECTURA).json() == []


def test_lista_vacia_se_rechaza(cliente):
    assert cliente.post(URL, json=[], headers=CARRITO).status_code == 422


def test_cuerpo_no_json_se_rechaza(cliente):
    r = cliente.post(URL, content="hola", headers={**CARRITO, "Content-Type": "application/json"})
    assert r.status_code == 422


# ---------------------------------------------------------------- consultas
def _cargar(cliente):
    datos = [("2026-12-04T10:00:00", "alto", "detener", "celula-1"),
             ("2026-12-04T10:05:00", "avanzar", "avanzar", "celula-1"),
             ("2026-12-04T10:10:00", "alto", "detener", "celula-2"),
             ("2026-12-04T10:20:00", "peaton", "detener", "celula-1")]
    lote = [{**EVENTO_VALIDO, "fecha": f, "senal": s, "accion": a, "carrito_id": c} for f, s, a, c in datos]
    assert cliente.post(URL, json=lote, headers=CARRITO).status_code == 201


def test_historial_del_mas_reciente_al_mas_antiguo(cliente):
    _cargar(cliente)
    fechas = [e["fecha"] for e in cliente.get(URL, headers=LECTURA).json()]
    assert fechas == sorted(fechas, reverse=True)


def test_filtros(cliente):
    _cargar(cliente)
    def contar(params):
        return len(cliente.get(URL, params=params, headers=APP).json())
    assert contar({"carrito_id": "celula-1"}) == 3
    assert contar({"senal": "alto"}) == 2
    assert contar({"accion": "detener", "carrito_id": "celula-1"}) == 2
    assert contar({"desde": "2026-12-04T10:05:00", "hasta": "2026-12-04T10:10:00"}) == 2
    assert contar({"limite": 1}) == 1


@pytest.mark.parametrize("params", [{"limite": 501}, {"limite": 0}, {"senal": "rojo"},
                                    {"desde": "2026-12-05T00:00:00", "hasta": "2026-12-04T00:00:00"}])
def test_filtros_invalidos(cliente, params):
    assert cliente.get(URL, params=params, headers=LECTURA).status_code == 422


def test_ultimo_evento(cliente):
    assert cliente.get(URL + "/ultimo", headers=LECTURA).status_code == 404
    _cargar(cliente)
    assert cliente.get(URL + "/ultimo", headers=LECTURA).json()["senal"] == "peaton"
    r = cliente.get(URL + "/ultimo", params={"carrito_id": "celula-2"}, headers=LECTURA)
    assert r.json()["fecha"] == "2026-12-04T10:10:00"
