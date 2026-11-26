"""
comunicacion.py
---------------
Etapa ACTUAR: envía el comando decidido al carrito por UDP (firmware v2).

Protocolo v2:
    Laptop -> carrito :  "CLAVE;SEQ;COMANDO;P1;P2"
        AVANZAR;vel   RETROCEDER;vel   GIRAR_IZQ;vel   GIRAR_DER;vel   (vel 0-255, 0 = por defecto)
        DETENER
        MOVER;vel_izq;vel_der     (cada rueda de -255 a 255, para giros suaves)
        ej.  "<CLAVE_UDP>;57;MOVER;180;120"
    Carrito -> laptop :  "ACK;SEQ"   (si la CLAVE está mal, NO responde)

Ideas clave:
- El comando se reenvía cada ~100 ms AUNQUE NO CAMBIE: el carrito se
  detiene solo si pasan 500 ms sin recibir nada (fail-safe).
- Vigilante (watchdog): si el ciclo de visión deja de dar comandos nuevos
  (programa trabado), este hilo manda DETENER en lugar de repetir el último
  comando para siempre.
- Pulso (modo "avance por pasos"): pulso(comando, duracion) manda el
  comando YA y, justo al cumplirse la duración, este hilo manda DETENER
  él mismo (sin esperar al ciclo de visión). Así el paso dura lo pedido
  aunque el ciclo de visión vaya lento.
- SEQ empieza en 1 y siempre aumenta. La hora de envío de cada SEQ se
  guarda y, cuando llega su ACK, la diferencia es la LATENCIA (ida y vuelta).
"""

import socket
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

COMANDOS_CON_VELOCIDAD = ("AVANZAR", "RETROCEDER", "GIRAR_IZQ", "GIRAR_DER")


def _limitar(valor, minimo, maximo):
    return int(max(minimo, min(maximo, round(valor))))


@dataclass(frozen=True)
class Comando:
    """Un comando del protocolo v2 con sus parámetros (P1, P2)."""
    nombre: str
    p1: Optional[int] = None
    p2: Optional[int] = None

    @staticmethod
    def detener():
        return Comando("DETENER")

    @staticmethod
    def simple(nombre, velocidad=0):
        """AVANZAR / RETROCEDER / GIRAR_IZQ / GIRAR_DER con velocidad 0-255."""
        return Comando(nombre, _limitar(velocidad, 0, 255))

    @staticmethod
    def mover(vel_izq, vel_der):
        """Velocidad de cada rueda (-255 a 255). Negativo = hacia atrás."""
        return Comando("MOVER", _limitar(vel_izq, -255, 255), _limitar(vel_der, -255, 255))

    def parametros(self, rellenar=False):
        """Lista de parámetros para el mensaje. rellenar=True siempre manda P1 y P2."""
        valores = [v for v in (self.p1, self.p2) if v is not None]
        if rellenar:
            valores = (valores + [0, 0])[:2]
        return valores

    def __str__(self):
        return ";".join([self.nombre] + [str(v) for v in self.parametros()])


