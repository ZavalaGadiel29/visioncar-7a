"""
modelos.py
----------
Modelos de Pydantic: describen EXACTAMENTE qué datos acepta y devuelve la API.

Si un dato no cumple el contrato (señal desconocida, confianza fuera de 0-1,
campo con otro nombre, etc.), FastAPI responde automáticamente 422.
"""

from datetime import datetime
from typing import Annotated, Literal, Optional, Union

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
)

from . import fechas

# --- Valores permitidos por el contrato (NO cambiar) ------------------------
SENALES = ("alto", "avanzar", "izquierda", "derecha", "ninguna", "peaton")
ACCIONES = ("avanzar", "detener", "girar_izq", "girar_der")
MODOS = ("autonomo", "manual")

Senal = Literal["alto", "avanzar", "izquierda", "derecha", "ninguna", "peaton"]
Accion = Literal["avanzar", "detener", "girar_izq", "girar_der"]
Modo = Literal["autonomo", "manual"]

# carrito_id: letras, números, guion y guion bajo (ej. "celula-1")
PATRON_CARRITO_ID = r"^[A-Za-z0-9_-]{1,50}$"
CarritoId = Annotated[
    str,
    Field(
        pattern=PATRON_CARRITO_ID,
        description="Identificador del carrito (letras, números, '-' o '_'; máx. 50).",
        examples=["celula-1"],
    ),
]

# Fecha en hora local del carrito. Se normaliza (ver fechas.py).
FechaLocal = Annotated[datetime, AfterValidator(fechas.normalizar)]

MAXIMO_EVENTOS_POR_LOTE = 1000

EJEMPLO_EVENTO = {
    "carrito_id": "celula-1",
    "fecha": "2026-12-04T10:15:32",
    "senal": "alto",
    "confianza": 0.93,
    "accion": "detener",
    "modo": "autonomo",
}


# --- Eventos ----------------------------------------------------------------
class EventoEntrada(BaseModel):
    """Una decisión tomada por el carrito (el contrato de datos)."""

    # extra="forbid": rechaza campos que no estén en el contrato (ej. "señal")
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [EJEMPLO_EVENTO]})

    carrito_id: CarritoId
    fecha: FechaLocal = Field(
        description="Momento de la decisión, hora local del carrito: AAAA-MM-DDTHH:MM:SS.",
        examples=["2026-12-04T10:15:32"],
    )
    senal: Senal = Field(description="Señal detectada por la cámara.")
    # strict=True: no acepta textos como "0.9" ni true/false, solo números
    confianza: float = Field(
        ge=0, le=1, strict=True, description="Confianza de la detección, entre 0 y 1."
    )
    accion: Accion = Field(description="Acción que decidió el carrito.")
    modo: Modo = Field(description="Modo de manejo en ese momento.")


class EventoGuardado(EventoEntrada):
    """Evento tal como lo devuelve la API: el contrato más su identificador."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"id": "675012ab34cd56ef7890abcd", **EJEMPLO_EVENTO}]}
    )

    id: str = Field(description="Identificador único asignado por la base de datos.")


def _tipo_de_cuerpo(valor) -> str:
    """Decide si el cuerpo del POST es un solo evento o una lista."""
    return "lista" if isinstance(valor, list) else "uno"


# El POST /eventos acepta un objeto o una lista de objetos
CuerpoEventos = Annotated[
    Union[
        Annotated[EventoEntrada, Tag("uno")],
        Annotated[
            list[EventoEntrada],
            Field(min_length=1, max_length=MAXIMO_EVENTOS_POR_LOTE),
            Tag("lista"),
        ],
    ],
    Discriminator(_tipo_de_cuerpo),
]


class RespuestaGuardado(BaseModel):
    guardados: int = Field(description="Cuántos eventos se guardaron.")
    ids: list[str] = Field(description="Identificadores de los eventos guardados, en orden.")


# --- Estadísticas -----------------------------------------------------------
class Estadisticas(BaseModel):
    carrito_id: Optional[str] = Field(description="Carrito consultado (null = todos).")
    total_eventos: int
    conteo_por_senal: dict[str, int] = Field(description="Cuántas veces se detectó cada señal.")
    conteo_por_accion: dict[str, int] = Field(description="Cuántas veces se tomó cada acción.")
    confianza_promedio_por_senal: dict[str, Optional[float]] = Field(
        description="Promedio de confianza de cada señal (null si nunca se detectó)."
    )
    senal_mas_detectada: Optional[str] = Field(
        description="Señal real más frecuente (no cuenta 'ninguna'). null si no hay datos."
    )
    evento_menor_confianza: Optional[EventoGuardado] = Field(
        description="Evento con la confianza más baja. null si no hay datos."
    )


# --- Control remoto ---------------------------------------------------------
class ControlEntrada(BaseModel):
    """Lo que manda la app para cambiar el modo y la acción manual."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"modo": "manual", "accion": "avanzar"}]},
    )

    modo: Modo
    accion: Accion = Field(
        default="detener", description="Acción manual. En modo autónomo se ignora."
    )


class ControlRespuesta(BaseModel):
    modo: Modo
    accion: Accion = Field(
        description="Acción que debe ejecutar el carrito. Es 'detener' si el modo manual "
        "no se renovó a tiempo."
    )
    actualizado: Optional[datetime] = Field(
        description="Última vez que la app cambió el control (hora local). null si nunca."
    )


# --- Salud y demo -----------------------------------------------------------
class Salud(BaseModel):
    estado: Literal["ok", "error"]
    base_datos: Literal["ok", "error", "memoria"]
    modo_demo: bool
    version: str
    hora_servidor: datetime


class DemoEntrada(BaseModel):
    model_config = ConfigDict(extra="forbid")

    carrito_id: CarritoId = "celula-1"
    cantidad: int = Field(default=50, ge=1, le=500, description="Eventos a generar (1-500).")
    minutos: int = Field(
        default=10, ge=1, le=1440, description="Se reparten en los últimos N minutos."
    )


class DemoRespuesta(BaseModel):
    generados: int
    carrito_id: str
