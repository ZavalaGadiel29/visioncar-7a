"""
interfaz.py
-----------
Todo lo que se dibuja en pantalla.

Diseño:
  +----------------------------------+-----------------+
  |                                  |  ACCIÓN (color) |
  |   Video ampliado con:            |  motivo         |
  |   - cajas de YOLO                |  FPS / latencia |
  |   - líneas de las 3 zonas        |  zonas          |
  |   - región de interés (ROI)      |  semáforo       |
  |                                  |  detecciones    |
  |                                  |  controles      |
  +----------------------------------+-----------------+

También contiene el Calibrador: ventanas con controles deslizantes
(trackbars) para ajustar en vivo los rangos HSV y los umbrales.

Nota: cv2.putText no puede dibujar acentos ni la "ñ", por eso todo texto
pasa antes por texto_seguro().
"""

import unicodedata
from dataclasses import dataclass, field

import cv2
import numpy as np

from percepcion_opencv import mascara_color_completa

NOMBRE_VENTANA = "Agente autonomo - Percibir / Razonar / Actuar"
FUENTE = cv2.FONT_HERSHEY_SIMPLEX

# Colores en formato BGR (azul, verde, rojo), así los usa OpenCV
BLANCO = (255, 255, 255)
GRIS = (150, 150, 150)
GRIS_OSCURO = (60, 60, 60)
FONDO_PANEL = (35, 30, 30)
ROJO = (40, 40, 230)
VERDE = (60, 190, 60)
AMARILLO = (0, 210, 255)
NARANJA = (0, 140, 255)
MORADO = (200, 70, 200)
CIAN = (230, 200, 0)

COLOR_ACCION = {
    "DETENER": ROJO,
    "AVANZAR": VERDE,
    "GIRAR_IZQ": NARANJA,
    "GIRAR_DER": NARANJA,
    "RETROCEDER": MORADO,
}

COLOR_TIPO = {           # color de las cajas de YOLO según el tipo
    "persona": ROJO,
    "senal_alto": MORADO,
    "semaforo": AMARILLO,
    "obstaculo": CIAN,
}

COLOR_SEMAFORO = {"ROJO": ROJO, "VERDE": VERDE, "DESCONOCIDO": GRIS, "NINGUNO": GRIS_OSCURO}


def texto_seguro(texto):
    """Quita acentos y caracteres que OpenCV no sabe dibujar (á -> a, ñ -> n)."""
    normal = unicodedata.normalize("NFKD", str(texto))
    return normal.encode("ascii", "ignore").decode("ascii")


def poner_texto(img, texto, x, y, escala=0.45, color=BLANCO, grosor=1):
    cv2.putText(img, texto_seguro(texto), (x, y), FUENTE, escala, color, grosor, cv2.LINE_AA)


def partir_en_lineas(texto, ancho_max, escala=0.45):
    """Divide un texto largo en varias líneas que quepan en ancho_max píxeles."""
    palabras = texto_seguro(texto).split()
    lineas, actual = [], ""
    for palabra in palabras:
        prueba = (actual + " " + palabra).strip()
        ancho = cv2.getTextSize(prueba, FUENTE, escala, 1)[0][0]
        if ancho <= ancho_max or not actual:
            actual = prueba
        else:
            lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    return lineas


@dataclass
class DatosPantalla:
    """Todo lo que la interfaz necesita para dibujar un cuadro."""
    accion: str = "DETENER"
    motivo: str = ""
    regla: int = 0
    estado_agente: str = "NORMAL"
    detecciones: list = field(default_factory=list)
    color_semaforo: str = "NINGUNO"
    obstaculos: object = None              # ResultadoObstaculos
    zonas_ocupadas: dict = field(default_factory=dict)
    fps: float = 0.0
    latencia_ms: object = None             # float o None
    enviados: int = 0
    acks: int = 0
    errores: int = 0
    perdidos: int = 0
    emergencia: bool = False
    pausado: bool = False
    sin_auto: bool = False
    video_ok: bool = True
    brillo: float = 0.0
    nitidez: float = 0.0


