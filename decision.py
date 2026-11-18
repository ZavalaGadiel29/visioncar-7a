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
  3) SEÑALES confirmadas y maniobras en curso:
       alto      -> DETENIDO_POR_ALTO (3 s) -> IGNORANDO_ALTO (4 s)
       izquierda -> PREPARANDO_GIRO -> GIRANDO -> (ignora giros unos segundos)
       derecha   -> igual que izquierda
       avanzar   -> reanuda si estaba detenido esperando
  4) Obstáculo MUY cercano o 3 zonas bloqueadas -> RETROCEDIENDO y luego
     girar hacia la zona más libre.
  5) Obstáculo en una zona -> girar hacia la zona más libre.
  6) Nada de lo anterior -> SIGUIENDO_CARRIL (control PD -> MOVER).

Una señal está CONFIRMADA si en K de los últimos M cuadros analizados
aparece con confianza >= umbral y con un área mayor que su "área de
activación" (que sea lo bastante grande = lo bastante cerca).

Cada Decision lleva: comando para el carrito, estado, motivo en texto,
número de regla, y los datos para la nube (senal, confianza, accion, modo).
"""

import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Optional

from carril import ControlPD, ResultadoCarril
from comunicacion import Comando
from percepcion_yolo import TIPO_OBSTACULO, TIPO_PERSONA

# --- Estados -----------------------------------------------------------
SIGUIENDO_CARRIL = "SIGUIENDO_CARRIL"
DETENIDO_POR_ALTO = "DETENIDO_POR_ALTO"
IGNORANDO_ALTO = "IGNORANDO_ALTO"
PREPARANDO_GIRO = "PREPARANDO_GIRO"
GIRANDO = "GIRANDO"
RETROCEDIENDO = "RETROCEDIENDO"
DETENIDO_SEGURIDAD = "DETENIDO_SEGURIDAD"
MANUAL = "MANUAL"

ZONAS = ("IZQ", "CENTRO", "DER")

# Acción manual de la API -> comando del carrito
COMANDO_MANUAL = {"avanzar": "AVANZAR", "girar_izq": "GIRAR_IZQ", "girar_der": "GIRAR_DER"}


# ======================================================================
# Datos de entrada y salida
# ======================================================================
@dataclass
class EntradaPercepcion:
    """Todo lo que se percibió en este instante."""
    ancho_imagen: int = 320
    video_ok: bool = False
    motivo_video: str = ""
    imagen_confiable: bool = False
    motivo_imagen: str = ""
    emergencia: bool = False
    detecciones: list = field(default_factory=list)      # personas y obstáculos (COCO)
    senales: list = field(default_factory=list)          # señales del modelo propio
    senales_ejecutado: bool = False                      # ¿corrió el modelo de señales este cuadro?
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

        c = self.cfg
        ventana = c["ventana_confirmacion_s"]
        self.confirmador_senales = ConfirmadorKdeM(c["confirmacion_senal_k"], c["confirmacion_senal_m"], ventana)
        self.confirmador_zonas = {z: ConfirmadorKdeM(c["confirmacion_obstaculo_k"], c["confirmacion_obstaculo_m"],
                                                     ventana) for z in ZONAS}

        self.estado = SIGUIENDO_CARRIL
        self.esperando_avanzar = bool(c.get("esperar_senal_avanzar_al_iniciar", False))
        self.fin_alto = 0.0
        self.ignorar_alto_hasta = 0.0
        self.ignorar_giros_hasta = 0.0
        self.direccion_giro = None        # "izquierda" / "derecha"
        self.fin_preparacion = 0.0
        self.inicio_giro = 0.0
        self.fin_giro = 0.0
        self.fin_retroceso = 0.0
        self.fin_giro_esquive = 0.0
        self.lado_esquive = None
        self.cuadros_sin_carril = 0
        self.inicio_busqueda = None
        self.ultimo_error = 0.0
        self.ultimo_mover = Comando.mover(0, 0)
        self.pedir_reinicio_carril = False   # main.py lo lee para reiniciar el detector de carril

        # Para la interfaz
        self.zonas_ocupadas = {z: False for z in ZONAS}
        self.senal_vista = None             # mejor detección cruda de este cuadro
        self.senal_confirmada = None        # (clase, confianza) o None

    # ------------------------------------------------------------------
    def decidir(self, p, ahora=None):
        if ahora is None:
            ahora = time.monotonic()
        self._actualizar_senales(p, ahora)
        puntajes, causas = self._actualizar_zonas(p, ahora)

        decision = (self._regla_1_seguridad(p)
                    or self._regla_2_manual(p)
                    or self._regla_3_senales_y_maniobras(p, ahora)
                    or self._regla_4_muy_cerca(p, ahora, puntajes)
                    or self._regla_5_esquivar(ahora, puntajes, causas)
                    or self._regla_6_carril(p, ahora))

        # Datos para la nube
        if decision.senal is None:
            if self.senal_confirmada is not None:
                decision.senal, decision.confianza = self.senal_confirmada
            else:
                decision.senal, decision.confianza = "ninguna", 0.0
        decision.confianza = round(min(max(float(decision.confianza), 0.0), 1.0), 3)
        decision.modo = "manual" if p.manual is not None else "autonomo"
        self.estado = decision.estado
        return decision

    # ------------------------------------------------------------------
    # Percepción -> memoria del agente
    # ------------------------------------------------------------------
    def _senal_ignorada(self, clase, ahora):
        if clase == "alto":
            return ahora < self.ignorar_alto_hasta or self.fin_alto > 0
        if clase in ("izquierda", "derecha"):
            return ahora < self.ignorar_giros_hasta or self.direccion_giro is not None
        return False

    def _actualizar_senales(self, p, ahora):
        umbral = self.cfg["umbral_confianza_senal"]
        areas = self.cfg["area_activacion"]
        self.senal_vista = max(p.senales, key=lambda d: d.confianza) if p.senales else None
        # Solo se cuentan los cuadros en los que el modelo realmente corrió, y
        # no se cuentan las señales que se están ignorando (para que no se
        # acumulen y se disparen justo cuando termina el tiempo de ignorar).
        if p.senales_ejecutado:
            validas = [d for d in p.senales
                       if d.confianza >= umbral and d.area >= areas.get(d.clase, 0.0)
                       and not self._senal_ignorada(d.clase, ahora)]
            mejor = max(validas, key=lambda d: d.confianza) if validas else None
            self.confirmador_senales.agregar(mejor.clase if mejor else None,
                                             mejor.confianza if mejor else 0.0, ahora)
        self.senal_confirmada = self.confirmador_senales.confirmada(ahora)

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

    def _reiniciar_maniobras(self):
        """La seguridad o el modo manual cancelan cualquier maniobra en curso."""
        self.direccion_giro = None
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
    def _regla_1_seguridad(self, p):
        motivo, senal, conf = None, None, 0.0
        if p.emergencia:
            motivo = "Paro de emergencia (ESPACIO)"
        elif not p.video_ok:
            motivo = p.motivo_video or "Video perdido"
        elif not p.imagen_confiable:
            motivo = p.motivo_imagen or "Imagen no confiable"
        else:
            persona = self._persona_en_carril(p)
            if persona is not None:
                motivo = f"Peaton en el carril (conf {persona.confianza:.2f}, area {persona.area:.0%})"
                senal, conf = "peaton", persona.confianza
        if motivo is None:
            return None
        self._reiniciar_maniobras()
        return Decision(Comando.detener(), DETENIDO_SEGURIDAD, motivo, 1, "detener", senal, conf)

    def _persona_en_carril(self, p):
        """Devuelve la persona que está en el carril (o muy cerca), o None."""
        c = self.cfg
        ancho = float(p.ancho_imagen)
        carril = p.carril
        for d in p.detecciones:
            if d.tipo != TIPO_PERSONA or d.area < c["area_persona_minima"]:
                continue
            if d.area >= c["area_persona_grande"]:
                return d
            if carril.detectado and carril.ancho_carril:
                margen = c["margen_carril_persona"] * ancho
                limite_izq = carril.centro_x - carril.ancho_carril / 2.0 - margen
                limite_der = carril.centro_x + carril.ancho_carril / 2.0 + margen
                x1, _, x2, _ = d.caja
                if x2 >= limite_izq and x1 <= limite_der:
                    return d
            elif d.zona == "CENTRO":
                return d
        return None

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
    # REGLA 3: SEÑALES CONFIRMADAS Y MANIOBRAS EN CURSO
    # ------------------------------------------------------------------
    def _regla_3_senales_y_maniobras(self, p, ahora):
        c = self.cfg
        # --- a) Maniobras que ya estaban en curso ---------------------
        if self.estado == DETENIDO_POR_ALTO or self.fin_alto > 0:
            if ahora < self.fin_alto:
                return Decision(Comando.detener(), DETENIDO_POR_ALTO,
                                f"Senal de ALTO: esperando {self.fin_alto - ahora:.1f} s", 3, "detener")
            self.fin_alto = 0.0
            self.ignorar_alto_hasta = ahora + c["tiempo_ignorar_alto_s"]
            self.confirmador_senales.limpiar()

        if self.direccion_giro is not None:
            return self._continuar_giro(p, ahora)

        # --- b) Señal nueva confirmada --------------------------------
        if self.senal_confirmada is None:
            return None
        clase, conf = self.senal_confirmada

        if clase == "alto" and ahora >= self.ignorar_alto_hasta:
            self.fin_alto = ahora + c["tiempo_alto_s"]
            self.confirmador_senales.limpiar()
            return Decision(Comando.detener(), DETENIDO_POR_ALTO,
                            f"Senal de ALTO confirmada: detenerse {c['tiempo_alto_s']:.0f} s", 3,
                            "detener", "alto", conf)

        if clase in ("izquierda", "derecha") and ahora >= self.ignorar_giros_hasta:
            self.direccion_giro = clase
            self.fin_preparacion = ahora + self._tiempo_preparacion()
            self.confirmador_senales.limpiar()
            decision = self._continuar_giro(p, ahora)
            decision.senal, decision.confianza = clase, conf
            return decision

        if clase == "avanzar" and self.esperando_avanzar:
            self.esperando_avanzar = False
            self.cuadros_sin_carril = 0
            # No se devuelve nada: las reglas 4-6 deciden cómo avanzar
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
        accion_giro = "girar_izq" if lado == "izquierda" else "girar_der"

        # PREPARANDO_GIRO: seguir el carril hasta llegar a la intersección
        if self.fin_preparacion > 0 and ahora < self.fin_preparacion:
            comando, texto = self._comando_carril(p, ahora)
            return Decision(comando, PREPARANDO_GIRO,
                            f"Giro a la {lado} en {self.fin_preparacion - ahora:.1f} s ({texto})", 3, "avanzar")

        # Inicio del GIRO
        if self.fin_preparacion > 0:
            self.fin_preparacion = 0.0
            self.inicio_giro = ahora
            self.fin_giro = ahora + c["duracion_giro_s"]
            self.pedir_reinicio_carril = True

        # GIRANDO: maniobra calibrable o hasta reencontrar el carril
        transcurrido = ahora - self.inicio_giro
        reenganche = (c["terminar_giro_al_encontrar_carril"]
                      and transcurrido >= c["tiempo_minimo_giro_s"]
                      and p.carril.detectado
                      and abs(p.carril.error) <= c["error_maximo_reenganche"])
        if ahora >= self.fin_giro or reenganche:
            motivo = "carril encontrado" if reenganche else "tiempo de giro cumplido"
            self.direccion_giro = None
            self.ignorar_giros_hasta = ahora + c["tiempo_ignorar_giros_s"]
            self.confirmador_senales.limpiar()
            self.pd.reiniciar()
            comando, texto = self._comando_carril(p, ahora)
            return Decision(comando, self._estado_libre(ahora),
                            f"Giro a la {lado} terminado ({motivo}); {texto}", 3, "avanzar")

        vel_izq, vel_der = c["giro_izquierda"] if lado == "izquierda" else c["giro_derecha"]
        return Decision(Comando.mover(vel_izq, vel_der), GIRANDO,
                        f"Girando a la {lado} ({transcurrido:.1f}/{c['duracion_giro_s']:.1f} s)", 3, accion_giro)

    # ------------------------------------------------------------------
    # REGLA 4: OBSTÁCULO MUY CERCA o TODO BLOQUEADO -> RETROCEDER y girar
    # ------------------------------------------------------------------
    def _regla_4_muy_cerca(self, p, ahora, puntajes):
        c = self.cfg
        # a) Maniobra en curso
        if ahora < self.fin_retroceso:
            return Decision(Comando.simple("RETROCEDER", c["velocidad_retroceso"]), RETROCEDIENDO,
                            f"Retroceso por obstaculo ({self.fin_retroceso - ahora:.1f} s)", 4, "detener")
        if ahora < self.fin_giro_esquive:
            if self.lado_esquive is None:
                self.lado_esquive = self._lado_mas_libre(puntajes)
            return self._mover_esquive(self.lado_esquive, RETROCEDIENDO,
                                       "Tras el retroceso: girando hacia la zona mas libre", 4)
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
                        f"{motivo} -> retroceso", 4, "detener")

    # ------------------------------------------------------------------
    # REGLA 5: OBSTÁCULO EN UNA ZONA -> GIRAR HACIA LA MÁS LIBRE
    # ------------------------------------------------------------------
    def _regla_5_esquivar(self, ahora, puntajes, causas):
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
                                   f"Obstaculo en {zona} ({causas[zona]}) -> esquivar a la {lado}", 5)

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
    # REGLA 6: SEGUIR EL CARRIL
    # ------------------------------------------------------------------
    def _regla_6_carril(self, p, ahora):
        if self.esperando_avanzar:
            return Decision(Comando.detener(), DETENIDO_SEGURIDAD,
                            "Esperando la senal AVANZAR para arrancar", 6, "detener")
        comando, texto = self._comando_carril(p, ahora)
        if comando.nombre == "DETENER":
            return Decision(comando, DETENIDO_SEGURIDAD, texto, 6, "detener")
        return Decision(comando, self._estado_libre(ahora), texto, 6, self._accion_de_ruedas(comando))

    def _comando_carril(self, p, ahora):
        """Control PD si hay carril; si se perdió, sigue igual, se detiene o lo busca."""
        c = self.cfg
        carril = p.carril
        if carril.detectado:
            self.cuadros_sin_carril = 0
            self.inicio_busqueda = None
            self.ultimo_error = carril.error
            vel_izq, vel_der = self.pd.calcular(carril.error, ahora)
            self.ultimo_mover = Comando.mover(vel_izq, vel_der)
            return self.ultimo_mover, f"Siguiendo carril (error {carril.error:+.2f})"

        self.cuadros_sin_carril += 1
        self.pd.reiniciar()
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
