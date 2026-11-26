"""
CAPTURAR DATASET  (fotos de las señales desde la cámara del carrito)
=====================================================================
Guarda fotos en:  dataset/<clase>/<clase>_0001.jpg

Uso:
    python capturar_dataset.py --ip 172.20.10.3

Teclas (con la ventana del video seleccionada):
    1 = alto      2 = avanzar     3 = izquierda     4 = derecha
    5 = peaton  (figuras de peatones)
    0 = fondo  (pista o salón SIN ninguna señal)
    ESPACIO = tomar UNA foto
    R       = ráfaga ON/OFF (una foto cada 0.4 s mientras mueven la señal)
    W / S   = mover el carrito adelante / atrás un poquito (para cambiar distancia)
    Q o ESC = salir

Meta: 200 fotos por señal y 150 de fondo.
"""
import argparse
import os
import socket
import threading
import time

import cv2
import numpy as np

CLASES = {ord("1"): "alto", ord("2"): "avanzar", ord("3"): "izquierda",
          ord("4"): "derecha", ord("5"): "peaton", ord("0"): "fondo"}
META = {"alto": 200, "avanzar": 200, "izquierda": 200, "derecha": 200, "fondo": 150,
        "peaton": 250}
INTERVALO_RAFAGA = 0.4
PUERTO = 4210


class LectorVideo:
    """Lee el stream en otro hilo para tener siempre el cuadro más nuevo."""
    def __init__(self, url):
        self.url = url
        self.cap = cv2.VideoCapture(url)
        self.cuadro = None
        self.lock = threading.Lock()
        self.activo = True
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self):
        while self.activo:
            ok, f = self.cap.read()
            if not ok:
                time.sleep(0.5)
                self.cap.release()
                self.cap = cv2.VideoCapture(self.url)
                continue
            with self.lock:
                self.cuadro = f

    def leer(self):
        with self.lock:
            return None if self.cuadro is None else self.cuadro.copy()

    def cerrar(self):
        self.activo = False
        self.cap.release()


def contar(carpeta, clase):
    d = os.path.join(carpeta, clase)
    return len([f for f in os.listdir(d) if f.lower().endswith(".jpg")]) if os.path.isdir(d) else 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ip", required=True, help="IP del carrito")
    p.add_argument("--clave", default="visioncar7a")
    p.add_argument("--carpeta", default="dataset")
    a = p.parse_args()

    for c in META:
        os.makedirs(os.path.join(a.carpeta, c), exist_ok=True)
    conteo = {c: contar(a.carpeta, c) for c in META}

    video = LectorVideo(f"http://{a.ip}:81/stream")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq = [int(time.time()) % 100000 + 10]

    def comando(cmd, vel=150, duracion=0.25):
        """Mueve el carrito un momento y lo detiene (para cambiar la distancia)."""
        fin = time.time() + duracion
        while time.time() < fin:
            seq[0] += 1
            sock.sendto(f"{a.clave};{seq[0]};{cmd};{vel}".encode(), (a.ip, PUERTO))
            time.sleep(0.08)
        for _ in range(3):
            seq[0] += 1
            sock.sendto(f"{a.clave};{seq[0]};DETENER;0".encode(), (a.ip, PUERTO))
            time.sleep(0.03)

    clase = "alto"
    rafaga = False
    t_ultima = 0.0
    flash = 0.0

    def guardar(frame):
        conteo[clase] += 1
        nombre = f"{clase}_{conteo[clase]:04d}.jpg"
        cv2.imwrite(os.path.join(a.carpeta, clase, nombre), frame)

    print("Ventana abierta. 1-4 / 0 = clase, 5 = peaton, ESPACIO = foto, R = ráfaga, W/S = mover, Q = salir")
    while True:
        frame = video.leer()
        if frame is None:
            vista = np.zeros((480, 640, 3), np.uint8)
            cv2.putText(vista, "Esperando video...", (180, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        else:
            if rafaga and time.time() - t_ultima >= INTERVALO_RAFAGA:
                guardar(frame)
                t_ultima = time.time()
                flash = time.time()
            vista = cv2.resize(frame, (640, 480))

        # Panel
        cv2.rectangle(vista, (0, 0), (640, 34), (20, 20, 20), -1)
        txt = f"Clase: {clase.upper()}   {conteo[clase]}/{META[clase]}"
        if rafaga:
            txt += "   [RAFAGA]"
        cv2.putText(vista, txt, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 255, 255) if rafaga else (0, 255, 0), 2)
        resumen = "  ".join(f"{c[:3]}:{conteo[c]}" for c in META)
        cv2.rectangle(vista, (0, 450), (640, 480), (20, 20, 20), -1)
        cv2.putText(vista, resumen, (10, 471), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        if time.time() - flash < 0.12:
            cv2.rectangle(vista, (0, 0), (639, 479), (255, 255, 255), 8)
        cv2.imshow("Capturar dataset", vista)

        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            break
        if k in CLASES:
            clase = CLASES[k]
            rafaga = False
            print(f"Clase: {clase} ({conteo[clase]}/{META[clase]})")
        elif k == ord(" ") and frame is not None:
            guardar(frame)
            flash = time.time()
        elif k == ord("r"):
            rafaga = not rafaga
            print("Ráfaga", "ON" if rafaga else "OFF")
        elif k == ord("w"):
            comando("AVANZAR")
        elif k == ord("s"):
            comando("RETROCEDER")

    for _ in range(3):
        seq[0] += 1
        sock.sendto(f"{a.clave};{seq[0]};DETENER;0".encode(), (a.ip, PUERTO))
    video.cerrar()
    cv2.destroyAllWindows()
    print("\nFotos guardadas:")
    for c in META:
        print(f"  {c:10s} {conteo[c]:4d} / {META[c]}")


if __name__ == "__main__":
    main()
