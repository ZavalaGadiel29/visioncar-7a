"""
PROBAR MODELO  (ver en vivo lo que detecta el modelo de señales)
================================================================
NO mueve el carrito. Solo muestra el video con los recuadros.

Uso:
    python probar_modelo.py --ip 172.20.10.3
    python probar_modelo.py --ip 192.168.1.100 --conf 0.6

Teclas:  Q o ESC = salir     F = guardar captura
"""
import argparse
import os
import threading
import time

import cv2
from ultralytics import YOLO

COLORES = {"alto": (40, 40, 230), "avanzar": (60, 180, 60),
           "izquierda": (0, 200, 255), "derecha": (230, 140, 30)}


class LectorVideo:
    def __init__(self, url):
        self.url = url
        self.cap = cv2.VideoCapture(url)
        self.cuadro = None
        self.lock = threading.Lock()
        threading.Thread(target=self._leer, daemon=True).start()

    def _leer(self):
        while True:
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ip", required=True)
    p.add_argument("--modelo", default=os.path.join("modelo", "best.pt"))
    p.add_argument("--conf", type=float, default=0.5, help="confianza mínima (0-1)")
    a = p.parse_args()

    if not os.path.exists(a.modelo):
        print(f"No encuentro {a.modelo}. Guarden best.pt en la carpeta 'modelo'.")
        return

    modelo = YOLO(a.modelo)
    print("Clases del modelo:", modelo.names)
    video = LectorVideo(f"http://{a.ip}:81/stream")
    fps, n, t0 = 0.0, 0, time.time()

    while True:
        frame = video.leer()
        if frame is None:
            time.sleep(0.05)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                break
            continue

        t = time.time()
        res = modelo.predict(frame, imgsz=320, conf=a.conf, verbose=False)[0]
        ms = (time.time() - t) * 1000

        for caja in res.boxes:
            x1, y1, x2, y2 = map(int, caja.xyxy[0])
            nombre = modelo.names[int(caja.cls)]
            conf = float(caja.conf)
            color = COLORES.get(nombre, (255, 255, 255))
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(frame, f"{nombre} {conf:.0%}", (x1, max(12, y1 - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 2)

        n += 1
        if time.time() - t0 >= 1:
            fps, n, t0 = n / (time.time() - t0), 0, time.time()

        vista = cv2.resize(frame, (640, 480))
        cv2.putText(vista, f"FPS {fps:.1f}   modelo {ms:.0f} ms   conf>={a.conf:.0%}",
                    (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.imshow("Probar modelo", vista)

        k = cv2.waitKey(1) & 0xFF
        if k in (ord("q"), 27):
            break
        if k == ord("f"):
            nombre = f"captura_{int(time.time())}.jpg"
            cv2.imwrite(nombre, vista)
            print("Guardada", nombre)

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
