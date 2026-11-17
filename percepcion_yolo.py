"""
percepcion_yolo.py
------------------
Percepción con la red neuronal YOLO (Ultralytics).

YOLO recibe una imagen y devuelve cajas: "aquí hay una persona con 87 %
de confianza". Nosotros nos quedamos solo con las clases que importan
para manejar y, para cada una, calculamos:

  - cx:   centro horizontal de la caja, normalizado de 0 (izquierda) a 1 (derecha)
  - zona: IZQ, CENTRO o DER según cx
  - area: área de la caja / área del cuadro (0 a 1). Mientras más grande,
          más cerca está el objeto de la cámara.

Como la laptop no tiene GPU, YOLO es la parte más lenta. Por eso existe
"cada_n_cuadros": YOLO corre 1 de cada N cuadros y entre medias se
reutiliza el último resultado.
"""

from dataclasses import dataclass

# Tipos de detección que entiende el módulo de decisión
TIPO_PERSONA = "persona"
TIPO_SENAL_ALTO = "senal_alto"
TIPO_SEMAFORO = "semaforo"
TIPO_OBSTACULO = "obstaculo"


@dataclass
class Deteccion:
    clase: str              # nombre que da YOLO, ej. "person"
    tipo: str               # persona / senal_alto / semaforo / obstaculo
    confianza: float        # 0 a 1
    caja: tuple             # (x1, y1, x2, y2) en píxeles
    cx: float               # centro horizontal normalizado (0 a 1)
    zona: str               # "IZQ", "CENTRO" o "DER"
    area: float             # área relativa al cuadro (0 a 1)
    color_semaforo: str = ""   # solo se llena en los semáforos (ROJO/VERDE/DESCONOCIDO)


def calcular_zona(cx, cfg_zonas):
    """Convierte un centro horizontal normalizado en IZQ / CENTRO / DER."""
    if cx < cfg_zonas["limite_izq"]:
        return "IZQ"
    if cx > cfg_zonas["limite_der"]:
        return "DER"
    return "CENTRO"


class PercepcionYOLO:
    def __init__(self, cfg_yolo, cfg_zonas):
        # Se importa aquí para que el resto del programa funcione aunque
        # ultralytics no esté instalado (opción --sin-yolo).
        from ultralytics import YOLO

        self.cfg = cfg_yolo
        self.cfg_zonas = cfg_zonas

        print(f"[YOLO] Cargando modelo {cfg_yolo['modelo']} ...")
        self.modelo = YOLO(cfg_yolo["modelo"])
        self.nombres = self.modelo.names        # diccionario {id: "nombre"}

        # Diccionario nombre_de_clase -> tipo, armado desde el config
        self.tipo_por_clase = {}
        for nombre in cfg_yolo["clases_persona"]:
            self.tipo_por_clase[nombre] = TIPO_PERSONA
        for nombre in cfg_yolo["clases_senal_alto"]:
            self.tipo_por_clase[nombre] = TIPO_SENAL_ALTO
        for nombre in cfg_yolo["clases_semaforo"]:
            self.tipo_por_clase[nombre] = TIPO_SEMAFORO
        for nombre in cfg_yolo["clases_obstaculo"]:
            self.tipo_por_clase[nombre] = TIPO_OBSTACULO

        # IDs numéricos de las clases que nos interesan. Se los pasamos a
        # YOLO para que descarte el resto desde adentro (un poco más rápido).
        self.ids_clases = [i for i, n in self.nombres.items() if n in self.tipo_por_clase]
        nombres_modelo = set(self.nombres.values())
        for nombre in self.tipo_por_clase:
            if nombre not in nombres_modelo:
                print(f"[YOLO] Aviso: la clase '{nombre}' no existe en el modelo; se ignora.")

        self._contador_cuadros = 0
        self._ultimas_detecciones = []
        print("[YOLO] Modelo listo.")

    def procesar(self, cuadro):
        """
        Devuelve (lista_de_detecciones, se_ejecuto_yolo).

        Si en este cuadro no toca ejecutar YOLO, se regresa la lista
        anterior y se_ejecuto_yolo = False.
        """
        n = max(1, int(self.cfg["cada_n_cuadros"]))
        toca_ejecutar = (self._contador_cuadros % n == 0)
        self._contador_cuadros += 1

        if not toca_ejecutar:
            return self._ultimas_detecciones, False

        self._ultimas_detecciones = self._detectar(cuadro)
        return self._ultimas_detecciones, True

    def _detectar(self, cuadro):
        alto, ancho = cuadro.shape[:2]
        area_cuadro = float(ancho * alto)

        resultados = self.modelo.predict(
            cuadro,
            imgsz=int(self.cfg["imgsz"]),
            conf=float(self.cfg["confianza"]),
            classes=self.ids_clases,
            device="cpu",
            verbose=False,
        )
        resultado = resultados[0]       # una sola imagen -> un solo resultado

        detecciones = []
        cajas = resultado.boxes.xyxy.cpu().numpy()
        confianzas = resultado.boxes.conf.cpu().numpy()
        clases = resultado.boxes.cls.cpu().numpy().astype(int)

        for (x1, y1, x2, y2), confianza, id_clase in zip(cajas, confianzas, clases):
            nombre = self.nombres[int(id_clase)]
            tipo = self.tipo_por_clase.get(nombre)
            if tipo is None:
                continue

            cx = ((x1 + x2) / 2.0) / ancho
            area = ((x2 - x1) * (y2 - y1)) / area_cuadro
            detecciones.append(Deteccion(
                clase=nombre,
                tipo=tipo,
                confianza=float(confianza),
                caja=(int(x1), int(y1), int(x2), int(y2)),
                cx=float(cx),
                zona=calcular_zona(cx, self.cfg_zonas),
                area=float(area),
            ))
        return detecciones
