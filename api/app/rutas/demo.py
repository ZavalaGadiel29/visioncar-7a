"""
demo.py
-------
POST /demo/generar -> crea eventos falsos pero realistas (solo con MODO_DEMO=1).

Así 4° puede programar la web, el dashboard y la app antes de que el
carrito funcione. Los eventos imitan al carrito real:
  - alto y peaton -> detener;  izquierda -> girar_izq;  derecha -> girar_der
  - avanzar -> avanzar;  ninguna -> casi siempre avanzar (a veces gira en curvas)
  - "ninguna" lleva confianza 0 (así lo registra el carrito)
  - de vez en cuando hay eventos en modo manual
"""

import random
from datetime import timedelta

from fastapi import APIRouter, Depends, status

from .. import fechas
from ..modelos import DemoEntrada, DemoRespuesta
from ..seguridad import PUEDE_GENERAR_DEMO
from . import ERROR_422, ERRORES_COMUNES, obtener_almacen

router = APIRouter(tags=["Modo demo"])

# Qué tan seguido aparece cada señal (suman 100)
PROBABILIDAD_SENAL = {"ninguna": 45, "avanzar": 18, "alto": 12, "izquierda": 9, "derecha": 9, "peaton": 7}
ACCION_POR_SENAL = {"alto": "detener", "peaton": "detener", "izquierda": "girar_izq",
                    "derecha": "girar_der", "avanzar": "avanzar"}


def generar_eventos_falsos(carrito_id: str, cantidad: int, minutos: int,
                           azar: random.Random | None = None) -> list[dict]:
    azar = azar or random.Random()
    ahora = fechas.ahora_local().replace(microsecond=0)
    inicio = ahora - timedelta(minutes=minutos)
    segundos = sorted(azar.uniform(0, minutos * 60) for _ in range(cantidad))
    senales, pesos = zip(*PROBABILIDAD_SENAL.items())
    modo_manual_hasta = -1.0

    eventos = []
    for s in segundos:
        senal = azar.choices(senales, weights=pesos)[0]
        if senal == "ninguna":
            confianza = 0.0
            accion = azar.choices(["avanzar", "girar_izq", "girar_der"], weights=[80, 10, 10])[0]
        else:
            # casi siempre alta; a veces baja (señal lejana o mal iluminada)
            confianza = azar.uniform(0.45, 0.62) if azar.random() < 0.08 else azar.uniform(0.62, 0.99)
            accion = ACCION_POR_SENAL[senal]
        # Rachas cortas de modo manual (como si alguien tomara el control desde la app)
        if s > modo_manual_hasta and azar.random() < 0.02:
            modo_manual_hasta = s + azar.uniform(10, 40)
        modo = "manual" if s <= modo_manual_hasta else "autonomo"
        if modo == "manual":
            accion = azar.choice(["avanzar", "detener", "girar_izq", "girar_der"])
        eventos.append({
            "carrito_id": carrito_id,
            "fecha": fechas.normalizar(inicio + timedelta(seconds=s)).replace(microsecond=0),
            "senal": senal,
            "confianza": round(confianza, 3),
            "accion": accion,
            "modo": modo,
        })
    return eventos


@router.post(
    "/demo/generar",
    status_code=status.HTTP_201_CREATED,
    response_model=DemoRespuesta,
    summary="Generar eventos falsos (solo MODO_DEMO)",
    description="Crea `cantidad` eventos realistas repartidos en los últimos `minutos`. "
                "Solo existe cuando la API corre con `MODO_DEMO=1`; los datos viven en memoria y "
                "se borran si la API se reinicia.\n\nPermiso: **TOKEN_APP** o **TOKEN_CARRITO**.",
    responses={**ERRORES_COMUNES, **ERROR_422},
)
async def generar(
    cuerpo: DemoEntrada = DemoEntrada(),
    _rol=Depends(PUEDE_GENERAR_DEMO),
    almacen=Depends(obtener_almacen),
):
    eventos = generar_eventos_falsos(cuerpo.carrito_id, cuerpo.cantidad, cuerpo.minutos)
    ids = await almacen.guardar_eventos(eventos)
    return DemoRespuesta(generados=len(ids), carrito_id=cuerpo.carrito_id)