class Interfaz:
    def __init__(self, cfg_interfaz, cfg_zonas, ancho_video, alto_video):
        self.escala = int(cfg_interfaz["escala_video"])
        self.ancho_panel = int(cfg_interfaz["ancho_panel"])
        self.cfg_zonas = cfg_zonas
        self.ancho_video = ancho_video
        self.alto_video = alto_video
        cv2.namedWindow(NOMBRE_VENTANA, cv2.WINDOW_AUTOSIZE)

    # ------------------------------------------------------------------
    def mostrar(self, cuadro, datos):
        """Arma la imagen completa (video + panel) y la muestra."""
        vista_video = self._dibujar_video(cuadro, datos)
        panel = self._dibujar_panel(datos, vista_video.shape[0])
        imagen = np.hstack([vista_video, panel])
        cv2.imshow(NOMBRE_VENTANA, imagen)

    # ------------------------------------------------------------------
    # Parte izquierda: el video con anotaciones
    # ------------------------------------------------------------------
    def _dibujar_video(self, cuadro, datos):
        e = self.escala
        ancho, alto = self.ancho_video * e, self.alto_video * e

        if cuadro is None:
            vista = np.zeros((alto, ancho, 3), np.uint8)
        else:
            vista = cv2.resize(cuadro, (ancho, alto), interpolation=cv2.INTER_LINEAR)

        # Región de interés de OpenCV + ocupación de cada zona
        obs = datos.obstaculos
        x_izq = int(ancho * self.cfg_zonas["limite_izq"])
        x_der = int(ancho * self.cfg_zonas["limite_der"])
        if obs is not None and obs.mascara is not None:
            y_roi = obs.y_inicio_roi * e
            # Pintar en rojo semitransparente lo que OpenCV considera obstáculo
            mascara = cv2.resize(obs.mascara, (ancho, alto - y_roi), interpolation=cv2.INTER_NEAREST)
            region = vista[y_roi:, :]
            capa = region.copy()
            capa[mascara > 0] = ROJO
            cv2.addWeighted(capa, 0.35, region, 0.65, 0, dst=region)
            cv2.rectangle(vista, (0, y_roi), (ancho - 1, alto - 1), AMARILLO, 1)
            poner_texto(vista, "ROI OpenCV", 6, y_roi - 6, 0.45, AMARILLO)
            for zona, (xa, xb) in (("IZQ", (0, x_izq)), ("CENTRO", (x_izq, x_der)), ("DER", (x_der, ancho))):
                ocupada = obs.bloqueada[zona]
                texto = f"{obs.ocupacion[zona]:.0%}"
                color = ROJO if ocupada else VERDE
                poner_texto(vista, texto, (xa + xb) // 2 - 15, alto - 10, 0.55, color, 2)

        # Líneas que dividen las tres zonas
        for x in (x_izq, x_der):
            cv2.line(vista, (x, 0), (x, alto), BLANCO, 1, cv2.LINE_AA)
        for zona, xc in (("IZQ", x_izq // 2), ("CENTRO", (x_izq + x_der) // 2), ("DER", (x_der + ancho) // 2)):
            ocupada = datos.zonas_ocupadas.get(zona, False)
            poner_texto(vista, zona, xc - 25, 22, 0.55, ROJO if ocupada else BLANCO, 2)

        # Cajas de YOLO
        for d in datos.detecciones:
            x1, y1, x2, y2 = (v * e for v in d.caja)
            color = COLOR_TIPO.get(d.tipo, BLANCO)
            if d.tipo == "semaforo" and d.color_semaforo:
                color = COLOR_SEMAFORO.get(d.color_semaforo, color)
            cv2.rectangle(vista, (x1, y1), (x2, y2), color, 2)
            etiqueta = f"{d.clase} {d.confianza:.2f}"
            if d.color_semaforo:
                etiqueta += f" {d.color_semaforo}"
            (tw, th), _ = cv2.getTextSize(etiqueta, FUENTE, 0.5, 1)
            y_texto = max(y1, th + 6)
            cv2.rectangle(vista, (x1, y_texto - th - 6), (x1 + tw + 6, y_texto), color, cv2.FILLED)
            poner_texto(vista, etiqueta, x1 + 3, y_texto - 4, 0.5, (0, 0, 0), 1)

        # Avisos grandes
        if not datos.video_ok:
            poner_texto(vista, "SIN VIDEO", ancho // 2 - 110, alto // 2, 1.3, ROJO, 3)
        if datos.emergencia:
            cv2.rectangle(vista, (0, 0), (ancho - 1, alto - 1), ROJO, 8)
            poner_texto(vista, "PARO DE EMERGENCIA", ancho // 2 - 190, alto // 2 + 50, 1.0, ROJO, 3)
        return vista

    # ------------------------------------------------------------------
    # Parte derecha: panel de información
    # ------------------------------------------------------------------
    def _dibujar_panel(self, datos, alto):
        w = self.ancho_panel
        panel = np.full((alto, w, 3), FONDO_PANEL, np.uint8)
        m = 12           # margen
        y = 24

        poner_texto(panel, "AGENTE AUTONOMO", m, y, 0.6, BLANCO, 2)
        y += 12

        # --- Acción elegida (caja grande del color de la acción) ---
        color = COLOR_ACCION.get(datos.accion, GRIS)
        cv2.rectangle(panel, (m, y), (w - m, y + 46), color, cv2.FILLED)
        tam = cv2.getTextSize(datos.accion, FUENTE, 0.9, 2)[0]
        poner_texto(panel, datos.accion, (w - tam[0]) // 2, y + 32, 0.9, BLANCO, 2)
        y += 64

        # --- Motivo ---
        poner_texto(panel, "Motivo:", m, y, 0.45, GRIS)
        y += 18
        for linea in partir_en_lineas(datos.motivo, w - 2 * m)[:3]:
            poner_texto(panel, linea, m, y, 0.45, BLANCO)
            y += 17
        poner_texto(panel, f"Regla {datos.regla}  |  Estado: {datos.estado_agente}", m, y, 0.4, GRIS)
        y += 12
        y = self._separador(panel, y)

        # --- Rendimiento y comunicación ---
        poner_texto(panel, f"FPS: {datos.fps:5.1f}", m, y, 0.5)
        if datos.sin_auto:
            texto_lat, color_lat = "Latencia: (sin auto)", GRIS
        elif datos.latencia_ms is None:
            texto_lat, color_lat = "Latencia: --", AMARILLO
        else:
            texto_lat, color_lat = f"Latencia: {datos.latencia_ms:.0f} ms", BLANCO
        poner_texto(panel, texto_lat, m + 120, y, 0.5, color_lat)
        y += 20
        poner_texto(panel, f"Env {datos.enviados}  ACK {datos.acks}  ERR {datos.errores}  Perd {datos.perdidos}",
                    m, y, 0.4, GRIS)
        y += 20
        if datos.sin_auto:
            estado_envio, color_envio = "MODO SIN AUTO (no se envia)", AMARILLO
        elif datos.pausado:
            estado_envio, color_envio = "ENVIO PAUSADO (prueba fail-safe)", AMARILLO
        else:
            estado_envio, color_envio = "Enviando comandos", VERDE
        poner_texto(panel, estado_envio, m, y, 0.45, color_envio)
        y += 10
        y = self._separador(panel, y)

        # --- Estado de las zonas (fusión YOLO + OpenCV) ---
        poner_texto(panel, "Zonas:", m, y, 0.45, GRIS)
        y += 8
        ancho_caja = (w - 2 * m - 10) // 3
        for i, zona in enumerate(("IZQ", "CENTRO", "DER")):
            xa = m + i * (ancho_caja + 5)
            ocupada = datos.zonas_ocupadas.get(zona, False)
            cv2.rectangle(panel, (xa, y), (xa + ancho_caja, y + 34), ROJO if ocupada else VERDE, cv2.FILLED)
            poner_texto(panel, zona, xa + 6, y + 15, 0.42, BLANCO, 1)
            poner_texto(panel, "OCUPADA" if ocupada else "LIBRE", xa + 6, y + 29, 0.38, BLANCO, 1)
        y += 52

        # --- Semáforo y calidad de imagen ---
        poner_texto(panel, "Semaforo:", m, y, 0.45, GRIS)
        cv2.circle(panel, (m + 95, y - 5), 8, COLOR_SEMAFORO.get(datos.color_semaforo, GRIS), cv2.FILLED)
        poner_texto(panel, datos.color_semaforo, m + 110, y, 0.45)
        y += 20
        poner_texto(panel, f"Brillo {datos.brillo:.0f}   Nitidez {datos.nitidez:.0f}", m, y, 0.45)
        y += 10
        y = self._separador(panel, y)

        # --- Detecciones de YOLO ---
        poner_texto(panel, f"Detecciones YOLO: {len(datos.detecciones)}", m, y, 0.45, GRIS)
        y += 18
        for d in datos.detecciones[:4]:
            poner_texto(panel, f"- {d.clase} {d.confianza:.2f} {d.zona} {d.area:.0%}", m, y, 0.42)
            y += 16

        # --- Controles (siempre abajo) ---
        yc = alto - 58
        cv2.line(panel, (m, yc - 14), (w - m, yc - 14), GRIS_OSCURO, 1)
        poner_texto(panel, "ESPACIO: paro emergencia", m, yc, 0.4, GRIS)
        poner_texto(panel, "P: pausar envio   Q: salir", m, yc + 16, 0.4, GRIS)
        poner_texto(panel, "C: calibrar   G: guardar config", m, yc + 32, 0.4, GRIS)
        return panel

    def _separador(self, panel, y):
        cv2.line(panel, (12, y), (panel.shape[1] - 12, y), GRIS_OSCURO, 1)
        return y + 22


# ======================================================================
# Calibrador con trackbars
# ======================================================================
VENTANA_HSV = "Calibracion HSV (semaforo)"
VENTANA_UMBRALES = "Calibracion umbrales"

# (nombre del control, sección del config, clave, índice dentro de la lista o None,
#  valor máximo del control, factor)   -> valor_config = posición / factor
CONTROLES_HSV = [
    ("R1 H min", "semaforo", "rojo1_bajo", 0, 179, 1),
    ("R1 H max", "semaforo", "rojo1_alto", 0, 179, 1),
    ("R1 S min", "semaforo", "rojo1_bajo", 1, 255, 1),
    ("R1 V min", "semaforo", "rojo1_bajo", 2, 255, 1),
    ("R2 H min", "semaforo", "rojo2_bajo", 0, 179, 1),
    ("R2 H max", "semaforo", "rojo2_alto", 0, 179, 1),
    ("R2 S min", "semaforo", "rojo2_bajo", 1, 255, 1),
    ("R2 V min", "semaforo", "rojo2_bajo", 2, 255, 1),
    ("Verde H min", "semaforo", "verde_bajo", 0, 179, 1),
    ("Verde H max", "semaforo", "verde_alto", 0, 179, 1),
    ("Verde S min", "semaforo", "verde_bajo", 1, 255, 1),
    ("Verde V min", "semaforo", "verde_bajo", 2, 255, 1),
    ("Prop min %", "semaforo", "proporcion_minima", None, 50, 100),
]

CONTROLES_UMBRALES = [
    ("ROI inicio %", "opencv_obstaculos", "roi_inicio_y", None, 95, 100),
    ("Desenfoque", "opencv_obstaculos", "desenfoque_kernel", None, 15, 1),
    ("Canny bajo", "opencv_obstaculos", "canny_bajo", None, 255, 1),
    ("Canny alto", "opencv_obstaculos", "canny_alto", None, 255, 1),
    ("Dilatacion", "opencv_obstaculos", "iteraciones_dilatacion", None, 6, 1),
    ("Area min cont", "opencv_obstaculos", "area_minima_contorno", None, 1000, 1),
    ("Ocupacion %", "opencv_obstaculos", "umbral_ocupacion_zona", None, 100, 100),
    ("Brillo min", "calidad_imagen", "brillo_minimo", None, 255, 1),
    ("Nitidez min", "calidad_imagen", "nitidez_minima", None, 500, 1),
    ("Conf YOLO %", "yolo", "confianza", None, 100, 100),
]


class Calibrador:
    """
    Abre dos ventanas con trackbars. Cada cuadro, leer() copia la posición
    de los controles al diccionario de configuración, así que los cambios
    se ven al instante. La tecla G guarda el config.json.
    """

    def __init__(self, config):
        self.config = config
        self.abierto = False

    def abrir(self):
        for ventana, controles in ((VENTANA_HSV, CONTROLES_HSV), (VENTANA_UMBRALES, CONTROLES_UMBRALES)):
            cv2.namedWindow(ventana, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(ventana, 420, 640)
            for nombre, seccion, clave, indice, maximo, factor in controles:
                valor = self.config[seccion][clave]
                if indice is not None:
                    valor = valor[indice]
                posicion = int(round(valor * factor))
                cv2.createTrackbar(nombre, ventana, min(posicion, maximo), maximo, lambda _x: None)
        self.abierto = True

    def cerrar(self):
        for ventana in (VENTANA_HSV, VENTANA_UMBRALES):
            try:
                cv2.destroyWindow(ventana)
            except cv2.error:
                pass
        self.abierto = False

    def alternar(self):
        self.cerrar() if self.abierto else self.abrir()

    def leer(self):
        """Copia los valores de los trackbars al diccionario de configuración."""
        if not self.abierto:
            return
        # Si el usuario cerró alguna ventana con la X, se cierra el calibrador
        for ventana in (VENTANA_HSV, VENTANA_UMBRALES):
            if cv2.getWindowProperty(ventana, cv2.WND_PROP_VISIBLE) < 1:
                self.cerrar()
                return

        for ventana, controles in ((VENTANA_HSV, CONTROLES_HSV), (VENTANA_UMBRALES, CONTROLES_UMBRALES)):
            for nombre, seccion, clave, indice, _maximo, factor in controles:
                posicion = cv2.getTrackbarPos(nombre, ventana)
                valor = posicion if factor == 1 else posicion / factor
                if indice is None:
                    self.config[seccion][clave] = valor
                else:
                    self.config[seccion][clave][indice] = valor

    def mostrar_vistas(self, cuadro, obstaculos):
        """Muestra en las ventanas de calibración lo que 've' cada filtro."""
        if not self.abierto:
            return
        if cuadro is None:
            negro = np.zeros((240, 320, 3), np.uint8)
            cv2.imshow(VENTANA_HSV, negro)
            cv2.imshow(VENTANA_UMBRALES, negro)
            return

        # Ventana HSV: píxeles que caen en los rangos rojo / verde
        cv2.imshow(VENTANA_HSV, mascara_color_completa(cuadro, self.config["semaforo"]))

        # Ventana de umbrales: máscara de obstáculos de la ROI
        if obstaculos is not None and obstaculos.mascara is not None:
            cv2.imshow(VENTANA_UMBRALES, obstaculos.mascara)
        else:
            cv2.imshow(VENTANA_UMBRALES, np.zeros((120, 320), np.uint8))
