"""Catálogo de componentes: lista, filtro por tipo, uno por id, 404 y permisos."""

import json

import pytest

from app.rutas.componentes import ARCHIVO_COMPONENTES
from conftest import APP, CARRITO, LECTURA, con_token

URL = "/api/v1/componentes"
IDS = ["esp32cam", "ov2640", "antena", "l298n", "motor_tt", "hw131",
       "bateria_18650", "portapilas", "chasis", "ruedas", "rueda_loca"]


def test_lista_completa(cliente):
    r = cliente.get(URL, headers=LECTURA)
    assert r.status_code == 200
    datos = r.json()
    assert [c["id"] for c in datos] == IDS                     # mismo orden que el JSON
    for c in datos:
        assert set(c) == {"id", "nombre", "tipo", "voltaje", "cantidad", "funcion"}


def test_lista_igual_al_archivo(cliente):
    with open(ARCHIVO_COMPONENTES, encoding="utf-8") as archivo:
        assert cliente.get(URL, headers=APP).json() == json.load(archivo)


@pytest.mark.parametrize("tipo, esperados", [
    ("energía", ["hw131", "bateria_18650", "portapilas"]),
    ("actuador", ["l298n", "motor_tt"]),
    ("estructura", ["chasis", "ruedas", "rueda_loca"]),
    ("comunicación", ["antena"]),
    ("control", ["esp32cam"]),
    ("sensor", ["ov2640"]),
])
def test_filtro_por_tipo(cliente, tipo, esperados):
    r = cliente.get(URL, params={"tipo": tipo}, headers=LECTURA)
    assert r.status_code == 200
    assert [c["id"] for c in r.json()] == esperados
    assert all(c["tipo"] == tipo for c in r.json())


def test_tipo_invalido_da_422(cliente):
    assert cliente.get(URL, params={"tipo": "motor"}, headers=LECTURA).status_code == 422


def test_un_componente(cliente):
    r = cliente.get(f"{URL}/l298n", headers=LECTURA)
    assert r.status_code == 200
    assert r.json() == {"id": "l298n", "nombre": "Driver de motores L298N", "tipo": "actuador",
                        "voltaje": "5–35 V (en el carrito 7.4 V)", "cantidad": 1,
                        "funcion": "Da la potencia a los motores y controla su sentido"}


def test_cantidades_y_voltaje_nulo(cliente):
    assert cliente.get(f"{URL}/motor_tt", headers=APP).json()["cantidad"] == 2
    assert cliente.get(f"{URL}/chasis", headers=APP).json()["voltaje"] is None


def test_componente_inexistente_da_404(cliente):
    r = cliente.get(f"{URL}/motor_x", headers=LECTURA)
    assert r.status_code == 404
    assert r.json() == {"detail": "No existe el componente 'motor_x'."}


@pytest.mark.parametrize("url", [URL, f"{URL}/esp32cam"])
def test_sin_token_da_401(cliente, url):
    assert cliente.get(url).status_code == 401
    assert cliente.get(url, headers=con_token("token-que-no-existe-000000")).status_code == 401


@pytest.mark.parametrize("url", [URL, f"{URL}/esp32cam"])
def test_token_carrito_no_puede_leer_componentes(cliente, url):
    assert cliente.get(url, headers=CARRITO).status_code == 403


def test_solo_lectura(cliente):
    assert cliente.post(URL, json={}, headers=APP).status_code == 405
    assert cliente.put(f"{URL}/l298n", json={}, headers=APP).status_code == 405


def test_aparece_en_docs(cliente):
    rutas = cliente.get("/openapi.json").json()["paths"]
    assert "/api/v1/componentes" in rutas and "/api/v1/componentes/{id}" in rutas
