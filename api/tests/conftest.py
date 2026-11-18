"""
Preparación común de las pruebas.

Las pruebas usan MODO_DEMO (memoria), así que NO necesitan MongoDB ni
internet. Los tokens son de prueba y solo existen aquí.
"""

import pytest
from fastapi.testclient import TestClient

from app.configuracion import Configuracion
from app.main import crear_app

TOKEN_CARRITO = "token-carrito-de-prueba-123"
TOKEN_LECTURA = "token-lectura-de-prueba-456"
TOKEN_APP = "token-app-de-prueba-789"

EVENTO_VALIDO = {
    "carrito_id": "celula-1",
    "fecha": "2026-12-04T10:15:32",
    "senal": "alto",
    "confianza": 0.93,
    "accion": "detener",
    "modo": "autonomo",
}


def con_token(token):
    return {"Authorization": f"Bearer {token}"}


CARRITO = con_token(TOKEN_CARRITO)
LECTURA = con_token(TOKEN_LECTURA)
APP = con_token(TOKEN_APP)


def configuracion(**cambios):
    datos = dict(token_carrito=TOKEN_CARRITO, token_lectura=TOKEN_LECTURA, token_app=TOKEN_APP,
                 modo_demo=True, demo_eventos_iniciales=0, limite_por_segundo=10_000)
    datos.update(cambios)
    return Configuracion(**datos)


@pytest.fixture
def crear_cliente():
    """Permite crear un cliente con configuración distinta en una prueba."""
    clientes = []

    def _crear(**cambios):
        cliente = TestClient(crear_app(configuracion(**cambios)))
        cliente.__enter__()          # ejecuta el arranque (lifespan)
        clientes.append(cliente)
        return cliente

    yield _crear
    for cliente in clientes:
        cliente.__exit__(None, None, None)


@pytest.fixture
def cliente(crear_cliente):
    return crear_cliente()
