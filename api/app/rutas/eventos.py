"""
eventos.py
----------
POST /eventos         -> registra uno o varios eventos (solo el carrito)
GET  /eventos         -> historial con filtros, del más reciente al más antiguo
GET  /eventos/ultimo  -> último evento
"""

from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from .. import fechas
from ..modelos import (EJEMPLO_EVENTO, PATRON_CARRITO_ID, Accion, CuerpoEventos, EventoGuardado,
                       RespuestaGuardado, Senal)
from ..seguridad import PUEDE_LEER, PUEDE_REGISTRAR
from . import ERROR_422, ERRORES_COMUNES, obtener_almacen

router = APIRouter(tags=["Eventos"])

LIMITE_MAXIMO = 500

EJEMPLOS_POST = {
    "un_evento": {
        "summary": "Un evento",
        "description": "Lo normal: el carrito manda cada decisión en cuanto la toma.",
        "value": EJEMPLO_EVENTO,
    },
    "lote": {
        "summary": "Lista de eventos (guardados sin internet)",
        "description": "Si el carrito se quedó sin internet, sube después todos juntos (máximo 1000).",
        "value": [
            EJEMPLO_EVENTO,
            {**EJEMPLO_EVENTO, "fecha": "2026-12-04T10:15:35", "senal": "avanzar",
             "confianza": 0.88, "accion": "avanzar"},
        ],
    },
}

CarritoFiltro = Annotated[Optional[str], Query(pattern=PATRON_CARRITO_ID,
                                               description="Solo eventos de este carrito, ej. celula-1.")]


@router.post(
    "/eventos",
    status_code=status.HTTP_201_CREATED,
    response_model=RespuestaGuardado,
    summary="Registrar uno o varios eventos",
    description="Guarda una decisión del carrito. También acepta una **lista** de eventos "
                "(para subir los que se guardaron sin internet). Si algún evento de la lista "
                "es inválido, no se guarda ninguno y se responde 422.\n\n"
                "Permiso: **TOKEN_CARRITO**.",
    responses={**ERRORES_COMUNES, **ERROR_422},
)
async def registrar_eventos(
    cuerpo: Annotated[CuerpoEventos, Body(openapi_examples=EJEMPLOS_POST)],
    _rol=Depends(PUEDE_REGISTRAR),
    almacen=Depends(obtener_almacen),
):
    lista = cuerpo if isinstance(cuerpo, list) else [cuerpo]
    ids = await almacen.guardar_eventos([e.model_dump() for e in lista])
    return RespuestaGuardado(guardados=len(ids), ids=ids)


@router.get(
    "/eventos",
    response_model=list[EventoGuardado],
    summary="Historial de eventos",
    description="Devuelve los eventos del **más reciente al más antiguo**. Todos los filtros son "
                "opcionales y se pueden combinar. Las fechas van en hora local del carrito "
                "(`2026-12-04T10:15:32`).\n\nPermiso: **TOKEN_LECTURA** o **TOKEN_APP**.",
    responses={**ERRORES_COMUNES, **ERROR_422},
)
async def historial(
    carrito_id: CarritoFiltro = None,
    senal: Annotated[Optional[Senal], Query(description="Solo esta señal.")] = None,
    accion: Annotated[Optional[Accion], Query(description="Solo esta acción.")] = None,
    desde: Annotated[Optional[datetime], Query(description="Desde esta fecha (incluida).",
                                               examples=["2026-12-04T10:00:00"])] = None,
    hasta: Annotated[Optional[datetime], Query(description="Hasta esta fecha (incluida).",
                                               examples=["2026-12-04T11:00:00"])] = None,
    limite: Annotated[int, Query(ge=1, le=LIMITE_MAXIMO,
                                 description=f"Cuántos eventos devolver (1 a {LIMITE_MAXIMO}).")] = 50,
    _rol=Depends(PUEDE_LEER),
    almacen=Depends(obtener_almacen),
):
    desde = fechas.normalizar(desde) if desde else None
    hasta = fechas.normalizar(hasta) if hasta else None
    if desde and hasta and desde > hasta:
        raise HTTPException(status_code=422, detail="'desde' no puede ser posterior a 'hasta'.")
    return await almacen.buscar_eventos(carrito_id, senal, accion, desde, hasta, limite)


@router.get(
    "/eventos/ultimo",
    response_model=EventoGuardado,
    summary="Último evento",
    description="El evento más reciente (de un carrito o de todos). Útil para el dashboard en vivo: "
                "consúltalo cada 1-2 segundos.\n\nPermiso: **TOKEN_LECTURA** o **TOKEN_APP**.",
    responses={**ERRORES_COMUNES, **ERROR_422,
               404: {"description": "Todavía no hay eventos.",
                     "content": {"application/json": {"example": {"detail": "No hay eventos registrados."}}}}},
)
async def ultimo(
    carrito_id: CarritoFiltro = None,
    _rol=Depends(PUEDE_LEER),
    almacen=Depends(obtener_almacen),
):
    evento = await almacen.ultimo_evento(carrito_id)
    if evento is None:
        raise HTTPException(status_code=404, detail="No hay eventos registrados.")
    return evento
