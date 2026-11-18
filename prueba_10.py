"""
prueba_10.py
------------
Registra N pruebas (10 por defecto) por cada señal y calcula el % de éxito.

Cómo se usa (con main.py corriendo en otra terminal y el carrito en la pista):
  1. Pon la señal indicada en la pista y deja que el carrito llegue a ella.
  2. Observa si hizo lo correcto (ver CRITERIOS) y responde en esta ventana:
       s = éxito      n = fallo      r = repetir (no cuenta)      q = terminar
  3. Opcional: escribe una nota (ej. "giró tarde", "no vio la señal").

Los resultados se guardan en pruebas/pruebas_AAAAMMDD_HHMMSS.csv (una
fila por prueba) y al final se imprime la tabla de porcentajes para el
reporte. Si se interrumpe, se puede continuar con --continuar archivo.csv.

Ejemplos:
    python prueba_10.py
    python prueba_10.py --n 5 --senales alto,izquierda
    python prueba_10.py --incluir-seguridad          # agrega peatón y obstáculo
    python prueba_10.py --resumen pruebas/pruebas_X.csv
    python prueba_10.py --continuar pruebas/pruebas_X.csv
"""

import argparse
import csv
import os
from collections import defaultdict
from datetime import datetime

CRITERIOS = {
    "alto": "Se detiene ante la señal ~3 s y luego continúa sin volver a frenar por la misma señal.",
    "avanzar": "Estando detenido (esperando), arranca al ver la señal AVANZAR.",
    "izquierda": "Avanza hasta la intersección, gira a la izquierda y retoma el carril.",
    "derecha": "Avanza hasta la intersección, gira a la derecha y retoma el carril.",
    "peaton": "Se detiene antes de tocar a la persona/figura y reanuda cuando se retira.",
    "obstaculo": "No choca: retrocede o esquiva hacia el lado libre.",
}
SENALES_BASE = ["alto", "avanzar", "izquierda", "derecha"]
COLUMNAS = ["fecha", "senal", "intento", "resultado", "nota"]


def leer_csv(ruta):
    with open(ruta, "r", encoding="utf-8-sig", newline="") as archivo:
        return list(csv.DictReader(archivo))


def imprimir_resumen(filas):
    exitos, totales = defaultdict(int), defaultdict(int)
    orden = []
    for f in filas:
        senal = f["senal"]
        if senal not in orden:
            orden.append(senal)
        totales[senal] += 1
        exitos[senal] += f["resultado"] == "exito"

    print("\n" + "=" * 52)
    print(f" {'Señal':<12}{'Éxitos':>8}{'Pruebas':>9}{'% éxito':>11}")
    print("-" * 52)
    for senal in orden:
        porcentaje = 100.0 * exitos[senal] / totales[senal]
        print(f" {senal:<12}{exitos[senal]:>8}{totales[senal]:>9}{porcentaje:>10.1f}%")
    total_e, total_t = sum(exitos.values()), sum(totales.values())
    if total_t:
        print("-" * 52)
        print(f" {'TOTAL':<12}{total_e:>8}{total_t:>9}{100.0 * total_e / total_t:>10.1f}%")
    print("=" * 52)


def preguntar(texto, opciones):
    while True:
        respuesta = input(texto).strip().lower()
        if respuesta in opciones:
            return respuesta
        print(f"   Responde una de: {', '.join(opciones)}")


def main():
    parser = argparse.ArgumentParser(description="Registro de pruebas por señal y % de éxito")
    parser.add_argument("--n", type=int, default=10, help="pruebas por señal (10)")
    parser.add_argument("--senales", default=",".join(SENALES_BASE), help="lista separada por comas")
    parser.add_argument("--incluir-seguridad", action="store_true", help="agrega peaton y obstaculo")
    parser.add_argument("--continuar", default=None, help="CSV de una sesión anterior para seguirla")
    parser.add_argument("--resumen", default=None, help="solo mostrar el resumen de un CSV")
    args = parser.parse_args()

    if args.resumen:
        imprimir_resumen(leer_csv(args.resumen))
        return

    senales = [s.strip() for s in args.senales.split(",") if s.strip()]
    if args.incluir_seguridad:
        senales += [s for s in ("peaton", "obstaculo") if s not in senales]

    if args.continuar:
        ruta = args.continuar
        previas = leer_csv(ruta)
    else:
        os.makedirs("pruebas", exist_ok=True)
        ruta = os.path.join("pruebas", datetime.now().strftime("pruebas_%Y%m%d_%H%M%S.csv"))
        previas = []
    hechas = defaultdict(int)
    for f in previas:
        hechas[f["senal"]] += 1

    nuevo = not os.path.exists(ruta)
    archivo = open(ruta, "a", newline="", encoding="utf-8-sig")
    escritor = csv.DictWriter(archivo, fieldnames=COLUMNAS)
    if nuevo:
        escritor.writeheader()
    filas = list(previas)
    print(f"Guardando en {ruta}")
    print("Respuestas: s = éxito, n = fallo, r = repetir, q = terminar\n")

    try:
        for senal in senales:
            if hechas[senal] >= args.n:
                continue
            print(f"\n### Señal: {senal.upper()}")
            print(f"    Criterio de éxito: {CRITERIOS.get(senal, 'definido por el equipo')}")
            intento = hechas[senal] + 1
            while intento <= args.n:
                r = preguntar(f"  Prueba {intento}/{args.n} de '{senal}' -> ¿resultado? [s/n/r/q]: ",
                              ("s", "n", "r", "q"))
                if r == "q":
                    raise KeyboardInterrupt
                if r == "r":
                    print("   (no cuenta, repite la prueba)")
                    continue
                nota = input("   Nota (Enter para omitir): ").strip()
                fila = {"fecha": datetime.now().isoformat(timespec="seconds"), "senal": senal,
                        "intento": intento, "resultado": "exito" if r == "s" else "fallo", "nota": nota}
                escritor.writerow(fila)
                archivo.flush()
                filas.append(fila)
                intento += 1
    except (KeyboardInterrupt, EOFError):
        print(f"\nSesión interrumpida. Para continuar: python prueba_10.py --continuar {ruta}")
    finally:
        archivo.close()

    if filas:
        imprimir_resumen(filas)
        print(f"Resultados en {ruta}")


if __name__ == "__main__":
    main()
