"""
main.py
-------
Programa principal: une todo en el ciclo del agente inteligente.

    while True:
        1. PERCIBIR  -> leer cuadro, YOLO, OpenCV (semáforo, obstáculos, calidad)
        2. RAZONAR   -> reglas de prioridad + máquina de estados + anti-titubeo
        3. ACTUAR    -> enviar el comando por UDP (otro hilo lo envía cada 100 ms)
        4. Mostrar en pantalla, guardar métricas y leer el teclado

Ejemplos:
    python main.py                       # auto real (IP del config.json)
    python main.py --ip 192.168.1.50     # auto real con otra IP
    python main.py --webcam --sin-auto   # prueba con webcam, sin enviar comandos
    python main.py --webcam --sin-auto --calibrar
"""

import argparse
import time

import cv2

from comunicacion import ClienteUDP
from configuracion import cargar_config, guardar_config
from decision import AgenteDecision, EntradaPercepcion, FiltroAntiTitubeo
from interfaz import NOMBRE_VENTANA, Calibrador, DatosPantalla, Interfaz
from percepcion_opencv import ResultadoObstaculos, analizar_obstaculos, clasificar_semaforo, evaluar_calidad
from percepcion_yolo import TIPO_SEMAFORO
from registro import RegistroCSV, detecciones_a_texto
from video import LectorVideo


def leer_argumentos():
    parser = argparse.ArgumentParser(description="Agente autónomo: Percibir -> Razonar -> Actuar")
    parser.add_argument("--config", default="config.json", help="ruta del archivo de configuración")
    parser.add_argument("--webcam", action="store_true", help="usar la webcam USB en vez del stream del auto")
    parser.add_argument("--sin-auto", action="store_true", help="no enviar comandos UDP (modo de prueba)")
    parser.add_argument("--ip", default=None, help="IP del auto (reemplaza la del config)")
    parser.add_argument("--calibrar", action="store_true", help="abrir las ventanas de calibración al iniciar")
    parser.add_argument("--sin-yolo", action="store_true", help="no cargar YOLO (solo percepción OpenCV)")
    parser.add_argument("--sin-registro", action="store_true", help="no guardar el CSV de métricas")
    return parser.parse_args()


def elegir_semaforo(cuadro, detecciones, config):
    """
    Clasifica el color de cada semáforo detectado y devuelve el color del
    más grande (el más cercano). Si no hay semáforo, devuelve "NINGUNO".
    """
    semaforos = [d for d in detecciones
                 if d.tipo == TIPO_SEMAFORO and d.area >= config["decision"]["area_semaforo_minima"]]
    if not semaforos:
        return "NINGUNO"
    for d in semaforos:
        d.color_semaforo, _, _ = clasificar_semaforo(cuadro, d.caja, config["semaforo"])
    mas_cercano = max(semaforos, key=lambda d: d.area)
    return mas_cercano.color_semaforo


