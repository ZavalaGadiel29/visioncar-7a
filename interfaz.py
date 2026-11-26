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
COLOR_ASFALTO = (255, 160, 60)       # azul claro: máscara de asfalto

COLOR_ESTADO = {
    "SIGUIENDO_CARRIL": VERDE,
    "IGNORANDO_ALTO": (90, 160, 60),
    "DETENIDO_POR_ALTO": ROJO,
    "PREPARANDO_GIRO": NARANJA,
    "GIRANDO": NARANJA,
    "GIRO_DIRIGIDO": NARANJA,
    "RETROCEDIENDO": MORADO,
    "DETENIDO_SEGURIDAD": ROJO,
    "MANUAL": AZUL,
    "DETENIDO_OBSTACULO": ROJO,
}

# Texto que se muestra para algunos estados (los demás se muestran tal cual)
TEXTO_ESTADO = {"DETENIDO_OBSTACULO": "DETENIDO: OBSTACULO"}

COLOR_TIPO = {"persona": ROJO, "peaton": ROJO, "obstaculo": CIAN, "senal": MORADO}

# Colores de cada señal (iguales a probar_modelo.py) y de cada acción
COLOR_SENAL = {"alto": ROJO, "avanzar": VERDE, "izquierda": AMARILLO, "derecha": AZUL,
               "peaton": ROJO}
# Tecla D: color de cada candidato a guion según por qué se aceptó o rechazó
COLOR_DEBUG_GUIA = {"ok": VERDE, "ancho": MORADO, "contraste": ROJO, "verde": CIAN,
                    "orilla": BLANCO, "alineacion": NARANJA}
