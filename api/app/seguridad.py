"""
seguridad.py
------------
Tokens Bearer: cada petición manda el encabezado

    Authorization: Bearer <token>

Hay tres tokens (leídos de variables de entorno) y cada uno tiene un ROL:

    TOKEN_CARRITO -> rol "carrito": solo POST /eventos y GET /control
    TOKEN_LECTURA -> rol "lectura": solo los GET (página web y dashboard)
    TOKEN_APP     -> rol "app":     los GET y PUT /control (app móvil)

Errores:
    401 = no mandaste token o el token no existe
    403 = el token es válido, pero ese rol no tiene permiso para esa ruta
"""

import secrets
from enum import Enum

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


class Rol(str, Enum):
    CARRITO = "carrito"
    LECTURA = "lectura"
    APP = "app"


esquema_bearer = HTTPBearer(
    auto_error=False,
    description="Pega aquí el token (sin la palabra Bearer).",
)


def identificar_rol(token: str, config) -> Rol | None:
    """Compara el token con los tres configurados (en tiempo constante)."""
    encontrado = None
    for rol, valor in (
        (Rol.CARRITO, config.token_carrito),
        (Rol.LECTURA, config.token_lectura),
        (Rol.APP, config.token_app),
    ):
        if valor and secrets.compare_digest(token.encode(), valor.encode()):
            encontrado = rol
    return encontrado


def requiere(*roles: Rol):
    """Crea una dependencia de FastAPI que solo deja pasar a los roles indicados."""

    async def dependencia(
        request: Request,
        credenciales: HTTPAuthorizationCredentials | None = Depends(esquema_bearer),
    ) -> Rol:
        if credenciales is None or not credenciales.credentials:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Falta el token. Envía el encabezado 'Authorization: Bearer <token>'.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        rol = identificar_rol(credenciales.credentials, request.app.state.config)
        if rol is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token incorrecto.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if rol not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"El token de '{rol.value}' no tiene permiso para esta operación.",
            )
        return rol

    return dependencia


# Permisos de cada grupo de rutas
PUEDE_REGISTRAR = requiere(Rol.CARRITO)                       # POST /eventos
PUEDE_LEER = requiere(Rol.LECTURA, Rol.APP)                   # GET /eventos, /estadisticas
PUEDE_LEER_CONTROL = requiere(Rol.CARRITO, Rol.LECTURA, Rol.APP)  # GET /control
PUEDE_CONTROLAR = requiere(Rol.APP)                           # PUT /control
PUEDE_GENERAR_DEMO = requiere(Rol.APP, Rol.CARRITO)           # POST /demo/generar
