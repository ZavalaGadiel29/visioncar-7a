"""
percepcion_yolo.py
------------------
Percepción con redes neuronales YOLO (Ultralytics). Se usan DOS modelos:

1. PercepcionYOLO  (yolov8n.pt, entrenado con COCO)
   -> personas (clase 'person' -> senal 'peaton') y obstáculos
      (config.json -> yolo -> clases_obstaculo: bottle, cup, chair, ...).
2. DetectorSenales (modelo/best_v2.pt, YOLOv8n propio, imgsz=320;
   respaldo: modelo/best.pt)
   -> señales: alto, avanzar, izquierda, derecha. Los nombres del modelo
      YA son los del contrato de la API, así que no se traducen.
   -> peaton: figuras de papel de peatones (COCO no las reconoce). NO son
      señales: salen con tipo TIPO_PEATON y main.py las separa.
   Si el archivo todavía NO existe, el detector queda "sin modelo": no
   detecta nada, el programa sigue funcionando y la pantalla lo avisa.
   Cuando se copie best.pt, se usa automáticamente al volver a ejecutar.

Para cada objeto se calcula:
  - cx:   centro horizontal normalizado, de 0 (izquierda) a 1 (derecha)
  - zona: IZQ, CENTRO o DER según cx
  - area: área de la caja / área del cuadro (0 a 1). Más grande = más cerca.
  - alto_rel: alto de la caja / alto del cuadro (0 a 1). Es lo que decide
    si una señal ya está "cerca" (config.json -> decision -> altura_activacion_senal).

Como la laptop no tiene GPU, YOLO es lo más lento. Cada modelo tiene
"cada_n_cuadros": corre 1 de cada N cuadros y entre medias se reutiliza su
último resultado. Además, cada modelo mide cuánto tarda (ms_promedio) para
que main.py decida si el de personas debe correr cada 2 cuadros.
"""

import os
import time
from dataclasses import dataclass

import numpy as np

# Tipos de detección que entiende el módulo de decisión
TIPO_PERSONA = "persona"
TIPO_OBSTACULO = "obstaculo"
TIPO_SENAL = "senal"
TIPO_PEATON = "peaton"      # figura de peatón del modelo propio (no es señal)

SENALES_MODELO = ("alto", "avanzar", "izquierda", "derecha")
CLASE_PEATON_MODELO = "peaton"


@dataclass
class Deteccion:
    clase: str              # persona/obstáculo: nombre COCO ("person"); señal: alto/avanzar/izquierda/derecha
    tipo: str               # persona / obstaculo / senal
    confianza: float        # 0 a 1
    caja: tuple             # (x1, y1, x2, y2) en píxeles
    cx: float               # centro horizontal normalizado (0 a 1)
    zona: str               # "IZQ", "CENTRO" o "DER"
    area: float             # área relativa al cuadro (0 a 1)
    alto_rel: float = 0.0   # alto de la caja / alto del cuadro (0 a 1)
    fuera_pista: bool = False   # pies fuera del asfalto: se ignora (persona/obstáculo)
    en_camino_forzado: bool = False   # sobre la guía o pies fuera de la imagen: cuenta como en el carril
    motivo_ignorado: str = ""   # peatón: por qué NO detuvo al carrito (para la pantalla)


def calcular_zona(cx, cfg_zonas):
    """Convierte un centro horizontal normalizado en IZQ / CENTRO / DER."""
    if cx < cfg_zonas["limite_izq"]:
        return "IZQ"
    if cx > cfg_zonas["limite_der"]:
        return "DER"
    return "CENTRO"