COLOR_ACCION = {"detener": ROJO, "avanzar": VERDE, "girar_izq": NARANJA, "girar_der": NARANJA}


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
    senal_activa: object = None             # alto/avanzar/izquierda/derecha/peaton o None
    altura_activacion: float = 0.20         # alto mínimo del recuadro para que una señal cuente
    personas_cada_n: int = 1                # el modelo de personas corre 1 de cada N cuadros
    fps_estimado: object = None             # FPS estimado con los dos modelos en cada cuadro
    aviso: str = ""                         # mensaje temporal (resultado de calibrar con C)
    ajuste: object = None                   # tecla K: líneas con kp, kd, suavizado, dif. ruedas
    debug_guia: bool = False                # tecla D: candidatos a guion y por qué se aceptan/rechazan
    avance: str = ""                        # modo pasos: "PASOS  PARADA" / "PASOS  PASO ..." ("" = continuo)
    fase_avance: str = ""                   # "PARADA" / "PASO" ("" = continuo)


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

        # Carril: máscara de asfalto, tramos, puntos amarillos, línea ajustada y objetivo
        carril = datos.carril
        if carril is not None and cuadro is not None:
            y_roi = carril.y_inicio_roi * e
            if carril.mascara_asfalto is not None:
                mascara = cv2.resize(carril.mascara_asfalto, (ancho, alto - y_roi), interpolation=cv2.INTER_NEAREST)
                region = vista[y_roi:, :]
                capa = region.copy()
                capa[mascara > 0] = COLOR_ASFALTO
                cv2.addWeighted(capa, 0.35, region, 0.65, 0, dst=region)
            if carril.mascara_guia is not None and np.any(carril.mascara_guia):
                # Guiones de la línea guía (detectados por forma) en amarillo
                guia = cv2.resize(carril.mascara_guia, (ancho, alto - y_roi), interpolation=cv2.INTER_NEAREST)
                vista[y_roi:][guia > 0] = AMARILLO
            cv2.rectangle(vista, (0, y_roi), (ancho - 1, alto - 1), AMARILLO, 1)
            cv2.line(vista, (ancho // 2, y_roi), (ancho // 2, alto), GRIS, 1)
            for y, x_izq, x_der, _centro in carril.puntos:          # tramo de asfalto elegido
                cv2.line(vista, (int(x_izq * e), y * e), (int(x_der * e), y * e), COLOR_ASFALTO, 2)
            curva = [(int(x * e), int(y * e)) for x, y in carril.curva_linea]
            if len(curva) >= 2:
                cv2.polylines(vista, [np.array(curva, np.int32)], False, NARANJA, 2)
            for x, y, nuevo in carril.puntos_amarillos:
                cv2.circle(vista, (int(x * e), int(y * e)), 5 if nuevo else 3,
                           AMARILLO if nuevo else GRIS, cv2.FILLED if nuevo else 1)
            for x, y in carril.objetivos:
                cv2.drawMarker(vista, (int(x * e), int(y * e)), VERDE, cv2.MARKER_CROSS, 10, 2)
            if carril.detectado:
                cx = int(carril.centro_x * e)
                cv2.line(vista, (cx, y_roi), (cx, alto), VERDE, 2)
                cv2.arrowedLine(vista, (ancho // 2, alto - 15), (cx, alto - 15), VERDE, 2, tipLength=0.3)
            for x, y, fuera in carril.puntos_orilla:                # orilla seguida (rojo)
                if fuera:
                    # Orilla fuera de imagen: triángulo rojo en el borde, apuntando hacia afuera
                    yc = int(y * e)
                    if x > ancho / (2 * e):
                        pts = [(ancho - 1, yc), (ancho - 15, yc - 9), (ancho - 15, yc + 9)]
                    else:
                        pts = [(0, yc), (14, yc - 9), (14, yc + 9)]
                    cv2.fillPoly(vista, [np.array(pts, np.int32)], ROJO)
                else:
                    cv2.circle(vista, (int(x * e), int(y * e)), 6, ROJO, cv2.FILLED)
            color_modo = {"LINEA": AMARILLO, "ASFALTO": COLOR_ASFALTO,
                          "ORILLA-D": BLANCO, "ORILLA-I": BLANCO,
                          "LINEA-PRED": NARANJA}.get(carril.modo, ROJO)
            texto = (f"{carril.modo}  error {carril.error:+.2f}" if carril.detectado
                     else ("PERDIDO" if carril.perdido else "SIN CARRIL"))
            if getattr(carril, "prediccion", False):
                texto += f"  {carril.segundos_sin_linea:.1f}s sin guia"
            elif getattr(carril, "linea_perdida", False):
                texto = f"LINEA PERDIDA  {carril.segundos_sin_linea:.1f}s"
            texto += f"  asf {carril.fraccion_asfalto:.0%}"
            if carril.bifurcacion:
                texto += f"  BIFURC->{carril.rama[:3].upper()}"
            fondo = vista[max(0, y_roi - 24):y_roi, 0:min(ancho, 430)]
            cv2.addWeighted(np.zeros_like(fondo), 0.55, fondo, 0.45, 0, dst=fondo)
            poner_texto(vista, texto, 6, y_roi - 6, 0.55, color_modo, 2)

        # Líneas de las tres zonas
        x_izq = int(ancho * self.cfg_zonas["limite_izq"])
        x_der = int(ancho * self.cfg_zonas["limite_der"])
        for x in (x_izq, x_der):
            cv2.line(vista, (x, 0), (x, alto), GRIS_OSCURO, 1, cv2.LINE_AA)
        for zona, xc in (("IZQ", x_izq // 2), ("CENTRO", (x_izq + x_der) // 2), ("DER", (x_der + ancho) // 2)):
            ocupada = datos.zonas_ocupadas.get(zona, False)
            poner_texto(vista, zona, xc - 25, 22, 0.55, ROJO if ocupada else BLANCO, 2)

        # Cajas de YOLO: personas (COCO) y señales (best.pt)
        for d in list(datos.detecciones) + list(datos.senales):
            x1, y1, x2, y2 = (v * e for v in d.caja)
            if d.tipo == "senal":
                # alto del recuadro vs. umbral: "CERCA" = ya puede activarse
                cerca = d.alto_rel > datos.altura_activacion
                color = COLOR_SENAL.get(d.clase, MORADO)
                grosor = 4 if d.clase == datos.senal_activa else 2
                etiqueta = f"{d.clase} {d.confianza:.0%} h{d.alto_rel:.0%}" + (" CERCA" if cerca else "")
            elif getattr(d, "motivo_ignorado", ""):
                # Peatón u objeto que NO frenó: se dice por qué (fuera de pista, confianza baja, 1 cuadro...)
                color = GRIS
                grosor = 1
                nombre = 'peaton' if d.tipo in ('persona', 'peaton') else d.clase
                etiqueta = f"{nombre} {d.confianza:.0%} IGNORADO: {d.motivo_ignorado}"
            elif getattr(d, "fuera_pista", False):
                color = GRIS
                grosor = 1
                etiqueta = f"{'peaton' if d.tipo in ('persona', 'peaton') else d.clase} fuera de pista"
            else:
                color = COLOR_TIPO.get(d.tipo, BLANCO)
                grosor = 2
                etiqueta = f"{'peaton' if d.tipo in ('persona', 'peaton') else d.clase} {d.confianza:.0%}"
            cv2.rectangle(vista, (x1, y1), (x2, y2), color, grosor)
            (tw, th), _ = cv2.getTextSize(etiqueta, FUENTE, 0.5, 1)
            y_texto = max(y1, th + 6)
            xt = max(0, min(x1, ancho - tw - 7))      # que la etiqueta no se salga por la derecha
            cv2.rectangle(vista, (xt, y_texto - th - 6), (xt + tw + 6, y_texto), color, cv2.FILLED)
            poner_texto(vista, etiqueta, xt + 3, y_texto - 4, 0.5, (0, 0, 0), 1)

        # Señal activa y acción actual (abajo a la izquierda, sobre fondo oscuro)
        d = datos.decision
        if d is not None:
            activa = datos.senal_activa or "ninguna"
            accion = f"{d.accion_nube.upper()}  ({d.comando})"
            fondo = vista[alto - 62:alto, 0:min(ancho, 420)]
            cv2.addWeighted(np.zeros_like(fondo), 0.55, fondo, 0.45, 0, dst=fondo)   # semitransparente
            poner_texto(vista, f"SENAL ACTIVA: {activa}", 10, alto - 38, 0.65,
                        COLOR_SENAL.get(activa, GRIS), 2)
            poner_texto(vista, f"ACCION: {accion}", 10, alto - 12, 0.65,
                        COLOR_ACCION.get(d.accion_nube, BLANCO), 2)
        if datos.sin_auto:
            poner_texto(vista, "SIN MOTORES", ancho - 150, alto - 12, 0.6, AMARILLO, 2)
        # Avance por pasos: modo y fase arriba a la derecha (PARADA naranja, PASO verde)
        if datos.fase_avance:
            etiqueta = f"PASOS  {datos.fase_avance}"
            (tw, th), _ = cv2.getTextSize(etiqueta, FUENTE, 0.7, 2)
            xt = ancho - tw - 12
            color = VERDE if datos.fase_avance == "PASO" else NARANJA
            cv2.rectangle(vista, (xt - 6, 6), (ancho - 6, 14 + th), (0, 0, 0), cv2.FILLED)
            poner_texto(vista, etiqueta, xt, 10 + th, 0.7, color, 2)

        # Detenido por obstáculo: se marca el "camino" (zona CENTRO dentro del ROI del carril)
        if d is not None and d.estado == "DETENIDO_OBSTACULO":
            y_roi = (datos.carril.y_inicio_roi * e) if datos.carril is not None else alto // 2
            cv2.rectangle(vista, (x_izq, y_roi), (x_der, alto - 1), ROJO, 3)
            texto = "DETENIDO: OBSTACULO"
            tam = cv2.getTextSize(texto, FUENTE, 1.0, 3)[0]
            xt, yt = (ancho - tam[0]) // 2, 70
            cv2.rectangle(vista, (xt - 10, yt - tam[1] - 12), (xt + tam[0] + 10, yt + 10), (0, 0, 0), cv2.FILLED)
            poner_texto(vista, texto, xt, yt, 1.0, ROJO, 3)

        # Avisos grandes
        if not datos.video_ok:
            poner_texto(vista, "SIN VIDEO", ancho // 2 - 110, alto // 2, 1.3, ROJO, 3)
        if not datos.modelo_senales_ok:
            poner_texto(vista, "sin modelo de senales", 8, 48, 0.6, AMARILLO, 2)
        if datos.ajuste:
            lineas = datos.ajuste
            ancho_caja = max(cv2.getTextSize(texto_seguro(l), FUENTE, 0.5, 1)[0][0] for l in lineas) + 16
            xa = max(0, ancho - ancho_caja - 6)
            fondo = vista[34:34 + 22 * len(lineas) + 8, xa:ancho - 6]
            cv2.addWeighted(np.zeros_like(fondo), 0.7, fondo, 0.3, 0, dst=fondo)
            for i, linea in enumerate(lineas):
                poner_texto(vista, linea, xa + 8, 54 + 22 * i, 0.5, CIAN, 1)
        if datos.debug_guia and datos.carril is not None:
            self._dibujar_debug_guia(vista, datos.carril.candidatos_guia, e, datos.carril.y_inicio_roi * e)
        if datos.aviso:
            for i, linea in enumerate(partir_en_lineas(datos.aviso, ancho - 20, 0.5)[:3]):
                poner_texto(vista, linea, 10, 75 + 20 * i, 0.5, VERDE, 2)
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
        texto_estado = (getattr(d, "texto_estado", "") if d else "") or TEXTO_ESTADO.get(estado, estado)
        cv2.rectangle(panel, (m, y), (w - m, y + 36), color, cv2.FILLED)
        tam = cv2.getTextSize(texto_estado, FUENTE, 0.65, 2)[0]
        poner_texto(panel, texto_estado, (w - tam[0]) // 2, y + 25, 0.65, BLANCO, 2)
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
            conf_txt = (f"ACTIVA: {datos.senal_confirmada[0]} ({datos.senal_confirmada[1]:.2f}) 3 seguidos"
                        if datos.senal_confirmada else f"Activa: -  (cerca = alto > {datos.altura_activacion:.0%})")
            poner_texto(panel, conf_txt, m, y, 0.42, VERDE if datos.senal_confirmada else GRIS)
            y += 8
        y = self._separador(panel, y)

        # --- Carril ---
        carril = datos.carril
        if carril is not None and carril.detectado:
            poner_texto(panel, f"Carril: {carril.modo}  error {carril.error:+.2f}", m, y, 0.45, VERDE)
        elif carril is not None and carril.perdido:
            poner_texto(panel, "Carril: PERDIDO (detener)", m, y, 0.45, ROJO)
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
        if datos.avance:
            poner_texto(panel, f"Avance: {datos.avance}"[:44], m, y, 0.45,
                        VERDE if datos.fase_avance == "PASO" else NARANJA)
            y += 18
        poner_texto(panel, f"FPS: {datos.fps:5.1f}", m, y, 0.5)

        if datos.sin_auto:
            texto_lat, color_lat = "Latencia: (sin motores)", GRIS
        elif datos.latencia_ms is None:
            texto_lat, color_lat = "Latencia: --", AMARILLO
        else:
            texto_lat, color_lat = f"Latencia: {datos.latencia_ms:.0f} ms", BLANCO
        poner_texto(panel, texto_lat, m + 120, y, 0.5, color_lat)
        y += 18
        poner_texto(panel, f"UDP env {datos.enviados}  ACK {datos.acks}  perd {datos.perdidos}", m, y, 0.4, GRIS)
        y += 18
        if datos.sin_auto:
            texto, color = "SIN MOTORES (no se envia UDP)", AMARILLO
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
        ritmo = ""
        if datos.personas_cada_n:
            estimado = f" (2 modelos ~{datos.fps_estimado:.0f} FPS)" if datos.fps_estimado else ""
            ritmo = f"  COCO 1/{datos.personas_cada_n}{estimado}"
        poner_texto(panel, f"Brillo {datos.brillo:.0f}  Nitidez {datos.nitidez:.0f}{ritmo}", m, y, 0.38, GRIS)

        # --- Controles (abajo) ---
        yc = alto - 38
        cv2.line(panel, (m, yc - 14), (w - m, yc - 14), GRIS_OSCURO, 1)
        poner_texto(panel, "ESPACIO: emergencia  Q: salir  P: pausa", m, yc, 0.38, GRIS)
        poner_texto(panel, "C: calibrar   G: guardar config   D: debug guia", m, yc + 16, 0.38, GRIS)
        return panel

    def _dibujar_debug_guia(self, vista, candidatos, e, y_roi):
        """Tecla D: TODOS los candidatos a guion, con color y texto según el motivo."""
        ancho = vista.shape[1]
        for c in candidatos:
            x1, y1, x2, y2 = (int(v * e) for v in c["caja"])
            color = COLOR_DEBUG_GUIA.get(c["motivo"], GRIS)
            cv2.rectangle(vista, (x1, y1), (x2, y2), color, 3 if c["motivo"] == "ok" else 1)
            etiqueta = f"{c['motivo']} {c['texto']}".strip()
            (tw, th), _ = cv2.getTextSize(etiqueta, FUENTE, 0.4, 1)
            xt = max(0, min(x1, ancho - tw - 4))
            yt = max(th + 2, y1 - 3)
            cv2.rectangle(vista, (xt, yt - th - 3), (xt + tw + 4, yt + 2), (0, 0, 0), cv2.FILLED)
            poner_texto(vista, etiqueta, xt + 2, yt, 0.4, color, 1)
        # Leyenda y conteo
        conteo = {}
        for c in candidatos:
            conteo[c["motivo"]] = conteo.get(c["motivo"], 0) + 1
        x = 8
        y = max(20, y_roi - 34)          # justo arriba del texto del modo (LINEA / ORILLA ...)
        fondo = vista[y - 14:y + 6, 0:ancho]
        cv2.addWeighted(np.zeros_like(fondo), 0.6, fondo, 0.4, 0, dst=fondo)
        for motivo, color in COLOR_DEBUG_GUIA.items():
            texto = f"{motivo}:{conteo.get(motivo, 0)}"
            poner_texto(vista, texto, x, y, 0.45, color, 1)
            x += cv2.getTextSize(texto, FUENTE, 0.45, 1)[0][0] + 12

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
    ("Asfalto S max", "carril", "asfalto_alto", 1, 255, 1),
    ("Asfalto V min", "carril", "asfalto_bajo", 2, 255, 1),
    ("Asfalto V max", "carril", "asfalto_alto", 2, 255, 1),
    ("Verde S min", "carril", "verde_bajo", 1, 255, 1),
    ("Offset carril %", "carril", "offset_carril", None, 50, 100),
    ("Min pix col", "carril", "min_pixeles_columna", None, 30, 1),
    ("Kp", "carril", "kp", None, 400, 1),
    ("Kd", "carril", "kd", None, 100, 1),
    ("Vel base", "carril", "velocidad_base", None, 255, 1),
    ("Suavizado %", "carril", "suavizado_error", None, 100, 100),
    ("Max dif ruedas", "carril", "max_diferencia_ruedas", None, 200, 1),
    ("Max cambio/cuadro", "carril", "max_cambio_por_cuadro", None, 100, 1),
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

    def sincronizar(self):
        """Pone los trackbars en los valores actuales del config (tras calibrar con C)."""
        if not self.abierto:
            return
        for ventana, controles in self._ventanas:
            for nombre, seccion, clave, indice, maximo, factor in controles:
                valor = self.config[seccion][clave]
                if indice is not None:
                    valor = valor[indice]
                cv2.setTrackbarPos(nombre, ventana, min(int(round(valor * factor)), maximo))

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
            y0 = carril.y_inicio_roi
            for y, x_izq, x_der, _centro in carril.puntos:
                cv2.line(vista, (int(x_izq), y - y0), (int(x_der), y - y0), (255, 128, 0), 1)
            for x, y, _nuevo in carril.puntos_amarillos:
                cv2.circle(vista, (int(x), int(y) - y0), 3, (0, 0, 255), cv2.FILLED)
            for x, y, _fuera in carril.puntos_orilla:
                xd = int(np.clip(x, 2, vista.shape[1] - 3))
                cv2.circle(vista, (xd, int(y) - y0), 4, (0, 0, 255), cv2.FILLED)
            for x, y in carril.objetivos:
                cv2.circle(vista, (int(x), int(y) - y0), 3, (0, 255, 0), cv2.FILLED)
            cv2.imshow(VENTANA_CARRIL, cv2.resize(vista, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))
        else:
            cv2.imshow(VENTANA_CARRIL, np.zeros((120, 320, 3), np.uint8))
        if obstaculos is not None and obstaculos.mascara is not None:
            cv2.imshow(VENTANA_OBSTACULOS, obstaculos.mascara)
        else:
            cv2.imshow(VENTANA_OBSTACULOS, np.zeros((120, 320), np.uint8))
