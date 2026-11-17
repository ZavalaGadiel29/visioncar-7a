"""
analizar_metricas.py
--------------------
Lee el CSV de un recorrido y muestra un resumen para el reporte:
  - duración, FPS promedio / mínimo / máximo
  - latencia promedio / mínima / máxima
  - cuántas veces se eligió cada acción (y su porcentaje)
  - cuántas veces se activó cada regla
  - cuántas detecciones hubo por clase

Uso:
    python analizar_metricas.py                       # analiza el CSV más reciente
    python analizar_metricas.py registros/recorrido_X.csv
    python analizar_metricas.py --graficas            # además guarda gráficas PNG

Nota sobre las detecciones: como YOLO puede ejecutarse cada N cuadros y
reutilizar su resultado, solo se cuentan las filas donde yolo_ejecutado = 1,
para no contar la misma detección varias veces.
"""

import argparse
import csv
import glob
import os
from collections import Counter


def buscar_ultimo_csv(carpeta="registros"):
    archivos = glob.glob(os.path.join(carpeta, "recorrido_*.csv"))
    if not archivos:
        return None
    return max(archivos, key=os.path.getmtime)


def leer_filas(ruta):
    with open(ruta, "r", encoding="utf-8-sig", newline="") as archivo:
        return list(csv.DictReader(archivo))


def a_numeros(filas, columna):
    """Extrae una columna como lista de números, saltando celdas vacías."""
    valores = []
    for fila in filas:
        texto = fila.get(columna, "")
        if texto not in ("", None):
            valores.append(float(texto))
    return valores


def resumen_numerico(nombre, valores, unidad=""):
    if not valores:
        print(f"  {nombre}: sin datos")
        return
    promedio = sum(valores) / len(valores)
    print(f"  {nombre}: promedio {promedio:.2f}{unidad}  |  mín {min(valores):.2f}{unidad}"
          f"  |  máx {max(valores):.2f}{unidad}  ({len(valores)} muestras)")


def imprimir_conteo(titulo, contador, total):
    print(f"\n{titulo}")
    if not contador:
        print("  (ninguna)")
        return
    for clave, cantidad in contador.most_common():
        porcentaje = 100.0 * cantidad / total if total else 0
        print(f"  {str(clave):<14} {cantidad:>7}   ({porcentaje:5.1f} %)")


def guardar_graficas(filas, ruta_csv):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n[!] matplotlib no está instalado; no se generan gráficas.")
        return

    tiempos = [float(f["tiempo_s"]) for f in filas]
    fps = [float(f["fps"]) for f in filas]
    lat_t = [float(f["tiempo_s"]) for f in filas if f["latencia_ms"]]
    lat_v = [float(f["latencia_ms"]) for f in filas if f["latencia_ms"]]

    figura, (eje1, eje2) = plt.subplots(2, 1, figsize=(9, 6), sharex=True)
    eje1.plot(tiempos, fps)
    eje1.set_ylabel("FPS")
    eje1.set_title("Rendimiento del recorrido")
    eje1.grid(True, alpha=0.3)
    eje2.plot(lat_t, lat_v, color="tab:orange")
    eje2.set_ylabel("Latencia (ms)")
    eje2.set_xlabel("Tiempo (s)")
    eje2.grid(True, alpha=0.3)
    base = os.path.splitext(ruta_csv)[0]
    figura.tight_layout()
    figura.savefig(base + "_rendimiento.png", dpi=120)

    acciones = Counter(f["accion"] for f in filas)
    figura2, eje = plt.subplots(figsize=(7, 4))
    eje.bar(list(acciones.keys()), list(acciones.values()))
    eje.set_title("Cantidad de cuadros por acción")
    eje.set_ylabel("Cuadros")
    figura2.tight_layout()
    figura2.savefig(base + "_acciones.png", dpi=120)
    print(f"\nGráficas guardadas: {base}_rendimiento.png y {base}_acciones.png")


def main():
    parser = argparse.ArgumentParser(description="Resumen de métricas de un recorrido")
    parser.add_argument("csv", nargs="?", default=None, help="archivo CSV (por defecto el más reciente)")
    parser.add_argument("--graficas", action="store_true", help="guardar gráficas PNG (requiere matplotlib)")
    args = parser.parse_args()

    ruta = args.csv or buscar_ultimo_csv()
    if ruta is None or not os.path.exists(ruta):
        print("No se encontró ningún CSV. Ejecuta primero main.py o indica la ruta del archivo.")
        return

    filas = leer_filas(ruta)
    if not filas:
        print(f"El archivo {ruta} no tiene datos.")
        return

    total = len(filas)
    duracion = float(filas[-1]["tiempo_s"]) - float(filas[0]["tiempo_s"])

    print("=" * 64)
    print(f" RESUMEN DEL RECORRIDO: {os.path.basename(ruta)}")
    print("=" * 64)
    print(f"  Cuadros registrados: {total}")
    print(f"  Duración: {duracion:.1f} s")
    resumen_numerico("FPS", a_numeros(filas, "fps"))
    resumen_numerico("Latencia", a_numeros(filas, "latencia_ms"), " ms")
    sin_video = sum(1 for f in filas if f["video_ok"] == "0")
    print(f"  Cuadros sin video: {sin_video}")

    imprimir_conteo("Acciones (cuadros):", Counter(f["accion"] for f in filas), total)
    imprimir_conteo("Reglas que decidieron:", Counter(f"Regla {f['regla']}" for f in filas), total)

    # Cambios de acción: cuántas veces el auto cambió de una acción a otra
    cambios = sum(1 for a, b in zip(filas, filas[1:]) if a["accion"] != b["accion"])
    print(f"\nCambios de acción durante el recorrido: {cambios}")

    # Detecciones por clase (solo en cuadros donde YOLO realmente se ejecutó)
    detecciones = Counter()
    cuadros_yolo = 0
    for f in filas:
        if f["yolo_ejecutado"] != "1":
            continue
        cuadros_yolo += 1
        if f["detecciones"]:
            for item in f["detecciones"].split("|"):
                detecciones[item.split(":")[0]] += 1
    print(f"\nCuadros en los que se ejecutó YOLO: {cuadros_yolo}")
    total_det = sum(detecciones.values())
    imprimir_conteo("Detecciones por clase:", detecciones, total_det)

    semaforos = Counter(f["semaforo"] for f in filas if f["semaforo"] not in ("NINGUNO", ""))
    imprimir_conteo("Colores de semáforo vistos (cuadros):", semaforos, sum(semaforos.values()))
    print()

    if args.graficas:
        guardar_graficas(filas, ruta)


if __name__ == "__main__":
    main()