def main():
    args = leer_argumentos()
    config = cargar_config(args.config)
    if args.ip:
        config["auto"]["ip"] = args.ip

    # ------------------------------------------------------------------
    # Preparar cada módulo
    # ------------------------------------------------------------------
    if args.webcam:
        fuente = config["video"]["indice_webcam"]
    else:
        a = config["auto"]
        fuente = f"http://{a['ip']}:{a['puerto_stream']}{a['ruta_stream']}"
    lector = LectorVideo(fuente, args.webcam, config["video"])
    lector.iniciar()

    yolo = None
    if not args.sin_yolo:
        from percepcion_yolo import PercepcionYOLO
        yolo = PercepcionYOLO(config["yolo"], config["zonas"])

    agente = AgenteDecision(config["decision"])
    filtro = FiltroAntiTitubeo(config["decision"])

    cliente = ClienteUDP(config["auto"]["ip"], config["auto"]["puerto_udp"],
                         config["comunicacion"], config["velocidades"], simulado=args.sin_auto)
    cliente.iniciar()

    interfaz = Interfaz(config["interfaz"], config["zonas"], config["video"]["ancho"], config["video"]["alto"])
    calibrador = Calibrador(config)
    if args.calibrar:
        calibrador.abrir()

    registro = None
    if config["registro"]["activado"] and not args.sin_registro:
        registro = RegistroCSV(config["registro"]["carpeta"])

    emergencia = False
    fps = 0.0
    inicio = time.monotonic()
    ultimo_ciclo = time.monotonic()

    print("[MAIN] Listo. ESPACIO=emergencia  P=pausar envío  C=calibrar  G=guardar  Q=salir")

    try:
        while True:
            # ==========================================================
            # 1. PERCIBIR
            # ==========================================================
            cuadro, antiguedad, _es_nuevo = lector.obtener_cuadro(tiempo_espera=0.2)
            p = EntradaPercepcion(emergencia=emergencia)

            # 1a. ¿Hay video reciente?
            if cuadro is None:
                p.video_ok, p.motivo_video = False, "Sin video (aun no llega ningun cuadro)"
            elif antiguedad > config["video"]["max_antiguedad_cuadro_s"]:
                p.video_ok, p.motivo_video = False, f"Video congelado ({antiguedad:.1f} s sin cuadros)"
            else:
                p.video_ok = True

            brillo = nitidez = 0.0
            obstaculos = ResultadoObstaculos()
            yolo_ejecutado = False

            if p.video_ok:
                # 1b. Calidad de la imagen
                p.imagen_confiable, p.motivo_imagen, brillo, nitidez = evaluar_calidad(cuadro, config["calidad_imagen"])

                # 1c. YOLO (personas, señales, semáforos, obstáculos conocidos)
                if yolo is not None:
                    p.detecciones, yolo_ejecutado = yolo.procesar(cuadro)

                # 1d. OpenCV: color del semáforo y obstáculos por bordes
                p.color_semaforo = elegir_semaforo(cuadro, p.detecciones, config)
                obstaculos = analizar_obstaculos(cuadro, config["opencv_obstaculos"], config["zonas"])
                p.ocupacion_opencv = obstaculos.ocupacion
                p.bloqueo_opencv = obstaculos.bloqueada

            # ==========================================================
            # 2. RAZONAR
            # ==========================================================
            decision_cruda = agente.decidir(p)
            decision = filtro.filtrar(decision_cruda)

            # ==========================================================
            # 3. ACTUAR (el hilo de comunicación lo envía cada ~100 ms)
            # ==========================================================
            cliente.establecer_comando(decision.accion)

            # ==========================================================
            # 4. Métricas, pantalla y teclado
            # ==========================================================
            ahora = time.monotonic()
            dt = ahora - ultimo_ciclo
            ultimo_ciclo = ahora
            if dt > 0:
                # Promedio móvil exponencial: el número no "brinca" tanto
                fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps > 0 else 1.0 / dt
            latencia = cliente.latencia_promedio_ms

            datos = DatosPantalla(
                accion=decision.accion, motivo=decision.motivo, regla=decision.regla,
                estado_agente=agente.estado, detecciones=p.detecciones,
                color_semaforo=p.color_semaforo, obstaculos=obstaculos,
                zonas_ocupadas=agente.zonas_ocupadas, fps=fps, latencia_ms=latencia,
                enviados=cliente.enviados, acks=cliente.acks, errores=cliente.errores,
                perdidos=cliente.perdidos, emergencia=emergencia, pausado=cliente.pausado,
                sin_auto=args.sin_auto, video_ok=p.video_ok, brillo=brillo, nitidez=nitidez,
            )
            interfaz.mostrar(cuadro if p.video_ok else None, datos)
            calibrador.leer()
            calibrador.mostrar_vistas(cuadro if p.video_ok else None, obstaculos)

            if registro is not None:
                zonas = agente.zonas_ocupadas
                registro.escribir({
                    "tiempo_s": f"{ahora - inicio:.3f}",
                    "fps": f"{fps:.2f}",
                    "latencia_ms": "" if latencia is None else f"{latencia:.1f}",
                    "accion": decision.accion,
                    "motivo": decision.motivo,
                    "regla": decision.regla,
                    "estado": agente.estado,
                    "emergencia": int(emergencia),
                    "pausado": int(cliente.pausado),
                    "video_ok": int(p.video_ok),
                    "brillo": f"{brillo:.1f}",
                    "nitidez": f"{nitidez:.1f}",
                    "semaforo": p.color_semaforo,
                    "yolo_ejecutado": int(yolo_ejecutado),
                    "num_detecciones": len(p.detecciones),
                    "detecciones": detecciones_a_texto(p.detecciones),
                    "ocup_izq": f"{obstaculos.ocupacion['IZQ']:.3f}",
                    "ocup_centro": f"{obstaculos.ocupacion['CENTRO']:.3f}",
                    "ocup_der": f"{obstaculos.ocupacion['DER']:.3f}",
                    "zona_izq": "OCUPADA" if zonas["IZQ"] else "LIBRE",
                    "zona_centro": "OCUPADA" if zonas["CENTRO"] else "LIBRE",
                    "zona_der": "OCUPADA" if zonas["DER"] else "LIBRE",
                })

            tecla = cv2.waitKey(1) & 0xFF
            if tecla in (ord("q"), ord("Q")):
                break
            elif tecla == ord(" "):
                emergencia = not emergencia
                print(f"[MAIN] Paro de emergencia: {'ACTIVADO' if emergencia else 'desactivado'}")
            elif tecla in (ord("p"), ord("P")):
                cliente.pausado = not cliente.pausado
                print(f"[MAIN] Envío de comandos: {'PAUSADO' if cliente.pausado else 'reanudado'}")
            elif tecla in (ord("c"), ord("C")):
                calibrador.alternar()
            elif tecla in (ord("g"), ord("G")):
                guardar_config(config, args.config)
                print(f"[MAIN] Configuración guardada en {args.config}")

            # Si cerraron la ventana principal con la X, también salimos
            if cv2.getWindowProperty(NOMBRE_VENTANA, cv2.WND_PROP_VISIBLE) < 1:
                break

    except KeyboardInterrupt:
        print("\n[MAIN] Interrumpido con Ctrl+C")
    finally:
        # Salida segura: siempre se manda DETENER varias veces
        print("[MAIN] Saliendo: enviando DETENER...")
        cliente.establecer_comando("DETENER")
        cliente.pausado = False
        cliente.cerrar()
        lector.detener()
        if registro is not None:
            registro.cerrar()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