class _ModeloYOLO:
    """Parte común: carga un modelo, lo ejecuta cada N cuadros y arma Detecciones."""

    def __init__(self, ruta, cfg, cfg_zonas, tipo_por_clase, etiqueta, conf_prediccion=None):
        # Se importa aquí para que el programa funcione sin ultralytics (--sin-yolo)
        from ultralytics import YOLO

        self.cfg = cfg
        self.cfg_zonas = cfg_zonas
        self.etiqueta = etiqueta
        # Confianza mínima que se le pide a YOLO (por defecto la del config)
        self.conf_prediccion = float(cfg["confianza"] if conf_prediccion is None else conf_prediccion)
        print(f"[{etiqueta}] Cargando modelo {ruta} ...")
        self.modelo = YOLO(ruta)
        self.nombres = self.modelo.names            # {id: "nombre"}

        # tipo_por_clase: nombre_en_el_modelo (minúsculas) -> (tipo, nombre_para_nosotros)
        self.tipo_por_clase = tipo_por_clase
        self.ids_clases = [i for i, n in self.nombres.items() if n.lower() in tipo_por_clase]
        nombres_modelo = {n.lower() for n in self.nombres.values()}
        for nombre in tipo_por_clase:
            if nombre not in nombres_modelo:
                print(f"[{etiqueta}] Aviso: la clase '{nombre}' no existe en el modelo; se ignora.")

        self._contador = 0
        self._ultimas = []
        self.cada_n = max(1, int(cfg["cada_n_cuadros"]))   # main.py puede cambiarlo en vivo
        self.ms_promedio = None                             # duración promedio de una inferencia

        # Calentamiento: la PRIMERA inferencia tarda ~3 s (prepara la red).
        # Se hace aquí, al arrancar, para que no congele el ciclo con el
        # carrito ya en movimiento.
        lado = int(cfg["imgsz"])
        self.modelo.predict(np.zeros((lado, lado, 3), np.uint8), imgsz=lado,
                            device="cpu", verbose=False)
        print(f"[{etiqueta}] Modelo listo. Clases usadas: {len(self.ids_clases)}")

    def procesar(self, cuadro):
        """Devuelve (detecciones, se_ejecuto). Si no toca, repite el último resultado."""
        toca = (self._contador % max(1, int(self.cada_n)) == 0)
        self._contador += 1
        if not toca:
            return self._ultimas, False
        inicio = time.perf_counter()
        self._ultimas = self._detectar(cuadro)
        ms = (time.perf_counter() - inicio) * 1000.0
        self.ms_promedio = ms if self.ms_promedio is None else 0.9 * self.ms_promedio + 0.1 * ms
        return self._ultimas, True

    def _detectar(self, cuadro):
        alto, ancho = cuadro.shape[:2]
        area_cuadro = float(ancho * alto)
        if not self.ids_clases:
            return []
        resultado = self.modelo.predict(
            cuadro,
            imgsz=int(self.cfg["imgsz"]),
            conf=self.conf_prediccion,
            classes=self.ids_clases,
            device="cpu",
            verbose=False,
        )[0]

        detecciones = []
        cajas = resultado.boxes.xyxy.cpu().numpy()
        confianzas = resultado.boxes.conf.cpu().numpy()
        clases = resultado.boxes.cls.cpu().numpy().astype(int)
        for (x1, y1, x2, y2), confianza, id_clase in zip(cajas, confianzas, clases):
            info = self.tipo_por_clase.get(self.nombres[int(id_clase)].lower())
            if info is None:
                continue
            tipo, nombre = info
            cx = ((x1 + x2) / 2.0) / ancho
            detecciones.append(Deteccion(
                clase=nombre,
                tipo=tipo,
                confianza=float(confianza),
                caja=(int(x1), int(y1), int(x2), int(y2)),
                cx=float(cx),
                zona=calcular_zona(cx, self.cfg_zonas),
                area=float(((x2 - x1) * (y2 - y1)) / area_cuadro),
                alto_rel=float((y2 - y1) / alto),
            ))
        return detecciones


