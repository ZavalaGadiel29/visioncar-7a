"""
decision.py
-----------
El "cerebro" del agente: etapa RAZONAR del ciclo Percibir -> Razonar -> Actuar.

Recibe todo lo que se percibió en un cuadro (EntradaPercepcion) y devuelve
UNA acción con su motivo (Decision).

Funciona con REGLAS DE PRIORIDAD: se revisan en orden y la PRIMERA que se
cumple decide la acción. Las demás ya no se revisan.

    Regla 1. SEGURIDAD     -> DETENER (inmediato, sin confirmación)
    Regla 2. SEÑALES       -> DETENER por señal de alto o semáforo rojo
    Regla 3. MUY CERCA     -> RETROCEDER un momento
    Regla 4. OBSTÁCULO     -> GIRAR_IZQ / GIRAR_DER hacia la zona más libre
    Regla 5. CAMINO LIBRE  -> AVANZAR

Además hay una pequeña MÁQUINA DE ESTADOS para las acciones que duran un
tiempo (no dependen solo del cuadro actual):

    NORMAL          : estado habitual, se aplican las reglas.
    ESPERANDO_ALTO  : vio una señal de alto; se queda DETENIDO X segundos.
                      Al terminar, ignora las señales de alto unos segundos
                      para poder arrancar y pasar la señal.
    RETROCEDIENDO   : obstáculo muy cerca; RETROCEDE X segundos y luego
                      vuelve a NORMAL para evaluar de nuevo.

Por último, el FILTRO ANTI-TITUBEO evita que el auto cambie de acción por
un solo cuadro con ruido: una acción nueva debe repetirse K cuadros
seguidos para confirmarse (excepto el DETENER de seguridad).
"""

import time
from dataclasses import dataclass, field

from percepcion_yolo import TIPO_OBSTACULO, TIPO_PERSONA, TIPO_SEMAFORO, TIPO_SENAL_ALTO

# Acciones posibles (las mismas que entiende la ESP32-CAM)
AVANZAR = "AVANZAR"
DETENER = "DETENER"
GIRAR_IZQ = "GIRAR_IZQ"
GIRAR_DER = "GIRAR_DER"
RETROCEDER = "RETROCEDER"

# Estados de la máquina de estados
ESTADO_NORMAL = "NORMAL"
ESTADO_ESPERANDO_ALTO = "ESPERANDO_ALTO"
ESTADO_RETROCEDIENDO = "RETROCEDIENDO"

ZONAS = ("IZQ", "CENTRO", "DER")


# ======================================================================
# Datos de entrada y salida
# ======================================================================
@dataclass
class EntradaPercepcion:
    """Todo lo que el agente percibió en este instante."""
    video_ok: bool = False
    motivo_video: str = ""
    imagen_confiable: bool = False
    motivo_imagen: str = ""
    emergencia: bool = False
    detecciones: list = field(default_factory=list)          # lista de Deteccion (YOLO)
    color_semaforo: str = "NINGUNO"                          # ROJO / VERDE / DESCONOCIDO / NINGUNO
    ocupacion_opencv: dict = field(default_factory=lambda: {z: 0.0 for z in ZONAS})
    bloqueo_opencv: dict = field(default_factory=lambda: {z: False for z in ZONAS})


@dataclass
class Decision:
    accion: str
    motivo: str
    regla: int                  # número de regla que la produjo (0 = arranque)
    inmediata: bool = False     # True = se aplica sin esperar confirmación


