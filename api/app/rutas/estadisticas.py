"""
estadisticas.py
---------------
GET /estadisticas -> resumen de los eventos (de un carrito o de todos).
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query

from ..modelos import ACCIONES, PATRON_CARRITO_ID, SENALES, Estadisticas
from ..seguridad import PUEDE_LEER
from . import ERROR_422, ERRORES_COMUNES, obtener_almacen

router = APIRouter(tags=["Estadísticas"])


def armar_estadisticas(carrito_id, datos: dict) -> Estadisticas:
    """Convierte los datos crudos del almacenamiento en la respuesta final."""
    por_senal = datos["por_senal"]
    conteo_senal = {s: por_senal.get(s, {}).get("n", 0) for s in SENALES}
    promedios = {
        s: (round(por_senal[s]["suma"] / por_senal[s]["n"], 4) if por_senal.get(s, {}).get("n") else None)
        for s in SENALES
    }
    # Señal más detectada: la de mayor conteo, sin contar "ninguna".
    # Si empatan, gana la que aparece primero en la lista del contrato.
    candidatas = [s for s in SENALES if s != "ninguna" and conteo_senal[s] > 0]
    mas_detectada = max(candidatas, key=lambda s: (conteo_senal[s], -SENALES.index(s))) if candidatas else None
    return Estadisticas(
        carrito_id=carrito_id,
        total_eventos=datos["total"],
        conteo_por_senal=conteo_senal,
        conteo_por_accion={a: datos["por_accion"].get(a, 0) for a in ACCIONES},
        confianza_promedio_por_senal=promedios,
        senal_mas_detectada=mas_detectada,
        evento_menor_confianza=datos["menor"],
    )


@router.get(
    "/estadisticas",
    response_model=Estadisticas,
    summary="Estadísticas de los eventos",
    description="Total de eventos, conteo por señal y por acción, confianza promedio por señal, "
                "señal más detectada (sin contar `ninguna`) y el evento de menor confianza.\n\n"
                "Sin `carrito_id` se calcula con todos los carritos.\n\n"
                "Permiso: **TOKEN_LECTURA** o **TOKEN_APP**.",
    responses={**ERRORES_COMUNES, **ERROR_422},
)
async def estadisticas(
    carrito_id: Annotated[Optional[str], Query(pattern=PATRON_CARRITO_ID,
                                               description="Carrito a resumir, ej. celula-1.")] = None,
    _rol=Depends(PUEDE_LEER),
    almacen=Depends(obtener_almacen),
):
    return armar_estadisticas(carrito_id, await almacen.datos_estadisticas(carrito_id))
