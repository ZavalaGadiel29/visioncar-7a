"""
MEDIR CARRITO  (velocidad, frenado y que avance derecho)
=========================================================
Hace que el carrito avance un tiempo EXACTO y luego frene, para medir
con cinta métrica. No usa video, solo comandos UDP (firmware v2).

Uso:
    python medir_carrito.py --ip 172.20.10.5 --tiempo 2
    python medir_carrito.py --ip 172.20.10.5 --tiempo 3 --vel 200
    python medir_carrito.py --ip 172.20.10.5 --tiempo 3 --izq 200 --der 190   (probar corrección)

--vel     velocidad para AVANZAR (0-255), igual a VEL_DEFECTO del firmware: 200
--izq/--der  si los pones, usa MOVER con una velocidad distinta por rueda
"""
import argparse
import socket
import time

PUERTO = 4210


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ip", required=True)
    p.add_argument("--clave", default="visioncar7a")
    p.add_argument("--tiempo", type=float, default=2.0, help="segundos avanzando")
    p.add_argument("--vel", type=int, default=200)
    p.add_argument("--izq", type=int, default=None)
    p.add_argument("--der", type=int, default=None)
    a = p.parse_args()

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    seq = [int(time.time()) % 100000 + 10]   # siempre mayor que el anterior

    def enviar(cmd, p1=0, p2=0):
        seq[0] += 1
        s.sendto(f"{a.clave};{seq[0]};{cmd};{p1};{p2}".encode(), (a.ip, PUERTO))

    if a.izq is not None and a.der is not None:
        cmd, p1, p2 = "MOVER", a.izq, a.der
        desc = f"MOVER izq={a.izq} der={a.der}"
    else:
        cmd, p1, p2 = "AVANZAR", a.vel, 0
        desc = f"AVANZAR vel={a.vel}"

    print(f"\nPrueba: {desc} durante {a.tiempo} s")
    print("Coloquen la parte de ENFRENTE del carrito en la marca 0 de la cinta.")
    for n in (3, 2, 1):
        print(f"  {n}...")
        time.sleep(1)
    print("  ¡YA!\a")

    t0 = time.time()
    while time.time() - t0 < a.tiempo:
        enviar(cmd, p1, p2)
        time.sleep(0.1)          # < 500 ms del fail-safe

    for _ in range(5):
        enviar("DETENER")
        time.sleep(0.03)
    print("  DETENER enviado\a")
    print("\nMidan desde la marca 0 hasta la parte de enfrente del carrito (cm)")
    print("y cuánto se desvió hacia un lado (cm). Anótenlo en la tabla.\n")


if __name__ == "__main__":
    main()
