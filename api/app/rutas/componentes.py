"""
componentes.py
--------------
Catálogo de las piezas del carrito (solo lectura):

    GET /componentes        -> lista completa (filtro opcional ?tipo=)
    GET /componentes/{id}   -> un componente (404 si no existe)

Los datos están en app/datos/componentes.json. Para agregar o corregir una
pieza se edita ese archivo y se hace push: Render vuelve a publicar solo.
"""

import json
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Path as RutaParametro, Query
from pydantic import TypeAdapter

from ..modelos import Componente, TipoComponente
from ..seguridad import PUEDE_LEER
from . import ERROR_422, ERRORES_COMUNES, _error

router = APIRouter(tags=["Componentes"])

ARCHIVO_COMPONENTES = Path(__file__).resolve().parent.parent / "datos" / "componentes.json"


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
POR_ID = {c.id: c for c in COMPONENTES}


@router.get(
    "/componentes",
    response_model=list[Componente],
    summary="Lista de componentes del carrito",
    description="Todas las piezas del carrito con su tipo, voltaje, cantidad y función.\n\n"
                "Filtro opcional `tipo`: `control`, `sensor`, `comunicación`, `actuador`, `energía` "
                "o `estructura` (con acento; `fetch` y el navegador lo codifican solos).\n\n"
                "Permiso: **TOKEN_LECTURA** o **TOKEN_APP**.",
    responses={**ERRORES_COMUNES, **ERROR_422},
)
async def listar_componentes(
    tipo: Annotated[Optional[TipoComponente], Query(description="Solo los de ese tipo, ej. energía.",
                                                    openapi_examples={
                                                        "todos": {"summary": "Sin filtro", "value": None},
                                                        "energia": {"summary": "Solo energía", "value": "energía"},
                                                        "actuador": {"summary": "Solo actuadores", "value": "actuador"},
                                                    })] = None,
    _rol=Depends(PUEDE_LEER),
):
    return [c for c in COMPONENTES if tipo is None or c.tipo == tipo]


@router.get(
    "/componentes/{id}",
    response_model=Componente,
    summary="Un componente",
    description="Devuelve una pieza por su `id` (ej. `esp32cam`, `l298n`, `motor_tt`).\n\n"
                "Permiso: **TOKEN_LECTURA** o **TOKEN_APP**.",
    responses={**ERRORES_COMUNES,
               404: _error("No existe un componente con ese id.", "No existe el componente 'motor_x'.")},
)
async def obtener_componente(
    id: Annotated[str, RutaParametro(description="Id del componente.", examples=["esp32cam"])],
    _rol=Depends(PUEDE_LEER),
):
    componente = POR_ID.get(id)
    if componente is None:
        raise HTTPException(status_code=404, detail=f"No existe el componente '{id}'.")
    return componente
