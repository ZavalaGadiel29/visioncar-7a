"""
componentes.py
--------------
Catálogo de las piezas del carrito (solo lectura):

    GET /componentes        -> lista completa (filtro opcional ?tipo=)
    GET /componentes/{id}   -> un componente (404 si no existe)

Los datos están en app/datos/componentes.json. Para agregar o corregir una
pieza se edita ese archivo y se hace push: Render vuelve a publicar solo.
Con PostgreSQL se leen de la tabla `componentes`, que se llena desde ese
mismo archivo la primera vez (si la tabla está vacía).
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Path as RutaParametro, Query

from ..catalogo import ARCHIVO_COMPONENTES, COMPONENTES, cargar_componentes  # noqa: F401
from ..modelos import Componente, TipoComponente
from ..seguridad import PUEDE_LEER
from . import ERROR_422, ERRORES_COMUNES, _error, obtener_almacen

router = APIRouter(tags=["Componentes"])


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
    almacen=Depends(obtener_almacen),
):
    return await almacen.listar_componentes(tipo)


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
    almacen=Depends(obtener_almacen),
):
    componente = await almacen.obtener_componente(id)
    if componente is None:
        raise HTTPException(status_code=404, detail=f"No existe el componente '{id}'.")
    return componente