class ClienteUDP:
    def __init__(self, ip, puerto, clave, cfg_comunicacion, simulado=False):
        """
        simulado=True: no se abre ningún socket ni se envía nada; solo se
        lleva la cuenta para mostrarla en pantalla (útil con --sin-auto).
        """
        self.direccion = (ip, puerto)
        self.clave = clave
        self.cfg = cfg_comunicacion
        self.simulado = simulado

        self._candado = threading.Lock()
        self._comando = Comando.detener()      # comando actual a enviar
        self._hora_comando = time.monotonic()  # cuándo lo actualizó el ciclo principal
        self._seq = 0
        self._pendientes = {}                  # seq -> hora de envío (esperando ACK)
        self._despertar = threading.Event()   # para mandar ya (sin esperar los 100 ms)
        self._fin_pulso = None                 # monotonic en que termina el pulso en curso
        self.fin_ultimo_pulso = None           # monotonic en que se mandó el DETENER del último pulso
        self._latencias = deque(maxlen=int(cfg_comunicacion["muestras_promedio_latencia"]))

        self.pausado = False      # tecla P: deja de enviar (prueba del fail-safe del carrito)
        self.vigilante_activo = False   # True si el watchdog está forzando DETENER
        self.enviados = 0
        self.acks = 0
        self.perdidos = 0         # comandos sin ACK a tiempo (¿IP o CLAVE incorrecta?)
        self.ultimo_ack = None    # hora (monotonic) del último ACK recibido
        self.ultimo_texto = ""    # último mensaje enviado (para la pantalla)

        self._activo = False
        self._socket = None
        if not simulado:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.settimeout(0.1)   # para que el hilo receptor pueda terminar

    # ------------------------------------------------------------------
    def iniciar(self):
        self._activo = True
        threading.Thread(target=self._bucle_envio, daemon=True).start()
        if not self.simulado:
            threading.Thread(target=self._bucle_recepcion, daemon=True).start()

    def establecer_comando(self, comando, inmediato=False):
        """
        El programa principal llama esto en cada cuadro con el comando elegido.
        Cancela un pulso en curso. inmediato=True: se envía ya (no en el próximo tick).
        """
        with self._candado:
            self._comando = comando
            self._hora_comando = time.monotonic()
            self._fin_pulso = None
        if inmediato:
            self._despertar.set()

    def pulso(self, comando, duracion):
        """Manda `comando` ya y DETENER al cumplirse `duracion` segundos (lo hace este hilo)."""
        with self._candado:
            ahora = time.monotonic()
            self._comando = comando
            self._hora_comando = ahora
            self._fin_pulso = ahora + max(0.0, float(duracion))
        self._despertar.set()

    @property
    def comando_actual(self):
        """El comando que se está mandando ahora (para el registro)."""
        with self._candado:
            return self._comando

    @property
    def pulso_activo(self):
        with self._candado:
            return self._fin_pulso is not None

    @property
    def latencia_promedio_ms(self):
        with self._candado:
            if not self._latencias:
                return None
            return sum(self._latencias) / len(self._latencias)

    @property
    def responde(self):
        """True si llegó algún ACK en los últimos 2 segundos."""
        return self.ultimo_ack is not None and time.monotonic() - self.ultimo_ack < 2.0

    # ------------------------------------------------------------------
    def _armar_mensaje(self, seq, comando):
        rellenar = bool(self.cfg.get("rellenar_parametros", False))
        partes = [self.clave, str(seq), comando.nombre] + [str(v) for v in comando.parametros(rellenar)]
        return ";".join(partes)

    def _enviar(self, comando):
        with self._candado:
            self._seq += 1                     # el primero es 1
            seq = self._seq
            self._pendientes[seq] = time.monotonic()
        mensaje = self._armar_mensaje(seq, comando)
        self.ultimo_texto = mensaje
        self.enviados += 1
        if self.simulado:
            return
        try:
            self._socket.sendto(mensaje.encode("ascii"), self.direccion)
        except OSError as error:
            print(f"[UDP] Error al enviar: {error}")

    def _bucle_envio(self):
        maximo_sin_decision = float(self.cfg.get("max_sin_decision_s", 0.5))
        intervalo = float(self.cfg["intervalo_envio_s"])
        while self._activo:
            espera = intervalo
            if not self.pausado:
                with self._candado:
                    ahora = time.monotonic()
                    if self._fin_pulso is not None and ahora >= self._fin_pulso:
                        # Fin del pulso: DETENER exacto, sin esperar al ciclo de visión
                        self._comando = Comando.detener()
                        self._hora_comando = ahora
                        self._fin_pulso = None
                        self.fin_ultimo_pulso = ahora
                    if self._fin_pulso is not None:
                        espera = min(intervalo, max(0.0, self._fin_pulso - ahora))
                    comando = self._comando
                    edad = ahora - self._hora_comando
                # Vigilante: si el ciclo de visión se trabó, no repetir un
                # comando viejo; mejor detener el carrito.
                self.vigilante_activo = edad > maximo_sin_decision
                if self.vigilante_activo:
                    comando = Comando.detener()
                self._enviar(comando)
            self._limpiar_pendientes()
            # Espera el intervalo normal, o menos si hay que cortar un pulso o mandar ya
            self._despertar.wait(timeout=espera)
            self._despertar.clear()

    def _limpiar_pendientes(self):
        """Los SEQ que no recibieron ACK a tiempo se cuentan como perdidos."""
        limite = time.monotonic() - self.cfg["tiempo_max_espera_ack_s"]
        with self._candado:
            viejos = [s for s, t in self._pendientes.items() if t < limite]
            for s in viejos:
                del self._pendientes[s]
            if not self.simulado:
                self.perdidos += len(viejos)

    def _bucle_recepcion(self):
        while self._activo:
            try:
                datos, _ = self._socket.recvfrom(64)
            except socket.timeout:
                continue
            except OSError:
                # En Windows, si el destino no existe, recvfrom puede lanzar
                # "conexión reiniciada". Se ignora y se sigue escuchando.
                time.sleep(0.05)
                continue

            partes = datos.decode("ascii", errors="ignore").strip().split(";")
            if len(partes) != 2 or partes[0] != "ACK" or not partes[1].isdigit():
                continue
            seq = int(partes[1])
            ahora = time.monotonic()
            with self._candado:
                hora_envio = self._pendientes.pop(seq, None)
                self.acks += 1
                self.ultimo_ack = ahora
                if hora_envio is not None:
                    self._latencias.append((ahora - hora_envio) * 1000.0)

    # ------------------------------------------------------------------
    def cerrar(self):
        """Al salir: enviar DETENER varias veces (por si alguno se pierde)."""
        self._activo = False
        time.sleep(self.cfg["intervalo_envio_s"])   # deja terminar el último envío
        for _ in range(int(self.cfg["repeticiones_detener_al_salir"])):
            self._enviar(Comando.detener())
            time.sleep(0.05)
        if self._socket is not None:
            self._socket.close()
