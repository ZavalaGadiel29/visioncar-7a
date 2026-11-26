"""
decision.py
-----------
El "cerebro" del carrito: etapa RAZONAR del ciclo Percibir -> Razonar -> Actuar.

Es una MÁQUINA DE ESTADOS con REGLAS DE PRIORIDAD. En cada cuadro se
revisan las reglas en orden y la PRIMERA que se cumple decide:

  1) SEGURIDAD (inmediato, sin confirmación) -> DETENIDO_SEGURIDAD
       video perdido o con más de 1 s, imagen oscura o borrosa,
       paro de emergencia, persona en el carril.
  2) MODO MANUAL (desde la app, vía la API) -> MANUAL
  3-4) OBSTÁCULOS (12 clases COCO + bordes de OpenCV). Depende de
     config.json -> decision -> modo_obstaculo:
     "detener" (pedido del profesor):
       3) Obstáculo EN EL CAMINO (zona CENTRO y dentro del ROI del carril)
          -> DETENIDO_OBSTACULO (sin retroceder ni esquivar). Lo que está
          solo en IZQ o DER no detiene. Para seguir, el camino debe estar
          libre segundos_libre_para_seguir (1 s) SEGUIDOS; luego sigue el
          carril. A la nube va senal "ninguna" + accion "detener".
     "esquivar" (lógica anterior, se conserva):
       3) Obstáculo MUY cercano o 3 zonas bloqueadas -> RETROCEDIENDO y
          luego girar hacia la zona más libre.
       4) Obstáculo en una zona -> girar hacia la zona más libre.
  5) SEÑALES confirmadas y maniobras en curso (alto > giros > avanzar):
       alto      -> DETENIDO_POR_ALTO (3 s) -> sigue el carril e ignora
                    otro "alto" por 5 s (IGNORANDO_ALTO)
       izquierda -> GIRO_DIRIGIDO (máx. giro_dirigido_max_s = 3 s). La SEÑAL
                    manda sobre las líneas:
                      a) gira (MOVER giro_izquierda) giro_tiempo_s (0.8 s)
                      b) sigue la ORILLA de ese lado (ORILLA-I) e ignora la
                         guía y las bifurcaciones del otro lado
                      c) termina al ver la guía estable (giro_linea_cuadros_estable
                         = 3 cuadros seguidos con |error| < giro_linea_error_max = 0.3)
                         o al pasar el máximo -> LINEA normal.
                    Ignora otra "izquierda" por 5 s. Un peatón u obstáculo lo
                    PAUSA (también su reloj) y al liberarse continúa.
       derecha   -> igual que izquierda, con ORILLA-D
       avanzar   -> sigue el carril; durante avanzar_recto_s (3 s), en una
                    bifurcación toma el tramo más al centro (el más recto)
     (los tiempos están en config.json -> decision)
     Si un obstáculo interrumpe un alto o un giro, al quitarse el
     obstáculo la maniobra sigue si aún no se le acabó el tiempo.
  6) Nada de lo anterior -> SIGUIENDO_CARRIL (control PD -> MOVER).

Una señal está CONFIRMADA (ACTIVA) si aparece en N cuadros SEGUIDOS
(cuadros_seguidos_senal = 3) con confianza >= umbral_confianza_senal y
CERCA: el alto de su recuadro es mayor que altura_activacion_senal
(0.25 = 25 % de la altura de la imagen). Así se evitan falsos positivos
y la señal se obedece cuando ya está cerca, no desde lejos.

Cada Decision lleva: comando para el carrito, estado, motivo en texto,
número de regla, y los datos para la nube (senal, confianza, accion, modo).
"""

import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Optional

from carril import MODO_LINEA, MODO_LINEA_PREDICHA, ControlPD, ResultadoCarril
from comunicacion import Comando
from percepcion_yolo import TIPO_OBSTACULO, TIPO_PERSONA, TIPO_PEATON

# --- Estados -----------------------------------------------------------
SIGUIENDO_CARRIL = "SIGUIENDO_CARRIL"
DETENIDO_POR_ALTO = "DETENIDO_POR_ALTO"
IGNORANDO_ALTO = "IGNORANDO_ALTO"
PREPARANDO_GIRO = "PREPARANDO_GIRO"
GIRANDO = "GIRANDO"
GIRO_DIRIGIDO = "GIRO_DIRIGIDO"
RETROCEDIENDO = "RETROCEDIENDO"
DETENIDO_OBSTACULO = "DETENIDO_OBSTACULO"
DETENIDO_SEGURIDAD = "DETENIDO_SEGURIDAD"
MANUAL = "MANUAL"

ZONAS = ("IZQ", "CENTRO", "DER")
MODOS_OBSTACULO = ("detener", "esquivar")

# Prioridad entre señales (menor = más importante)
PRIORIDAD_SENAL = {"alto": 0, "izquierda": 1, "derecha": 1, "avanzar": 2}

# Acción manual de la API -> comando del carrito
COMANDO_MANUAL = {"avanzar": "AVANZAR", "girar_izq": "GIRAR_IZQ", "girar_der": "GIRAR_DER"}


# ======================================================================
# Datos de entrada y salida
# ======================================================================
@dataclass
class EntradaPercepcion:
    """Todo lo que se percibió en este instante."""
    ancho_imagen: int = 320
    alto_imagen: int = 240
    video_ok: bool = False
    motivo_video: str = ""
    imagen_confiable: bool = False
    imagen_borrosa: bool = False                         # no confiable por BORROSA (no por oscura)
    motivo_imagen: str = ""
    emergencia: bool = False
    detecciones: list = field(default_factory=list)      # personas y obstáculos (COCO)
    senales: list = field(default_factory=list)          # señales del modelo propio
    senales_ejecutado: bool = False                      # ¿corrió el modelo de señales este cuadro?
    peatones_modelo: list = field(default_factory=list)  # figuras 'peaton' del modelo propio (pies en el asfalto)
    peatones_fuera: list = field(default_factory=list)   # figuras 'peaton' fuera de pista (solo para avisar)
    carril: ResultadoCarril = field(default_factory=ResultadoCarril)
    ocupacion_opencv: dict = field(default_factory=lambda: {z: 0.0 for z in ZONAS})
    bloqueo_opencv: dict = field(default_factory=lambda: {z: False for z in ZONAS})
    manual: Optional[tuple] = None                       # (accion, motivo) si la app manda en modo manual


