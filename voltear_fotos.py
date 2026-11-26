"""
VOLTEAR FOTOS  (corrige fotos tomadas con la cámara en espejo)
===============================================================
Voltea horizontalmente TODAS las fotos .jpg de dataset/<clase>/.
Solo se hace UNA vez: deja un archivo "YA_VOLTEADAS.txt" en cada carpeta
para no voltearlas dos veces.

Uso:
    python voltear_fotos.py
    python voltear_fotos.py --carpeta dataset
"""
import argparse
import os

import cv2

MARCA = "YA_VOLTEADAS.txt"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--carpeta", default="dataset")
    a = p.parse_args()

    if not os.path.isdir(a.carpeta):
        print(f"No encuentro la carpeta '{a.carpeta}'. Corran esto dentro de CARRITO.")
        return

    total = 0
    for clase in sorted(os.listdir(a.carpeta)):
        d = os.path.join(a.carpeta, clase)
        if not os.path.isdir(d):
            continue
        if os.path.exists(os.path.join(d, MARCA)):
            print(f"  {clase:10s} ya estaba volteada, no se toca")
            continue
        fotos = [f for f in os.listdir(d) if f.lower().endswith(".jpg")]
        for f in fotos:
            ruta = os.path.join(d, f)
            img = cv2.imread(ruta)
            if img is None:
                continue
            cv2.imwrite(ruta, cv2.flip(img, 1))
        with open(os.path.join(d, MARCA), "w", encoding="utf-8") as m:
            m.write(f"{len(fotos)} fotos volteadas. Fotos nuevas tomadas despues del firmware corregido NO necesitan voltearse.\n")
        print(f"  {clase:10s} {len(fotos)} fotos volteadas")
        total += len(fotos)

    print(f"\nListo: {total} fotos corregidas.")
    print("IMPORTANTE: antes de tomar más fotos, suban el firmware con ESPEJO_HORIZONTAL = true.")


if __name__ == "__main__":
    main()
