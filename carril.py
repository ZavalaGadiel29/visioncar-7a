"""
carril.py
---------
Seguimiento de carril con OpenCV (sin redes neuronales).

La pista: fondo oscuro, bordes BLANCOS y línea central AMARILLA discontinua.

Pasos de detectar_carril():
  1. Región de interés (ROI): solo la parte de abajo de la imagen (el piso
     que está justo delante del carrito).
  2. Máscaras de las líneas:
       - por COLOR (HSV): blanco = poca saturación y mucho brillo;
                          amarillo = matiz entre ~15 y ~35.
       - por BORDES (Canny): cambios fuertes de brillo (útil si la luz
         cambia y el color falla).
     "metodo" en config.json decide cuál usar: "color", "canny" o "ambos".
  3. La ROI se corta en varias FRANJAS horizontales. En cada franja se
     cuenta cuántos píxeles de línea hay en cada columna (histograma) y,
     partiendo del centro del carril anterior, se busca la línea más
     cercana hacia la izquierda y hacia la derecha.
  4. Centro del carril = punto medio entre las dos líneas. Si solo se ve
     una, se estima el otro lado con el ancho de carril aprendido.
  5. ERROR = (centro del carril - centro deseado) / (medio ancho de imagen)
       -1 = carril totalmente a la izquierda, 0 = centrado, +1 = a la derecha.

Qué líneas forman el carril ("carril_objetivo" en config.json):
  - "derecho":   entre la línea amarilla (izq.) y el borde blanco derecho.
  - "izquierdo": entre el borde blanco izquierdo y la línea amarilla (der.).
  - "completo":  entre los dos bordes blancos (ignora la amarilla).

Además está el CONTROL PD, que convierte el error en velocidad de cada
rueda para el comando MOVER.
"""

from dataclasses import dataclass, field

import cv2
import numpy as np


# ======================================================================
# Máscaras de líneas (también las usa percepcion_opencv para NO confundir
# las líneas del carril con obstáculos)
# ======================================================================
def _kernel_impar(k):
    k = max(1, int(k))
    return k if k % 2 == 1 else k + 1


def mascaras_lineas(imagen, cfg, solo_color=False):
    """
    Devuelve (mascara_borde, mascara_amarilla) de una imagen BGR.

    mascara_borde: líneas blancas (o bordes de Canny) SIN la línea amarilla.
    mascara_amarilla: solo la línea amarilla (siempre por color).
    solo_color=True ignora Canny (lo usa el detector de obstáculos, porque
    Canny también marcaría los bordes de los propios obstáculos).
    """
    suave = cv2.GaussianBlur(imagen, (_kernel_impar(cfg["desenfoque_kernel"]),) * 2, 0)
    hsv = cv2.cvtColor(suave, cv2.COLOR_BGR2HSV)

    amarilla = cv2.inRange(hsv, np.array(cfg["amarillo_bajo"]), np.array(cfg["amarillo_alto"]))
    blanca = cv2.inRange(hsv, np.array(cfg["blanco_bajo"]), np.array(cfg["blanco_alto"]))

    metodo = "color" if solo_color else cfg.get("metodo", "ambos")
    if metodo == "color":
        borde = blanca
    else:
        gris = cv2.cvtColor(suave, cv2.COLOR_BGR2GRAY)
        canny = cv2.Canny(gris, int(cfg["canny_bajo"]), int(cfg["canny_alto"]))
        canny = cv2.dilate(canny, np.ones((3, 3), np.uint8), iterations=1)
        borde = canny if metodo == "canny" else cv2.bitwise_or(blanca, canny)

    # Quitar de los bordes lo que es amarillo (los bordes de Canny también
    # "ven" la línea amarilla y la confundirían con un borde blanco)
    amarilla_gruesa = cv2.dilate(amarilla, np.ones((5, 5), np.uint8), iterations=1)
    borde = cv2.bitwise_and(borde, cv2.bitwise_not(amarilla_gruesa))
    return borde, amarilla


# ======================================================================
# Resultado
# ======================================================================
@dataclass
class ResultadoCarril:
    detectado: bool = False
    error: float = 0.0                 # -1 (carril a la izq.) ... +1 (a la der.)
    centro_x: float = 0.0              # en píxeles de la imagen
    x_izq: float = None                # límite izquierdo del carril (fila más baja)
    x_der: float = None                # límite derecho del carril (fila más baja)
    franjas_validas: int = 0
    y_inicio_roi: int = 0
    puntos: list = field(default_factory=list)   # (y, x_izq, x_der, centro) por franja, para dibujar
    mascara: object = None             # imagen de la ROI para calibrar (borde=blanco, amarilla=amarillo)
    descripcion: str = "sin datos"