# ======================================================================
# Agente de decisión (reglas + máquina de estados)
# ======================================================================
class AgenteDecision:
    def __init__(self, cfg_decision):
        self.cfg = cfg_decision
        self.estado = ESTADO_NORMAL
        self.fin_alto = 0.0              # hasta cuándo seguir detenido por la señal de alto
        self.ignorar_alto_hasta = 0.0    # hasta cuándo ignorar señales de alto
        self.fin_retroceso = 0.0         # hasta cuándo seguir retrocediendo
        self.zonas_ocupadas = {z: False for z in ZONAS}   # se guarda para la interfaz

    def decidir(self, p, ahora=None):
        """Aplica las reglas EN ORDEN. La primera que responda gana."""
        if ahora is None:
            ahora = time.monotonic()

        # Se calcula una sola vez qué zonas están ocupadas (YOLO + OpenCV)
        self.zonas_ocupadas, puntajes, causas = self._ocupacion_de_zonas(p)

        decision = self._regla_1_seguridad(p)
        if decision is None:
            decision = self._regla_2_senales(p, ahora)
        if decision is None:
            decision = self._regla_3_muy_cerca(p, ahora)
        if decision is None:
            decision = self._regla_4_esquivar(puntajes, causas)
        if decision is None:
            decision = self._regla_5_avanzar(p)
        return decision

    # ------------------------------------------------------------------
    # REGLA 1: SEGURIDAD
    # ------------------------------------------------------------------
    def _regla_1_seguridad(self, p):
        if p.emergencia:
            return Decision(DETENER, "Paro de emergencia activo", 1, inmediata=True)
        if not p.video_ok:
            return Decision(DETENER, p.motivo_video or "Video perdido", 1, inmediata=True)
        if not p.imagen_confiable:
            return Decision(DETENER, p.motivo_imagen or "Imagen no confiable", 1, inmediata=True)

        for d in p.detecciones:
            if d.tipo != TIPO_PERSONA or d.area < self.cfg["area_persona_minima"]:
                continue
            if d.zona == "CENTRO":
                return Decision(DETENER, "Persona en el centro", 1, inmediata=True)
            if d.area >= self.cfg["area_persona_grande"]:
                return Decision(DETENER, f"Persona muy cerca ({d.zona}, area {d.area:.0%})", 1, inmediata=True)
        return None

    # ------------------------------------------------------------------
    # REGLA 2: SEÑALES DE TRÁNSITO
    # ------------------------------------------------------------------
    def _regla_2_senales(self, p, ahora):
        # a) ¿Ya estamos detenidos por una señal de alto?
        if self.estado == ESTADO_ESPERANDO_ALTO:
            if ahora < self.fin_alto:
                faltan = self.fin_alto - ahora
                return Decision(DETENER, f"Senal de ALTO: esperando {faltan:.1f} s", 2)
            # Terminó la espera: volver a NORMAL e ignorar la señal un rato
            self.estado = ESTADO_NORMAL
            self.ignorar_alto_hasta = ahora + self.cfg["tiempo_ignorar_alto_s"]

        # b) ¿Hay una señal de alto cercana (y no la estamos ignorando)?
        if ahora >= self.ignorar_alto_hasta:
            for d in p.detecciones:
                if d.tipo == TIPO_SENAL_ALTO and d.area >= self.cfg["area_senal_alto_cercana"]:
                    self.estado = ESTADO_ESPERANDO_ALTO
                    self.fin_alto = ahora + self.cfg["tiempo_alto_s"]
                    return Decision(DETENER, f"Senal de ALTO cercana: detenerse {self.cfg['tiempo_alto_s']:.0f} s", 2)

        # c) Semáforo en rojo
        if p.color_semaforo == "ROJO":
            return Decision(DETENER, "Semaforo en ROJO", 2)

        # Semáforo VERDE: no se detiene; las siguientes reglas deciden.
        return None

    # ------------------------------------------------------------------
    # REGLA 3: OBSTÁCULO MUY CERCA o TODO BLOQUEADO -> RETROCEDER
    # ------------------------------------------------------------------
    def _regla_3_muy_cerca(self, p, ahora):
        # a) ¿Ya estamos retrocediendo?
        if self.estado == ESTADO_RETROCEDIENDO:
            if ahora < self.fin_retroceso:
                faltan = self.fin_retroceso - ahora
                return Decision(RETROCEDER, f"Retrocediendo ({faltan:.1f} s)", 3)
            # Terminó: volver a NORMAL y evaluar de nuevo con las reglas
            self.estado = ESTADO_NORMAL

        # b) ¿Hay un obstáculo de YOLO enorme (muy cerca)?
        motivo = None
        for d in p.detecciones:
            if d.tipo == TIPO_OBSTACULO and d.area >= self.cfg["area_obstaculo_muy_cercano"]:
                motivo = f"{d.clase} muy cerca (area {d.area:.0%})"
                break

        # c) ¿Las tres zonas están bloqueadas?
        if motivo is None and all(self.zonas_ocupadas[z] for z in ZONAS):
            motivo = "Las tres zonas bloqueadas"

        if motivo is not None:
            self.estado = ESTADO_RETROCEDIENDO
            self.fin_retroceso = ahora + self.cfg["tiempo_retroceso_s"]
            return Decision(RETROCEDER, motivo, 3)
        return None

    # ------------------------------------------------------------------
    # REGLA 4: OBSTÁCULO EN UNA ZONA -> GIRAR HACIA LA ZONA MÁS LIBRE
    # ------------------------------------------------------------------
    def _regla_4_esquivar(self, puntajes, causas):
        ocupadas = self.zonas_ocupadas
        hay_que_girar = ocupadas["CENTRO"] or (
            self.cfg["girar_por_obstaculo_lateral"] and (ocupadas["IZQ"] or ocupadas["DER"])
        )
        if not hay_que_girar:
            return None

        # ¿Hacia dónde? Hacia el lado libre; si los dos están igual,
        # hacia el que tenga menos "puntaje" de ocupación.
        if ocupadas["IZQ"] and not ocupadas["DER"]:
            accion = GIRAR_DER
        elif ocupadas["DER"] and not ocupadas["IZQ"]:
            accion = GIRAR_IZQ
        elif puntajes["IZQ"] < puntajes["DER"]:
            accion = GIRAR_IZQ
        elif puntajes["DER"] < puntajes["IZQ"]:
            accion = GIRAR_DER
        else:
            accion = self.cfg["giro_preferido"]

        # Motivo legible: qué zona está ocupada y por qué
        zona_obstaculo = "CENTRO" if ocupadas["CENTRO"] else ("IZQ" if ocupadas["IZQ"] else "DER")
        lado_libre = "IZQ" if accion == GIRAR_IZQ else "DER"
        motivo = f"Obstaculo en {zona_obstaculo} ({causas[zona_obstaculo]}) -> {lado_libre} mas libre"
        return Decision(accion, motivo, 4)

    # ------------------------------------------------------------------
    # REGLA 5: CAMINO LIBRE -> AVANZAR
    # ------------------------------------------------------------------
    def _regla_5_avanzar(self, p):
        if p.color_semaforo == "VERDE":
            return Decision(AVANZAR, "Semaforo en VERDE, camino libre", 5)
        return Decision(AVANZAR, "Camino libre", 5)

    # ------------------------------------------------------------------
    # Auxiliar: fusiona YOLO + OpenCV en "zonas ocupadas"
    # ------------------------------------------------------------------
    def _ocupacion_de_zonas(self, p):
        """
        Devuelve tres diccionarios por zona:
          ocupadas: True/False
          puntajes: número que crece mientras más ocupada esté (para comparar)
          causas:   texto que explica qué ocupa la zona
        """
        ocupadas = {z: False for z in ZONAS}
        puntajes = {z: 0.0 for z in ZONAS}
        causas = {z: [] for z in ZONAS}

        # Fuente 1: YOLO (obstáculos y personas a los lados)
        for d in p.detecciones:
            if d.tipo in (TIPO_OBSTACULO, TIPO_PERSONA) and d.area >= self.cfg["area_obstaculo_zona"]:
                ocupadas[d.zona] = True
                puntajes[d.zona] += d.area
                causas[d.zona].append(d.clase)

        # Fuente 2: OpenCV (bordes / contornos en la parte baja)
        for z in ZONAS:
            puntajes[z] += p.ocupacion_opencv[z]
            if p.bloqueo_opencv[z]:
                ocupadas[z] = True
                causas[z].append(f"bordes {p.ocupacion_opencv[z]:.0%}")

        causas_texto = {z: ", ".join(causas[z]) for z in ZONAS}
        return ocupadas, puntajes, causas_texto


