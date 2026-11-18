"""Tokens incorrectos -> 401, tokens sin permiso -> 403, más límites y CORS."""

import pytest

from conftest import APP, CARRITO, EVENTO_VALIDO, LECTURA, con_token

GETS = ["/api/v1/eventos", "/api/v1/eventos/ultimo", "/api/v1/estadisticas", "/api/v1/control/celula-1"]


@pytest.mark.parametrize("encabezados", [
    {},                                              # sin token
    con_token("token-que-no-existe-000000"),         # token inventado
    {"Authorization": "token-carrito-de-prueba-123"},  # falta "Bearer"
    {"Authorization": "Basic dXN1YXJpbzpjbGF2ZQ=="},   # otro esquema
])
@pytest.mark.parametrize("metodo, url", [("get", u) for u in GETS] + [
    ("post", "/api/v1/eventos"), ("put", "/api/v1/control/celula-1")])
def test_token_incorrecto_da_401(cliente, encabezados, metodo, url):
    cuerpo = EVENTO_VALIDO if metodo == "post" else {"modo": "manual", "accion": "avanzar"}
    r = getattr(cliente, metodo)(url, headers=encabezados, **({} if metodo == "get" else {"json": cuerpo}))
    assert r.status_code == 401


def test_token_incorrecto_se_revisa_antes_que_los_datos(cliente):
    r = cliente.post("/api/v1/eventos", json={"basura": 1}, headers=con_token("x" * 30))
    assert r.status_code == 401


# --- Permisos por rol -------------------------------------------------------
def test_carrito_solo_registra_y_lee_control(cliente):
    assert cliente.post("/api/v1/eventos", json=EVENTO_VALIDO, headers=CARRITO).status_code == 201
    assert cliente.get("/api/v1/control/celula-1", headers=CARRITO).status_code == 200
    assert cliente.get("/api/v1/eventos", headers=CARRITO).status_code == 403
    assert cliente.get("/api/v1/eventos/ultimo", headers=CARRITO).status_code == 403
    assert cliente.get("/api/v1/estadisticas", headers=CARRITO).status_code == 403
    assert cliente.put("/api/v1/control/celula-1", json={"modo": "manual"}, headers=CARRITO).status_code == 403


def test_lectura_solo_hace_get(cliente):
    for url in GETS:
        assert cliente.get(url, headers=LECTURA).status_code in (200, 404), url
    assert cliente.post("/api/v1/eventos", json=EVENTO_VALIDO, headers=LECTURA).status_code == 403
    assert cliente.put("/api/v1/control/celula-1", json={"modo": "manual"}, headers=LECTURA).status_code == 403


def test_app_hace_get_y_put_control(cliente):
    for url in GETS:
        assert cliente.get(url, headers=APP).status_code in (200, 404), url
    assert cliente.put("/api/v1/control/celula-1", json={"modo": "manual"}, headers=APP).status_code == 200
    assert cliente.post("/api/v1/eventos", json=EVENTO_VALIDO, headers=APP).status_code == 403


def test_demo_lectura_no_puede_generar(cliente):
    assert cliente.post("/api/v1/demo/generar", headers=LECTURA).status_code == 403
    assert cliente.post("/api/v1/demo/generar", headers=APP).status_code == 201


def test_health_sin_token(cliente):
    r = cliente.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["base_datos"] == "memoria"


# --- Límites ----------------------------------------------------------------
def test_limite_de_peticiones_da_429(crear_cliente):
    cliente = crear_cliente(limite_por_segundo=5)
    codigos = [cliente.get("/api/v1/health").status_code for _ in range(8)]
    assert codigos[:5] == [200] * 5
    assert 429 in codigos[5:]


def test_cuerpo_demasiado_grande_da_413(crear_cliente):
    cliente = crear_cliente(tamano_maximo_bytes=1000)
    lote = [EVENTO_VALIDO] * 50       # ~6 KB
    assert cliente.post("/api/v1/eventos", json=lote, headers=CARRITO).status_code == 413


def test_cuerpo_grande_sin_content_length_da_413(crear_cliente):
    cliente = crear_cliente(tamano_maximo_bytes=1000)

    def trozos():
        for _ in range(10):
            yield b" " * 200

    r = cliente.post("/api/v1/eventos", content=trozos(),
                     headers={**CARRITO, "Content-Type": "application/json"})
    assert r.status_code == 413


def test_cors_permite_la_web(cliente):
    r = cliente.options("/api/v1/eventos", headers={
        "Origin": "https://web-de-4.ejemplo.com",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] in ("*", "https://web-de-4.ejemplo.com")
