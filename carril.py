"""
carril.py
---------
Seguimiento de carril HÍBRIDO con OpenCV (sin redes neuronales).

La pista real: asfalto NEGRO (en la cámara gris oscuro), orillas BLANCAS,
zonas VERDES fuera del camino, ISLAS verdes con borde blanco que forman
bifurcaciones y una LÍNEA AMARILLA PUNTEADA como guía.

Hay dos modos; en cada cuadro se usa el primero que funcione:

A) "LINEA" (prioridad): línea amarilla punteada. Solo si
   "usar_linea_amarilla" es true (mientras la pista no tenga la línea, false).
   1. En la ROI (parte de abajo de la imagen) se busca amarillo en HSV.
   2. La ROI se corta en franjas; en cada franja se toma el segmento amarillo
      más cercano a donde se esperaba la línea -> un punto (x, y).
   3. Como la línea es punteada, se juntan los puntos de este cuadro con los
      de los últimos "memoria_linea_s" (0.5 s) y se ajusta una recta (o curva
      suave) x = f(y). Así se cubren los huecos entre rayas.
   4. Objetivo:  "carril_derecho" -> línea + offset_carril * ancho
                 "sobre_linea"    -> la línea misma (en "objetivo_linea", ~0.5)
   "deteccion_linea":
      "color" -> la línea se busca por color amarillo (amarillo_bajo/alto).
      "forma" -> LÍNEA GUÍA: la cámara la ve casi blanca, así que se busca
                 por forma (mascara_guia): mancha clara, alargada en
                 vertical/diagonal, de grosor guia_ancho_min..guia_ancho_max y
                 con ASFALTO a los dos lados. Sus guiones cuentan como camino
                 (no parten el tramo) y no son orilla.

B0) "ORILLA" (sin amarillo; el modo normal en la pista ancha):
   El camino es más ancho que la imagen, así que el centro del asfalto casi
   no cambia en las curvas. En su lugar se sigue la ORILLA BLANCA:
   1. En cada franja, desde el centro hacia la derecha, la primera transición
      asfalto -> blanco es la orilla derecha (y hacia la izquierda, la izquierda).
   2. Se quiere la orilla en una posición fija por franja
      ("objetivo_orilla_der" / "objetivo_orilla_izq", fracción del ancho; la
      tecla C los toma con el carrito centrado en una recta).
   3. Error por franja = (x_orilla - x_objetivo) / medio ancho. Si la orilla
      derecha se acerca al centro, el error es negativo -> gira a la izquierda.
   4. Lado: "lado_orilla" (derecha), o el de la señal izquierda/derecha
      activada en los últimos 4 s. Si la orilla elegida no se ve en la mayoría
      de las franjas se usa la otra; si ninguna, ASFALTO.
   5. Orilla "FUERA DE IMAGEN": si del centro al borde de la imagen todo es
      asfalto (sin blanco), la orilla está justo afuera: x = 1 + margen_fuera
      (derecha) o 0 - margen_fuera (izquierda). Cuenta como orilla vista, así
      el carrito se acerca a ella en vez de saltar a ASFALTO.

B) "ASFALTO" (respaldo, sin amarillo ni orillas):
   1. Máscara de camino: oscuro y poco saturado = asfalto. Verde saturado y
      blanco muy claro = fuera. Las orillas blancas SEPARAN tramos (por eso
      la isla con borde blanco parte el camino en dos).
   2. La ROI se corta en franjas. En cada franja se busca el tramo de asfalto
      CONECTADO con el de la franja de abajo (en la de más abajo, el tramo
      donde está el carrito o el más cercano). Su centro es el objetivo.
   3. Error = promedio ponderado de los centros (más peso abajo).

Común:
   - BIFURCACIÓN: una franja con dos tramos conectados al de abajo separados
     por una ISLA real: un hueco de al menos "min_ancho_isla" (15 %) del ancho
     que contiene verde o blanco (solo blanco si "isla_requiere_blanco"). Las manchas (huecos sin blanco y sin verde
     ancho) se cuentan como asfalto. Se toma el de "preferencia_temporal" (lo pone main.py si
     en los últimos 4 s se activó izquierda/derecha, o "recto" = el tramo más
     al centro si en los últimos 3 s se activó avanzar) o, si no hay,
     "preferencia_bifurcacion" de config.json.
   - ORILLA FORZADA (orilla_forzada, la pone main.py durante GIRO_DIRIGIDO):
     se sigue SOLO la orilla de ese lado y la guía NO manda (pero se reporta
     linea_visible / error_linea para saber cuándo regresar a LINEA).
   - CARRIL PERDIDO: sin amarillo y menos de "min_fraccion_asfalto" (15 %) de
     la ROI es asfalto -> resultado.perdido = True (decision.py DETIENE).
   - ERROR = (objetivo - centro deseado) / (medio ancho), de -1 a +1.

calibrar(cuadro) (tecla C): toma un parche del centro-abajo para los umbrales
del asfalto y, si hay amarillo visible en la ROI, también los del amarillo.

Además está el CONTROL PD, que convierte el error en velocidad de cada
rueda para el comando MOVER.
"""

import time
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

MODO_LINEA = "LINEA"
MODO_LINEA_PREDICHA = "LINEA-PRED"      # solo_linea: la guía no se ve, se sigue la última recta
MODO_LINEA_PERDIDA = "LINEA PERDIDA"    # solo_linea: más de tiempo_prediccion_linea_s sin guía
MODO_ASFALTO = "ASFALTO"
MODO_ORILLA = {"derecha": "ORILLA-D", "izquierda": "ORILLA-I"}


# ======================================================================
# Máscaras de líneas (también las usa percepcion_opencv para NO confundir
# las líneas del carril con obstáculos)
# ======================================================================
def _kernel_impar(k):
    k = max(1, int(k))
    return k if k % 2 == 1 else k + 1


def _hsv(imagen, cfg):
    suave = cv2.GaussianBlur(imagen, (_kernel_impar(cfg["desenfoque_kernel"]),) * 2, 0)
    return suave, cv2.cvtColor(suave, cv2.COLOR_BGR2HSV)


def _rango(hsv, bajo, alto):
    return cv2.inRange(hsv, np.array(bajo, np.uint8), np.array(alto, np.uint8))


def _usa_forma(cfg):
    return str(cfg.get("deteccion_linea", "color")).strip().lower() == "forma"


# Motivos de cada candidato a guion (la tecla D los dibuja con estos nombres)
GUIA_OK = "ok"
GUIA_ANCHO = "ancho"
GUIA_VERDE = "verde"
GUIA_CONTRASTE = "contraste"
GUIA_ORILLA = "orilla"
GUIA_ALINEACION = "alineacion"


