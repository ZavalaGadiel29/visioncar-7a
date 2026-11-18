"""
control.py
----------
GET /control/{carrito_id} -> lo consulta el carrito (cada 300 ms) y la app.
PUT /control/{carrito_id} -> la app cambia el modo y la acción manual.

Regla de seguridad ("hombre muerto"): en modo manual la app debe repetir
el PUT al menos cada 2 segundos. Si no lo hace (se cerró la app, se fue
el internet del teléfono...), el GET responde accion "detener".
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from .. import fechas
from ..modelos import PATRON_CARRITO_ID, ControlEntrada, ControlRespuesta
from ..seguridad import PUEDE_CONTROLAR, PUEDE_LEER_CONTROL
from . import ERROR_422, ERRORES_COMUNES, obtener_almacen

router = APIRouter(tags=["Control remoto"])

CarritoRuta = Annotated[str, Path(pattern=PATRON_CARRITO_ID, description="Carrito, ej. celula-1.",
                                  examples=["celula-1"])]


def calcular_respuesta(datos: dict | None, vigencia_s: float) -> ControlRespuesta:
    if datos is None:   # la app nunca ha mandado nada: el carrito maneja solo
        return ControlRespuesta(modo="autonomo", accion="detener", actualizado=None)
    accion = datos["accion"]
    edad = (fechas.ahora_utc() - datos["actualizado"]).total_seconds()
    if datos["modo"] == "manual" and edad > vigencia_s:
        accion = "detener"
    return ControlRespuesta(modo=datos["modo"], accion=accion,
                            actualizado=fechas.utc_a_local(datos["actualizado"]))


@router.get(
    "/control/{carrito_id}",
    response_model=ControlRespuesta,
    summary="Consultar el modo y la acción manual",
    description="Si `modo` es `manual`, el carrito debe obedecer `accion`. Si la app no renovó "
                "el modo manual en los últimos 2 segundos, `accion` es **detener** por seguridad. "
                "Si nunca se ha configurado, responde modo `autonomo`.\n\n"
                "Permiso: **TOKEN_CARRITO**, **TOKEN_LECTURA** o **TOKEN_APP**.",
    responses={**ERRORES_COMUNES, **ERROR_422},
)
async def leer_control(
    carrito_id: CarritoRuta,
    request: Request,
    _rol=Depends(PUEDE_LEER_CONTROL),
    almacen=Depends(obtener_almacen),
):
    datos = await almacen.leer_control(carrito_id)
    return calcular_respuesta(datos, request.app.state.config.segundos_vigencia_manual)


@router.put(
    "/control/{carrito_id}",
    response_model=ControlRespuesta,
    summary="Cambiar el modo y la acción manual",
    description="La app manda el modo y, en manual, la acción. **Mientras esté en manual, repite "
                "este PUT cada 0.5-1 s** (aunque la acción no cambie); si pasan más de 2 s sin "
                "renovarlo, el carrito recibe `detener`. Para devolverle el control al carrito, "
                "manda `{\"modo\": \"autonomo\"}`.\n\nPermiso: **TOKEN_APP**.",
    responses={**ERRORES_COMUNES, **ERROR_422},
)
async def cambiar_control(
    carrito_id: CarritoRuta,
    cuerpo: ControlEntrada,
    request: Request,
    _rol=Depends(PUEDE_CONTROLAR),
    almacen=Depends(obtener_almacen),
):
    datos = await almacen.guardar_control(carrito_id, cuerpo.modo, cuerpo.accion, fechas.ahora_utc())
    return calcular_respuesta(datos, request.app.state.config.segundos_vigencia_manual)
