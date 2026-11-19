"""
main.py
-------
Arma la aplicación FastAPI de VisionCar.

Se ejecuta con (la opción --factory hace que uvicorn llame a crear_app()):

    uvicorn app.main:crear_app --factory --reload

Qué hace crear_app():
  1. Lee la configuración (.env / variables de entorno).
  2. Elige dónde guardar: memoria si MODO_DEMO=1, MongoDB Atlas si no.
  3. Agrega las protecciones: límite de peticiones, tamaño máximo y CORS.
  4. Registra las rutas bajo /api/v1 y la documentación en /docs.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pymongo.errors import PyMongoError

from . import VERSION, fechas
from .almacen_memoria import AlmacenMemoria
from .configuracion import Configuracion, cargar_configuracion
from .limites import LimitePeticiones, TamanoMaximoCuerpo
from .rutas import componentes, control, demo, estadisticas, eventos, salud

PREFIJO = "/api/v1"
registro = logging.getLogger("visioncar")

DESCRIPCION = """
API en la nube del carrito autónomo **VisionCar**. El carrito registra cada decisión que toma;
la página web, el dashboard en vivo y la app móvil la consultan.

### Cómo autenticarse
Todas las rutas (menos `/api/v1/health`) piden un token en el encabezado:

```
Authorization: Bearer <token>
```

En esta página: botón **Authorize** (arriba a la derecha), pega el token **sin** la palabra *Bearer*.

| Token | Puede usar |
|---|---|
| `TOKEN_CARRITO` | `POST /eventos`, `GET /control` |
| `TOKEN_LECTURA` | todos los `GET` (web y dashboard) |
| `TOKEN_APP` | todos los `GET` y `PUT /control` (app móvil) |

### Códigos de error
`401` falta el token o es incorrecto · `403` el token no tiene permiso · `404` no hay datos ·
`413` cuerpo demasiado grande · `422` datos fuera del contrato · `429` demasiadas peticiones ·
`503` la base de datos no responde.

### Fechas
Hora local del carrito, sin zona: `2026-12-04T10:15:32`.
"""

ETIQUETAS = [
    {"name": "Eventos", "description": "Decisiones registradas por el carrito."},
    {"name": "Estadísticas", "description": "Resúmenes para el dashboard."},
    {"name": "Control remoto", "description": "Modo manual desde la app móvil."},
    {"name": "Componentes", "description": "Catálogo de las piezas del carrito (solo lectura)."},
    {"name": "Modo demo", "description": "Datos falsos para probar sin carrito (solo MODO_DEMO=1)."},
    {"name": "Salud", "description": "Estado de la API (sin token)."},
]


def _crear_almacen(config: Configuracion):
    if config.modo_demo:
        return AlmacenMemoria()
    from .almacen_mongo import AlmacenMongo
    return AlmacenMongo(config.mongodb_uri, config.mongodb_base)


def crear_app(config: Configuracion | None = None) -> FastAPI:
    config = config or cargar_configuracion()
    fechas.establecer_zona(config.zona_horaria)
    almacen = _crear_almacen(config)

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        await almacen.iniciar()
        if config.modo_demo:
            registro.warning("MODO_DEMO=1: los datos se guardan en MEMORIA y se pierden al reiniciar.")
            if config.demo_eventos_iniciales > 0:
                eventos_falsos = demo.generar_eventos_falsos("celula-1", config.demo_eventos_iniciales, 30)
                await almacen.guardar_eventos(eventos_falsos)
        yield
        await almacen.cerrar()

    app = FastAPI(
        title="VisionCar API",
        description=DESCRIPCION,
        version=VERSION,
        openapi_tags=ETIQUETAS,
        lifespan=ciclo_de_vida,
        swagger_ui_parameters={"persistAuthorization": True, "docExpansion": "list"},
    )
    app.state.config = config
    app.state.almacen = almacen

    # --- Protecciones (la última que se agrega es la primera que se ejecuta) ---
    app.add_middleware(TamanoMaximoCuerpo, maximo_bytes=config.tamano_maximo_bytes)
    app.add_middleware(LimitePeticiones, por_segundo=config.limite_por_segundo)
    # CORS va por fuera, para que incluso los errores 413/429 lleven sus encabezados
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.cors_origenes),
        allow_credentials=False,          # se usan tokens, no cookies
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # --- Errores con mensaje en español ---
    @app.exception_handler(RequestValidationError)
    async def datos_invalidos(_request: Request, error: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"mensaje": "Los datos no cumplen el contrato de la API. Revisa 'detail'.",
                     "detail": jsonable_encoder(error.errors())},
        )

    @app.exception_handler(PyMongoError)
    async def base_caida(_request: Request, error: PyMongoError):
        registro.error("Error de MongoDB: %s", error)
        return JSONResponse(status_code=503, content={"detail": "La base de datos no responde. Intenta más tarde."})

    # --- Rutas ---
    app.include_router(eventos.router, prefix=PREFIJO)
    app.include_router(estadisticas.router, prefix=PREFIJO)
    app.include_router(control.router, prefix=PREFIJO)
    app.include_router(componentes.router, prefix=PREFIJO)
    app.include_router(salud.router, prefix=PREFIJO)
    if config.modo_demo:
        app.include_router(demo.router, prefix=PREFIJO)

    @app.get("/", include_in_schema=False)
    async def inicio():
        return RedirectResponse(url="/docs")

    return app