@dataclass
class Decision:
    comando: Comando
    estado: str
    motivo: str
    regla: int
    accion_nube: str                 # avanzar / detener / girar_izq / girar_der (contrato API)
    senal: Optional[str] = None      # alto/avanzar/izquierda/derecha/ninguna/peaton
    confianza: float = 0.0
    modo: str = "autonomo"
    texto_estado: str = ""           # texto para la pantalla (ej. "GIRO_DIRIGIDO IZQ 2.1 s"); "" = estado
    mantener: bool = False           # imagen borrosa en movimiento: se repite el último comando


# ======================================================================
# Confirmación "K de los últimos M"
# ======================================================================
class ConfirmadorKdeM:
    """
    Guarda lo visto en los últimos M cuadros y confirma lo que aparece K veces.
    Además, lo visto hace más de 'ventana_s' segundos ya no cuenta (así una
    detección vieja no se mezcla con las nuevas).
    """

    def __init__(self, k, m, ventana_s=1.5):
        self.k = max(1, int(k))
        self.ventana_s = float(ventana_s)
        self.historial = deque(maxlen=max(self.k, int(m)))

    def agregar(self, etiqueta, confianza=0.0, ahora=0.0):
        self.historial.append((etiqueta, confianza, ahora))

    def confirmada(self, ahora=0.0):
        """Devuelve (etiqueta, confianza_promedio) de la más repetida si llega a K; si no, None."""
        recientes = [(e, c) for e, c, t in self.historial
                     if e is not None and ahora - t <= self.ventana_s]
        conteo = Counter(e for e, _ in recientes)
        if not conteo:
            return None
        etiqueta, veces = conteo.most_common(1)[0]
        if veces < self.k:
            return None
        confs = [c for e, c in recientes if e == etiqueta]
        return etiqueta, sum(confs) / len(confs)

    def limpiar(self):
        self.historial.clear()


