"""
interfaz.py
-----------
Todo lo que se dibuja en pantalla.

  +----------------------------------+-------------------------+
  |  Video ampliado con:             |  ESTADO (color)         |
  |  - región del carril, bordes     |  comando + motivo       |
  |    detectados y centro           |  SEÑAL + barra conf.    |
  |  - cajas de YOLO (personas,      |  CARRIL + barra error   |
  |    obstáculos, señales)          |  ZONAS de obstáculos    |
  |  - líneas de las 3 zonas         |  NUBE / pendientes      |
  |                                  |  FPS / latencia / UDP   |
  +----------------------------------+-------------------------+

También contiene el Calibrador: ventanas con trackbars para ajustar en
vivo el carril (HSV, Canny, ROI, control PD) y los obstáculos. Los valores
se guardan en config.json con la tecla G o con el control "GUARDAR".

Nota: cv2.putText no dibuja acentos ni la "ñ"; todo texto pasa por texto_seguro().
"""

import unicodedata
from dataclasses import dataclass, field

import cv2
import numpy as np

NOMBRE_VENTANA = "VisionCar - Percibir / Razonar / Actuar"
FUENTE = cv2.FONT_HERSHEY_SIMPLEX

# Colores BGR
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
AZUL = (220, 120, 40)

COLOR_ESTADO = {
    "SIGUIENDO_CARRIL": VERDE,
    "IGNORANDO_ALTO": (90, 160, 60),
    "DETENIDO_POR_ALTO": ROJO,
    "PREPARANDO_GIRO": NARANJA,
    "GIRANDO": NARANJA,
    "RETROCEDIENDO": MORADO,
    "DETENIDO_SEGURIDAD": ROJO,
    "MANUAL": AZUL,
}

COLOR_TIPO = {"persona": ROJO, "obstaculo": CIAN, "senal": MORADO}


def texto_seguro(texto):
    """Quita acentos y caracteres que OpenCV no sabe dibujar (á -> a, ñ -> n)."""
    normal = unicodedata.normalize("NFKD", str(texto))
    return normal.encode("ascii", "ignore").decode("ascii")


def poner_texto(img, texto, x, y, escala=0.45, color=BLANCO, grosor=1):
    cv2.putText(img, texto_seguro(texto), (int(x), int(y)), FUENTE, escala, color, grosor, cv2.LINE_AA)


def partir_en_lineas(texto, ancho_max, escala=0.45):
    """Divide un texto largo en varias líneas que quepan en ancho_max píxeles."""
    palabras = texto_seguro(texto).split()
    lineas, actual = [], ""
    for palabra in palabras:
        prueba = (actual + " " + palabra).strip()
        if cv2.getTextSize(prueba, FUENTE, escala, 1)[0][0] <= ancho_max or not actual:
            actual = prueba
        else:
            lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    return lineas


def barra(img, x, y, ancho, alto, fraccion, color, centrada=False):
    """Barra horizontal. centrada=True dibuja desde el centro (para el error -1..1)."""
    cv2.rectangle(img, (x, y), (x + ancho, y + alto), GRIS_OSCURO, cv2.FILLED)
    if centrada:
        medio = x + ancho // 2
        fin = int(medio + max(-1.0, min(1.0, fraccion)) * ancho / 2)
        cv2.rectangle(img, (min(medio, fin), y), (max(medio, fin), y + alto), color, cv2.FILLED)
        cv2.line(img, (medio, y - 2), (medio, y + alto + 2), BLANCO, 1)
    else:
        fin = int(x + max(0.0, min(1.0, fraccion)) * ancho)
        cv2.rectangle(img, (x, y), (fin, y + alto), color, cv2.FILLED)


@dataclass
class DatosPantalla:
    """Todo lo que la interfaz necesita para dibujar un cuadro."""
    decision: object = None                 # decision.Decision
    detecciones: list = field(default_factory=list)
    senales: list = field(default_factory=list)
    senal_vista: object = None              # Deteccion o None
    senal_confirmada: object = None         # (clase, conf) o None
    modelo_senales: str = ""                # mensaje del detector de señales
    modelo_senales_ok: bool = False
    carril: object = None                   # ResultadoCarril
    obstaculos: object = None               # ResultadoObstaculos
    zonas_ocupadas: dict = field(default_factory=dict)
    fps: float = 0.0
    latencia_ms: object = None
    enviados: int = 0
    acks: int = 0
    perdidos: int = 0
    carrito_responde: bool = False
    vigilante: bool = False
    emergencia: bool = False
    pausado: bool = False
    sin_auto: bool = False
    video_ok: bool = True
    brillo: float = 0.0
    nitidez: float = 0.0
    nube_activa: bool = False
    nube_conectada: bool = False
    nube_mensaje: str = ""
    nube_pendientes: int = 0
    modo_remoto: str = "?"


