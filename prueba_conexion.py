"""
PRUEBA DE CONEXIÓN  (paso 1 del proyecto)
==========================================
Sirve SOLO para comprobar que el hardware funciona:
  - Que llega el video de la ESP32-CAM
  - Que el auto obedece los comandos UDP
  - Que el fail-safe funciona
  - Medir FPS del video y latencia de los comandos

¡OJO! Este script usa teclado. En la demostración final el auto debe
ser autónomo; el control manual es únicamente para pruebas.

Uso:
    python prueba_conexion.py --ip 192.168.43.50
    python prueba_conexion.py --webcam      (sin auto, solo probar video con la Redragon)

Teclas:
    W = AVANZAR     S = RETROCEDER
    A = GIRAR_IZQ   D = GIRAR_DER
    ESPACIO = DETENER
    F = probar FAIL-SAFE (deja de enviar comandos)
    Q o ESC = salir
"""

import argparse
import socket
import threading
import time

import cv2
import numpy as np

UDP_PORT = 4210
INTERVALO_ENVIO = 0.1   # se reenvía el comando cada 100 ms (< 500 ms del fail-safe)


# ---------------------------------------------------------------
#  Lector de video en hilo separado: siempre da el cuadro más nuevo
# ---------------------------------------------------------------
class LectorVideo:
    def __init__(self, fuente):
        self.fuente = fuente
        self.cap = cv2.VideoCapture(fuente)
        self.cuadro = None
        self.t_ultimo = 0.0
        self.activo = True
        self.lock = threading.Lock()
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self):
        while self.activo:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.5)                         # reintentar conexión
                self.cap.release()
                self.cap = cv2.VideoCapture(self.fuente)
                continue
            with self.lock:
                self.cuadro = frame
                self.t_ultimo = time.time()

    def leer(self):
        with self.lock:
            edad = time.time() - self.t_ultimo
            return (None if self.cuadro is None else self.cuadro.copy()), edad

    def cerrar(self):
        self.activo = False
        self.cap.release()


# ---------------------------------------------------------------
#  Cliente UDP: envía comandos y mide latencia con el ACK
# ---------------------------------------------------------------
class ClienteAuto:
    def __init__(self, ip, clave):
        self.ip = ip
        self.clave = clave
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.05)
        self.seq = 0
        self.enviados = {}
        self.latencia_ms = None
        self.acks = 0
        threading.Thread(target=self._escuchar, daemon=True).start()

    def enviar(self, comando, velocidad=0):
        self.seq += 1
        self.enviados[self.seq] = time.time()
        # Formato del firmware v2: CLAVE;SEQ;COMANDO;VELOCIDAD
        msg = f"{self.clave};{self.seq};{comando};{velocidad}"
        self.sock.sendto(msg.encode(), (self.ip, UDP_PORT))

    def _escuchar(self):
        while True:
            try:
                data, _ = self.sock.recvfrom(64)
            except (socket.timeout, OSError):
                continue
            partes = data.decode(errors="ignore").split(";")
            if len(partes) == 2 and partes[0] == "ACK":
                t = self.enviados.pop(int(partes[1]), None)
                if t:
                    self.latencia_ms = (time.time() - t) * 1000
                    self.acks += 1


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ip", help="IP del auto (aparece en el Monitor Serie)")
    p.add_argument("--webcam", action="store_true", help="usar la webcam de la laptop")
    p.add_argument("--clave", default=None, help="clave compartida con el firmware v2 (por defecto, CLAVE_UDP del .env)")
    args = p.parse_args()
    if args.clave is None:
        from configuracion import obtener_clave_udp
        args.clave = obtener_clave_udp()
    if args.ip and not args.clave:
        p.error("Falta CLAVE_UDP en el archivo .env (o pásala con --clave)")

    if args.webcam:
        fuente = 0
        auto = None
    elif args.ip:
        fuente = f"http://{args.ip}:81/stream"
        auto = ClienteAuto(args.ip, args.clave)
    else:
        p.error("Indica --ip <IP_DEL_AUTO> o --webcam")

    video = LectorVideo(fuente)
    if args.webcam:
        video.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
        video.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)

    teclas = {ord("w"): "AVANZAR", ord("s"): "RETROCEDER",
              ord("a"): "GIRAR_IZQ", ord("d"): "GIRAR_DER", ord(" "): "DETENER"}
    comando = "DETENER"
    probando_failsafe = False
    t_envio = 0.0
    fps, n_cuadros, t_fps = 0.0, 0, time.time()

    print("Ventana abierta. Usa W/A/S/D, ESPACIO, F (fail-safe), Q (salir).")

    while True:
        frame, edad = video.leer()
        if frame is None:
            frame = np.zeros((240, 320, 3), dtype=np.uint8)
            cv2.putText(frame, "Esperando video...", (40, 120),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        else:
            n_cuadros += 1

        # FPS cada segundo
        if time.time() - t_fps >= 1.0:
            fps = n_cuadros / (time.time() - t_fps)
            n_cuadros, t_fps = 0, time.time()

        # Enviar comando periódicamente (mantiene vivo el fail-safe)
        if auto and not probando_failsafe and time.time() - t_envio >= INTERVALO_ENVIO:
            auto.enviar(comando)
            t_envio = time.time()

        # Panel de información
        vista = cv2.resize(frame, (640, 480))
        lat = f"{auto.latencia_ms:.0f} ms" if auto and auto.latencia_ms else "--"
        estado = "FAIL-SAFE (sin envio)" if probando_failsafe else comando
        alerta = edad > 1.0
        lineas = [f"FPS video: {fps:.1f}", f"Latencia: {lat}",
                  f"Comando: {estado}",
                  "VIDEO PERDIDO" if alerta else ""]
        for i, txt in enumerate(lineas):
            color = (0, 0, 255) if (alerta and i == 3) else (0, 255, 0)
            cv2.putText(vista, txt, (10, 25 + i * 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
        cv2.imshow("Prueba de conexion", vista)

        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            break
        if k in teclas:
            comando = teclas[k]
            probando_failsafe = False
        elif k == ord("f"):
            probando_failsafe = not probando_failsafe
            print("Fail-safe:", "dejando de enviar (el auto debe frenar en 0.5 s)"
                  if probando_failsafe else "reanudado")

    if auto:
        for _ in range(3):
            auto.enviar("DETENER")
            time.sleep(0.05)
    video.cerrar()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
