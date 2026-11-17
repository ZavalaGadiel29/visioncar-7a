"""
fechas.py
---------
Manejo de fechas en un solo lugar.

Regla de la API: todas las fechas que entran y salen están en la HORA LOCAL
del carrito y SIN zona horaria, igual que el contrato:  "2026-12-04T10:15:32".

- Si alguien manda una fecha CON zona (por ejemplo "2026-12-04T16:15:32Z",
  que es lo que da JavaScript con toISOString()), se convierte a la hora
  local de ZONA_HORARIA y se le quita la zona. Así todo queda comparable.
- MongoDB guarda milisegundos, así que se recortan los microsegundos para
  que la versión en memoria y la de MongoDB se comporten igual.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_zona = ZoneInfo("America/Mexico_City")


def establecer_zona(nombre: str) -> None:
    global _zona
    _zona = ZoneInfo(nombre)


def normalizar(fecha: datetime) -> datetime:
    """Devuelve la fecha en hora local, sin zona y con precisión de milisegundos."""
    if fecha.tzinfo is not None:
        fecha = fecha.astimezone(_zona).replace(tzinfo=None)
    return fecha.replace(microsecond=(fecha.microsecond // 1000) * 1000)


def ahora_utc() -> datetime:
    """Hora actual en UTC sin zona (así la guarda y devuelve MongoDB)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ahora_local() -> datetime:
    """Hora actual en la zona del carrito, sin zona."""
    return normalizar(datetime.now(_zona))


def utc_a_local(fecha_utc: datetime) -> datetime:
    """Convierte una fecha UTC (sin zona) a hora local sin zona."""
    return normalizar(fecha_utc.replace(tzinfo=timezone.utc))
