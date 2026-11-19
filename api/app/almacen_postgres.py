"""
almacen_postgres.py
-------------------
Almacenamiento en PostgreSQL (Supabase). Se usa cuando existe la variable
de entorno DATABASE_URL.

Tiene exactamente las mismas funciones que almacen_memoria.py y
almacen_mongo.py, así que las rutas (y 4°A) no notan ninguna diferencia.

Tablas (ver sql/esquema.sql y DIAGRAMA_BD.md):
  - eventos:     una decisión del carrito por fila.
  - control:     una fila por carrito con el modo y la acción manual.
  - componentes: catálogo de piezas (se llena desde app/datos/componentes.json).

Detalles técnicos:
  - Usa psycopg 3 con un "pool" (grupo) de conexiones que se reutilizan.
  - Las consultas de psycopg son bloqueantes; se ejecutan en un hilo aparte
    (asyncio.to_thread) para no frenar a FastAPI. Así además funciona igual
    en Windows y en Linux (Render).
  - prepare_threshold=None: no usa "prepared statements", porque el pooler
    de Supabase en modo transacción no los soporta.
  - Las tablas se crean al arrancar. Si la base no responde en ese momento,
    la API arranca igual (/health dirá "error") y vuelve a intentarlo en
    la siguiente petición.
"""

import asyncio
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import catalogo

registro = logging.getLogger("visioncar.postgres")

ARCHIVO_ESQUEMA = Path(__file__).resolve().parent.parent / "sql" / "esquema.sql"
COLUMNAS_EVENTO = ("carrito_id", "fecha", "senal", "confianza", "accion", "modo")
SELECT_EVENTO = "SELECT id, carrito_id, fecha, senal, confianza, accion, modo FROM eventos"
SELECT_COMPONENTE = "SELECT id, nombre, tipo, voltaje, cantidad, funcion FROM componentes"