def mascara_guia(hsv, verde, blanca, cfg, linea_previa=None, candidatos=None):
    """
    Línea guía detectada por FORMA + CONTRASTE + ALINEACIÓN (no por color).

    1. Candidato = mancha CLARA (V >= guia_v_min) de ANCHO entre
       guia_ancho_min y guia_ancho_max del ancho de la imagen (ancho =
       mediana del ancho por fila; sin importar la orientación: de lejos los
       guiones se ven como rayas horizontales) y casi sin verde dentro
       (<= guia_max_verde_dentro).
    2. CONTRASTE local: en franjas de guia_franja_lado_px a la izquierda y a
       la derecha (dejando guia_hueco_px por el desenfoque) el brillo V
       promedio es al menos guia_contraste_min menor que el de la mancha, y
       casi sin verde (<= guia_max_verde_lados). NO se usa la máscara de
       asfalto (con luz, el asfalto de un lado se ve más claro).
    3. ORILLA: se descarta si hay una orilla blanca REAL (blanco con verde
       del lado de afuera) a menos de guia_distancia_orilla del ancho.
    4. ALINEACIÓN: se aceptan los candidatos que quedan en una recta
       (tolerancia guia_tolerancia_recta del ancho, pendiente |dx/dy| <=
       guia_pendiente_max) con al menos guia_min_alineados en franjas
       distintas (franjas_linea). O 1 solo si cerca de la línea de los
       últimos memoria_linea_s (linea_previa) a menos de
       guia_tolerancia_memoria del ancho.

    linea_previa: (coef, y0) = ajuste x = f(y) en coordenadas de la imagen y
    el inicio de esta región; None si no hay línea reciente.
    candidatos: si es una lista, se llena con dicts (caja, cx, cy, motivo,
    texto) en coordenadas de esta región, para la tecla D.
    Devuelve una máscara uint8 (255 = guía aceptada).
    """
    alto, ancho = verde.shape[:2]
    guia = np.zeros((alto, ancho), np.uint8)
    v = hsv[..., 2]
    claro = (v >= int(cfg.get("guia_v_min", 150))).astype(np.uint8) * 255
    claro = cv2.morphologyEx(claro, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, etiquetas, stats, centros = cv2.connectedComponentsWithStats(claro, connectivity=8)
    if candidatos is None:
        candidatos = []
    if n <= 1:
        return guia

    ancho_min = float(cfg.get("guia_ancho_min", 0.02)) * ancho
    ancho_max = float(cfg.get("guia_ancho_max", 0.22)) * ancho
    area_min = int(cfg.get("guia_area_min", 20))
    max_verde_dentro = float(cfg.get("guia_max_verde_dentro", 0.05))
    lado = max(1, int(cfg.get("guia_franja_lado_px", 10)))
    hueco = max(0, int(cfg.get("guia_hueco_px", 2)))
    contraste_min = float(cfg.get("guia_contraste_min", 40))
    max_verde_lados = float(cfg.get("guia_max_verde_lados", 0.02))
    dist_orilla = float(cfg.get("guia_distancia_orilla", 0.08)) * ancho
    es_verde = verde > 0
    v = v.astype(np.float32)

    # Orilla blanca REAL: blanco que tiene verde pegado (a orilla_ventana_px)
    ventana = max(2, int(cfg.get("orilla_ventana_px", 10)))
    verde_cerca = cv2.dilate(verde, np.ones((1, 2 * ventana + 1), np.uint8))
    orilla_real = cv2.bitwise_and(blanca, verde_cerca) > 0

    lista = []
    for k in range(1, n):
        x, y, w, h, area = (int(s) for s in stats[k])
        if area < area_min:
            continue
        sub = etiquetas[y:y + h, x:x + w] == k
        cand = {"caja": (x, y, x + w, y + h), "cx": float(centros[k][0]), "cy": float(centros[k][1]),
                "k": k, "motivo": GUIA_OK, "texto": ""}
        lista.append(cand)

        anchos = sub.sum(axis=1)
        ancho_px = float(np.median(anchos[anchos > 0]))
        if not ancho_min <= ancho_px <= ancho_max:
            cand["motivo"], cand["texto"] = GUIA_ANCHO, f"{ancho_px / ancho:.0%}"
            continue
        if es_verde[y:y + h, x:x + w][sub].mean() > max_verde_dentro:
            cand["motivo"], cand["texto"] = GUIA_VERDE, "dentro"
            continue

        # Contraste por fila: franjas a la izquierda y a la derecha
        v_mancha = float(v[y:y + h, x:x + w][sub].mean())
        suma_i = suma_d = 0.0
        n_i = n_d = 0
        verde_px = total_px = 0
        for fila in range(h):
            cols = np.flatnonzero(sub[fila])
            if cols.size == 0:
                continue
            yy = y + fila
            xl, xr = x + int(cols[0]), x + int(cols[-1])
            izq = slice(max(0, xl - hueco - lado), max(0, xl - hueco))
            der = slice(min(ancho, xr + 1 + hueco), min(ancho, xr + 1 + hueco + lado))
            vi, vd = v[yy, izq], v[yy, der]
            suma_i += float(vi.sum())
            n_i += vi.size
            suma_d += float(vd.sum())
            n_d += vd.size
            verde_px += int(es_verde[yy, izq].sum() + es_verde[yy, der].sum())
            total_px += vi.size + vd.size
        if n_i == 0 or n_d == 0:
            cand["motivo"], cand["texto"] = GUIA_CONTRASTE, "borde"
            continue
        c_izq, c_der = v_mancha - suma_i / n_i, v_mancha - suma_d / n_d
        cand["texto"] = f"c {c_izq:.0f}/{c_der:.0f}"
        if total_px and verde_px / total_px > max_verde_lados:
            cand["motivo"], cand["texto"] = GUIA_VERDE, "al lado"
            continue
        if min(c_izq, c_der) < contraste_min:
            cand["motivo"] = GUIA_CONTRASTE
            continue

        # ¿Pegado a una orilla blanca real?
        xa, xb = int(max(0, x - dist_orilla)), int(min(ancho, x + w + dist_orilla))
        zona = orilla_real[y:y + h, xa:xb].copy()
        zona[:, x - xa:x - xa + w][sub] = False          # la mancha misma no cuenta
        if zona.any():
            cand["motivo"], cand["texto"] = GUIA_ORILLA, ""
            continue

    # --- Alineación: los que pasaron todo lo anterior
    buenos = [c for c in lista if c["motivo"] == GUIA_OK]
    n_franjas = max(1, int(cfg.get("franjas_linea", 8)))
    alto_f = max(1.0, alto / n_franjas)
    for c in buenos:
        c["franja"] = int(min(n_franjas - 1, (alto - 1 - c["cy"]) // alto_f))
    tolerancia = float(cfg.get("guia_tolerancia_recta", 0.05)) * ancho
    pendiente_max = float(cfg.get("guia_pendiente_max", 2.0))
    min_alineados = max(2, int(cfg.get("guia_min_alineados", 2)))

    mejor = []
    for i in range(len(buenos)):
        for j in range(i + 1, len(buenos)):
            a, b = buenos[i], buenos[j]
            if a["franja"] == b["franja"]:
                continue
            pendiente = (b["cx"] - a["cx"]) / (b["cy"] - a["cy"]) if b["cy"] != a["cy"] else np.inf
            if abs(pendiente) > pendiente_max:
                continue
            en_recta = [c for c in buenos
                        if abs(a["cx"] + pendiente * (c["cy"] - a["cy"]) - c["cx"]) <= tolerancia]
            if len({c["franja"] for c in en_recta}) >= min_alineados and len(en_recta) > len(mejor):
                mejor = en_recta
    aceptados = {id(c) for c in mejor}

    if not mejor and linea_previa is not None:
        # Línea reciente: basta 1 candidato cerca de donde estaba la línea
        coef, y0 = linea_previa
        tol_mem = float(cfg.get("guia_tolerancia_memoria", 0.1)) * ancho
        for c in buenos:
            if abs(float(np.polyval(coef, c["cy"] + y0)) - c["cx"]) <= tol_mem:
                aceptados.add(id(c))
                c["texto"] += " (memoria)"

    for c in buenos:
        if id(c) in aceptados:
            guia[etiquetas == c["k"]] = 255
        else:
            c["motivo"] = GUIA_ALINEACION
            c["texto"] = ""
    for c in lista:
        c.pop("k", None)
        c.pop("franja", None)
    candidatos.extend(lista)
    return guia


def mascaras_lineas(imagen, cfg, solo_color=False):
    """
    Devuelve (mascara_borde, mascara_amarilla) de una imagen BGR.

    mascara_borde: líneas blancas (o bordes de Canny) SIN la línea amarilla.
    mascara_amarilla: la línea amarilla (por color) y, con deteccion_linea
    = "forma", también la línea guía (para que el detector de obstáculos
    no la confunda con un objeto).
    solo_color=True ignora Canny (lo usa el detector de obstáculos).
    """
    suave, hsv = _hsv(imagen, cfg)
    amarilla = _rango(hsv, cfg["amarillo_bajo"], cfg["amarillo_alto"])
    blanca = _rango(hsv, cfg["blanco_bajo"], cfg["blanco_alto"])
    if _usa_forma(cfg):
        verde = _rango(hsv, cfg["verde_bajo"], cfg["verde_alto"])
        amarilla = cv2.bitwise_or(amarilla, mascara_guia(hsv, verde, blanca, cfg))

    metodo = "color" if solo_color else cfg.get("metodo", "ambos")
    if metodo == "color":
        borde = blanca
    else:
        gris = cv2.cvtColor(suave, cv2.COLOR_BGR2GRAY)
        canny = cv2.Canny(gris, int(cfg["canny_bajo"]), int(cfg["canny_alto"]))
        canny = cv2.dilate(canny, np.ones((3, 3), np.uint8), iterations=1)
        borde = canny if metodo == "canny" else cv2.bitwise_or(blanca, canny)

    amarilla_gruesa = cv2.dilate(amarilla, np.ones((5, 5), np.uint8), iterations=1)
    borde = cv2.bitwise_and(borde, cv2.bitwise_not(amarilla_gruesa))
    return borde, amarilla


def mascaras_pista(roi, cfg, con_guia=True, linea_previa=None, candidatos=None):
    """
    Máscaras de la pista en la ROI: (camino, amarilla, blanca, verde, guia).

    camino = asfalto (o línea amarilla / guía encima del asfalto), sin verde
    y sin blanco. El blanco se engorda 1 px para que las orillas (y el borde
    de las islas) siempre separen tramos.
    guia = línea guía por forma (solo con deteccion_linea = "forma" y
    con_guia=True). Sus guiones se QUITAN del blanco y cuentan como camino:
    no parten el tramo, no crean bifurcación y no parecen orilla.
    linea_previa / candidatos: ver mascara_guia.
    """
    _, hsv = _hsv(roi, cfg)
    amarilla = _rango(hsv, cfg["amarillo_bajo"], cfg["amarillo_alto"])
    amarilla = cv2.morphologyEx(amarilla, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    blanca = _rango(hsv, cfg["blanco_bajo"], cfg["blanco_alto"])
    blanca = cv2.bitwise_and(blanca, cv2.bitwise_not(amarilla))
    verde = _rango(hsv, cfg["verde_bajo"], cfg["verde_alto"])
    asfalto = _rango(hsv, cfg["asfalto_bajo"], cfg["asfalto_alto"])

    # La línea amarilla está SOBRE el asfalto: cuenta como camino para que no
    # parta el carril en dos tramos. Lo mismo con los guiones de la guía.
    sobre_asfalto = cv2.dilate(amarilla, np.ones((5, 5), np.uint8))
    guia = np.zeros_like(asfalto)
    if con_guia and _usa_forma(cfg):
        guia = mascara_guia(hsv, verde, blanca, cfg, linea_previa, candidatos)
        guia_gruesa = cv2.dilate(guia, np.ones((5, 5), np.uint8))
        blanca = cv2.bitwise_and(blanca, cv2.bitwise_not(guia_gruesa))
        sobre_asfalto = cv2.bitwise_or(sobre_asfalto, guia_gruesa)
    camino = cv2.bitwise_or(asfalto, sobre_asfalto)
    fuera = cv2.bitwise_or(verde, cv2.dilate(blanca, np.ones((3, 3), np.uint8)))
    camino = cv2.bitwise_and(camino, cv2.bitwise_not(fuera))
    camino = cv2.morphologyEx(camino, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    return camino, amarilla, blanca, verde, guia


def _angulo_curva(curva):
    """
    Ángulo (rad) de la línea ajustada entre su punto más bajo y el más alto de la ROI.
    0 = recta hacia adelante; + = hacia adelante se va a la DERECHA (curva a la derecha).
    """
    if len(curva) < 2:
        return 0.0
    (x_abajo, y_abajo), (x_arriba, y_arriba) = max(curva, key=lambda p: p[1]), min(curva, key=lambda p: p[1])
    if y_abajo - y_arriba < 1:
        return 0.0
    return float(np.arctan2(x_arriba - x_abajo, y_abajo - y_arriba))


def _segmentos(columnas_bool):
    """Lista de (inicio, fin) [fin exclusivo] de los grupos de True seguidos."""
    v = np.concatenate(([0], columnas_bool.astype(np.int8), [0]))
    cambios = np.flatnonzero(np.diff(v))
    return list(zip(cambios[::2], cambios[1::2]))


def _caja_sobre_guia(caja, carril, radio_px):
    """True si la parte de abajo del recuadro tapa la línea guía detectada en este cuadro."""
    if carril is None:
        return False
    x1, _y1, x2, y2 = caja
    # 1) Curva ajustada de la línea: ¿pasa entre x1 y x2 a la altura de los pies?
    curva = getattr(carril, "curva_linea", None) or []
    if curva:
        x_linea, _ = min(curva, key=lambda p: abs(p[1] - y2))
        if x1 - radio_px <= x_linea <= x2 + radio_px:
            return True
    # 2) Guiones de la guía (máscara de la ROI) dentro de la parte de abajo del recuadro
    guia = getattr(carril, "mascara_guia", None)
    if guia is not None and guia.size:
        y0 = int(carril.y_inicio_roi)
        ya = max(0, int(y2) - 4 * radio_px - y0)
        yb = min(guia.shape[0], int(y2) + 1 - y0)
        xa, xb = max(0, int(x1)), min(guia.shape[1], int(x2) + 1)
        if yb > ya and xb > xa and np.count_nonzero(guia[ya:yb, xa:xb]) >= 3:
            return True
    return False


def pies_en_camino(cuadro, caja, cfg_carril, radio_px=6, min_altura=0.5, carril=None):
    """
    ¿La detección está EN EL CAMINO? Devuelve (en_camino, motivo, forzado).
      - Borde inferior del recuadro pegado al borde de la imagen (figura muy
        cerca, los pies se salen de la imagen) -> en el camino (forzado).
      - Pies (punto medio del lado de ABAJO) arriba de min_altura -> lejos, NO.
      - Parte de abajo del recuadro sobre la línea guía -> en el camino (forzado).
        (La guía es blanca y la máscara de asfalto la excluye, por eso va aparte.)
      - Cuadrito de radio_px alrededor de los pies sobre asfalto -> en el camino.
    "forzado" = cuenta como en el carril sin revisar el ancho del carril.
    """
    alto, ancho = cuadro.shape[:2]
    x1, _y1, x2, y2 = caja
    if y2 >= alto - 3:
        return True, "pies fuera de la imagen (muy cerca)", True
    xp = int(round((x1 + x2) / 2.0))
    yp = int(min(max(y2, 0), alto - 1))
    if yp < min_altura * alto:
        return False, f"fuera de pista (pies arriba, y={yp / alto:.0%})", False
    if _caja_sobre_guia(caja, carril, radio_px):
        return True, "sobre la linea guia", True
    xa, xb = max(0, xp - radio_px), min(ancho, xp + radio_px + 1)
    ya, yb = max(0, yp - radio_px), min(alto, yp + radio_px + 1)
    if xb - xa < 3 or yb - ya < 3:
        return False, "fuera de pista (borde)", False
    camino = mascaras_pista(cuadro[ya:yb, xa:xb], cfg_carril, con_guia=False)[0]
    if np.count_nonzero(camino) >= max(3, int(0.1 * camino.size)):
        return True, "pies en asfalto", False
    return False, "fuera de pista (pies sin asfalto)", False


def pies_en_asfalto(cuadro, caja, cfg_carril, radio_px=6, min_altura=0.5, carril=None):
    """Versión que solo devuelve True/False (ver pies_en_camino)."""
    return pies_en_camino(cuadro, caja, cfg_carril, radio_px, min_altura, carril)[0]


# ======================================================================
# Resultado
# ======================================================================
@dataclass
class ResultadoCarril:
    detectado: bool = False
    perdido: bool = False              # sin amarillo y casi sin asfalto -> DETENER
    modo: str = "-"                    # "LINEA" / "ORILLA-D" / "ORILLA-I" / "ASFALTO" / "-"
    error: float = 0.0                 # -1 (objetivo a la izq.) ... +1 (a la der.)
    centro_x: float = 0.0              # objetivo en píxeles de la imagen
    x_izq: float = None                # tramo de asfalto (franja más baja)
    x_der: float = None
    ancho_carril: float = 0.0          # ancho del camino (px), para el peatón
    franjas_validas: int = 0
    fraccion_asfalto: float = 0.0      # fracción de la ROI que es camino
    bifurcacion: bool = False
    rama: str = ""                     # "izquierda"/"derecha" si hubo bifurcación
    y_inicio_roi: int = 0
    puntos: list = field(default_factory=list)          # (y, x_izq, x_der, centro) tramos de asfalto
    puntos_amarillos: list = field(default_factory=list)  # (x, y, es_de_este_cuadro)
    objetivos: list = field(default_factory=list)       # (x, y) objetivo por franja
    curva_linea: list = field(default_factory=list)     # (x, y) de la línea ajustada
    puntos_orilla: list = field(default_factory=list)   # (x, y, fuera_de_imagen) orilla seguida
    mascara_asfalto: object = None     # máscara binaria de camino en la ROI
    mascara_guia: object = None        # guiones de la línea guía (deteccion_linea = "forma")
    candidatos_guia: list = field(default_factory=list)  # tecla D: todos los candidatos y su motivo
    linea_visible: bool = False        # se vio la guía en ESTE cuadro (aunque no mande)
    puntos_linea_nuevos: int = 0       # franjas con guía en este cuadro
    error_linea: float = None          # error que daría seguir la guía (-1..1), o None
    orilla_forzada: str = None         # "izquierda"/"derecha" durante GIRO_DIRIGIDO
    prediccion: bool = False           # solo_linea: se sigue la posición PREDICHA de la guía
    linea_perdida: bool = False        # solo_linea: demasiado tiempo sin guía -> DETENER
    segundos_sin_linea: float = 0.0    # desde la última vez que se vio la guía (CSV: memoria_linea)
    angulo_linea: float = 0.0          # rad; + = la guía se va a la DERECHA hacia adelante (anticipación)
    mascara: object = None            # vista de colores de la ROI (calibración)
    descripcion: str = "sin datos"


# ======================================================================
# Detector
# ======================================================================
class DetectorCarril:
    def __init__(self, cfg_carril):
        self.cfg = cfg_carril
        self.preferencia_temporal = None   # "izquierda"/"derecha"/"recto" (main.py, señal reciente) o None
        self.orilla_forzada = None         # "izquierda"/"derecha": GIRO_DIRIGIDO, sigue SOLO esa orilla
        self._pred_objetivos = None        # solo_linea: objetivos de la última recta de la guía
        self._pred_t = None                # cuándo se vio la guía por última vez (para la predicción)
        self._pred_angulo = 0.0            # ángulo de esa última recta
        self._ref_abajo = None             # centro del tramo de abajo en el cuadro anterior (px)
        self._memoria = deque()            # (t, x, y) puntos amarillos recientes
        self._ultima_linea_t = None        # cuándo se vio amarillo por última vez
        self._coef_linea = None            # ajuste anterior x = f(y)

    def compensar_pausa(self, segundos):
        """Cuadros negros/vacíos ignorados: ese tiempo no cuenta como 'sin línea'."""
        if segundos <= 0:
            return
        if self._ultima_linea_t is not None:
            self._ultima_linea_t += segundos
        if self._pred_t is not None:
            self._pred_t += segundos
        self._memoria = deque((t + segundos, x, y) for t, x, y in self._memoria)

    def reiniciar(self):
        self._pred_objetivos = None
        self._pred_t = None
        self._ref_abajo = None
        self._memoria.clear()
        self._ultima_linea_t = None
        self._coef_linea = None

    # ------------------------------------------------------------------
    def procesar(self, cuadro, ahora=None):
        cfg = self.cfg
        if ahora is None:
            ahora = time.monotonic()
        alto, ancho = cuadro.shape[:2]
        r = ResultadoCarril()

        y0 = min(max(int(alto * cfg["roi_inicio_y"]), 0), alto - 8)
        r.y_inicio_roi = y0
        roi = cuadro[y0:, :]
        # Línea de los últimos memoria_linea_s: con ella basta 1 guion (ver mascara_guia)
        reciente = (self._ultima_linea_t is not None and self._coef_linea is not None
                    and ahora - self._ultima_linea_t <= cfg["memoria_linea_s"])
        r.candidatos_guia = []
        camino, amarilla, blanca, verde, guia = mascaras_pista(
            roi, cfg, linea_previa=(self._coef_linea, y0) if reciente else None,
            candidatos=r.candidatos_guia)
        for c in r.candidatos_guia:              # a coordenadas de la imagen
            x1, y1, x2, y2 = c["caja"]
            c["caja"] = (x1, y1 + y0, x2, y2 + y0)
            c["cy"] += y0
        r.mascara_asfalto = camino
        r.mascara_guia = guia
        r.fraccion_asfalto = float(np.count_nonzero(camino)) / camino.size

        vista = np.zeros_like(roi)
        vista[camino > 0] = (90, 90, 90)
        vista[verde > 0] = (0, 140, 0)
        vista[blanca > 0] = (255, 255, 255)
        vista[amarilla > 0] = (0, 220, 255)
        vista[guia > 0] = (0, 220, 255)
        r.mascara = vista

        # --- B) Asfalto: siempre se calcula (respaldo, bifurcaciones, pantalla)
        franjas_asf = self._franjas_asfalto(camino, blanca, verde, ancho, y0, r)

        # --- A) Línea (amarilla por color, o guía por forma)
        objetivos_linea = []
        if cfg.get("usar_linea_amarilla", False):
            fuente = guia if _usa_forma(cfg) else amarilla
            objetivos_linea = self._linea_amarilla(fuente, ancho, y0, ahora, franjas_asf, r)

        centro_deseado = ancho * cfg["centro_objetivo"]
        # Datos de la guía (aunque no mande): GIRO_DIRIGIDO los usa para saber cuándo regresar a LINEA
        r.puntos_linea_nuevos = sum(1 for p in r.puntos_amarillos if p[2])
        r.angulo_linea = _angulo_curva(r.curva_linea) if objetivos_linea else 0.0
        r.linea_visible = bool(objetivos_linea) and r.puntos_linea_nuevos > 0
        if objetivos_linea:
            xs_l = np.array([o[0] for o in objetivos_linea], float)
            ps_l = np.array([self._peso(o[2]) for o in objetivos_linea])
            x_l = float(np.average(xs_l, weights=ps_l)) if ps_l.sum() > 0 else float(xs_l.mean())
            r.error_linea = float(np.clip((x_l - centro_deseado) / (ancho / 2.0), -1.0, 1.0))
        forzada = self.orilla_forzada if self.orilla_forzada in ("izquierda", "derecha") else None
        r.orilla_forzada = forzada
        if forzada:
            # GIRO_DIRIGIDO: la SEÑAL manda. Se ignora la guía para el control y se
            # sigue SOLO la orilla de ese lado (si no se ve, asfalto con rama de ese lado).
            objetivos_linea = []
        # solo_linea (fuera de un GIRO_DIRIGIDO): nunca ORILLA ni ASFALTO. Sin guía se
        # sigue la última recta durante tiempo_prediccion_linea_s; después LINEA PERDIDA.
        if cfg.get("solo_linea", False) and not forzada:
            return self._solo_linea(r, objetivos_linea, centro_deseado, ancho, ahora)
        offset = cfg.get("offset_asfalto", 0.0) * ancho
        objetivos_asf = [(f["centro"] + offset, f["y"], f["i"]) for f in franjas_asf if self._peso(f["i"]) > 0]
        objetivos_orilla = [] if objetivos_linea else self._seguir_orilla(camino, blanca, verde, ancho, y0, r,
                                                                          solo_lado=forzada)
        if objetivos_linea:
            r.modo = MODO_LINEA
            objetivos = objetivos_linea
        elif objetivos_orilla:
            objetivos = objetivos_orilla      # r.modo y r.objetivos ya los puso _seguir_orilla
        elif len(objetivos_asf) >= int(cfg["min_franjas_validas"]):
            r.modo = MODO_ASFALTO
            objetivos = objetivos_asf
        else:
            r.perdido = r.fraccion_asfalto < cfg["min_fraccion_asfalto"]
            r.descripcion = (f"carril perdido: sin amarillo y {r.fraccion_asfalto:.0%} de asfalto"
                             if r.perdido else f"carril no visible ({len(franjas_asf)} franjas)")
            return r

        xs = np.array([o[0] for o in objetivos], float)
        pesos = np.array([self._peso(o[2]) for o in objetivos])
        objetivo = float(np.clip(np.average(xs, weights=pesos), 0, ancho))
        if not objetivos_orilla:
            r.objetivos = [(o[0], o[1]) for o in objetivos]
        r.detectado = True
        r.centro_x = objetivo
        r.error = float(np.clip((objetivo - centro_deseado) / (ancho / 2.0), -1.0, 1.0))
        if r.ancho_carril <= 0:
            r.ancho_carril = 2.0 * cfg["offset_carril"] * ancho
        extra = f", bifurcacion -> {r.rama}" if r.bifurcacion else ""
        if forzada:
            extra += f", forzado {forzada}"
        r.descripcion = f"{r.modo} ({len(objetivos)} franjas{extra})"
        return r

    def _solo_linea(self, r, objetivos_linea, centro_deseado, ancho, ahora):
        """Modo solo_linea: LINEA, LINEA-PRED (última recta) o LINEA PERDIDA."""
        cfg = self.cfg
        limite = float(cfg.get("tiempo_prediccion_linea_s", 1.5))
        if objetivos_linea and r.linea_visible:
            self._pred_objetivos = list(objetivos_linea)
            self._pred_t = ahora
            self._pred_angulo = r.angulo_linea
        r.segundos_sin_linea = 0.0 if self._pred_t is None else max(0.0, ahora - self._pred_t)
        if objetivos_linea:
            r.modo = MODO_LINEA
            objetivos = objetivos_linea
            r.objetivos = [(o[0], o[1]) for o in objetivos]
        elif self._pred_objetivos is not None and r.segundos_sin_linea <= limite:
            r.modo = MODO_LINEA_PREDICHA
            r.prediccion = True
            r.angulo_linea = self._pred_angulo
            objetivos = self._pred_objetivos
            r.objetivos = [(o[0], o[1]) for o in objetivos]
        else:
            r.modo = MODO_LINEA_PERDIDA
            r.linea_perdida = True
            r.perdido = True
            r.descripcion = (f"LINEA PERDIDA ({r.segundos_sin_linea:.1f} s sin guia)"
                             if self._pred_t is not None else "LINEA PERDIDA (aun no se ve la guia)")
            return r
        xs = np.array([o[0] for o in objetivos], float)
        pesos = np.array([self._peso(o[2]) for o in objetivos])
        objetivo = float(np.clip(np.average(xs, weights=pesos) if pesos.sum() > 0 else xs.mean(), 0, ancho))
        r.detectado = True
        r.centro_x = objetivo
        r.error = float(np.clip((objetivo - centro_deseado) / (ancho / 2.0), -1.0, 1.0))
        if r.ancho_carril <= 0:
            r.ancho_carril = 2.0 * cfg["offset_carril"] * ancho
        extra = f", prediccion {r.segundos_sin_linea:.1f}/{limite:.1f} s" if r.prediccion else ""
        r.descripcion = f"{r.modo} ({len(objetivos)} franjas{extra})"
        return r

    # ------------------------------------------------------------------
    # B0) Orillas blancas
    # ------------------------------------------------------------------
    def _orillas(self, camino, blanca, verde, ancho):
        """
        Por franja (de abajo hacia arriba, las mismas que el asfalto):
        lista de (y_en_roi, der, izq); der/izq = (x, fuera_de_imagen) o None.
        La orilla es la primera columna BLANCA que aparece después de haber
        visto asfalto, avanzando desde el centro hacia cada lado.
        Con orilla_requiere_fuera_de_pista, ese blanco solo es orilla si del
        OTRO lado (ventana de orilla_ventana_px) hay verde, algo que no es
        asfalto, o se acaba la imagen. Si del otro lado vuelve a haber
        asfalto (sin verde) es una franja pintada (p. ej. la línea guía): se
        salta y se sigue buscando.
        Si no hay blanco y del centro al borde es asfalto (al menos
        min_asfalto_fuera de las columnas, y asfalto pegado al borde), la
        orilla está "fuera de imagen": x = 1 + margen_fuera o 0 - margen_fuera.
        Si no, None (esa franja no tiene asfalto suficiente).
        """
        cfg = self.cfg
        n = max(1, int(cfg["franjas_asfalto"]))
        alto_roi = camino.shape[0]
        alto_f = max(2, alto_roi // n)
        minimo = max(1, int(cfg["min_pixeles_columna"]))
        hueco = int(cfg["max_hueco_orilla_px"])
        centro = int(np.clip(ancho * cfg["centro_objetivo"], 0, ancho - 1))

        margen = float(cfg["margen_fuera"])
        min_fuera = float(cfg["min_asfalto_fuera"])
        requiere_fuera = bool(cfg.get("orilla_requiere_fuera_de_pista", True))
        ventana = max(2, int(cfg.get("orilla_ventana_px", 10)))
        min_asf_otro_lado = float(cfg.get("guia_min_asfalto_lados", 0.6))

        def buscar(col_camino, col_blanca, col_verde, columnas, x_fuera):
            columnas = list(columnas)
            col_camino = col_camino.copy()      # las franjas saltadas cuentan como asfalto
            ultimo_asfalto = None
            j = 0
            while j < len(columnas):
                x = columnas[j]
                if col_camino[x]:
                    ultimo_asfalto = x
                elif col_blanca[x] and ultimo_asfalto is not None and abs(x - ultimo_asfalto) <= hueco:
                    if not requiere_fuera:
                        return float(x), False
                    # Fin del blanco y lo que hay del otro lado
                    k = j
                    while k < len(columnas) and col_blanca[columnas[k]]:
                        k += 1
                    otro_lado = columnas[k:k + ventana]
                    if (len(otro_lado) < ventana // 2 or col_verde[otro_lado].any()
                            or col_camino[otro_lado].mean() < min_asf_otro_lado):
                        return float(x), False          # verde / fuera de pista: orilla real
                    col_camino[columnas[j:k]] = True    # franja sobre asfalto: NO es orilla
                    ultimo_asfalto = columnas[k - 1]
                    j = k
                    continue
                j += 1
            # Sin orilla blanca: ¿todo asfalto hasta el borde? -> orilla justo afuera
            borde = columnas[-1]
            if (ultimo_asfalto is not None and abs(borde - ultimo_asfalto) <= hueco
                    and col_camino[np.asarray(columnas)].mean() >= min_fuera):
                return x_fuera, True
            return None

        resultado = []
        for i in range(n):
            ya, yb = alto_roi - (i + 1) * alto_f, alto_roi - i * alto_f
            if ya < 0:
                break
            col_camino = (camino[ya:yb] > 0).mean(axis=0) >= cfg["fraccion_columna_asfalto"]
            col_blanca = (blanca[ya:yb] > 0).sum(axis=0) >= minimo
            col_verde = (verde[ya:yb] > 0).sum(axis=0) >= minimo
            der = buscar(col_camino, col_blanca, col_verde, range(centro, ancho), (1.0 + margen) * ancho)
            izq = buscar(col_camino, col_blanca, col_verde, range(centro, -1, -1), (0.0 - margen) * ancho)
            resultado.append(((ya + yb) // 2, der, izq))
        return resultado

    def _seguir_orilla(self, camino, blanca, verde, ancho, y0, r, solo_lado=None):
        """
        Objetivos (x equivalente, y, franja) siguiendo una orilla, o [] si no se puede.
        Con solo_lado ("izquierda"/"derecha") se prueba SOLO esa orilla (GIRO_DIRIGIDO).
        """
        cfg = self.cfg
        orillas = self._orillas(camino, blanca, verde, ancho)
        if not orillas:
            return []
        minimo = max(1, int(cfg["min_franjas_orilla"]))
        pref = self.preferencia_temporal if self.preferencia_temporal in ("izquierda", "derecha") else None
        elegido = pref or cfg.get("lado_orilla", "derecha")
        otro = "izquierda" if elegido == "derecha" else "derecha"
        centro = ancho * cfg["centro_objetivo"]
        for lado in ((solo_lado,) if solo_lado else (elegido, otro)):
            indice = 1 if lado == "derecha" else 2
            meta = cfg["objetivo_orilla_der"] if lado == "derecha" else cfg["objetivo_orilla_izq"]
            encontradas = [(i, o[0], o[indice]) for i, o in enumerate(orillas) if o[indice] is not None]
            if len(encontradas) < minimo:          # las "fuera de imagen" también cuentan
                continue
            objetivos, dibujo, puntos = [], [], []
            for i, y, (x, fuera) in encontradas:
                if i >= len(meta):
                    continue
                x_meta = meta[i] * ancho
                puntos.append((x, y0 + y, fuera))
                dibujo.append((x_meta, y0 + y))
                if self._peso(i) > 0:
                    # (x - x_meta) es el error en píxeles; se expresa como un "objetivo"
                    # equivalente para usar la misma fórmula que los otros modos
                    objetivos.append((centro + (x - x_meta), y0 + y, i))
            if not objetivos:
                continue
            r.modo = MODO_ORILLA[lado]
            r.puntos_orilla = puntos
            r.objetivos = dibujo
            return objetivos
        return []

    def _peso(self, i):
        """Peso de la franja i (0 = la de más abajo) según pesos_franjas; las que faltan pesan 0."""
        pesos = self.cfg["pesos_franjas"]
        return float(pesos[i]) if i < len(pesos) else 0.0

    # ------------------------------------------------------------------
    # B) Tramos de asfalto por franja
    # ------------------------------------------------------------------
    def _tramos(self, camino_f, blanca_f, verde_f, ancho):
        """
        Tramos (inicio, fin) de asfalto en una franja.
        Un hueco entre dos tramos se UNE (se considera asfalto) salvo que tenga
        una orilla blanca, o verde y sea ancho (>= min_ancho_isla): así una
        mancha oscura o un poco de verde suelto no parten el camino.
        """
        cfg = self.cfg
        col_camino = (camino_f > 0).mean(axis=0) >= cfg["fraccion_columna_asfalto"]
        col_blanca = (blanca_f > 0).sum(axis=0) >= 2
        col_verde = (verde_f > 0).sum(axis=0) >= 2
        min_isla = cfg["min_ancho_isla"] * ancho
        # Con isla_requiere_blanco, el verde solo (sin borde blanco) no separa: una mancha
        # oscura con tono verdoso también cae en el rango de verde.
        verde_separa = not cfg.get("isla_requiere_blanco", False)
        tramos = []
        for a, b in _segmentos(col_camino):
            if tramos:
                ha, hb = int(tramos[-1][1]), int(a)          # hueco entre el tramo anterior y este
                separa = col_blanca[ha:hb].any() or (verde_separa and col_verde[ha:hb].any() and hb - ha >= min_isla)
                if not separa:
                    tramos[-1] = (tramos[-1][0], b)
                    continue
            tramos.append((a, b))
        minimo = cfg["ancho_min_tramo"] * ancho
        return [(float(a), float(b)) for a, b in tramos if b - a >= minimo]

    def _es_isla(self, izq, der, ancho):
        """El hueco entre dos tramos es una isla (lo bastante ancho). Ya se sabe que tiene verde o blanco."""
        return der[0] - izq[1] >= self.cfg["min_ancho_isla"] * ancho

    def _franjas_asfalto(self, camino, blanca, verde, ancho, y0, r):
        cfg = self.cfg
        n = max(1, int(cfg["franjas_asfalto"]))
        alto_roi = camino.shape[0]
        alto_f = max(2, alto_roi // n)
        margen = cfg["margen_conexion_tramo"] * ancho
        preferencia = self.preferencia_temporal or cfg.get("preferencia_bifurcacion", "derecha")
        ref = self._ref_abajo if self._ref_abajo is not None else ancho * cfg["centro_objetivo"]

        franjas = []
        anterior = None             # tramo elegido en la franja de abajo
        for i in range(n):
            ya, yb = alto_roi - (i + 1) * alto_f, alto_roi - i * alto_f
            if ya < 0:
                break
            tramos = self._tramos(camino[ya:yb], blanca[ya:yb], verde[ya:yb], ancho)
            if not tramos:
                if anterior is None:
                    continue        # la franja de abajo puede estar tapada: se prueba la siguiente
                break
            if anterior is None:
                # Franja más baja con camino: tramo donde está el carrito (o el más cercano)
                conectados = [t for t in tramos if t[0] - margen <= ref <= t[1] + margen]
                if not conectados:
                    conectados = [min(tramos, key=lambda t: abs((t[0] + t[1]) / 2 - ref))]
            else:
                conectados = [t for t in tramos if t[0] <= anterior[1] + margen and t[1] >= anterior[0] - margen]
                if not conectados:
                    break           # el camino ya no sigue conectado hacia arriba
            # Bifurcación solo si dos tramos conectados están separados por una isla real
            hay_isla = any(self._es_isla(conectados[k], conectados[k + 1], ancho)
                           for k in range(len(conectados) - 1))
            if hay_isla:
                r.bifurcacion = True
                r.rama = preferencia
                if preferencia == "recto":
                    # señal AVANZAR reciente: el tramo más al centro de la imagen (el más recto)
                    centro_img = ancho * cfg["centro_objetivo"]
                    elegido = min(conectados, key=lambda t: abs((t[0] + t[1]) / 2.0 - centro_img))
                else:
                    elegido = conectados[0] if preferencia == "izquierda" else conectados[-1]
            else:
                # Sin isla: el tramo más cercano al de abajo (o a donde está el carrito)
                cerca = ((anterior[0] + anterior[1]) / 2.0) if anterior is not None else ref
                elegido = min(conectados, key=lambda t: abs((t[0] + t[1]) / 2.0 - cerca))
            anterior = elegido
            centro = (elegido[0] + elegido[1]) / 2.0
            y = y0 + (ya + yb) // 2
            franjas.append({"i": i, "y": y, "ya": y0 + ya, "yb": y0 + yb, "tramo": elegido,
                            "centro": centro, "bifurcacion": hay_isla})
            r.puntos.append((y, elegido[0], elegido[1], centro))

        r.franjas_validas = len(franjas)
        if franjas:
            abajo = franjas[0]
            r.x_izq, r.x_der = abajo["tramo"]
            r.ancho_carril = abajo["tramo"][1] - abajo["tramo"][0]
            self._ref_abajo = abajo["centro"]
        return franjas

    # ------------------------------------------------------------------
    # A) Línea amarilla punteada
    # ------------------------------------------------------------------
    def _x_esperada_linea(self, y, ancho, reciente):
        cfg = self.cfg
        if reciente and self._coef_linea is not None:
            return float(np.polyval(self._coef_linea, y))
        # Sin historial: donde estaría la línea si el carrito fuera bien centrado
        if cfg.get("modo_linea") == "carril_derecho":
            return ancho * (cfg["centro_objetivo"] - cfg["offset_carril"])
        return ancho * float(cfg.get("objetivo_linea", cfg["centro_objetivo"]))

    def _linea_amarilla(self, amarilla, ancho, y0, ahora, franjas_asf, r):
        cfg = self.cfg
        memoria_s = cfg["memoria_linea_s"]
        reciente = self._ultima_linea_t is not None and ahora - self._ultima_linea_t <= memoria_s
        n = max(1, int(cfg["franjas_linea"]))
        alto_roi = amarilla.shape[0]
        alto_f = max(2, alto_roi // n)
        # La máscara de la guía por forma ya está validada: basta 1 píxel por
        # columna (de lejos los guiones se ven delgados y horizontales).
        minimo = 1 if _usa_forma(cfg) else max(1, int(cfg["min_pixeles_columna"]))
        salto = cfg["salto_max_linea"] * ancho if reciente else ancho

        def tramo_elegido(y):
            """Si en esa altura hubo bifurcación, el tramo de asfalto elegido."""
            for f in franjas_asf:
                if f["bifurcacion"] and f["ya"] <= y < f["yb"]:
                    return f["tramo"]
            return None

        nuevos = []
        for i in range(n):
            ya, yb = alto_roi - (i + 1) * alto_f, alto_roi - i * alto_f
            if ya < 0:
                break
            columnas = (amarilla[ya:yb] > 0).sum(axis=0) >= minimo
            segmentos = _segmentos(columnas)
            if not segmentos:
                continue
            y = float(y0 + (ya + yb) // 2)
            esperada = self._x_esperada_linea(y, ancho, reciente)
            tramo = tramo_elegido(y)
            candidatos = []
            for a, b in segmentos:
                x = (a + b - 1) / 2.0
                if tramo is not None and not (tramo[0] - 3 <= x <= tramo[1] + 3):
                    continue        # en la bifurcación solo cuenta la línea de la rama elegida
                if abs(x - esperada) <= salto:
                    candidatos.append(x)
            if candidatos:
                nuevos.append((min(candidatos, key=lambda x: abs(x - esperada)), y))

        # Memoria: puntos de los últimos memoria_s segundos (cubre los huecos)
        while self._memoria and ahora - self._memoria[0][0] > memoria_s:
            self._memoria.popleft()
        viejos = [(x, y) for _, x, y in self._memoria]
        for x, y in nuevos:
            self._memoria.append((ahora, x, y))
        if nuevos:
            self._ultima_linea_t = ahora

        r.puntos_amarillos = [(x, y, False) for x, y in viejos] + [(x, y, True) for x, y in nuevos]
        todos = nuevos + viejos
        if not todos or not reciente and not nuevos:
            self._coef_linea = None
            return []

        xs = np.array([p[0] for p in todos])
        ys = np.array([p[1] for p in todos])
        w = np.array([1.0] * len(nuevos) + [cfg["peso_memoria_linea"]] * len(viejos))
        alcance = ys.max() - ys.min()
        grado = 0
        if len(todos) >= 2 and alcance >= 0.15 * alto_roi:
            grado = 1
        if int(cfg["grado_ajuste_linea"]) >= 2 and len(todos) >= 4 and alcance >= 0.4 * alto_roi:
            grado = 2
        coef = np.polyfit(ys, xs, grado, w=w) if grado > 0 else np.array([np.average(xs, weights=w)])
        self._coef_linea = coef

        if cfg.get("modo_linea") == "carril_derecho":
            offset = cfg["offset_carril"] * ancho
        else:
            # sobre_linea: la línea debe quedar en objetivo_linea (la tecla C lo mide; normalmente 0.5)
            offset = (cfg["centro_objetivo"] - float(cfg.get("objetivo_linea", cfg["centro_objetivo"]))) * ancho
        # Los objetivos se evalúan en las MISMAS franjas que el asfalto, para usar pesos_franjas
        n_obj = max(1, int(cfg["franjas_asfalto"]))
        alto_obj = max(2, alto_roi // n_obj)
        objetivos = []
        for i in range(n_obj):
            ya, yb = alto_roi - (i + 1) * alto_obj, alto_roi - i * alto_obj
            if ya < 0:
                break
            y = float(y0 + (ya + yb) // 2)
            x_linea = float(np.polyval(coef, y))
            r.curva_linea.append((x_linea, y))
            if self._peso(i) > 0:
                objetivos.append((x_linea + offset, y, i))
        return objetivos

    # ------------------------------------------------------------------
    # Calibración con la tecla C
    # ------------------------------------------------------------------
    def calibrar(self, cuadro):
        """
        Ajusta los umbrales HSV del asfalto con un parche del centro-abajo de
        la imagen y, si hay amarillo visible en la ROI, los del amarillo.
        Cambia self.cfg (el mismo diccionario de config.json): G lo guarda.
        Devuelve un texto con lo que hizo.
        """
        cfg = self.cfg
        alto, ancho = cuadro.shape[:2]
        _, hsv = _hsv(cuadro, cfg)
        mensajes = []
        forma = _usa_forma(cfg)
        y0 = min(max(int(alto * cfg["roi_inicio_y"]), 0), alto - 8)

        # Con la guía por forma: sus guiones (y lo muy claro) NO son asfalto
        # para el parche, porque la guía está justo en el centro.
        no_asfalto = np.zeros((alto, ancho), bool)
        techo_v = cfg["blanco_bajo"][2]
        if forma:
            guia0 = mascaras_pista(cuadro[y0:], cfg)[4]
            no_asfalto[y0:] = cv2.dilate(guia0, np.ones((7, 7), np.uint8)) > 0
            techo_v = min(techo_v, int(cfg.get("guia_v_min", 150)))

        # --- Asfalto: parche centro-abajo, sin píxeles blancos ni amarillos (ni guía)
        px, py = cfg["parche_calibracion"]
        if forma:
            # El carrito está SOBRE la guía: de cerca el guion tapa casi todo el
            # parche del centro. Se usa uno más ancho (la guía se excluye abajo).
            px = max(px, 0.6)
        x1, x2 = int(ancho * (0.5 - px / 2)), int(ancho * (0.5 + px / 2))
        y1 = int(alto * (1.0 - py))
        parche = hsv[y1:alto, x1:x2].reshape(-1, 3)
        h, s, v = parche[:, 0].astype(int), parche[:, 1].astype(int), parche[:, 2].astype(int)
        amarillo_aprox = (h >= 12) & (h <= 45) & (s >= 70) & (v >= 70)
        blanco_aprox = (v >= techo_v) & (s <= cfg["blanco_alto"][1]) if not forma else (v >= techo_v)
        util = ~amarillo_aprox & ~blanco_aprox & ~no_asfalto[y1:alto, x1:x2].reshape(-1)
        if util.sum() < 0.3 * len(parche):
            mensajes.append("asfalto NO calibrado: el parche centro-abajo no parece asfalto")
        else:
            s_u, v_u = s[util], v[util]
            s_max = int(np.clip(np.percentile(s_u, 95) + 25, 40, 255))
            v_min = int(np.clip(np.percentile(v_u, 3) - 25, 0, 255))
            v_max = int(np.clip(np.percentile(v_u, 97) + 25, v_min + 10, techo_v - 5))
            if np.median(s_u) > 110:
                mensajes.append(f"asfalto NO calibrado: parche muy saturado (S={np.median(s_u):.0f}), "
                                "apunta la camara al asfalto")
            else:
                cfg["asfalto_bajo"] = [0, 0, v_min]
                cfg["asfalto_alto"] = [179, s_max, v_max]
                mensajes.append(f"asfalto: S<={s_max}  V {v_min}-{v_max}")

        # --- Amarillo: solo si se usa la línea y el tono medido es amarillo real
        #     (H 15-35 en OpenCV). Se mide con un rango amplio para poder avisar
        #     cuando lo que se ve es verdoso o naranja.
        h_ok_min, h_ok_max = 15, 35
        if forma:
            pass        # la guía se mide más abajo, por forma
        elif not cfg.get("usar_linea_amarilla", False):
            mensajes.append("amarillo: sin cambios (usar_linea_amarilla = false)")
        else:
            y0 = int(alto * cfg["roi_inicio_y"])
            roi = hsv[y0:].reshape(-1, 3).astype(int)
            h, s, v = roi[:, 0], roi[:, 1], roi[:, 2]
            cand = (h >= 8) & (h <= 50) & (s >= 60) & (v >= 70)
            if cand.sum() < max(30, int(cfg["min_pixeles_calibrar_amarillo"])):
                mensajes.append("amarillo: no se ve (sin cambios)")
            else:
                hh, ss, vv = h[cand], s[cand], v[cand]
                h_p5, h_p95 = np.percentile(hh, 5), np.percentile(hh, 95)
                if h_p5 < h_ok_min or h_p95 > h_ok_max:
                    mensajes.append(f"amarillo NO calibrado: el tono medido H {h_p5:.0f}-{h_p95:.0f} "
                                    f"no es amarillo ({h_ok_min}-{h_ok_max}), sin cambios")
                else:
                    h_min = int(max(h_ok_min, h_p5 - 3))
                    h_max = int(min(h_ok_max, h_p95 + 3))
                    s_min = int(max(60, np.percentile(ss, 5) - 25))
                    v_min = int(max(70, np.percentile(vv, 5) - 25))
                    cfg["amarillo_bajo"] = [h_min, s_min, v_min]
                    cfg["amarillo_alto"] = [h_max, 255, 255]
                    mensajes.append(f"amarillo: H {h_min}-{h_max}  S>={s_min}  V>={v_min}")

        # --- Orillas: objetivo = posición medida, corrida margen_calibracion_orilla
        #     hacia el centro, sin pasar de max_objetivo_orilla_der / min_objetivo_orilla_izq.
        #     Solo cuentan las orillas VISTAS (no las "fuera de imagen"); las demás
        #     franjas se quedan igual. Se usan los umbrales recién calibrados.
        #     Los guiones de la guía ya no están en "blanca" y una franja con
        #     asfalto de los dos lados no cuenta como orilla.
        corrimiento = float(cfg["margen_calibracion_orilla"])
        camino, _amarilla, blanca, verde, guia = mascaras_pista(cuadro[y0:], cfg)
        if forma:
            mensajes.append(self._calibrar_guia(hsv[y0:], guia, ancho))
        orillas = self._orillas(camino, blanca, verde, ancho)
        for clave, indice, nombre in (("objetivo_orilla_der", 1, "orilla der"), ("objetivo_orilla_izq", 2, "orilla izq")):
            meta = list(cfg[clave])
            vistas = 0
            for i, o in enumerate(orillas):
                if o[indice] is None or o[indice][1] or i >= len(meta):
                    continue
                x = o[indice][0] / ancho
                if indice == 1:
                    x = min(x - corrimiento, cfg["max_objetivo_orilla_der"])
                else:
                    x = max(x + corrimiento, cfg["min_objetivo_orilla_izq"])
                meta[i] = round(x, 3)
                vistas += 1
            cfg[clave] = meta
            mensajes.append(f"{nombre}: {vistas}/{len(orillas)} franjas {meta}")
        self.reiniciar()
        return "Calibrado -> " + " | ".join(mensajes) + "  (G = guardar)"

    def _calibrar_guia(self, hsv_roi, guia, ancho):
        """
        Con la guía encontrada por forma: guarda su color medido (solo de
        referencia, "color_guia_hsv") y su posición x promedio como
        "objetivo_linea" (fracción del ancho; normalmente ~0.5). La x se
        promedia por franja con pesos_franjas, como el control.
        """
        cfg = self.cfg
        if np.count_nonzero(guia) < int(cfg.get("guia_area_min", 20)):
            return "linea guia: no se ve (sin cambios)"
        pix = hsv_roi[guia > 0].astype(int)
        color = [int(np.median(pix[:, k])) for k in range(3)]
        cfg["color_guia_hsv"] = color

        n = max(1, int(cfg["franjas_asfalto"]))
        alto_roi = guia.shape[0]
        alto_f = max(2, alto_roi // n)
        xs, pesos = [], []
        for i in range(n):
            ya, yb = alto_roi - (i + 1) * alto_f, alto_roi - i * alto_f
            if ya < 0:
                break
            cols = np.flatnonzero((guia[ya:yb] > 0).any(axis=0))
            if cols.size:
                xs.append(cols.mean())
                pesos.append(self._peso(i))
        if not xs:
            return f"linea guia: color HSV {color}, sin posicion"
        pesos = np.array(pesos) if sum(pesos) > 0 else np.ones(len(xs))
        objetivo = float(np.average(xs, weights=pesos)) / ancho
        if not 0.25 <= objetivo <= 0.75:
            return (f"linea guia: color HSV {color}; x={objetivo:.2f} muy lejos del centro, "
                    "objetivo_linea sin cambios (centra el carrito sobre la linea)")
        cfg["objetivo_linea"] = round(objetivo, 3)
        return f"linea guia: color HSV {color}  objetivo_linea {objetivo:.3f}"


# ======================================================================
# Control proporcional-derivativo (PD)
# ======================================================================
class ControlPD:
    """
    1. Error suavizado (promedio exponencial):
         error_f = alpha * error + (1 - alpha) * error_f_anterior      (alpha = suavizado_error)
    2. Anticipación: + k_angulo * ángulo de la recta de la guía (empieza a girar al
       ENTRAR a la curva, no cuando ya se pasó).
    3. Integral pequeña: termino_i += ki * error_f * dt, limitada a +-limite_integral
       (anti-windup). Se reinicia al cambiar de signo el error o al detenerse (reiniciar).
    4. error_c = error_f + k_angulo * ángulo + termino_i
       correccion = Kp * error_c + Kd * (cambio de error_f por segundo)
    5. diferencia entre ruedas = 2 * correccion, limitada y sin cambiar más de
       max_cambio_por_cuadro respecto al cuadro anterior.
    6. Ruedas según |error_c|:
       - normal (<= umbral_giro_cerrado): vel = base +- diferencia/2 y ninguna rueda
         abajo de velocidad_rueda_min (110): si una quedaría abajo, se suben LAS DOS.
       - GIRO CERRADO (> umbral_giro_cerrado, 0.35): la rueda exterior va a
         max(vel_exterior_min_giro (140), base + |dif|/2) y la interior = exterior - |dif|,
         que puede bajar hasta 0 (detenida). |dif| <= max_diferencia_giro_cerrado.
       - PIVOTE (> umbral_pivote, 0.6): igual, pero la interior puede ir en reversa
         hasta -vel_reversa_pivote (120). |dif| <= max_diferencia_pivote.

    Si el objetivo está a la derecha (error > 0), la rueda izquierda gira más
    rápido que la derecha y el carrito se va hacia la derecha.
    """

    def __init__(self, cfg_carril):
        self.cfg = cfg_carril
        self._error_previo = None
        self._tiempo_previo = None
        self._error_filtrado = None
        self._diferencia_previa = 0.0
        self.ultima_correccion = 0.0
        self.ultimo_error_filtrado = 0.0
        self.ultima_diferencia = 0.0      # vel_izq - vel_der del último cálculo
        self.termino_i = 0.0              # integral (en unidades de error)
        self.ultimo_angulo = 0.0
        self.ultimo_error_control = 0.0   # error_f + ángulo + integral
        self.modo_giro = "normal"         # "normal" / "cerrado" / "pivote"

    def reiniciar(self):
        # La diferencia previa vuelve a 0 (recto): al retomar, el giro crece de a poco
        self._error_previo = None
        self._tiempo_previo = None
        self._error_filtrado = None
        self._diferencia_previa = 0.0
        self.termino_i = 0.0
        self.modo_giro = "normal"

    def calcular(self, error, ahora, base=None, angulo=0.0):
        """
        base: velocidad base distinta a velocidad_base (ej. velocidad_sin_linea).
        angulo: ángulo de la recta de la guía (rad), solo en LINEA / LINEA-PRED.
        """
        cfg = self.cfg
        alpha = float(np.clip(cfg.get("suavizado_error", 1.0), 0.01, 1.0))
        if self._error_filtrado is None:
            self._error_filtrado = error
        else:
            self._error_filtrado = alpha * error + (1.0 - alpha) * self._error_filtrado
        error_f = self._error_filtrado
        self.ultimo_error_filtrado = error_f

        derivada = 0.0
        dt = 0.0
        if self._error_previo is not None:
            dt = ahora - self._tiempo_previo
            if dt > 1e-3:
                derivada = (error_f - self._error_previo) / dt
            # Integral: se reinicia al cambiar de signo el error (anti-windup)
            if (error_f > 0) != (self._error_previo > 0):
                self.termino_i = 0.0
        if dt > 1e-3:
            limite_i = float(cfg.get("limite_integral", 0.3))
            self.termino_i = float(np.clip(self.termino_i + float(cfg.get("ki", 0.0)) * error_f * min(dt, 0.5),
                                           -limite_i, limite_i))
        self._error_previo = error_f
        self._tiempo_previo = ahora

        self.ultimo_angulo = float(angulo)
        error_c = error_f + float(cfg.get("k_angulo", 0.0)) * self.ultimo_angulo + self.termino_i
        self.ultimo_error_control = error_c

        correccion = cfg["kp"] * error_c + cfg["kd"] * derivada
        self.ultima_correccion = correccion

        magnitud = abs(error_c)
        if magnitud > float(cfg.get("umbral_pivote", 9e9)):
            self.modo_giro = "pivote"
            max_dif = float(cfg.get("max_diferencia_pivote", 260))
        elif magnitud > float(cfg.get("umbral_giro_cerrado", 9e9)):
            self.modo_giro = "cerrado"
            max_dif = float(cfg.get("max_diferencia_giro_cerrado", 160))
        else:
            self.modo_giro = "normal"
            max_dif = float(cfg.get("max_diferencia_ruedas", 1000))
        max_cambio = float(cfg.get("max_cambio_por_cuadro", 1000))
        diferencia = float(np.clip(2.0 * correccion, -max_dif, max_dif))
        diferencia = float(np.clip(diferencia, self._diferencia_previa - max_cambio,
                                   self._diferencia_previa + max_cambio))
        self._diferencia_previa = diferencia

        base = cfg["velocidad_base"] if base is None else base
        minimo, maximo = cfg["velocidad_rueda_min"], cfg["velocidad_rueda_max"]
        if self.modo_giro == "normal":
            vel_izq, vel_der = base + diferencia / 2.0, base - diferencia / 2.0
            falta = minimo - min(vel_izq, vel_der)
            if falta > 0:
                vel_izq += falta
                vel_der += falta
            vel_izq = float(np.clip(vel_izq, minimo, maximo))
            vel_der = float(np.clip(vel_der, minimo, maximo))
        else:
            dif = abs(diferencia)
            exterior = float(np.clip(max(float(cfg.get("vel_exterior_min_giro", 140)), base + dif / 2.0),
                                     minimo, maximo))
            piso = -float(cfg.get("vel_reversa_pivote", 120)) if self.modo_giro == "pivote" else 0.0
            interior = float(max(piso, exterior - dif))
            vel_izq, vel_der = (exterior, interior) if diferencia >= 0 else (interior, exterior)
        self.ultima_diferencia = vel_izq - vel_der
        return vel_izq, vel_der
