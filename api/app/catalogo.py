"""
catalogo.py
-----------
Lee el catálogo de piezas del carrito desde app/datos/componentes.json.

- Memoria y MongoDB lo sirven directamente desde este archivo.
- PostgreSQL lo copia a la tabla `componentes` la primera vez (si está vacía).
"""

import json
from pathlib import Path

from pydantic import TypeAdapter

from .modelos import Componente

ARCHIVO_COMPONENTES = Path(__file__).resolve().parent / "datos" / "componentes.json"


def cargar_componentes(ruta: Path = ARCHIVO_COMPONENTES) -> list[Componente]:
    """Lee y valida el JSON. Si tiene un error, la API no arranca (mejor que fallar después)."""
    with open(ruta, encoding="utf-8") as archivo:
        componentes = TypeAdapter(list[Componente]).validate_python(json.load(archivo))
    ids = [c.id for c in componentes]
    if len(ids) != len(set(ids)):
        raise RuntimeError(f"Hay ids repetidos en {ruta.name}.")
    return componentes


# Se carga una sola vez al importar: es un catálogo fijo.
COMPONENTES = cargar_componentes()


def listar(tipo: str | None = None) -> list[dict]:
    return [c.model_dump() for c in COMPONENTES if tipo is None or c.tipo == tipo]


def obtener(id_componente: str) -> dict | None:
    return next((c.model_dump() for c in COMPONENTES if c.id == id_componente), None)
