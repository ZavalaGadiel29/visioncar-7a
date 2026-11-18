"""
video.py
--------
Lectura de video en un hilo separado.

¿Por qué un hilo? cv2.VideoCapture.read() se bloquea hasta que llega un
cuadro. Si leyéramos en el mismo ciclo que YOLO, los cuadros se irían
acumulando en un búfer y procesaríamos imágenes viejas (retraso).
Con un hilo dedicado, el lector descarta lo viejo y siempre guarda SOLO
el cuadro más reciente, junto con la hora en que llegó. Así el agente
puede saber qué tan "antiguo" es lo que está viendo.

Fuentes posibles: stream del carrito (URL), webcam (índice) o un archivo
de video grabado (--video archivo.mp4). El archivo se reproduce a su
velocidad real y vuelve a empezar al terminar.
"""

import sys
import threading
import time

import cv2


class LectorVideo:
    def __init__(self, fuente, es_webcam, cfg_video, es_archivo=False):
        """
        fuente:     índice de la webcam (int), URL del stream o ruta de un video (str).
        es_webcam:  True si la fuente es una webcam USB.
        es_archivo: True si la fuente es un archivo de video grabado.
        cfg_video:  sección "video" del config.json.
        """
        self.fuente = fuente
        self.es_webcam = es_webcam
        self.es_archivo = es_archivo
        self._pausa_archivo = 0.0        # segundos entre cuadros del archivo
        self.cfg = cfg_video

        self._captura = None
        self._cuadro = None              # último cuadro recibido
        self._marca_tiempo = 0.0         # hora (time.time) del último cuadro
        self._contador = 0               # cuántos cuadros han llegado en total
        self._ultimo_entregado = -1      # contador del último cuadro que se entregó
        self._conectado = False

        # La "condición" combina un candado (lock) con la posibilidad de
        # esperar a que llegue un cuadro nuevo.
        self._condicion = threading.Condition()
        self._activo = False
        self._hilo = None

    # ------------------------------------------------------------------
    # Control del hilo
    # ------------------------------------------------------------------
    def iniciar(self):
        self._activo = True
        self._hilo = threading.Thread(target=self._bucle_lectura, daemon=True)
        self._hilo.start()

    def detener(self):
        self._activo = False
        if self._hilo is not None:
            self._hilo.join(timeout=2.0)
        if self._captura is not None:
            self._captura.release()

    @property
    def conectado(self):
        return self._conectado

    # ------------------------------------------------------------------
    # Apertura de la fuente
    # ------------------------------------------------------------------
    def _abrir(self):
        if self.es_archivo:
            captura = cv2.VideoCapture(self.fuente)
            fps = captura.get(cv2.CAP_PROP_FPS) or 0
            self._pausa_archivo = 1.0 / fps if 1 <= fps <= 120 else 1.0 / 30
            return captura
        if self.es_webcam:
            # En Windows, DirectShow abre la webcam mucho más rápido.
            if sys.platform.startswith("win"):
                captura = cv2.VideoCapture(self.fuente, cv2.CAP_DSHOW)
            else:
                captura = cv2.VideoCapture(self.fuente)
            captura.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg["ancho"])
            captura.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg["alto"])
        else:
            # Stream MJPEG por HTTP: se usa el backend FFMPEG con tiempos
            # máximos de espera para no quedarnos colgados si el auto se apaga.
            parametros = []
            timeout = int(self.cfg["timeout_conexion_ms"])
            if hasattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC"):
                parametros += [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout]
            if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
                parametros += [cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout]
            try:
                captura = cv2.VideoCapture(self.fuente, cv2.CAP_FFMPEG, parametros)
            except TypeError:
                # Versiones viejas de OpenCV no aceptan la lista de parámetros
                captura = cv2.VideoCapture(self.fuente, cv2.CAP_FFMPEG)

        # Pedimos un búfer de 1 cuadro (no todos los backends lo respetan).
        captura.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return captura

    # ------------------------------------------------------------------
    # Bucle del hilo: lee cuadros sin parar
    # ------------------------------------------------------------------
    def _bucle_lectura(self):
        while self._activo:
            # 1) Si no hay conexión, intentar abrir la fuente
            if self._captura is None or not self._captura.isOpened():
                self._conectado = False
                print(f"[VIDEO] Conectando a {self.fuente} ...")
                self._captura = self._abrir()
                if not self._captura.isOpened():
                    print("[VIDEO] No se pudo abrir. Reintentando...")
                    self._captura.release()
                    self._captura = None
                    time.sleep(self.cfg["espera_reconexion_s"])
                    continue
                print("[VIDEO] Conectado.")

            # 2) Leer un cuadro
            inicio_lectura = time.monotonic()
            exito, cuadro = self._captura.read()
            if self.es_archivo and (not exito or cuadro is None):
                # Fin del archivo: volver al inicio
                self._captura.set(cv2.CAP_PROP_POS_FRAMES, 0)
                exito, cuadro = self._captura.read()
            if not exito or cuadro is None:
                print("[VIDEO] Se perdió el video. Reconectando...")
                self._conectado = False
                self._captura.release()
                self._captura = None
                time.sleep(self.cfg["espera_reconexion_s"])
                continue

            # 3) Asegurar el tamaño de trabajo (la webcam puede ignorar la petición)
            ancho, alto = self.cfg["ancho"], self.cfg["alto"]
            if cuadro.shape[1] != ancho or cuadro.shape[0] != alto:
                cuadro = cv2.resize(cuadro, (ancho, alto))

            # 4) Guardar como "el más reciente" y avisar a quien espera
            with self._condicion:
                self._cuadro = cuadro
                self._marca_tiempo = time.time()
                self._contador += 1
                self._conectado = True
                self._condicion.notify_all()

            # 5) Con un archivo, esperar para ir a su velocidad real
            if self.es_archivo:
                resto = self._pausa_archivo - (time.monotonic() - inicio_lectura)
                if resto > 0:
                    time.sleep(resto)

    # ------------------------------------------------------------------
    # Lo que usa el programa principal
    # ------------------------------------------------------------------
    def obtener_cuadro(self, tiempo_espera=0.2):
        """
        Devuelve (cuadro, antiguedad_s, es_nuevo).

        - Espera hasta `tiempo_espera` segundos a que llegue un cuadro nuevo.
          Si no llega, devuelve el último que se tenga (es_nuevo=False).
        - antiguedad_s: segundos desde que llegó ese cuadro.
        - Si nunca ha llegado un cuadro, devuelve (None, infinito, False).
        """
        with self._condicion:
            if self._contador == self._ultimo_entregado:
                self._condicion.wait(timeout=tiempo_espera)

            if self._cuadro is None:
                return None, float("inf"), False

            es_nuevo = self._contador != self._ultimo_entregado
            self._ultimo_entregado = self._contador
            antiguedad = time.time() - self._marca_tiempo
            return self._cuadro.copy(), antiguedad, es_nuevo