# ======================================================================
# Agente
# ======================================================================
class AgenteDecision:
    def __init__(self, config):
        self.cfg = config["decision"]
        self.velocidades = config["velocidades"]
        self.pd = ControlPD(config["carril"])
        self.cfg_carril = config["carril"]
        self.cfg_calidad = config.get("calidad_imagen", {})
        self.roi_carril_y = float(config["carril"]["roi_inicio_y"])   # fracción de la altura

        c = self.cfg
        ventana = c["ventana_confirmacion_s"]
        # N de los últimos N = N cuadros seguidos (un cuadro sin la señal rompe la racha)
        seguidos = int(c["cuadros_seguidos_senal"])
        self.confirmador_senales = ConfirmadorKdeM(seguidos, seguidos, ventana)
        # Figuras de peatón del modelo propio (aparte de las señales):
        #   conf >= umbral_peaton_1_cuadro (0.6)          -> frena con 1 cuadro
        #   conf >= umbral_confianza_peaton (0.45)        -> frena con N cuadros seguidos (2)
        #   ya detenido: sigue detenido hasta tiempo_libre_peaton_s (1 s) sin verlo
        self.peaton_umbral = float(c.get("umbral_confianza_peaton", 0.45))
        self.peaton_umbral_1 = float(c.get("umbral_peaton_1_cuadro", 0.6))
        self.peaton_seguidos = max(1, int(c.get("cuadros_seguidos_peaton", 2)))
        self.peaton_tiempo_libre = float(c.get("tiempo_libre_peaton_s", 1.0))
        self.peaton_racha = 0               # cuadros seguidos (del modelo) con peatón en el camino
        self.peaton_visto_t = None          # última vez que se vio un peatón válido en el camino
        self.peaton_detenido = False        # True mientras el peatón tiene detenido al carrito
        self.peaton_modelo = None           # última figura de peatón vista en el camino
        self._ultimo_aviso_peaton = ("", 0.0)   # (texto, tiempo) para no repetir en consola
        self.confirmador_zonas = {z: ConfirmadorKdeM(c["confirmacion_obstaculo_k"], c["confirmacion_obstaculo_m"],
                                                     ventana) for z in ZONAS}

        # Obstáculos: "detener" (solo frenar) o "esquivar" (retroceder / girar)
        self.modo_obstaculo = str(c.get("modo_obstaculo", "esquivar")).strip().lower()
        if self.modo_obstaculo not in MODOS_OBSTACULO:
            raise ValueError(f"config.json -> decision -> modo_obstaculo debe ser 'detener' o 'esquivar', "
                             f"no '{self.modo_obstaculo}'.")
        self.confirmador_camino = ConfirmadorKdeM(c["confirmacion_obstaculo_k"], c["confirmacion_obstaculo_m"],
                                                  ventana)
        self.detenido_obstaculo = False   # True mientras el carrito espera a que se libere el camino
        self.libre_desde = None           # desde cuándo el camino está libre (sin interrupciones)
        self.causa_obstaculo = ""         # qué lo detuvo (para la pantalla)

        self.estado = SIGUIENDO_CARRIL
        self.esperando_avanzar = bool(c.get("esperar_senal_avanzar_al_iniciar", False))
        self.fin_alto = 0.0
        self.ignorar_alto_hasta = 0.0
        self.ignorar_giro_hasta = {"izquierda": 0.0, "derecha": 0.0}   # se ignora la MISMA señal
        self.direccion_giro = None        # "izquierda" / "derecha"
        self.fin_preparacion = 0.0
        self.inicio_giro = 0.0
        self.fin_giro = 0.0                # fin de la fase "giro" (MOVER) del GIRO_DIRIGIDO
        self.fin_giro_dirigido = 0.0       # tiempo máximo del GIRO_DIRIGIDO
        self.giro_fase = None              # None / "giro" / "orilla"
        self.giro_cuadros_linea = 0        # cuadros seguidos con la guía estable (fase "orilla")
        self.giro_pausa_desde = None       # peatón u obstáculo durante el GIRO_DIRIGIDO
        self.ultimo_avanzar_t = None       # última vez que mandó la señal AVANZAR
        self.borrosa_desde = None          # desde cuándo la imagen está borrosa (seguido)
        self.ultimo_comando = Comando.detener()   # último comando decidido (para "mantener")
        self.fin_retroceso = 0.0
        self.fin_giro_esquive = 0.0
        self.lado_esquive = None
        self.cuadros_sin_carril = 0
        self.inicio_busqueda = None
        self.ultimo_error = 0.0
        self.ultimo_mover = Comando.mover(0, 0)
        self.pedir_reinicio_carril = False   # main.py lo lee para reiniciar el detector de carril
        self.ultimo_giro_activado = None     # (clase, tiempo) de la última señal izquierda/derecha activada

        # Para la interfaz
        self.zonas_ocupadas = {z: False for z in ZONAS}
        self.senal_vista = None             # mejor detección cruda de este cuadro
        self.senal_confirmada = None        # (clase, confianza) o None
        self.senal_activa = None            # señal que manda ahora (incluye la maniobra en curso)
        self.conf_maniobra = 0.0            # confianza de la señal que inició el alto o el giro

    # ------------------------------------------------------------------
    def giro_reciente(self, ventana_s, ahora=None):
        """'izquierda'/'derecha' si esa señal se activó hace menos de ventana_s; si no, None."""
        if self.ultimo_giro_activado is None:
            return None
        if ahora is None:
            ahora = time.monotonic()
        clase, t = self.ultimo_giro_activado
        return clase if ahora - t <= ventana_s else None

    def orilla_forzada(self):
        """'izquierda'/'derecha' mientras hay GIRO_DIRIGIDO (el detector sigue SOLO esa orilla)."""
        if self.direccion_giro is not None and self.giro_fase is not None:
            return self.direccion_giro
        return None

    def preferencia_carril(self, ahora=None):
        """
        Rama a tomar en una bifurcación: el lado del GIRO_DIRIGIDO en curso; si no,
        la señal más reciente: izquierda/derecha (ventana_giro_bifurcacion_s) o
        "recto" si fue AVANZAR (avanzar_recto_s). None = preferencia_bifurcacion.
        """
        if ahora is None:
            ahora = time.monotonic()
        if self.direccion_giro is not None:
            return self.direccion_giro
        opciones = []
        if self.ultimo_giro_activado is not None:
            clase, t = self.ultimo_giro_activado
            if ahora - t <= float(self.cfg_carril.get("ventana_giro_bifurcacion_s", 4.0)):
                opciones.append((t, clase))
        if self.ultimo_avanzar_t is not None and \
                ahora - self.ultimo_avanzar_t <= float(self.cfg.get("avanzar_recto_s", 3.0)):
            opciones.append((self.ultimo_avanzar_t, "recto"))
        return max(opciones)[1] if opciones else None

    def decidir(self, p, ahora=None):
        if ahora is None:
            ahora = time.monotonic()
        self._actualizar_senales(p, ahora)
        self._actualizar_peaton_modelo(p, ahora)
        puntajes, causas = self._actualizar_zonas(p, ahora)

        # Prioridad: peatón (y seguridad) > manual > obstáculo > alto > giros > avanzar > carril
        decision = (self._regla_1_seguridad(p, ahora)
                    or self._regla_2_manual(p)
                    or self._regla_3_obstaculo(p, ahora, puntajes)
                    or self._regla_4_esquivar(ahora, puntajes, causas)
                    or self._regla_5_senales_y_maniobras(p, ahora)
                    or self._regla_6_carril(p, ahora))

        # Peatón u obstáculo durante el GIRO_DIRIGIDO: se PAUSA (el reloj también)
        if (self.giro_fase is not None and self.direccion_giro is not None and decision.regla != 5
                and not decision.mantener):
            if self.giro_pausa_desde is None:
                self.giro_pausa_desde = ahora

        # Datos para la nube. Mientras dura un alto o un giro, el evento
        # lleva la señal que lo provocó (ej. "alto" + "detener" los 3 s).
        if decision.senal is None:
            if decision.estado == DETENIDO_POR_ALTO and self.fin_alto > 0:
                decision.senal, decision.confianza = "alto", self.conf_maniobra
            elif decision.estado in (PREPARANDO_GIRO, GIRANDO, GIRO_DIRIGIDO) and self.direccion_giro is not None:
                decision.senal, decision.confianza = self.direccion_giro, self.conf_maniobra
            elif self.senal_confirmada is not None:
                decision.senal, decision.confianza = self.senal_confirmada
            else:
                decision.senal, decision.confianza = "ninguna", 0.0
        decision.confianza = round(min(max(float(decision.confianza), 0.0), 1.0), 3)
        decision.modo = "manual" if p.manual is not None else "autonomo"
        self.estado = decision.estado
        self.ultimo_comando = decision.comando
        self.senal_activa = self._calcular_senal_activa(decision)
        return decision

    def _calcular_senal_activa(self, decision):
        """Para la pantalla: qué señal está mandando en este momento."""
        if decision.senal == "peaton":
            return "peaton"
        if self.fin_alto > 0:
            return "alto"
        if self.direccion_giro is not None:
            return self.direccion_giro
        if self.senal_confirmada is not None:
            return self.senal_confirmada[0]
        return None

    # ------------------------------------------------------------------
    # Percepción -> memoria del agente
    # ------------------------------------------------------------------
    def _senal_ignorada(self, clase, ahora):
        if clase == "alto":
            return ahora < self.ignorar_alto_hasta or self.fin_alto > 0
        if clase in ("izquierda", "derecha"):
            return self.direccion_giro is not None or ahora < self.ignorar_giro_hasta[clase]
        return False

    def senal_cerca(self, d):
        """¿La señal ya está cerca? (alto del recuadro mayor al umbral)."""
        return d.alto_rel > self.cfg["altura_activacion_senal"]

    def _actualizar_senales(self, p, ahora):
        umbral = self.cfg["umbral_confianza_senal"]
        self.senal_vista = max(p.senales, key=lambda d: d.confianza) if p.senales else None
        # Solo se cuentan los cuadros en los que el modelo realmente corrió, y
        # no se cuentan las señales que se están ignorando (para que no se
        # acumulen y se disparen justo cuando termina el tiempo de ignorar).
        if p.senales_ejecutado:
            validas = [d for d in p.senales
                       if d.confianza >= umbral and self.senal_cerca(d)
                       and not self._senal_ignorada(d.clase, ahora)]
            # Si hay varias señales a la vez gana la de mayor prioridad (alto > giros > avanzar)
            mejor = min(validas, key=lambda d: (PRIORIDAD_SENAL.get(d.clase, 9), -d.confianza)) if validas else None
            self.confirmador_senales.agregar(mejor.clase if mejor else None,
                                             mejor.confianza if mejor else 0.0, ahora)
        self.senal_confirmada = self.confirmador_senales.confirmada(ahora)

    def _actualizar_peaton_modelo(self, p, ahora):
        """
        Figuras de peatón del modelo propio. Frena con 1 cuadro si conf >= 0.6,
        o con 2 cuadros seguidos si conf >= 0.45. Ya detenido, se mantiene
        hasta tiempo_libre_peaton_s sin ver ningún peatón válido en el camino.
        A cada figura que NO frena se le pone motivo_ignorado (pantalla y consola).
        """
        if p.senales_ejecutado:
            en_camino = None
            for d in p.peatones_modelo:
                if d.tipo != TIPO_PEATON:
                    continue
                fuera = self._motivo_fuera_de_carril(p, d)
                if fuera:
                    d.motivo_ignorado = fuera
                elif d.confianza < self.peaton_umbral:
                    d.motivo_ignorado = f"confianza baja {d.confianza:.2f} < {self.peaton_umbral:.2f}"
                else:
                    d.motivo_ignorado = ""
                    if en_camino is None or d.confianza > en_camino.confianza:
                        en_camino = d
            if en_camino is not None:
                self.peaton_racha += 1
                self.peaton_visto_t = ahora
                self.peaton_modelo = en_camino
                if en_camino.confianza >= self.peaton_umbral_1 or self.peaton_racha >= self.peaton_seguidos:
                    if not self.peaton_detenido:
                        print(f"[PEATON] DETENER: conf {en_camino.confianza:.2f}, "
                              f"{self.peaton_racha} cuadro(s) seguido(s)")
                    self.peaton_detenido = True
                elif not self.peaton_detenido:
                    en_camino.motivo_ignorado = (f"{self.peaton_racha} cuadro de {self.peaton_seguidos} "
                                                 f"(conf {en_camino.confianza:.2f} < {self.peaton_umbral_1:.2f})")
            else:
                self.peaton_racha = 0
        # Liberar: tiempo_libre_peaton_s sin verlo
        if self.peaton_detenido and (self.peaton_visto_t is None
                                     or ahora - self.peaton_visto_t > self.peaton_tiempo_libre):
            self.peaton_detenido = False
            print(f"[PEATON] {self.peaton_tiempo_libre:.1f} s sin verlo: se reanuda la marcha.")
        if p.senales_ejecutado:
            self._avisar_peatones_ignorados(p, ahora)

    def _avisar_peatones_ignorados(self, p, ahora):
        """Imprime en consola por qué se ignoró cada peatón (sin repetir el mismo texto cada cuadro)."""
        motivos = [d.motivo_ignorado for d in list(p.peatones_fuera) + list(p.peatones_modelo)
                   if d.tipo == TIPO_PEATON and d.motivo_ignorado]
        if self.peaton_detenido or not motivos:
            return
        texto = " | ".join(motivos)
        anterior, t = self._ultimo_aviso_peaton
        if texto != anterior or ahora - t >= 1.0:
            print(f"[PEATON] Ignorado: {texto}")
            self._ultimo_aviso_peaton = (texto, ahora)

    def _actualizar_zonas(self, p, ahora):
        """Fusiona YOLO + OpenCV en zonas ocupadas (confirmadas K de M)."""
        crudas = {z: False for z in ZONAS}
        puntajes = {z: 0.0 for z in ZONAS}
        causas = {z: [] for z in ZONAS}
        for d in p.detecciones:
            if d.tipo in (TIPO_OBSTACULO, TIPO_PERSONA) and d.area >= self.cfg["area_obstaculo_zona"]:
                crudas[d.zona] = True
                puntajes[d.zona] += d.area
                causas[d.zona].append(d.clase)
        for z in ZONAS:
            puntajes[z] += p.ocupacion_opencv[z]
            if p.bloqueo_opencv[z]:
                crudas[z] = True
                causas[z].append(f"bordes {p.ocupacion_opencv[z]:.0%}")
        for z in ZONAS:
            self.confirmador_zonas[z].agregar(True if crudas[z] else None, 0.0, ahora)
            self.zonas_ocupadas[z] = self.confirmador_zonas[z].confirmada(ahora) is not None
        return puntajes, {z: ", ".join(causas[z]) for z in ZONAS}

    def _reiniciar_maniobras(self, conservar_giro=False):
        """
        La seguridad o el modo manual cancelan cualquier maniobra en curso.
        Con conservar_giro (peatón) el GIRO_DIRIGIDO solo se pausa: sigue al liberarse.
        """
        if conservar_giro and self.giro_fase is not None:
            self.lado_esquive = None
            self.fin_alto = 0.0
            self.fin_retroceso = self.fin_giro_esquive = 0.0
            self.inicio_busqueda = None
            self.pd.reiniciar()
            return
        self.direccion_giro = None
        self.giro_fase = None
        self.giro_pausa_desde = None
        self.lado_esquive = None
        self.fin_alto = self.fin_preparacion = self.fin_giro = 0.0
        self.fin_retroceso = self.fin_giro_esquive = 0.0
        self.inicio_busqueda = None
        self.pd.reiniciar()

    def _estado_libre(self, ahora):
        """Estado 'normal' al seguir carril: indica si se está ignorando el alto."""
        return IGNORANDO_ALTO if ahora < self.ignorar_alto_hasta else SIGUIENDO_CARRIL

    # ------------------------------------------------------------------
    # REGLA 1: SEGURIDAD
    # ------------------------------------------------------------------
    def _en_movimiento(self):
        c = self.ultimo_comando
        return c.nombre != "DETENER" and any(v for v in (c.p1, c.p2) if v is not None)

    def _regla_1_seguridad(self, p, ahora):
        motivo, senal, conf = None, None, 0.0
        mantener = False
        borrosa = p.video_ok and not p.imagen_confiable and p.imagen_borrosa
        if borrosa:
            if self.borrosa_desde is None:
                self.borrosa_desde = ahora
        else:
            self.borrosa_desde = None
        if p.emergencia:
            motivo = "Paro de emergencia (ESPACIO)"
        elif not p.video_ok:
            motivo = p.motivo_video or "Video perdido"
        elif not p.imagen_confiable:
            # Borrosa mientras se mueve (es por el movimiento): NO detiene, se mantiene el
            # último comando. Solo se detiene si sigue borrosa más de max_borrosa_s.
            if (borrosa and self._en_movimiento()
                    and ahora - self.borrosa_desde <= float(self.cfg_calidad.get("max_borrosa_s", 1.0))):
                mantener = True
            else:
                motivo = p.motivo_imagen or "Imagen no confiable"
        if motivo is None:
            # Personas reales (COCO 'person'): al instante, como siempre
            persona = self._persona_en_carril(p, [d for d in p.detecciones if d.tipo == TIPO_PERSONA])
            # Figuras de peatón (modelo propio): 1 cuadro >= 0.6 o 2 seguidos >= 0.45,
            # y se mantiene hasta 1 s después de dejar de verlo
            if persona is None and self.peaton_detenido and self.peaton_modelo is not None:
                persona = self.peaton_modelo
            if persona is not None:
                motivo = f"Peaton en el carril (conf {persona.confianza:.2f}, area {persona.area:.0%})"
                senal, conf = "peaton", persona.confianza
        if motivo is None:
            if mantener:
                comando = self.ultimo_comando
                return Decision(comando, self.estado,
                                f"{p.motivo_imagen or 'Imagen borrosa'} en movimiento: mantengo el ultimo "
                                f"comando ({ahora - self.borrosa_desde:.1f} s)", 1,
                                self._accion_de_ruedas(comando), mantener=True)
            return None
        self._reiniciar_maniobras(conservar_giro=(senal == "peaton"))
        return Decision(Comando.detener(), DETENIDO_SEGURIDAD, motivo, 1, "detener", senal, conf)

    def _persona_en_carril(self, p, candidatas):
        """De las candidatas (personas o figuras de peatón), la que está en el carril (o muy cerca), o None."""
        for d in candidatas:
            if not self._motivo_fuera_de_carril(p, d):
                return d
        return None

    def _motivo_fuera_de_carril(self, p, d):
        """'' si la detección está en el carril (o muy cerca); si no, el motivo en texto."""
        c = self.cfg
        if getattr(d, "en_camino_forzado", False):
            return ""       # sobre la línea guía o pies fuera de la imagen (muy cerca)
        if d.area >= c["area_persona_grande"]:
            return ""
        if d.area < c["area_persona_minima"]:
            return f"muy pequeno (area {d.area:.1%})"
        ancho = float(p.ancho_imagen)
        carril = p.carril
        if carril.detectado and carril.ancho_carril:
            margen = c["margen_carril_persona"] * ancho
            limite_izq = carril.centro_x - carril.ancho_carril / 2.0 - margen
            limite_der = carril.centro_x + carril.ancho_carril / 2.0 + margen
            x1, _, x2, _ = d.caja
            return "" if (x2 >= limite_izq and x1 <= limite_der) else "fuera de pista (a un lado del carril)"
        return "" if d.zona == "CENTRO" else f"fuera de pista (zona {d.zona}, sin carril)"

    # ------------------------------------------------------------------
    # REGLA 2: MODO MANUAL
    # ------------------------------------------------------------------
    def _regla_2_manual(self, p):
        if p.manual is None:
            return None
        accion, motivo = p.manual
        self._reiniciar_maniobras()
        if accion in COMANDO_MANUAL:
            nombre = COMANDO_MANUAL[accion]
            comando = Comando.simple(nombre, self.velocidades.get(nombre, 0))
        else:
            accion, comando = "detener", Comando.detener()
        return Decision(comando, MANUAL, f"Modo manual: {accion}. {motivo}".strip(), 2, accion)

    # ------------------------------------------------------------------
    # REGLA 3: OBSTÁCULO (según modo_obstaculo)
    # ------------------------------------------------------------------
    def _regla_3_obstaculo(self, p, ahora, puntajes):
        if self.modo_obstaculo == "esquivar":
            return self._regla_3_muy_cerca(p, ahora, puntajes)
        return self._regla_3_detener_obstaculo(p, ahora)

    def _obstaculos_en_camino(self, p):
        """
        Obstáculos EN EL CAMINO: zona CENTRO y dentro del ROI del carril
        (la parte de abajo de la imagen, el piso justo delante). Devuelve
        una lista de causas en texto; vacía si el camino está libre.
        """
        y_roi = self.roi_carril_y * p.alto_imagen
        causas = []
        for d in p.detecciones:
            # caja[3] = borde de abajo del recuadro: si entra al ROI, el objeto está en el piso de enfrente
            if d.tipo == TIPO_OBSTACULO and d.zona == "CENTRO" and d.caja[3] > y_roi:
                causas.append(f"{d.clase} {d.confianza:.2f}")
        # El ROI de OpenCV (opencv_obstaculos.roi_inicio_y) está dentro del ROI del carril
        if p.bloqueo_opencv.get("CENTRO"):
            causas.append(f"bordes {p.ocupacion_opencv['CENTRO']:.0%}")
        return causas

    def _regla_3_detener_obstaculo(self, p, ahora):
        causas = self._obstaculos_en_camino(p)
        hay = bool(causas)
        self.confirmador_camino.agregar(True if hay else None, 0.0, ahora)

        if not self.detenido_obstaculo:
            # Para frenar se pide la confirmación K de M de obstáculos (2 de 3 cuadros)
            if self.confirmador_camino.confirmada(ahora) is None:
                return None
            self.detenido_obstaculo = True
            self.libre_desde = None
            self.pd.reiniciar()
        elif hay:
            self.libre_desde = None          # algo volvió a aparecer: se reinicia la cuenta
        else:
            if self.libre_desde is None:
                self.libre_desde = ahora
            if ahora - self.libre_desde >= self.cfg["segundos_libre_para_seguir"]:
                # Camino libre el tiempo pedido: se vuelve a seguir el carril
                self.detenido_obstaculo = False
                self.libre_desde = None
                self.confirmador_camino.limpiar()
                self.cuadros_sin_carril = 0
                return None

        if hay:
            self.causa_obstaculo = ", ".join(causas)
            motivo = f"Obstaculo en el camino ({self.causa_obstaculo})"
        else:
            libre = ahora - self.libre_desde
            motivo = f"Camino libre {libre:.1f}/{self.cfg['segundos_libre_para_seguir']:.1f} s para seguir"
        # La API no tiene la señal "obstaculo": se manda senal "ninguna" + accion "detener"
        return Decision(Comando.detener(), DETENIDO_OBSTACULO, motivo, 3, "detener", "ninguna", 0.0)

    # ------------------------------------------------------------------
    # REGLA 3 (modo "esquivar"): OBSTÁCULO MUY CERCA o TODO BLOQUEADO -> RETROCEDER y girar
    # ------------------------------------------------------------------
    def _regla_3_muy_cerca(self, p, ahora, puntajes):
        c = self.cfg
        # a) Maniobra en curso
        if ahora < self.fin_retroceso:
            return Decision(Comando.simple("RETROCEDER", c["velocidad_retroceso"]), RETROCEDIENDO,
                            f"Retroceso por obstaculo ({self.fin_retroceso - ahora:.1f} s)", 3, "detener")
        if ahora < self.fin_giro_esquive:
            if self.lado_esquive is None:
                self.lado_esquive = self._lado_mas_libre(puntajes)
            return self._mover_esquive(self.lado_esquive, RETROCEDIENDO,
                                       "Tras el retroceso: girando hacia la zona mas libre", 3)
        self.lado_esquive = None

        # b) ¿Hay que empezar a retroceder?
        motivo = None
        for d in p.detecciones:
            if d.tipo == TIPO_OBSTACULO and d.area >= c["area_obstaculo_muy_cercano"]:
                motivo = f"{d.clase} muy cerca (area {d.area:.0%})"
                break
        if motivo is None and all(self.zonas_ocupadas[z] for z in ZONAS):
            motivo = "Las tres zonas bloqueadas"
        if motivo is None:
            return None
        self.fin_retroceso = ahora + c["tiempo_retroceso_s"]
        self.fin_giro_esquive = self.fin_retroceso + c["tiempo_giro_tras_retroceso_s"]
        self.pd.reiniciar()
        return Decision(Comando.simple("RETROCEDER", c["velocidad_retroceso"]), RETROCEDIENDO,
                        f"{motivo} -> retroceso", 3, "detener")

    # ------------------------------------------------------------------
    # REGLA 4 (solo modo "esquivar"): OBSTÁCULO EN UNA ZONA -> GIRAR HACIA LA MÁS LIBRE
    # ------------------------------------------------------------------
    def _regla_4_esquivar(self, ahora, puntajes, causas):
        if self.modo_obstaculo != "esquivar":
            return None          # en modo "detener" no se esquiva
        ocupadas = self.zonas_ocupadas
        hay_que_girar = ocupadas["CENTRO"] or (
            self.cfg["esquivar_obstaculo_lateral"] and (ocupadas["IZQ"] or ocupadas["DER"]))
        if not hay_que_girar:
            return None
        zona = "CENTRO" if ocupadas["CENTRO"] else ("IZQ" if ocupadas["IZQ"] else "DER")
        if zona == "IZQ":
            lado = "derecha"
        elif zona == "DER":
            lado = "izquierda"
        else:
            lado = self._lado_mas_libre(puntajes)
        self.pd.reiniciar()
        return self._mover_esquive(lado, self._estado_libre(ahora),
                                   f"Obstaculo en {zona} ({causas[zona]}) -> esquivar a la {lado}", 4)

    def _lado_mas_libre(self, puntajes):
        if self.zonas_ocupadas["IZQ"] != self.zonas_ocupadas["DER"]:
            return "derecha" if self.zonas_ocupadas["IZQ"] else "izquierda"
        if puntajes["IZQ"] != puntajes["DER"]:
            return "izquierda" if puntajes["IZQ"] < puntajes["DER"] else "derecha"
        return self.cfg["giro_preferido"]

    def _mover_esquive(self, lado, estado, motivo, regla):
        lenta, rapida = self.cfg["esquive_rueda_lenta"], self.cfg["esquive_rueda_rapida"]
        if lado == "izquierda":
            return Decision(Comando.mover(lenta, rapida), estado, motivo, regla, "girar_izq")
        return Decision(Comando.mover(rapida, lenta), estado, motivo, regla, "girar_der")

    # ------------------------------------------------------------------
    # REGLA 5: SEÑALES CONFIRMADAS Y MANIOBRAS EN CURSO
    # ------------------------------------------------------------------
    def _regla_5_senales_y_maniobras(self, p, ahora):
        c = self.cfg
        # --- a) Maniobras que ya estaban en curso ---------------------
        if self.estado == DETENIDO_POR_ALTO or self.fin_alto > 0:
            if ahora < self.fin_alto:
                return Decision(Comando.detener(), DETENIDO_POR_ALTO,
                                f"Senal de ALTO: esperando {self.fin_alto - ahora:.1f} s", 5, "detener")
            self.fin_alto = 0.0
            self.ignorar_alto_hasta = ahora + c["tiempo_ignorar_alto_s"]
            self.confirmador_senales.limpiar()

        if self.direccion_giro is not None:
            alto_nuevo = (self.senal_confirmada is not None and self.senal_confirmada[0] == "alto"
                          and ahora >= self.ignorar_alto_hasta)
            if not alto_nuevo:
                return self._continuar_giro(p, ahora)
            # alto > giros: se corta el giro (y esa señal de giro se ignora 5 s)
            self.ignorar_giro_hasta[self.direccion_giro] = ahora + c["tiempo_ignorar_giro_s"]
            self.direccion_giro = None
            self.giro_fase = None
            self.giro_pausa_desde = None
            self.fin_preparacion = self.fin_giro = self.fin_giro_dirigido = 0.0
            self.pd.reiniciar()

        # --- b) Señal nueva confirmada --------------------------------
        if self.senal_confirmada is None:
            return None
        clase, conf = self.senal_confirmada

        if clase == "alto" and ahora >= self.ignorar_alto_hasta:
            self.fin_alto = ahora + c["tiempo_alto_s"]
            self.conf_maniobra = conf
            self.confirmador_senales.limpiar()
            return Decision(Comando.detener(), DETENIDO_POR_ALTO,
                            f"Senal de ALTO confirmada: detenerse {c['tiempo_alto_s']:.0f} s", 5,
                            "detener", "alto", conf)

        if clase in ("izquierda", "derecha") and ahora >= self.ignorar_giro_hasta[clase]:
            self.direccion_giro = clase
            self.ultimo_giro_activado = (clase, ahora)   # para elegir rama en bifurcaciones
            self.conf_maniobra = conf
            self.giro_fase = None                        # empieza al acabar la preparación
            self.giro_pausa_desde = None
            prep = self._tiempo_preparacion()
            self.fin_preparacion = ahora + prep if prep > 0 else 0.0
            self.confirmador_senales.limpiar()
            decision = self._continuar_giro(p, ahora)
            decision.senal, decision.confianza = clase, conf
            return decision

        if clase == "avanzar":
            self.ultimo_avanzar_t = ahora     # bifurcaciones: tramo más recto avanzar_recto_s
        if clase == "avanzar" and self.esperando_avanzar:
            self.esperando_avanzar = False
            self.cuadros_sin_carril = 0
        # avanzar: no cambia la maniobra; las reglas 4-6 siguen el carril y
        # decide() pone senal="avanzar" en el evento que va a la nube.
        return None

    def _tiempo_preparacion(self):
        """Distancia (si se configuró) convertida a tiempo, o el tiempo directo."""
        c = self.cfg
        if c["preparacion_distancia_cm"] > 0 and c["velocidad_estimada_cm_s"] > 0:
            return c["preparacion_distancia_cm"] / c["velocidad_estimada_cm_s"]
        return c["preparacion_tiempo_s"]

    def _continuar_giro(self, p, ahora):
        c = self.cfg
        lado = self.direccion_giro
        corto = "IZQ" if lado == "izquierda" else "DER"
        accion_giro = "girar_izq" if lado == "izquierda" else "girar_der"

        # PREPARANDO_GIRO: seguir el carril hasta llegar a la intersección
        if self.giro_fase is None and self.fin_preparacion > 0 and ahora < self.fin_preparacion:
            comando, texto = self._comando_carril(p, ahora)
            return Decision(comando, PREPARANDO_GIRO,
                            f"Giro a la {lado} en {self.fin_preparacion - ahora:.1f} s ({texto})", 5, "avanzar")

        # Inicio del GIRO_DIRIGIDO
        if self.giro_fase is None:
            self.fin_preparacion = 0.0
            self.giro_fase = "giro"
            self.inicio_giro = ahora
            self.fin_giro = ahora + float(c.get("giro_tiempo_s", 0.8))
            self.fin_giro_dirigido = ahora + float(c.get("giro_dirigido_max_s", 3.0))
            self.giro_cuadros_linea = 0
            self.giro_pausa_desde = None
            self.pedir_reinicio_carril = True       # olvidar la línea del camino viejo
            print(f"[GIRO] GIRO_DIRIGIDO {corto}: giro {self.fin_giro - ahora:.1f} s, "
                  f"max {self.fin_giro_dirigido - ahora:.1f} s")

        # Se reanuda tras un peatón u obstáculo: el tiempo en pausa no cuenta
        if self.giro_pausa_desde is not None:
            pausa = ahora - self.giro_pausa_desde
            self.inicio_giro += pausa
            self.fin_giro += pausa
            self.fin_giro_dirigido += pausa
            self.giro_pausa_desde = None
            self.giro_cuadros_linea = 0
            self.pd.reiniciar()

        restante = max(0.0, self.fin_giro_dirigido - ahora)
        texto_estado = f"GIRO_DIRIGIDO {corto} {restante:.1f} s"

        # a) Fase "giro": MOVER fijo
        if self.giro_fase == "giro":
            if ahora < self.fin_giro:
                vel_izq, vel_der = c["giro_izquierda"] if lado == "izquierda" else c["giro_derecha"]
                return Decision(Comando.mover(vel_izq, vel_der), GIRO_DIRIGIDO,
                                f"Girando a la {lado} ({ahora - self.inicio_giro:.1f}/"
                                f"{self.fin_giro - self.inicio_giro:.1f} s)", 5, accion_giro,
                                texto_estado=texto_estado)
            self.giro_fase = "orilla"
            self.giro_cuadros_linea = 0
            self.pd.reiniciar()
            # Si la orilla aún no se ve, "sigo igual" = seguir girando, y la búsqueda va hacia ese lado
            vel_izq, vel_der = c["giro_izquierda"] if lado == "izquierda" else c["giro_derecha"]
            self.ultimo_mover = Comando.mover(vel_izq, vel_der)
            self.ultimo_error = -0.5 if lado == "izquierda" else 0.5
            self.cuadros_sin_carril = 0
            self.pedir_reinicio_carril = True       # la memoria de la línea vieja ya no sirve

        # b) Fase "orilla": seguir la orilla de ese lado hasta ver la guía estable
        carril = p.carril
        error_max = float(c.get("giro_linea_error_max", 0.3))
        necesarios = max(1, int(c.get("giro_linea_cuadros_estable", 3)))
        if carril.linea_visible and carril.error_linea is not None and abs(carril.error_linea) < error_max:
            self.giro_cuadros_linea += 1
        else:
            self.giro_cuadros_linea = 0

        fin_motivo = None
        if self.giro_cuadros_linea >= necesarios:
            fin_motivo = f"guia estable {necesarios} cuadros (error {carril.error_linea:+.2f})"
        elif ahora >= self.fin_giro_dirigido:
            fin_motivo = f"tiempo maximo {float(c.get('giro_dirigido_max_s', 3.0)):.1f} s"
        if fin_motivo is not None:
            print(f"[GIRO] GIRO_DIRIGIDO {corto} terminado: {fin_motivo} -> LINEA")
            self.direccion_giro = None
            self.giro_fase = None
            self.giro_cuadros_linea = 0
            self.ignorar_giro_hasta[lado] = ahora + c["tiempo_ignorar_giro_s"]
            self.confirmador_senales.limpiar()
            self.pd.reiniciar()
            comando, texto = self._comando_carril(p, ahora)
            return Decision(comando, self._estado_libre(ahora),
                            f"Giro a la {lado} terminado ({fin_motivo}); {texto}", 5,
                            self._accion_de_ruedas(comando))

        comando, texto = self._comando_carril(p, ahora)
        if comando.nombre == "DETENER":
            accion = "detener"
        else:
            accion = self._accion_de_ruedas(comando)
        modo = carril.modo if carril.detectado else "sin orilla"
        return Decision(comando, GIRO_DIRIGIDO,
                        f"Siguiendo orilla {corto} ({modo}); guia estable {self.giro_cuadros_linea}/{necesarios}; "
                        f"{texto}", 5, accion, texto_estado=texto_estado)

    # ------------------------------------------------------------------
    # REGLA 6: SEGUIR EL CARRIL
    # ------------------------------------------------------------------
    def _regla_6_carril(self, p, ahora):
        if self.esperando_avanzar:
            return Decision(Comando.detener(), DETENIDO_SEGURIDAD,
                            "Esperando la senal AVANZAR para arrancar", 6, "detener")
        comando, texto = self._comando_carril(p, ahora)
        if comando.nombre == "DETENER":
            etiqueta = "LINEA PERDIDA" if p.carril.linea_perdida else ""
            return Decision(comando, DETENIDO_SEGURIDAD, texto, 6, "detener", texto_estado=etiqueta)
        return Decision(comando, self._estado_libre(ahora), texto, 6, self._accion_de_ruedas(comando))

    @staticmethod
    def _angulo_guia(carril):
        """Ángulo de la guía para la anticipación: solo si se está siguiendo la guía."""
        return carril.angulo_linea if carril.modo in (MODO_LINEA, MODO_LINEA_PREDICHA) else 0.0

    def _comando_carril(self, p, ahora):
        """Control PD si hay carril; si se perdió, sigue igual, se detiene o lo busca."""
        c = self.cfg
        carril = p.carril
        if carril.detectado:
            self.cuadros_sin_carril = 0
            self.inicio_busqueda = None
            self.ultimo_error = carril.error
            if carril.prediccion:
                # solo_linea: la guía no se ve; se sigue la última recta, más despacio
                base = float(self.cfg_carril.get("velocidad_sin_linea", 115))
                vel_izq, vel_der = self.pd.calcular(carril.error, ahora, base=base,
                                                    angulo=self._angulo_guia(carril))
                self.ultimo_mover = Comando.mover(vel_izq, vel_der)
                return self.ultimo_mover, (f"Linea predicha {carril.segundos_sin_linea:.1f} s "
                                           f"(error {carril.error:+.2f}, vel {base:.0f})")
            vel_izq, vel_der = self.pd.calcular(carril.error, ahora, angulo=self._angulo_guia(carril))
            self.ultimo_mover = Comando.mover(vel_izq, vel_der)
            return self.ultimo_mover, f"Siguiendo carril (error {carril.error:+.2f})"

        self.cuadros_sin_carril += 1
        self.pd.reiniciar()
        if carril.linea_perdida:
            # solo_linea: sin guía más de tiempo_prediccion_linea_s -> DETENER (sin girar a ciegas)
            return Comando.detener(), f"LINEA PERDIDA: {carril.descripcion}, detenido"
        if carril.perdido:
            # Sin línea amarilla y casi sin asfalto en la ROI: no hay por dónde ir
            return Comando.detener(), f"Carril perdido: sin amarillo y {carril.fraccion_asfalto:.0%} de asfalto, detenido"
        limite = int(c["max_cuadros_sin_carril"])
        if self.cuadros_sin_carril <= limite:
            return self.ultimo_mover, f"Carril no visible ({self.cuadros_sin_carril}/{limite}), sigo igual"

        if c["al_perder_carril"] == "buscar":
            if self.inicio_busqueda is None:
                self.inicio_busqueda = ahora
            if ahora - self.inicio_busqueda <= c["tiempo_max_busqueda_s"]:
                interna, externa = c["busqueda_rueda_interna"], c["busqueda_rueda_externa"]
                if self.ultimo_error >= 0:      # el carril se fue a la derecha
                    return Comando.mover(externa, interna), "Carril perdido: buscando a la derecha"
                return Comando.mover(interna, externa), "Carril perdido: buscando a la izquierda"
            return Comando.detener(), "Carril perdido: busqueda agotada, detenido"
        return Comando.detener(), "Carril perdido: detenido"

    def _accion_de_ruedas(self, comando):
        """Traduce un MOVER a la acción del contrato (para la nube)."""
        if comando.nombre != "MOVER":
            return {"AVANZAR": "avanzar", "GIRAR_IZQ": "girar_izq",
                    "GIRAR_DER": "girar_der"}.get(comando.nombre, "detener")
        vel_izq, vel_der = comando.p1, comando.p2
        if vel_izq <= 0 and vel_der <= 0:
            return "detener"
        diferencia = vel_izq - vel_der
        if diferencia > self.cfg["diferencia_reporte_giro"]:
            return "girar_der"
        if diferencia < -self.cfg["diferencia_reporte_giro"]:
            return "girar_izq"
        return "avanzar"
