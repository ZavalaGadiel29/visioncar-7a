"""
registro.py
-----------
Guarda un archivo CSV por recorrido con una fila por cuadro procesado.
Ese archivo luego se analiza con analizar_metricas.py para el reporte.
"""

import csv
import os
from datetime import datetime

COLUMNAS = [
    "tiempo_s", "fps", "latencia_ms", "estado", "comando", "accion_nube", "modo",
    "motivo", "regla", "senal", "confianza", "senal_vista", "conf_vista",
    "carril_ok", "error_carril", "modo_carril", "emergencia", "video_ok", "brillo", "nitidez",
    "yolo_ejecutado", "senales_ejecutado", "num_detecciones", "detecciones",
    "zona_izq", "zona_centro", "zona_der", "nube_ok", "pendientes",
    "senal_activa", "personas_cada_n", "memoria_linea",
    "angulo_linea", "termino_i", "rueda_izq", "rueda_der",
]


class RegistroCSV:
    def __init__(self, carpeta):
        os.makedirs(carpeta, exist_ok=True)
        nombre = datetime.now().strftime("recorrido_%Y%m%d_%H%M%S.csv")
        self.ruta = os.path.join(carpeta, nombre)
        # utf-8-sig: así Excel abre bien los acentos
        self._archivo = open(self.ruta, "w", newline="", encoding="utf-8-sig")
        self._escritor = csv.DictWriter(self._archivo, fieldnames=COLUMNAS)
        self._escritor.writeheader()
        self._filas = 0
        print(f"[REGISTRO] Guardando métricas en {self.ruta}")

    def escribir(self, fila):
        self._escritor.writerow(fila)
        self._filas += 1
        if self._filas % 30 == 0:      # vaciar a disco de vez en cuando
            self._archivo.flush()

    def cerrar(self):
        self._archivo.close()
        print(f"[REGISTRO] {self._filas} filas guardadas en {self.ruta}")


def detecciones_a_texto(detecciones):
    """Convierte la lista de detecciones en texto: clase:conf:zona:area|..."""
    partes = []
    for d in detecciones:
        partes.append(f"{d.clase}:{d.confianza:.2f}:{d.zona}:{d.area:.3f}")
    return "|".join(partes)
