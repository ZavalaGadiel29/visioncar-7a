"""
almacen_mongo.py
----------------
Almacenamiento en MongoDB Atlas (modo normal, MODO_DEMO=0).

Colecciones:
  - eventos: una decisión del carrito por documento (ver DIAGRAMA.md).
  - control: un documento por carrito (_id = carrito_id) con el modo y la
             acción manual que puso la app.

Usa el cliente asíncrono oficial de pymongo (AsyncMongoClient).
Los índices se crean al arrancar; si ya existen, MongoDB no hace nada.
"""

import logging
from datetime import datetime

from pymongo import ASCENDING, DESCENDING, AsyncMongoClient

registro = logging.getLogger("visioncar.mongo")


class AlmacenMongo:
    nombre = "mongodb"

    def __init__(self, uri: str, base: str):
        self._cliente = AsyncMongoClient(uri, serverSelectionTimeoutMS=5000, appname="visioncar-api")
        self._bd = self._cliente[base]
        self._eventos = self._bd["eventos"]
        self._control = self._bd["control"]

    # --- Ciclo de vida ----------------------------------------------------
    async def iniciar(self) -> None:
        try:
            await self._eventos.create_index([("carrito_id", ASCENDING), ("fecha", DESCENDING)],
                                             name="idx_carrito_fecha")
            await self._eventos.create_index([("fecha", DESCENDING)], name="idx_fecha")
            await self._eventos.create_index([("senal", ASCENDING), ("fecha", DESCENDING)],
                                             name="idx_senal_fecha")
            await self._eventos.create_index([("carrito_id", ASCENDING), ("confianza", ASCENDING)],
                                             name="idx_carrito_confianza")
        except Exception as error:   # la API arranca igual; /health mostrará el error
            registro.error("No se pudieron crear los índices en MongoDB: %s", error)

    async def cerrar(self) -> None:
        await self._cliente.close()

    async def ping(self) -> bool:
        try:
            await self._bd.command("ping")
            return True
        except Exception:
            return False

    # --- Eventos ----------------------------------------------------------
    async def guardar_eventos(self, eventos: list[dict]) -> list[str]:
        from .fechas import ahora_utc
        recibido = ahora_utc()
        documentos = [{**e, "recibido": recibido} for e in eventos]
        resultado = await self._eventos.insert_many(documentos, ordered=True)
        return [str(i) for i in resultado.inserted_ids]

    @staticmethod
    def _filtro(carrito_id=None, senal=None, accion=None, desde=None, hasta=None) -> dict:
        filtro: dict = {}
        if carrito_id is not None:
            filtro["carrito_id"] = carrito_id
        if senal is not None:
            filtro["senal"] = senal
        if accion is not None:
            filtro["accion"] = accion
        if desde is not None or hasta is not None:
            filtro["fecha"] = {}
            if desde is not None:
                filtro["fecha"]["$gte"] = desde
            if hasta is not None:
                filtro["fecha"]["$lte"] = hasta
        return filtro

    async def buscar_eventos(self, carrito_id=None, senal=None, accion=None,
                             desde: datetime | None = None, hasta: datetime | None = None,
                             limite: int = 50) -> list[dict]:
        cursor = (self._eventos.find(self._filtro(carrito_id, senal, accion, desde, hasta))
                  .sort([("fecha", DESCENDING), ("_id", DESCENDING)])
                  .limit(limite))
        return [_publico(d) async for d in cursor]

    async def ultimo_evento(self, carrito_id=None) -> dict | None:
        resultado = await self.buscar_eventos(carrito_id=carrito_id, limite=1)
        return resultado[0] if resultado else None

    async def datos_estadisticas(self, carrito_id=None) -> dict:
        tuberia = [
            {"$match": self._filtro(carrito_id)},
            {"$facet": {
                "total": [{"$count": "n"}],
                "por_senal": [{"$group": {"_id": "$senal", "n": {"$sum": 1}, "suma": {"$sum": "$confianza"}}}],
                "por_accion": [{"$group": {"_id": "$accion", "n": {"$sum": 1}}}],
                "menor": [{"$sort": {"confianza": 1, "fecha": -1, "_id": -1}}, {"$limit": 1}],
            }},
        ]
        cursor = await self._eventos.aggregate(tuberia)
        datos = (await cursor.to_list(length=1))[0]
        return {
            "total": datos["total"][0]["n"] if datos["total"] else 0,
            "por_senal": {g["_id"]: {"n": g["n"], "suma": g["suma"]} for g in datos["por_senal"]},
            "por_accion": {g["_id"]: g["n"] for g in datos["por_accion"]},
            "menor": _publico(datos["menor"][0]) if datos["menor"] else None,
        }

    # --- Control remoto ---------------------------------------------------
    async def leer_control(self, carrito_id: str) -> dict | None:
        return await self._control.find_one({"_id": carrito_id}, {"_id": 0})

    async def guardar_control(self, carrito_id: str, modo: str, accion: str,
                              actualizado: datetime) -> dict:
        datos = {"modo": modo, "accion": accion, "actualizado": actualizado}
        await self._control.update_one({"_id": carrito_id}, {"$set": datos}, upsert=True)
        return datos


def _publico(doc: dict) -> dict:
    """Convierte el _id de MongoDB en 'id' (texto) y quita campos internos."""
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    doc.pop("recibido", None)
    return doc
