"""
percepcion_opencv.py
--------------------
Percepción con visión por computadora "clásica" (sin redes neuronales).

Tiene dos funciones independientes:

1. evaluar_calidad():      ¿la imagen sirve? (muy oscura o muy borrosa -> no)
2. analizar_obstaculos():  respaldo para objetos que YOLO no conoce (cajas,
                           paredes, conos de cartón) usando bordes y contornos.
                           Las líneas del carril (blancas y amarilla) se
                           BORRAN antes, para no confundirlas con obstáculos.
"""

from dataclasses import dataclass, field

import cv2
import numpy as np

from carril import mascaras_lineas

ZONAS = ("IZQ", "CENTRO", "DER")


# ======================================================================
# 1) CALIDAD DE IMAGEN
# ======================================================================
def evaluar_calidad(cuadro, cfg_calidad):
    """
    Devuelve (es_confiable, motivo, brillo, nitidez).

    - brillo:  promedio de los píxeles en escala de grises (0 = negro, 255 = blanco).
    - nitidez: varianza del Laplaciano. El Laplaciano resalta los bordes;
               una imagen borrosa casi no tiene bordes, por lo que esa
               varianza sale baja.
    """
    gris = cv2.cvtColor(cuadro, cv2.COLOR_BGR2GRAY)
    brillo = float(gris.mean())
    nitidez = float(cv2.Laplacian(gris, cv2.CV_64F).var())

    if brillo < cfg_calidad["brillo_minimo"]:
        return False, f"Imagen muy oscura (brillo {brillo:.0f})", brillo, nitidez
    if nitidez < cfg_calidad["nitidez_minima"]:
        return False, f"Imagen borrosa (nitidez {nitidez:.0f})", brillo, nitidez
    return True, "Imagen correcta", brillo, nitidez


# ======================================================================
# 2) OBSTÁCULOS POR BORDES Y CONTORNOS (respaldo de YOLO)
# ======================================================================
@dataclass
class ResultadoObstaculos:
    y_inicio_roi: int = 0                                  # fila donde empieza la ROI
    ocupacion: dict = field(default_factory=lambda: {z: 0.0 for z in ZONAS})    # 0 a 1
    bloqueada: dict = field(default_factory=lambda: {z: False for z in ZONAS})
    mascara: object = None                                 # imagen binaria de la ROI (para ver/calibrar)


def analizar_obstaculos(cuadro, cfg_obs, cfg_zonas, cfg_carril=None):
    """
    Pasos:
      1. Región de interés (ROI): solo la parte inferior de la imagen, que
         es el piso justo delante del auto.
      2. Escala de grises + desenfoque gaussiano (quita ruido).
      3. Canny (bordes) o umbral de Otsu (objetos oscuros sobre piso claro).
      4. Se borran los píxeles de las líneas del carril (si se da cfg_carril)
         y se dilata: engrosa los bordes para unir pedazos sueltos.
      5. Contornos: se descartan los muy pequeños (ruido) y los demás se
         rellenan en una máscara.
      6. Para cada zona (IZQ, CENTRO, DER) se calcula qué fracción de sus
         píxeles quedó ocupada. Si pasa el umbral, la zona está bloqueada.
    """
    resultado = ResultadoObstaculos()
    if not cfg_obs["activado"]:
        return resultado

    alto, ancho = cuadro.shape[:2]

    # 1. ROI
    y_inicio = int(alto * cfg_obs["roi_inicio_y"])
    y_inicio = min(max(y_inicio, 0), alto - 2)
    resultado.y_inicio_roi = y_inicio
    roi = cuadro[y_inicio:, :]

    # 2. Gris + desenfoque (el tamaño del kernel debe ser impar)
    gris = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    k = int(cfg_obs["desenfoque_kernel"])
    if k % 2 == 0:
        k += 1
    k = max(k, 1)
    suave = cv2.GaussianBlur(gris, (k, k), 0)

    # 3. Bordes o umbral
    if cfg_obs["metodo"] == "umbral":
        _, binaria = cv2.threshold(suave, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    else:
        binaria = cv2.Canny(suave, int(cfg_obs["canny_bajo"]), int(cfg_obs["canny_alto"]))

    # 4. Borrar las líneas del carril (para que sus bordes, sobre todo en
    #    curvas, no parezcan obstáculos) y dilatar para unir pedazos sueltos
    nucleo = np.ones((3, 3), np.uint8)
    lineas = None
    if cfg_carril is not None:
        borde, amarilla = mascaras_lineas(roi, cfg_carril, solo_color=True)
        lineas = cv2.dilate(cv2.bitwise_or(borde, amarilla), nucleo,
                            iterations=int(cfg_obs.get("margen_lineas_carril", 3)))
        binaria = cv2.bitwise_and(binaria, cv2.bitwise_not(lineas))
    binaria = cv2.dilate(binaria, nucleo, iterations=int(cfg_obs["iteraciones_dilatacion"]))

    # 5. Contornos: se rellena la ENVOLVENTE CONVEXA de los suficientemente
    #    grandes. Así también se rellena un objeto cuyo contorno quedó
    #    abierto (por ejemplo, cuando tapa una línea del carril).
    contornos, _ = cv2.findContours(binaria, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    mascara = np.zeros_like(binaria)
    for contorno in contornos:
        casco = cv2.convexHull(contorno)
        if cv2.contourArea(casco) >= cfg_obs["area_minima_contorno"]:
            cv2.drawContours(mascara, [casco], -1, 255, thickness=cv2.FILLED)
    if lineas is not None:
        mascara = cv2.bitwise_and(mascara, cv2.bitwise_not(lineas))
    resultado.mascara = mascara

    # 6. Ocupación por zona (mismos límites que usan las zonas de YOLO)
    x_izq = int(ancho * cfg_zonas["limite_izq"])
    x_der = int(ancho * cfg_zonas["limite_der"])
    columnas = {
        "IZQ": (0, x_izq),
        "CENTRO": (x_izq, x_der),
        "DER": (x_der, ancho),
    }
    for zona, (xa, xb) in columnas.items():
        pedazo = mascara[:, xa:xb]
        ocupacion = cv2.countNonZero(pedazo) / float(max(pedazo.size, 1))
        resultado.ocupacion[zona] = ocupacion
        resultado.bloqueada[zona] = ocupacion >= cfg_obs["umbral_ocupacion_zona"]

    return resultado
