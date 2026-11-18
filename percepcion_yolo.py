"""
percepcion_yolo.py
------------------
Percepción con redes neuronales YOLO (Ultralytics). Se usan DOS modelos:

1. PercepcionYOLO  (yolov8n.pt, entrenado con COCO)
   -> personas (peatones) y obstáculos (bottle, cup, chair, backpack, ...).
2. DetectorSenales (modelo/best.pt, modelo propio)
   -> señales: alto, avanzar, izquierda, derecha.
   Si el archivo todavía NO existe, el detector queda "sin modelo": no
   detecta nada, el programa sigue funcionando y la pantalla lo avisa.
   Cuando se copie best.pt, se usa automáticamente al volver a ejecutar.

Para cada objeto se calcula:
  - cx:   centro horizontal normalizado, de 0 (izquierda) a 1 (derecha)
  - zona: IZQ, CENTRO o DER según cx
  - area: área de la caja / área del cuadro (0 a 1). Más grande = más cerca.

Como la laptop no tiene GPU, YOLO es lo más lento. Por eso cada modelo
tiene "cada_n_cuadros": corre 1 de cada N cuadros y entre medias se
reutiliza su último resultado.
"""

import os
from dataclasses import dataclass

import numpy as np

# Tipos de detección que entiende el módulo de decisión
TIPO_PERSONA = "persona"
TIPO_OBSTACULO = "obstaculo"
TIPO_SENAL = "senal"

SENALES_MODELO = ("alto", "avanzar", "izquierda", "derecha")


@dataclass
class Deteccion:
    clase: str              # persona/obstáculo: nombre COCO ("person"); señal: alto/avanzar/izquierda/derecha
    tipo: str               # persona / obstaculo / senal
    confianza: float        # 0 a 1
    caja: tuple             # (x1, y1, x2, y2) en píxeles
    cx: float               # centro horizontal normalizado (0 a 1)
    zona: str               # "IZQ", "CENTRO" o "DER"
    area: float             # área relativa al cuadro (0 a 1)


def calcular_zona(cx, cfg_zonas):
    """Convierte un centro horizontal normalizado en IZQ / CENTRO / DER."""
    if cx < cfg_zonas["limite_izq"]:
        return "IZQ"
    if cx > cfg_zonas["limite_der"]:
        return "DER"
    return "CENTRO"


class _ModeloYOLO:
    """Parte común: carga un modelo, lo ejecuta cada N cuadros y arma Detecciones."""

    def __init__(self, ruta, cfg, cfg_zonas, tipo_por_clase, etiqueta):
        # Se importa aquí para que el programa funcione sin ultralytics (--sin-yolo)
        from ultralytics import YOLO

        self.cfg = cfg
        self.cfg_zonas = cfg_zonas
        self.etiqueta = etiqueta
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

        # Calentamiento: la PRIMERA inferencia tarda ~3 s (prepara la red).
        # Se hace aquí, al arrancar, para que no congele el ciclo con el
        # carrito ya en movimiento.
        lado = int(cfg["imgsz"])
        self.modelo.predict(np.zeros((lado, lado, 3), np.uint8), imgsz=lado,
                            device="cpu", verbose=False)
        print(f"[{etiqueta}] Modelo listo. Clases usadas: {len(self.ids_clases)}")

    def procesar(self, cuadro):
        """Devuelve (detecciones, se_ejecuto). Si no toca, repite el último resultado."""
        n = max(1, int(self.cfg["cada_n_cuadros"]))
        toca = (self._contador % n == 0)
        self._contador += 1
        if not toca:
            return self._ultimas, False
        self._ultimas = self._detectar(cuadro)
        return self._ultimas, True

    def _detectar(self, cuadro):
        alto, ancho = cuadro.shape[:2]
        area_cuadro = float(ancho * alto)
        if not self.ids_clases:
            return []
        resultado = self.modelo.predict(
            cuadro,
            imgsz=int(self.cfg["imgsz"]),
            conf=float(self.cfg["confianza"]),
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
            ))
        return detecciones


class PercepcionYOLO(_ModeloYOLO):
    """Modelo COCO: personas y obstáculos."""

    def __init__(self, cfg_yolo, cfg_zonas):
        tipos = {}
        for nombre in cfg_yolo["clases_persona"]:
            tipos[nombre.lower()] = (TIPO_PERSONA, nombre)
        for nombre in cfg_yolo["clases_obstaculo"]:
            tipos[nombre.lower()] = (TIPO_OBSTACULO, nombre)
        super().__init__(cfg_yolo["modelo"], cfg_yolo, cfg_zonas, tipos, "YOLO")


class DetectorSenales:
    """
    Modelo propio de señales (modelo/best.pt).

    cfg_senales["clases"] traduce el nombre que trae el modelo al nombre del
    contrato, por si al entrenar se usaron otros nombres:
        {"alto": "alto", "stop": "alto", "left": "izquierda", ...}
    """

    def __init__(self, cfg_senales, cfg_zonas):
        self.cfg = cfg_senales
        self.ruta = cfg_senales["modelo"]
        self.disponible = False
        self.mensaje = "sin modelo de senales"
        self._modelo = None

        if not os.path.exists(self.ruta):
            print(f"[SENALES] No existe {self.ruta}: se trabaja SIN modelo de señales.")
            return

        tipos = {}
        for nombre_modelo, nombre_contrato in cfg_senales["clases"].items():
            if nombre_contrato in SENALES_MODELO:
                tipos[nombre_modelo.lower()] = (TIPO_SENAL, nombre_contrato)
        try:
            self._modelo = _ModeloYOLO(self.ruta, cfg_senales, cfg_zonas, tipos, "SENALES")
        except Exception as error:      # archivo dañado, versión incompatible, etc.
            print(f"[SENALES] No se pudo cargar {self.ruta}: {error}")
            self.mensaje = "error al cargar best.pt"
            return
        self.disponible = True
        self.mensaje = "modelo de senales OK"

    def procesar(self, cuadro):
        if not self.disponible:
            return [], False
        return self._modelo.procesar(cuadro)
