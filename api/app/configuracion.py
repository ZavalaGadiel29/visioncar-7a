"""
configuracion.py
----------------
Lee la configuración de la API desde variables de entorno (o del archivo .env).

Aquí NUNCA se escriben tokens ni contraseñas: solo se leen.
Si falta algo obligatorio, la API no arranca y explica qué falta.
"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

LONGITUD_MINIMA_TOKEN = 16


def _a_booleano(valor: str | None) -> bool:
    return str(valor or "").strip().lower() in {"1", "true", "si", "sí", "yes"}


@dataclass(frozen=True)
class Configuracion:
    token_carrito: str
    token_lectura: str
    token_app: str
    modo_demo: bool = False
    mongodb_uri: str = ""
    mongodb_base: str = "visioncar"
    database_url: str = ""
    cors_origenes: tuple[str, ...] = ("*",)
    limite_por_segundo: int = 20
    tamano_maximo_bytes: int = 512 * 1024
    segundos_vigencia_manual: float = 2.0
    zona_horaria: str = "America/Mexico_City"
    demo_eventos_iniciales: int = 0

    def __post_init__(self):
        # Con PostgreSQL solo hay datos reales: el modo demo queda apagado
        # aunque MODO_DEMO diga 1 (y con él, /demo/generar).
        if self.database_url:
            object.__setattr__(self, "modo_demo", False)

    @property
    def usa_postgres(self) -> bool:
        return bool(self.database_url)

    def validar(self) -> None:
        """Revisa que la configuración tenga sentido antes de arrancar."""
        tokens = {
            "TOKEN_CARRITO": self.token_carrito,
            "TOKEN_LECTURA": self.token_lectura,
            "TOKEN_APP": self.token_app,
        }
        for nombre, valor in tokens.items():
            if not valor:
                raise RuntimeError(f"Falta la variable de entorno {nombre}.")
            if len(valor) < LONGITUD_MINIMA_TOKEN:
                raise RuntimeError(
                    f"{nombre} es muy corto: usa al menos {LONGITUD_MINIMA_TOKEN} caracteres."
                )
        if len(set(tokens.values())) != 3:
            raise RuntimeError("Los tres tokens deben ser distintos entre sí.")
        if not self.modo_demo and not self.usa_postgres and not self.mongodb_uri:
            raise RuntimeError(
                "Falta la base de datos: configura DATABASE_URL (PostgreSQL), "
                "MONGODB_URI (MongoDB) o activa MODO_DEMO=1 para usar memoria."
            )


def cargar_configuracion() -> Configuracion:
    """Carga .env (si existe) y arma la configuración desde el entorno."""
    load_dotenv()  # no sobrescribe variables que ya existan en el sistema
    origenes = os.getenv("CORS_ORIGENES", "*")
    config = Configuracion(
        token_carrito=os.getenv("TOKEN_CARRITO", "").strip(),
        token_lectura=os.getenv("TOKEN_LECTURA", "").strip(),
        token_app=os.getenv("TOKEN_APP", "").strip(),
        modo_demo=_a_booleano(os.getenv("MODO_DEMO")),
        mongodb_uri=os.getenv("MONGODB_URI", "").strip(),
        mongodb_base=os.getenv("MONGODB_BASE", "visioncar").strip(),
        database_url=os.getenv("DATABASE_URL", "").strip(),
        cors_origenes=tuple(o.strip() for o in origenes.split(",") if o.strip()) or ("*",),
        limite_por_segundo=int(os.getenv("LIMITE_POR_SEGUNDO", "20")),
        tamano_maximo_bytes=int(os.getenv("TAMANO_MAXIMO_BYTES", str(512 * 1024))),
        segundos_vigencia_manual=float(os.getenv("SEGUNDOS_VIGENCIA_MANUAL", "2")),
        zona_horaria=os.getenv("ZONA_HORARIA", "America/Mexico_City").strip(),
        demo_eventos_iniciales=int(os.getenv("DEMO_EVENTOS_INICIALES", "100")),
    )
    config.validar()
    return config
