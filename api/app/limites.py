"""
limites.py
----------
Dos protecciones que se aplican a TODAS las peticiones, antes de llegar a
las rutas (son "middlewares"):

1. LimitePeticiones: máximo N peticiones por segundo por cada IP
   (por defecto 20). Si se pasa, responde 429 "Demasiadas peticiones".
   Usa una ventana deslizante: se guardan las horas de las últimas
   peticiones de cada IP y se cuentan las del último segundo.

2. TamanoMaximoCuerpo: rechaza con 413 los cuerpos más grandes que el
   máximo (por defecto 512 KB), para que nadie llene la memoria.

Nota: los contadores viven en la memoria del proceso. Con un solo
proceso (como en Render gratis) es exacto.
"""

import time
from collections import deque

from starlette.responses import JSONResponse


class LimitePeticiones:
    def __init__(self, app, por_segundo: int):
        self.app = app
        self.por_segundo = int(por_segundo)
        self._historial: dict[str, deque] = {}
        self._ultima_limpieza = time.monotonic()

    async def __call__(self, scope, receive, send):
        # Solo HTTP; las peticiones OPTIONS (pre-vuelo de CORS) no cuentan
        if scope["type"] != "http" or self.por_segundo <= 0 or scope["method"] == "OPTIONS":
            await self.app(scope, receive, send)
            return

        ahora = time.monotonic()
        ip = (scope.get("client") or ("desconocida", 0))[0]
        marcas = self._historial.setdefault(ip, deque())
        while marcas and ahora - marcas[0] >= 1.0:
            marcas.popleft()

        if len(marcas) >= self.por_segundo:
            respuesta = JSONResponse(
                status_code=429,
                content={"detail": f"Demasiadas peticiones: máximo {self.por_segundo} por segundo."},
                headers={"Retry-After": "1"},
            )
            await respuesta(scope, receive, send)
            return

        marcas.append(ahora)
        self._limpiar(ahora)
        await self.app(scope, receive, send)

    def _limpiar(self, ahora):
        """Cada minuto borra las IP que ya no han hecho peticiones."""
        if ahora - self._ultima_limpieza < 60:
            return
        self._ultima_limpieza = ahora
        for ip in [ip for ip, m in self._historial.items() if not m or ahora - m[-1] >= 1.0]:
            del self._historial[ip]


class TamanoMaximoCuerpo:
    def __init__(self, app, maximo_bytes: int):
        self.app = app
        self.maximo = int(maximo_bytes)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        # 1) Si el cliente declara el tamaño, se revisa de inmediato
        encabezados = dict(scope.get("headers") or [])
        declarado = encabezados.get(b"content-length")
        if declarado is not None and declarado.isdigit() and int(declarado) > self.maximo:
            await self._rechazar(scope, receive, send)
            return

        # 2) Si no lo declara (o miente), se lee el cuerpo aquí contando los
        #    bytes, y después se le entrega completo a la aplicación.
        partes, recibido = [], 0
        while True:
            mensaje = await receive()
            if mensaje["type"] != "http.request":      # el cliente se desconectó
                return
            trozo = mensaje.get("body", b"")
            recibido += len(trozo)
            if recibido > self.maximo:
                await self._rechazar(scope, receive, send)
                return
            partes.append(trozo)
            if not mensaje.get("more_body", False):
                break

        cuerpo = b"".join(partes)
        entregado = False

        async def recibir_guardado():
            nonlocal entregado
            if not entregado:
                entregado = True
                return {"type": "http.request", "body": cuerpo, "more_body": False}
            return await receive()

        await self.app(scope, recibir_guardado, send)

    async def _rechazar(self, scope, receive, send):
        respuesta = JSONResponse(
            status_code=413,
            content={"detail": f"El cuerpo de la petición supera el máximo de {self.maximo} bytes."},
        )
        await respuesta(scope, receive, send)
