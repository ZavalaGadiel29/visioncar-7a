"""
almacen_memoria.py
------------------
Almacenamiento EN MEMORIA, usado cuando MODO_DEMO=1.

Funciona igual que la versión de MongoDB (mismas funciones y mismos
resultados), pero todo vive en listas de Python: al reiniciar la API los
datos se pierden. Sirve para que 4° trabaje sin base de datos.

Para no llenar la memoria se guardan como máximo 50 000 eventos; al
pasarse, se borran los más antiguos.
"""

import itertools
from datetime import datetime

from .fechas import ahora_utc

MAXIMO_EVENTOS = 50_000


class AlmacenMemoria:
    nombre = "memoria"

    def __init__(self, maximo_eventos: int = MAXIMO_EVENTOS):
        self.maximo_eventos = maximo_eventos
        self._eventos: list[dict] = []
        self._control: dict[str, dict] = {}
        self._contador = itertools.count(1)

    # --- Ciclo de vida ----------------------------------------------------
    async def iniciar(self) -> None:
        pass

    async def cerrar(self) -> None:
        pass

    async def ping(self) -> bool:
        return True

    # --- Eventos ----------------------------------------------------------
    async def guardar_eventos(self, eventos: list[dict]) -> list[str]:
        ids = []
        recibido = ahora_utc()
        for evento in eventos:
            numero = next(self._contador)
            ident = f"{numero:024x}"          # 24 caracteres hex, como un ObjectId
            self._eventos.append({**evento, "id": ident, "_orden": numero, "recibido": recibido})
            ids.append(ident)
        sobrantes = len(self._eventos) - self.maximo_eventos
        if sobrantes > 0:
            self._eventos.sort(key=lambda e: (e["fecha"], e["_orden"]))
            del self._eventos[:sobrantes]
        return ids

    def _filtrar(self, carrito_id=None, senal=None, accion=None, desde=None, hasta=None):
        for e in self._eventos:
            if carrito_id is not None and e["carrito_id"] != carrito_id:
                continue
            if senal is not None and e["senal"] != senal:
                continue
            if accion is not None and e["accion"] != accion:
                continue
            if desde is not None and e["fecha"] < desde:
                continue
            if hasta is not None and e["fecha"] > hasta:
                continue
            yield e

    async def buscar_eventos(self, carrito_id=None, senal=None, accion=None,
                             desde: datetime | None = None, hasta: datetime | None = None,
                             limite: int = 50) -> list[dict]:
        encontrados = sorted(self._filtrar(carrito_id, senal, accion, desde, hasta),
                             key=lambda e: (e["fecha"], e["_orden"]), reverse=True)
        return [_publico(e) for e in encontrados[:limite]]

    async def ultimo_evento(self, carrito_id=None) -> dict | None:
        resultado = await self.buscar_eventos(carrito_id=carrito_id, limite=1)
        return resultado[0] if resultado else None

    async def datos_estadisticas(self, carrito_id=None) -> dict:
        por_senal: dict[str, dict] = {}
        por_accion: dict[str, int] = {}
        menor = None
        total = 0
        for e in self._filtrar(carrito_id):
            total += 1
            datos = por_senal.setdefault(e["senal"], {"n": 0, "suma": 0.0})
            datos["n"] += 1
            datos["suma"] += e["confianza"]
            por_accion[e["accion"]] = por_accion.get(e["accion"], 0) + 1
            # menor confianza; si empatan, el más reciente
            if menor is None or (e["confianza"], -_clave_tiempo(e)) < (menor["confianza"], -_clave_tiempo(menor)):
                menor = e
        return {"total": total, "por_senal": por_senal, "por_accion": por_accion,
                "menor": _publico(menor) if menor else None}

    # --- Control remoto ---------------------------------------------------
    async def leer_control(self, carrito_id: str) -> dict | None:
        return self._control.get(carrito_id)

    async def guardar_control(self, carrito_id: str, modo: str, accion: str,
                              actualizado: datetime) -> dict:
        self._control[carrito_id] = {"modo": modo, "accion": accion, "actualizado": actualizado}
        return self._control[carrito_id]


def _clave_tiempo(e):
    return e["fecha"].timestamp() + e["_orden"] * 1e-9


def _publico(e: dict) -> dict:
    """Quita los campos internos antes de devolver el evento."""
    return {k: v for k, v in e.items() if k not in ("_orden", "recibido")}