class PercepcionYOLO(_ModeloYOLO):
    """Modelo COCO: personas y obstáculos."""

    def __init__(self, cfg_yolo, cfg_zonas):
        tipos = {}
        for nombre in cfg_yolo["clases_persona"]:
            tipos[nombre.lower()] = (TIPO_PERSONA, nombre)
        for nombre in cfg_yolo.get("clases_obstaculo", []):
            tipos[nombre.lower()] = (TIPO_OBSTACULO, nombre)
        super().__init__(cfg_yolo["modelo"], cfg_yolo, cfg_zonas, tipos, "YOLO")


class DetectorSenales:
    """
    Modelo propio de señales (config.json -> senales -> modelo = modelo/best_v2.pt).

    Clases de best_v2.pt: {0: 'alto', 1: 'avanzar', 2: 'derecha', 3: 'izquierda', 4: 'peaton'}.
    Las 4 señales son los nombres del contrato de la API y se usan tal cual
    (tipo TIPO_SENAL). 'peaton' sale con tipo TIPO_PEATON: no es una señal.
    Si el modelo principal no existe o no carga, se usa modelo_respaldo
    (modelo/best.pt, sin 'peaton'). Otras clases se avisan y se ignoran.
    """

    def __init__(self, cfg_senales, cfg_zonas):
        self.cfg = cfg_senales
        self.disponible = False
        self.mensaje = "sin modelo de senales"
        self._modelo = None
        self.conf_senal = float(cfg_senales["confianza"])
        self.conf_peaton = float(cfg_senales.get("confianza_peaton", self.conf_senal))
        self.detecta_peaton = False

        rutas = [cfg_senales["modelo"]]
        respaldo = cfg_senales.get("modelo_respaldo")
        if respaldo and respaldo not in rutas:
            rutas.append(respaldo)
        self.ruta = rutas[0]

        tipos = {nombre: (TIPO_SENAL, nombre) for nombre in SENALES_MODELO}
        tipos[CLASE_PEATON_MODELO] = (TIPO_PEATON, CLASE_PEATON_MODELO)
        conf_minima = min(self.conf_senal, self.conf_peaton)
        for ruta in rutas:
            if not os.path.exists(ruta):
                print(f"[SENALES] No existe {ruta}.")
                continue
            try:
                self._modelo = _ModeloYOLO(ruta, cfg_senales, cfg_zonas, tipos, "SENALES",
                                           conf_prediccion=conf_minima)
            except Exception as error:      # archivo dañado, versión incompatible, etc.
                print(f"[SENALES] No se pudo cargar {ruta}: {error}")
                self.mensaje = f"error al cargar {os.path.basename(ruta)}"
                continue
            self.ruta = ruta
            break
        if self._modelo is None:
            print("[SENALES] Se trabaja SIN modelo de señales.")
            return

        nombres = {n.lower() for n in self._modelo.nombres.values()}
        self.detecta_peaton = CLASE_PEATON_MODELO in nombres
        extras = sorted(n for n in nombres if n not in tipos)
        if extras:
            print(f"[SENALES] Aviso: el modelo trae clases fuera del contrato (se ignoran): {extras}")
        self.disponible = True
        self.mensaje = f"{os.path.basename(self.ruta)} OK" + ("" if self.detecta_peaton else " (sin peaton)")
        if self.ruta != rutas[0]:
            self.mensaje += " [respaldo]"

    @property
    def ms_promedio(self):
        return self._modelo.ms_promedio if self._modelo else None

    def procesar(self, cuadro):
        """Devuelve (detecciones, se_ejecuto): señales (TIPO_SENAL) y peatones (TIPO_PEATON)."""
        if not self.disponible:
            return [], False
        detecciones, ejecutado = self._modelo.procesar(cuadro)
        # YOLO corrió con la confianza más baja de las dos; cada tipo se filtra con la suya
        detecciones = [d for d in detecciones
                       if d.confianza >= (self.conf_peaton if d.tipo == TIPO_PEATON else self.conf_senal)]
        return detecciones, ejecutado