# ======================================================================
# Detector
# ======================================================================
class DetectorCarril:
    def __init__(self, cfg_carril):
        self.cfg = cfg_carril
        self._centro_previo = None        # centro de la vez anterior (px)
        self._ancho_carril = None         # ancho aprendido del carril (px)

    def reiniciar(self):
        self._centro_previo = None

    def procesar(self, cuadro):
        cfg = self.cfg
        alto, ancho = cuadro.shape[:2]
        resultado = ResultadoCarril()

        # 1. ROI
        y0 = int(alto * cfg["roi_inicio_y"])
        y0 = min(max(y0, 0), alto - 4)
        resultado.y_inicio_roi = y0
        roi = cuadro[y0:, :]

        # 2. Máscaras
        borde, amarilla = mascaras_lineas(roi, cfg)
        vista = np.zeros_like(roi)
        vista[borde > 0] = (255, 255, 255)
        vista[amarilla > 0] = (0, 220, 255)
        resultado.mascara = vista

        # 3. Franjas: de abajo (cerca) hacia arriba (lejos)
        n_franjas = max(1, int(cfg["franjas"]))
        alto_roi = roi.shape[0]
        alto_franja = max(2, alto_roi // n_franjas)
        minimo = max(1, int(cfg["min_pixeles_columna"]))
        if self._ancho_carril is None:
            self._ancho_carril = cfg["ancho_carril_inicial"] * ancho
        referencia = self._centro_previo if self._centro_previo is not None else ancho * cfg["centro_objetivo"]
        objetivo = cfg.get("carril_objetivo", "derecho")

        centros, pesos = [], []
        for i in range(n_franjas):
            ya = alto_roi - (i + 1) * alto_franja
            yb = alto_roi - i * alto_franja
            if ya < 0:
                break
            col_borde = np.flatnonzero((borde[ya:yb] > 0).sum(axis=0) >= minimo)
            col_amarilla = np.flatnonzero((amarilla[ya:yb] > 0).sum(axis=0) >= minimo)

            # Qué máscara forma cada lado según el carril objetivo
            if objetivo == "derecho":
                lado_izq, lado_der = col_amarilla, col_borde
            elif objetivo == "izquierdo":
                lado_izq, lado_der = col_borde, col_amarilla
            else:
                lado_izq, lado_der = col_borde, col_borde

            izq = lado_izq[lado_izq < referencia]
            der = lado_der[lado_der > referencia]
            x_izq = float(izq.max()) if izq.size else None     # la más cercana al centro
            x_der = float(der.min()) if der.size else None

            if x_izq is not None and x_der is not None:
                medido = x_der - x_izq
                # Si el ancho medido es razonable, se aprende (promedio suave)
                minimo_ancho = cfg["ancho_carril_min"] * ancho
                if medido >= minimo_ancho:
                    self._ancho_carril = 0.9 * self._ancho_carril + 0.1 * medido
                    centro = (x_izq + x_der) / 2.0
                else:
                    continue
            elif x_izq is not None:
                centro = x_izq + self._ancho_carril / 2.0
            elif x_der is not None:
                centro = x_der - self._ancho_carril / 2.0
            else:
                continue

            y_media = y0 + (ya + yb) // 2
            resultado.puntos.append((y_media, x_izq, x_der, centro))
            if not resultado.puntos[:-1]:          # primera franja válida = la más cercana
                resultado.x_izq, resultado.x_der = x_izq, x_der
            centros.append(centro)
            # Las franjas cercanas pesan más que las lejanas
            pesos.append(1.0 / (1.0 + i * cfg["peso_franjas_lejanas"]))

        resultado.franjas_validas = len(centros)
        if resultado.franjas_validas < int(cfg["min_franjas_validas"]):
            resultado.descripcion = f"carril perdido ({resultado.franjas_validas} franjas)"
            return resultado

        centro = float(np.average(centros, weights=pesos))
        self._centro_previo = centro
        medio = ancho / 2.0
        resultado.detectado = True
        resultado.centro_x = centro
        resultado.error = float(np.clip((centro - ancho * cfg["centro_objetivo"]) / medio, -1.0, 1.0))
        resultado.descripcion = f"carril OK ({resultado.franjas_validas} franjas)"
        return resultado


# ======================================================================
# Control proporcional-derivativo (PD)
# ======================================================================
class ControlPD:
    """
    correccion = Kp * error + Kd * (cambio del error por segundo)

    vel_izq = base + correccion       vel_der = base - correccion

    Si el carril está a la derecha (error > 0), la rueda izquierda gira más
    rápido que la derecha y el carrito se va hacia la derecha.
    """

    def __init__(self, cfg_carril):
        self.cfg = cfg_carril
        self._error_previo = None
        self._tiempo_previo = None
        self.ultima_correccion = 0.0

    def reiniciar(self):
        self._error_previo = None
        self._tiempo_previo = None

    def calcular(self, error, ahora):
        cfg = self.cfg
        derivada = 0.0
        if self._error_previo is not None:
            dt = ahora - self._tiempo_previo
            if dt > 1e-3:
                derivada = (error - self._error_previo) / dt
        self._error_previo = error
        self._tiempo_previo = ahora

        correccion = cfg["kp"] * error + cfg["kd"] * derivada
        self.ultima_correccion = correccion
        base = cfg["velocidad_base"]
        minimo, maximo = cfg["velocidad_rueda_min"], cfg["velocidad_rueda_max"]
        vel_izq = float(np.clip(base + correccion, minimo, maximo))
        vel_der = float(np.clip(base - correccion, minimo, maximo))
        return vel_izq, vel_der
