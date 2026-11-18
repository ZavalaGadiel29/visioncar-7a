"""
Rutas de la API (cada archivo agrupa los endpoints de un tema).
Aquí está lo que comparten: cómo obtener el almacenamiento y las
respuestas de error que se muestran en /docs.
"""

from fastapi import Request

from ..modelos import CarritoId  # noqa: F401  (se reexporta para las rutas)


def obtener_almacen(request: Request):
    """Devuelve el almacenamiento activo (memoria en MODO_DEMO o MongoDB)."""
    return request.app.state.almacen


def _error(descripcion, ejemplo):
    return {"description": descripcion,
            "content": {"application/json": {"example": {"detail": ejemplo}}}}


ERROR_401 = {401: _error("Falta el token o es incorrecto.", "Token incorrecto.")}
ERROR_403 = {403: _error("El token es válido pero no tiene permiso para esta operación.",
                         "El token de 'lectura' no tiene permiso para esta operación.")}
ERROR_422 = {422: {"description": "Los datos no cumplen el contrato (valor no permitido, "
                                  "campo faltante o extra, número fuera de rango)."}}
ERROR_429 = {429: _error("Demasiadas peticiones desde la misma IP.",
                         "Demasiadas peticiones: máximo 20 por segundo.")}

ERRORES_COMUNES = {**ERROR_401, **ERROR_403, **ERROR_429}