class AlmacenPostgres:
    nombre = "postgresql"

    def __init__(self, url: str, max_conexiones: int = 5):
        self._pool = ConnectionPool(
            url,
            min_size=1,
            max_size=max_conexiones,
            open=False,
            timeout=10,                                  # espera máxima por una conexión libre
            max_idle=300,                                # cierra conexiones sin uso tras 5 min
            check=ConnectionPool.check_connection,       # descarta conexiones que el servidor cerró
            kwargs={"prepare_threshold": None, "connect_timeout": 10,
                    "application_name": "visioncar-api"},
            name="visioncar",
        )
        self._esquema_listo = False
        self._candado = threading.Lock()

    # --- Ciclo de vida ----------------------------------------------------
    async def iniciar(self) -> None:
        self._pool.open(wait=False)
        try:
            await asyncio.to_thread(self._asegurar_esquema)
        except Exception as error:   # la API arranca igual; /health mostrará el error
            registro.error("No se pudo preparar PostgreSQL al arrancar: %s", error)

    async def cerrar(self) -> None:
        await asyncio.to_thread(self._pool.close)

    async def ping(self) -> bool:
        def _ping():
            with self._pool.connection(timeout=5) as conexion:
                conexion.execute("SELECT 1")
        try:
            await asyncio.to_thread(_ping)
            return True
        except Exception as error:
            registro.error("PostgreSQL no responde: %s", error)
            return False

    def _asegurar_esquema(self) -> None:
        """Crea las tablas e índices (si no existen) y carga el catálogo si está vacío."""
        if self._esquema_listo:
            return
        with self._candado:
            if self._esquema_listo:
                return
            sql_esquema = ARCHIVO_ESQUEMA.read_text(encoding="utf-8")
            with self._pool.connection() as conexion:     # al salir hace COMMIT (o ROLLBACK si falla)
                conexion.execute(sql_esquema)
                vacia = conexion.execute("SELECT NOT EXISTS (SELECT 1 FROM componentes)").fetchone()[0]
                if vacia:
                    with conexion.cursor() as cursor:
                        cursor.executemany(
                            "INSERT INTO componentes (id, nombre, tipo, voltaje, cantidad, funcion, orden) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                            [(c.id, c.nombre, c.tipo, c.voltaje, c.cantidad, c.funcion, i)
                             for i, c in enumerate(catalogo.COMPONENTES, start=1)],
                        )
                    registro.info("Tabla componentes cargada desde componentes.json.")
            self._esquema_listo = True

    async def _ejecutar(self, funcion, *args):
        """Corre una función que usa la base en un hilo aparte (asegurando antes las tablas)."""
        def _trabajo():
            self._asegurar_esquema()
            return funcion(*args)
        return await asyncio.to_thread(_trabajo)

    def _consultar(self, sql: str, parametros=(), uno: bool = False):
        with self._pool.connection() as conexion:
            with conexion.cursor(row_factory=dict_row) as cursor:
                cursor.execute(sql, parametros)
                return cursor.fetchone() if uno else cursor.fetchall()

    # --- Eventos ----------------------------------------------------------
    async def guardar_eventos(self, eventos: list[dict]) -> list[str]:
        def _guardar():
            filas = ", ".join(["(%s, %s, %s, %s, %s, %s)"] * len(eventos))
            valores = [e[c] for e in eventos for c in COLUMNAS_EVENTO]
            sql = f"INSERT INTO eventos ({', '.join(COLUMNAS_EVENTO)}) VALUES {filas} RETURNING id"
            with self._pool.connection() as conexion:    # todo el lote en una transacción
                ids = [fila[0] for fila in conexion.execute(sql, valores).fetchall()]
            # Los id (SERIAL) se asignan en el orden de las filas: ordenarlos
            # garantiza que correspondan al orden en que llegaron los eventos.
            return [str(i) for i in sorted(ids)]
        return await self._ejecutar(_guardar)

    @staticmethod
    def _where(carrito_id=None, senal=None, accion=None, desde=None, hasta=None):
        condiciones, parametros = [], []
        for columna, operador, valor in (("carrito_id", "=", carrito_id), ("senal", "=", senal),
                                         ("accion", "=", accion), ("fecha", ">=", desde),
                                         ("fecha", "<=", hasta)):
            if valor is not None:
                condiciones.append(f"{columna} {operador} %s")
                parametros.append(valor)
        where = (" WHERE " + " AND ".join(condiciones)) if condiciones else ""
        return where, parametros

    async def buscar_eventos(self, carrito_id=None, senal=None, accion=None,
                             desde: datetime | None = None, hasta: datetime | None = None,
                             limite: int = 50) -> list[dict]:
        where, parametros = self._where(carrito_id, senal, accion, desde, hasta)
        sql = f"{SELECT_EVENTO}{where} ORDER BY fecha DESC, id DESC LIMIT %s"
        filas = await self._ejecutar(self._consultar, sql, [*parametros, limite])
        return [_publico(f) for f in filas]

    async def ultimo_evento(self, carrito_id=None) -> dict | None:
        resultado = await self.buscar_eventos(carrito_id=carrito_id, limite=1)
        return resultado[0] if resultado else None

    async def datos_estadisticas(self, carrito_id=None) -> dict:
        where, parametros = self._where(carrito_id)

        def _estadisticas():
            with self._pool.connection() as conexion:
                with conexion.cursor(row_factory=dict_row) as cursor:
                    cursor.execute(f"SELECT senal, COUNT(*) AS n, SUM(confianza) AS suma "
                                   f"FROM eventos{where} GROUP BY senal", parametros)
                    por_senal = {f["senal"]: {"n": f["n"], "suma": f["suma"]} for f in cursor.fetchall()}
                    cursor.execute(f"SELECT accion, COUNT(*) AS n FROM eventos{where} GROUP BY accion",
                                   parametros)
                    por_accion = {f["accion"]: f["n"] for f in cursor.fetchall()}
                    # menor confianza; si empatan, el más reciente
                    cursor.execute(f"{SELECT_EVENTO}{where} ORDER BY confianza ASC, fecha DESC, id DESC "
                                   f"LIMIT 1", parametros)
                    menor = cursor.fetchone()
            return {"total": sum(d["n"] for d in por_senal.values()), "por_senal": por_senal,
                    "por_accion": por_accion, "menor": _publico(menor) if menor else None}

        return await self._ejecutar(_estadisticas)

    # --- Control remoto ---------------------------------------------------
    async def leer_control(self, carrito_id: str) -> dict | None:
        fila = await self._ejecutar(
            self._consultar, "SELECT modo, accion, actualizado FROM control WHERE carrito_id = %s",
            (carrito_id,), True)
        if fila is None:
            return None
        # TIMESTAMPTZ -> UTC sin zona, igual que memoria y MongoDB
        fila["actualizado"] = fila["actualizado"].astimezone(timezone.utc).replace(tzinfo=None)
        return fila

    async def guardar_control(self, carrito_id: str, modo: str, accion: str,
                              actualizado: datetime) -> dict:
        def _guardar():
            with self._pool.connection() as conexion:
                conexion.execute(
                    "INSERT INTO control (carrito_id, modo, accion, actualizado) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (carrito_id) DO UPDATE "
                    "SET modo = EXCLUDED.modo, accion = EXCLUDED.accion, actualizado = EXCLUDED.actualizado",
                    (carrito_id, modo, accion, actualizado.replace(tzinfo=timezone.utc)),
                )
        await self._ejecutar(_guardar)
        return {"modo": modo, "accion": accion, "actualizado": actualizado}

    # --- Componentes (catálogo) -------------------------------------------
    async def listar_componentes(self, tipo: str | None = None) -> list[dict]:
        if tipo is None:
            return await self._ejecutar(self._consultar, f"{SELECT_COMPONENTE} ORDER BY orden, id")
        return await self._ejecutar(self._consultar,
                                    f"{SELECT_COMPONENTE} WHERE tipo = %s ORDER BY orden, id", (tipo,))

    async def obtener_componente(self, id_componente: str) -> dict | None:
        return await self._ejecutar(self._consultar, f"{SELECT_COMPONENTE} WHERE id = %s",
                                    (id_componente,), True)


def _publico(fila: dict) -> dict:
    """Convierte el id numérico en texto, como en las otras versiones."""
    return {**fila, "id": str(fila["id"])}