class Interfaz:
    def __init__(self, cfg_interfaz, cfg_zonas, ancho_video, alto_video):
        self.escala = int(cfg_interfaz["escala_video"])
        self.ancho_panel = int(cfg_interfaz["ancho_panel"])
        self.cfg_zonas = cfg_zonas
        self.ancho_video = ancho_video
        self.alto_video = alto_video
        cv2.namedWindow(NOMBRE_VENTANA, cv2.WINDOW_AUTOSIZE)

    def mostrar(self, cuadro, datos):
        vista = self._dibujar_video(cuadro, datos)
        panel = self._dibujar_panel(datos, vista.shape[0])
        cv2.imshow(NOMBRE_VENTANA, np.hstack([vista, panel]))

    # ------------------------------------------------------------------
    # Video con anotaciones
    # ------------------------------------------------------------------
    def _dibujar_video(self, cuadro, datos):
        e = self.escala
        ancho, alto = self.ancho_video * e, self.alto_video * e
        if cuadro is None:
            vista = np.zeros((alto, ancho, 3), np.uint8)
        else:
            vista = cv2.resize(cuadro, (ancho, alto), interpolation=cv2.INTER_LINEAR)

        # Obstáculos de OpenCV (rojo semitransparente)
        obs = datos.obstaculos
        if obs is not None and obs.mascara is not None:
            y_roi = obs.y_inicio_roi * e
            mascara = cv2.resize(obs.mascara, (ancho, alto - y_roi), interpolation=cv2.INTER_NEAREST)
            region = vista[y_roi:, :]
            capa = region.copy()
            capa[mascara > 0] = ROJO
            cv2.addWeighted(capa, 0.35, region, 0.65, 0, dst=region)

        # Carril: ROI, bordes, centro y centro deseado
        carril = datos.carril
        if carril is not None and cuadro is not None:
            y_roi = carril.y_inicio_roi * e
            cv2.rectangle(vista, (0, y_roi), (ancho - 1, alto - 1), AMARILLO, 1)
            poner_texto(vista, "ROI carril", 6, y_roi - 6, 0.45, AMARILLO)
            cv2.line(vista, (ancho // 2, y_roi), (ancho // 2, alto), GRIS, 1)
            for y, x_izq, x_der, centro in carril.puntos:
                if x_izq is not None:
                    cv2.circle(vista, (int(x_izq * e), y * e), 5, AMARILLO, cv2.FILLED)
                if x_der is not None:
                    cv2.circle(vista, (int(x_der * e), y * e), 5, BLANCO, cv2.FILLED)
                cv2.circle(vista, (int(centro * e), y * e), 4, VERDE, cv2.FILLED)
            if carril.detectado:
                cx = int(carril.centro_x * e)
                cv2.line(vista, (cx, y_roi), (cx, alto), VERDE, 2)
                cv2.arrowedLine(vista, (ancho // 2, alto - 15), (cx, alto - 15), VERDE, 2, tipLength=0.3)

        # Líneas de las tres zonas
        x_izq = int(ancho * self.cfg_zonas["limite_izq"])
        x_der = int(ancho * self.cfg_zonas["limite_der"])
        for x in (x_izq, x_der):
            cv2.line(vista, (x, 0), (x, alto), GRIS_OSCURO, 1, cv2.LINE_AA)
        for zona, xc in (("IZQ", x_izq // 2), ("CENTRO", (x_izq + x_der) // 2), ("DER", (x_der + ancho) // 2)):
            ocupada = datos.zonas_ocupadas.get(zona, False)
            poner_texto(vista, zona, xc - 25, 22, 0.55, ROJO if ocupada else BLANCO, 2)

        # Cajas de YOLO (COCO + señales)
        for d in list(datos.detecciones) + list(datos.senales):
            x1, y1, x2, y2 = (v * e for v in d.caja)
            color = COLOR_TIPO.get(d.tipo, BLANCO)
            cv2.rectangle(vista, (x1, y1), (x2, y2), color, 2)
            etiqueta = f"{d.clase} {d.confianza:.2f} {d.area:.0%}"
            (tw, th), _ = cv2.getTextSize(etiqueta, FUENTE, 0.5, 1)
            y_texto = max(y1, th + 6)
            cv2.rectangle(vista, (x1, y_texto - th - 6), (x1 + tw + 6, y_texto), color, cv2.FILLED)
            poner_texto(vista, etiqueta, x1 + 3, y_texto - 4, 0.5, (0, 0, 0), 1)

        # Avisos grandes
        if not datos.video_ok:
            poner_texto(vista, "SIN VIDEO", ancho // 2 - 110, alto // 2, 1.3, ROJO, 3)
        if not datos.modelo_senales_ok:
            poner_texto(vista, "sin modelo de senales", 8, 48, 0.6, AMARILLO, 2)
        if datos.emergencia:
            cv2.rectangle(vista, (0, 0), (ancho - 1, alto - 1), ROJO, 8)
            poner_texto(vista, "PARO DE EMERGENCIA", ancho // 2 - 190, alto // 2 + 50, 1.0, ROJO, 3)
        return vista

    # ------------------------------------------------------------------
    # Panel de información
    # ------------------------------------------------------------------
    def _dibujar_panel(self, datos, alto):
        w = self.ancho_panel
        panel = np.full((alto, w, 3), FONDO_PANEL, np.uint8)
        m = 12
        y = 22
        d = datos.decision

        poner_texto(panel, "VISIONCAR", m, y, 0.6, BLANCO, 2)
        if d is not None:
            poner_texto(panel, f"modo {d.modo}", w - 120, y, 0.45, AZUL if d.modo == "manual" else GRIS)
        y += 10

        # --- Estado (caja de color) y comando ---
        estado = d.estado if d else "-"
        color = COLOR_ESTADO.get(estado, GRIS)
        cv2.rectangle(panel, (m, y), (w - m, y + 36), color, cv2.FILLED)
        tam = cv2.getTextSize(estado, FUENTE, 0.65, 2)[0]
        poner_texto(panel, estado, (w - tam[0]) // 2, y + 25, 0.65, BLANCO, 2)
        y += 54
        if d is not None:
            poner_texto(panel, f"Comando: {d.comando}", m, y, 0.48, BLANCO)
            y += 18
            for linea in partir_en_lineas(d.motivo, w - 2 * m, 0.42)[:2]:
                poner_texto(panel, linea, m, y, 0.42, GRIS)
                y += 16
            poner_texto(panel, f"Regla {d.regla}  |  nube: {d.accion_nube}", m, y, 0.4, GRIS)
            y += 8
        y = self._separador(panel, y)

        # --- Señal ---
        if not datos.modelo_senales_ok:
            poner_texto(panel, f"Senal: {datos.modelo_senales}", m, y, 0.45, AMARILLO)
            y += 20
        else:
            vista = datos.senal_vista
            nombre = vista.clase if vista else "ninguna"
            conf = vista.confianza if vista else 0.0
            poner_texto(panel, f"Senal vista: {nombre}", m, y, 0.45)
            poner_texto(panel, f"{conf:.2f}", w - 50, y, 0.45)
            y += 8
            barra(panel, m, y, w - 2 * m, 10, conf, MORADO if conf else GRIS_OSCURO)
            y += 26
            conf_txt = (f"CONFIRMADA: {datos.senal_confirmada[0]} ({datos.senal_confirmada[1]:.2f})"
                        if datos.senal_confirmada else "Confirmada: -")
            poner_texto(panel, conf_txt, m, y, 0.42, VERDE if datos.senal_confirmada else GRIS)
            y += 8
        y = self._separador(panel, y)

        # --- Carril ---
        carril = datos.carril
        if carril is not None and carril.detectado:
            poner_texto(panel, f"Carril: OK  error {carril.error:+.2f}", m, y, 0.45, VERDE)
        else:
            poner_texto(panel, "Carril: NO DETECTADO", m, y, 0.45, ROJO)
        y += 8
        barra(panel, m, y, w - 2 * m, 10, carril.error if carril else 0.0, VERDE, centrada=True)
        y += 18
        y = self._separador(panel, y)

        # --- Zonas ---
        ancho_caja = (w - 2 * m - 10) // 3
        for i, zona in enumerate(("IZQ", "CENTRO", "DER")):
            xa = m + i * (ancho_caja + 5)
            ocupada = datos.zonas_ocupadas.get(zona, False)
            cv2.rectangle(panel, (xa, y - 12), (xa + ancho_caja, y + 16), ROJO if ocupada else VERDE, cv2.FILLED)
            poner_texto(panel, zona + (" X" if ocupada else ""), xa + 6, y + 7, 0.42, BLANCO)
        y += 22
        y = self._separador(panel, y)

        # --- Nube ---
        if not datos.nube_activa:
            poner_texto(panel, "Nube: desactivada (--sin-nube)", m, y, 0.45, GRIS)
        else:
            color_nube = VERDE if datos.nube_conectada else ROJO
            poner_texto(panel, "Nube: " + ("conectada" if datos.nube_conectada else "SIN CONEXION"), m, y, 0.45, color_nube)
            poner_texto(panel, f"control: {datos.modo_remoto}", w - 125, y, 0.42, GRIS)
            y += 18
            poner_texto(panel, f"Pendientes: {datos.nube_pendientes}", m, y, 0.45,
                        AMARILLO if datos.nube_pendientes else BLANCO)
            y += 16
            poner_texto(panel, datos.nube_mensaje[:40], m, y, 0.38, GRIS)
        y += 8
        y = self._separador(panel, y)

        # --- Rendimiento y carrito ---
        poner_texto(panel, f"FPS: {datos.fps:5.1f}", m, y, 0.5)
        if datos.sin_auto:
            texto_lat, color_lat = "Latencia: (sin auto)", GRIS
        elif datos.latencia_ms is None:
            texto_lat, color_lat = "Latencia: --", AMARILLO
        else:
            texto_lat, color_lat = f"Latencia: {datos.latencia_ms:.0f} ms", BLANCO
        poner_texto(panel, texto_lat, m + 120, y, 0.5, color_lat)
        y += 18
        poner_texto(panel, f"UDP env {datos.enviados}  ACK {datos.acks}  perd {datos.perdidos}", m, y, 0.4, GRIS)
        y += 18
        if datos.sin_auto:
            texto, color = "MODO SIN AUTO (no se envia)", AMARILLO
        elif datos.pausado:
            texto, color = "ENVIO PAUSADO (prueba fail-safe)", AMARILLO
        elif datos.vigilante:
            texto, color = "VIGILANTE: ciclo trabado -> DETENER", ROJO
        elif not datos.carrito_responde:
            texto, color = "Carrito no responde (IP o CLAVE?)", ROJO
        else:
            texto, color = "Carrito responde ACK", VERDE
        poner_texto(panel, texto, m, y, 0.42, color)
        y += 16
        poner_texto(panel, f"Brillo {datos.brillo:.0f}  Nitidez {datos.nitidez:.0f}", m, y, 0.4, GRIS)

        # --- Controles (abajo) ---
        yc = alto - 38
        cv2.line(panel, (m, yc - 14), (w - m, yc - 14), GRIS_OSCURO, 1)
        poner_texto(panel, "ESPACIO: emergencia  Q: salir  P: pausa", m, yc, 0.38, GRIS)
        poner_texto(panel, "C: calibrar   G: guardar config", m, yc + 16, 0.38, GRIS)
        return panel

    def _separador(self, panel, y):
        cv2.line(panel, (12, y), (panel.shape[1] - 12, y), GRIS_OSCURO, 1)
        return y + 20


# ======================================================================
# Calibrador con trackbars
# ======================================================================
VENTANA_CARRIL = "Calibracion carril"
VENTANA_OBSTACULOS = "Calibracion obstaculos"

# (nombre, sección, clave, índice en la lista o None, máximo del control, factor)
#   valor_config = posición / factor
CONTROLES_CARRIL = [
    ("ROI inicio %", "carril", "roi_inicio_y", None, 95, 100),
    ("Blanco S max", "carril", "blanco_alto", 1, 255, 1),
    ("Blanco V min", "carril", "blanco_bajo", 2, 255, 1),
    ("Amar H min", "carril", "amarillo_bajo", 0, 179, 1),
    ("Amar H max", "carril", "amarillo_alto", 0, 179, 1),
    ("Amar S min", "carril", "amarillo_bajo", 1, 255, 1),
    ("Amar V min", "carril", "amarillo_bajo", 2, 255, 1),
    ("Canny bajo", "carril", "canny_bajo", None, 255, 1),
    ("Canny alto", "carril", "canny_alto", None, 255, 1),
    ("Min pix col", "carril", "min_pixeles_columna", None, 30, 1),
    ("Kp", "carril", "kp", None, 400, 1),
    ("Kd", "carril", "kd", None, 100, 1),
    ("Vel base", "carril", "velocidad_base", None, 255, 1),
]

CONTROLES_OBSTACULOS = [
    ("ROI inicio %", "opencv_obstaculos", "roi_inicio_y", None, 95, 100),
    ("Canny bajo", "opencv_obstaculos", "canny_bajo", None, 255, 1),
    ("Canny alto", "opencv_obstaculos", "canny_alto", None, 255, 1),
    ("Dilatacion", "opencv_obstaculos", "iteraciones_dilatacion", None, 6, 1),
    ("Area min cont", "opencv_obstaculos", "area_minima_contorno", None, 1000, 1),
    ("Ocupacion %", "opencv_obstaculos", "umbral_ocupacion_zona", None, 100, 100),
    ("Brillo min", "calidad_imagen", "brillo_minimo", None, 255, 1),
    ("Nitidez min", "calidad_imagen", "nitidez_minima", None, 500, 1),
    ("Conf YOLO %", "yolo", "confianza", None, 100, 100),
]

CONTROL_GUARDAR = "GUARDAR (1)"


class Calibrador:
    """
    Cada cuadro, leer() copia la posición de los trackbars al diccionario de
    configuración (los cambios se ven al instante). Para guardar en
    config.json: tecla G, o mover el control "GUARDAR (1)" a 1.
    """

    def __init__(self, config, funcion_guardar):
        self.config = config
        self.guardar = funcion_guardar
        self.abierto = False
        self._ventanas = ((VENTANA_CARRIL, CONTROLES_CARRIL), (VENTANA_OBSTACULOS, CONTROLES_OBSTACULOS))

    def abrir(self):
        for ventana, controles in self._ventanas:
            cv2.namedWindow(ventana, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(ventana, 440, 720)
            for nombre, seccion, clave, indice, maximo, factor in controles:
                valor = self.config[seccion][clave]
                if indice is not None:
                    valor = valor[indice]
                cv2.createTrackbar(nombre, ventana, min(int(round(valor * factor)), maximo), maximo, lambda _x: None)
        cv2.createTrackbar(CONTROL_GUARDAR, VENTANA_CARRIL, 0, 1, lambda _x: None)
        self.abierto = True

    def cerrar(self):
        for ventana, _ in self._ventanas:
            try:
                cv2.destroyWindow(ventana)
            except cv2.error:
                pass
        self.abierto = False

    def alternar(self):
        self.cerrar() if self.abierto else self.abrir()

    def leer(self):
        if not self.abierto:
            return
        for ventana, _ in self._ventanas:
            if cv2.getWindowProperty(ventana, cv2.WND_PROP_VISIBLE) < 1:
                self.cerrar()
                return
        for ventana, controles in self._ventanas:
            for nombre, seccion, clave, indice, _maximo, factor in controles:
                posicion = cv2.getTrackbarPos(nombre, ventana)
                valor = posicion if factor == 1 else posicion / factor
                if indice is None:
                    self.config[seccion][clave] = valor
                else:
                    self.config[seccion][clave][indice] = valor
        if cv2.getTrackbarPos(CONTROL_GUARDAR, VENTANA_CARRIL) == 1:
            self.guardar()
            cv2.setTrackbarPos(CONTROL_GUARDAR, VENTANA_CARRIL, 0)

    def mostrar_vistas(self, carril, obstaculos):
        """Muestra lo que 've' cada filtro: líneas del carril y máscara de obstáculos."""
        if not self.abierto:
            return
        if carril is not None and carril.mascara is not None:
            vista = carril.mascara.copy()
            for y, x_izq, x_der, centro in carril.puntos:
                yr = y - carril.y_inicio_roi
                if x_izq is not None:
                    cv2.circle(vista, (int(x_izq), yr), 4, (0, 0, 255), cv2.FILLED)
                if x_der is not None:
                    cv2.circle(vista, (int(x_der), yr), 4, (255, 0, 0), cv2.FILLED)
                cv2.circle(vista, (int(centro), yr), 3, (0, 255, 0), cv2.FILLED)
            cv2.imshow(VENTANA_CARRIL, cv2.resize(vista, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))
        else:
            cv2.imshow(VENTANA_CARRIL, np.zeros((120, 320, 3), np.uint8))
        if obstaculos is not None and obstaculos.mascara is not None:
            cv2.imshow(VENTANA_OBSTACULOS, obstaculos.mascara)
        else:
            cv2.imshow(VENTANA_OBSTACULOS, np.zeros((120, 320), np.uint8))
