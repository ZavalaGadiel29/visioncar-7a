"""
MEDIR VIDEO  (cuántos cuadros NUEVOS por segundo manda la cámara)
==================================================================
No abre ventana ni usa IA. Mide durante 10 segundos.

Uso:
    python medir_video.py --ip 172.20.10.3
"""
import argparse
import time

import cv2


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ip", required=True)
    p.add_argument("--segundos", type=float, default=10)
    a = p.parse_args()

    cap = cv2.VideoCapture(f"http://{a.ip}:81/stream")
    if not cap.isOpened():
        print("No se pudo abrir el video. Revisen la IP y la red.")
        return

    print(f"Midiendo {a.segundos:.0f} s ...")
    n, huecos, t_ant = 0, [], None
    t0 = time.time()
    while time.time() - t0 < a.segundos:
        ok, _ = cap.read()          # bloquea hasta que llega un cuadro nuevo
        if not ok:
            continue
        ahora = time.time()
        if t_ant is not None:
            huecos.append((ahora - t_ant) * 1000)
        t_ant = ahora
        n += 1
        if n % 25 == 0:
            print(f"  {n} cuadros...")
    cap.release()

    dur = time.time() - t0
    fps = n / dur
    peor = max(huecos) if huecos else 0
    trabones = sum(1 for h in huecos if h > 150)
    print("\n===== RESULTADO =====")
    print(f"FPS reales de la cámara: {fps:.1f}")
    print(f"Peor espera entre cuadros: {peor:.0f} ms")
    print(f"Trabones (> 150 ms sin cuadro): {trabones}")
    if fps >= 20 and trabones <= 3:
        print("=> El video va FLUIDO. Si se ve trabado, es la laptop/IA.")
    elif fps >= 12:
        print("=> Aceptable. Acerquen el iPhone y revisen la antena para mejorar.")
    else:
        print("=> LENTO: problema de Wi-Fi o de la cámara.")


if __name__ == "__main__":
    main()
