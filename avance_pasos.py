"""
avance_pasos.py
---------------
Modo "avance por pasos" (config.json -> avance -> modo_avance = "pasos").

Con video a 10-15 FPS y cuadros borrosos en movimiento, el carrito no puede
ir más lento de forma continua (velocidad_base 120 ya está casi en el mínimo
del motor, 110). En vez de eso avanza a saltos cortos y decide con un cuadro
tomado ya DETENIDO (nítido y reciente):

    PARADA: DETENER al menos t_parada_s (0.20 s)
            -> esperar un cuadro NUEVO tomado después de detenerse + t_asentar_s
               (0.08 s). Si en max_espera_cuadro_s (0.6 s) no llega, sigue
               DETENIDO (nunca avanza a ciegas).
            -> con ese cuadro se calcula el comando (misma lógica de siempre:
               LINEA, señales, peatón, obstáculos). Si es DETENER, se queda
               en PARADA y decide otra vez con el siguiente cuadro.
    PASO:   el comando MOVER durante t_paso_s (0.25 s), con base vel_paso (160).
            El DETENER al final lo manda el hilo UDP justo a tiempo (pulso).
            Durante el paso se siguen analizando los cuadros, pero SOLO para
            seguridad: si una regla pide detener (peatón, obstáculo, alto,
            emergencia...), el paso se corta en ese momento.

Los relojes de las maniobras (giro dirigido, "ignorar la misma señal", etc.)
no cuentan el tiempo de la PARADA forzada: ver AgenteDecision.descontar_pausa.
"""

import time

from comunicacion import Comando

FASE_PARADA = "PARADA"
FASE_PASO = "PASO"


class AvancePorPasos:
    def __init__(self, cfg_avance, cliente):
        self.cfg = cfg_avance
        self.cliente = cliente
        self.fase = FASE_PARADA
        self.parada_desde = time.monotonic()
        # True si la PARADA actual es la del ciclo (el agente quería seguir avanzando);
        # False si la provocó una regla (peatón, alto...): ese tiempo sí cuenta.
        self.parada_forzada = False
        self.decidio_en_parada = False     # ya se decidió al menos una vez en esta PARADA
        self.ultimo_cuadro_t = None        # marca de tiempo del último cuadro usado para decidir
        self.comando_paso = None           # comando del paso en curso
        self.inicio_paso = None            # monotonic en que empezó el paso en curso
        self._descontado_hasta = None      # hasta dónde ya se descontó la PARADA actual
        self.esperando_cuadro_s = 0.0      # cuánto lleva la PARADA esperando un cuadro válido

    # ------------------------------------------------------------------
    @property
    def t_parada(self):
        return float(self.cfg.get("t_parada_s", 0.20))

    @property
    def t_asentar(self):
        return float(self.cfg.get("t_asentar_s", 0.08))

    @property
    def t_paso(self):
        return float(self.cfg.get("t_paso_s", 0.25))

    @property
    def max_espera(self):
        return float(self.cfg.get("max_espera_cuadro_s", 0.6))

    # ------------------------------------------------------------------
    def actualizar(self, ahora):
        """Pasa de PASO a PARADA cuando el hilo UDP ya mandó el DETENER del pulso."""
        if self.fase == FASE_PASO and not self.cliente.pulso_activo:
            fin = self.cliente.fin_ultimo_pulso
            # Si el pulso lo canceló otro (establecer_comando), fin_ultimo_pulso es viejo
            if fin is None or self.inicio_paso is None or fin < self.inicio_paso:
                fin = ahora
            self._entrar_parada(fin, forzada=True)
        if self.fase == FASE_PARADA:
            self.esperando_cuadro_s = ahora - self.parada_desde

    def espera_maxima_cuadro(self, ahora, normal):
        """Cuánto puede bloquearse el ciclo esperando video: en PARADA, solo hasta t_parada."""
        if self.fase != FASE_PARADA:
            return normal
        resto = self.parada_desde + self.t_parada - ahora
        return normal if resto <= 0 else max(0.01, min(normal, resto))

    def cuadro_listo(self, t_cuadro, ahora):
        """
        En PARADA: True si ya pasó t_parada y el cuadro se tomó después de detenerse
        + t_asentar (y no es uno ya usado). En PASO siempre False (ahí solo seguridad).
        """
        if self.fase != FASE_PARADA or t_cuadro is None:
            return False
        if ahora < self.parada_desde + self.t_parada:
            return False
        if t_cuadro < self.parada_desde + self.t_asentar:
            return False
        return self.ultimo_cuadro_t is None or t_cuadro > self.ultimo_cuadro_t

    def sin_cuadro(self):
        """True si la PARADA ya esperó más de max_espera sin un cuadro válido."""
        return (self.fase == FASE_PARADA and not self.decidio_en_parada
                and self.esperando_cuadro_s > self.max_espera)

    def pausa_a_descontar(self, ahora):
        """
        Segundos de PARADA forzada que el agente debe descontar de sus relojes.
        Solo hasta la primera decisión de una PARADA del ciclo; cada segundo se
        descuenta una sola vez (aunque un cuadro vacío obligue a esperar otro).
        """
        if self.fase != FASE_PARADA or not self.parada_forzada or self.decidio_en_parada:
            return 0.0
        inicio = self.parada_desde if self._descontado_hasta is None else self._descontado_hasta
        self._descontado_hasta = ahora
        return max(0.0, ahora - inicio)

    def aplicar_decision(self, decision, t_cuadro, ahora):
        """
        En PARADA, con un cuadro listo: MOVER (u otro movimiento) -> PASO de t_paso.
        DETENER -> se queda en PARADA (la provocó una regla: ese tiempo sí cuenta).
        """
        self.ultimo_cuadro_t = t_cuadro
        self.decidio_en_parada = True
        comando = decision.comando
        if comando.nombre == "DETENER":
            self.parada_forzada = False
            self.cliente.establecer_comando(comando)
            return
        self.fase = FASE_PASO
        self.comando_paso = comando
        self.inicio_paso = ahora
        self.cliente.pulso(comando, self.t_paso)

    def revisar_seguridad(self, decision, ahora):
        """En PASO: si la decisión de este cuadro es DETENER, el paso se corta ya."""
        if self.fase == FASE_PASO and decision.comando.nombre == "DETENER":
            self.detener_ya(ahora)
            return True
        return False

    def detener_ya(self, ahora):
        """Corta el paso en curso (emergencia o regla de seguridad)."""
        self.cliente.establecer_comando(Comando.detener(), inmediato=True)
        if self.fase == FASE_PASO:
            self._entrar_parada(ahora, forzada=False)
        else:
            self.parada_forzada = False

    def mantener_parada(self):
        """En PARADA se sigue mandando DETENER cada ciclo (así el vigilante no salta)."""
        if self.fase == FASE_PARADA:
            self.cliente.establecer_comando(Comando.detener())

    def _entrar_parada(self, desde, forzada):
        self.fase = FASE_PARADA
        self.parada_desde = desde
        self.parada_forzada = forzada
        self.decidio_en_parada = False
        self.comando_paso = None
        self._descontado_hasta = None
        self.esperando_cuadro_s = 0.0

    def texto(self):
        """Texto corto para la pantalla: 'PASOS PARADA' / 'PASOS PASO MOVER;170;150'."""
        if self.fase == FASE_PASO:
            return f"PASOS  PASO  {self.comando_paso}"
        if self.sin_cuadro():
            return f"PASOS  PARADA  sin cuadro nuevo {self.esperando_cuadro_s:.1f} s"
        return "PASOS  PARADA"