# ======================================================================
# Filtro anti-titubeo
# ======================================================================
class FiltroAntiTitubeo:
    """
    Solo deja pasar una acción NUEVA si se repite K cuadros seguidos.
    Mientras tanto se mantiene la última acción confirmada.
    Las decisiones marcadas como 'inmediata' (seguridad) pasan directo.
    """

    def __init__(self, cfg_decision):
        self.cfg = cfg_decision
        self.confirmada = Decision(DETENER, "Inicio del sistema", 0)
        self.candidata = None        # acción nueva que se está confirmando
        self.repeticiones = 0        # cuántas veces seguidas se ha visto

    def filtrar(self, decision):
        k = max(1, int(self.cfg["confirmaciones_k"]))

        # Caso 1: seguridad -> pasa de inmediato
        if decision.inmediata:
            return self._confirmar(decision)

        # Caso 2: es la misma acción que ya teníamos -> se actualiza el motivo
        if decision.accion == self.confirmada.accion:
            return self._confirmar(decision)

        # Caso 3: acción distinta -> contar repeticiones seguidas
        if decision.accion == self.candidata:
            self.repeticiones += 1
        else:
            self.candidata = decision.accion
            self.repeticiones = 1

        if self.repeticiones >= k:
            return self._confirmar(decision)

        # Aún no se confirma: seguir con la acción anterior
        motivo = f"{self.confirmada.motivo} (confirmando {self.candidata} {self.repeticiones}/{k})"
        return Decision(self.confirmada.accion, motivo, self.confirmada.regla)

    def _confirmar(self, decision):
        self.confirmada = decision
        self.candidata = None
        self.repeticiones = 0
        return decision
