"""
salud.py
--------
GET /health -> ¿la API y la base de datos funcionan? No pide token.
Render lo usa para saber si el servicio está vivo.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import VERSION, fechas
from ..modelos import Salud

router = APIRouter(tags=["Salud"])


@router.get(
    "/health",
    response_model=Salud,
    summary="Estado de la API y de la base de datos",
    description="No necesita token. Responde **200** si todo está bien y **503** si la base de "
                "datos no responde. `base_datos` dice cuál se usa (`memoria` en MODO_DEMO, `postgresql` "
                "con Supabase) y `conexion` si responde (`ok` / `error`).",
    responses={503: {"description": "La base de datos no responde.", "model": Salud}},
)
async def salud(request: Request):
    almacen = request.app.state.almacen
    config = request.app.state.config
    conectada = await almacen.ping()
    if almacen.nombre in ("memoria", "postgresql"):
        base = almacen.nombre
    else:                                   # MongoDB conserva su respuesta original
        base = "ok" if conectada else "error"
    cuerpo = Salud(estado="ok" if conectada else "error", base_datos=base,
                   conexion="ok" if conectada else "error", modo_demo=config.modo_demo,
                   version=VERSION, hora_servidor=fechas.ahora_local().replace(microsecond=0))
    return JSONResponse(status_code=200 if conectada else 503, content=cuerpo.model_dump(mode="json"))
