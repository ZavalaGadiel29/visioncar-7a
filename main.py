"""
main.py
-------
Programa principal de VisionCar: une todo en el ciclo del agente.

    while True:
        1. PERCIBIR  -> cuadro, calidad, YOLO (personas/obstáculos), señales
                        (modelo propio), carril (OpenCV), obstáculos (OpenCV)
        2. RAZONAR   -> máquina de estados con prioridades (decision.py)
        3. ACTUAR    -> comando UDP (otro hilo lo reenvía cada 100 ms)
        4. NUBE      -> evento a la API (otro hilo, nunca frena este ciclo)
        5. Pantalla, registro CSV y teclado

Ejemplos:
    python main.py                               # carrito real + nube
    python main.py --sin-nube                    # carrito real, sin internet
    python main.py --webcam --sin-auto --sin-nube --calibrar
    python main.py --video pista.mp4 --sin-auto --sin-nube
"""

import argparse
import time

import cv2

from carril import DetectorCarril
from comunicacion import ClienteUDP, Comando
from configuracion import cargar_config, guardar_config
from decision import AgenteDecision, EntradaPercepcion
from interfaz import NOMBRE_VENTANA, Calibrador, DatosPantalla, Interfaz
from nube import ClienteNube
from percepcion_opencv import ResultadoObstaculos, analizar_obstaculos, evaluar_calidad
from registro import RegistroCSV, detecciones_a_texto
from video import LectorVideo


def leer_argumentos():
    parser = argparse.ArgumentParser(description="VisionCar: Percibir -> Razonar -> Actuar")
    parser.add_argument("--config", default="config.json", help="ruta del archivo de configuración")
    parser.add_argument("--webcam", action="store_true", help="usar la webcam USB en vez del stream del carrito")
    parser.add_argument("--video", default=None, help="probar con un video grabado (ej. pista.mp4)")
    parser.add_argument("--sin-auto", action="store_true", help="no enviar comandos UDP (modo de prueba)")
    parser.add_argument("--sin-nube", action="store_true", help="no conectarse a la API (sin internet)")
    parser.add_argument("--ip", default=None, help="IP del carrito (reemplaza la del config)")
    parser.add_argument("--calibrar", action="store_true", help="abrir las ventanas de calibración al iniciar")
    parser.add_argument("--sin-yolo", action="store_true", help="no cargar YOLO COCO (personas/obstáculos)")
    parser.add_argument("--sin-registro", action="store_true", help="no guardar el CSV de métricas")
    return parser.parse_args()


def main():
    args = leer_argumentos()
    config = cargar_config(args.config)
    if args.ip:
        config["auto"]["ip"] = args.ip

    def guardar():
        guardar_config(config, args.config)
        print(f"[MAIN] Configuración guardada en {args.config}")

    # ------------------------------------------------------------------
    # Preparar módulos
    # ------------------------------------------------------------------
    if args.video:
        lector = LectorVideo(args.video, False, config["video"], es_archivo=True)
    elif args.webcam:
        lector = LectorVideo(config["video"]["indice_webcam"], True, config["video"])
    else:
        a = config["auto"]
        lector = LectorVideo(f"http://{a['ip']}:{a['puerto_stream']}{a['ruta_stream']}", False, config["video"])
    lector.iniciar()

    yolo = None
    if not args.sin_yolo:
        from percepcion_yolo import PercepcionYOLO
        yolo = PercepcionYOLO(config["yolo"], config["zonas"])
    from percepcion_yolo import DetectorSenales
    senales = DetectorSenales(config["senales"], config["zonas"])
    detector_carril = DetectorCarril(config["carril"])
    agente = AgenteDecision(config)

    cliente = ClienteUDP(config["auto"]["ip"], config["auto"]["puerto_udp"], config["auto"]["clave"],
                         config["comunicacion"], simulado=args.sin_auto)
    cliente.iniciar()

    nube = ClienteNube(config["nube"], desactivado=args.sin_nube)
    nube.iniciar()

    interfaz = Interfaz(config["interfaz"], config["zonas"], config["video"]["ancho"], config["video"]["alto"])
    calibrador = Calibrador(config, guardar)
    if args.calibrar:
        calibrador.abrir()

    registro = None
    if config["registro"]["activado"] and not args.sin_registro:
        registro = RegistroCSV(config["registro"]["carpeta"])

    emergencia = False
    fps = 0.0
    inicio = time.monotonic()
    ultimo_ciclo = inicio
    decision = None

    print("[MAIN] Listo. ESPACIO=emergencia  P=pausar envío  C=calibrar  G=guardar  Q=salir")

    try:
        while True:
            # ==========================================================
            # 1. PERCIBIR
            # ==========================================================
            cuadro, antiguedad, _ = lector.obtener_cuadro(tiempo_espera=0.2)
            p = EntradaPercepcion(ancho_imagen=config["video"]["ancho"], emergencia=emergencia)

            if cuadro is None:
                p.video_ok, p.motivo_video = False, "Sin video (aun no llega ningun cuadro)"
            elif antiguedad > config["video"]["max_antiguedad_cuadro_s"]:
                p.video_ok, p.motivo_video = False, f"Video congelado ({antiguedad:.1f} s sin cuadros)"
            else:
                p.video_ok = True

            brillo = nitidez = 0.0
            obstaculos = ResultadoObstaculos()
            yolo_ejecutado = False
            if agente.pedir_reinicio_carril:
                detector_carril.reiniciar()
                agente.pedir_reinicio_carril = False

            if p.video_ok:
                p.imagen_confiable, p.motivo_imagen, brillo, nitidez = evaluar_calidad(cuadro, config["calidad_imagen"])
                if yolo is not None:
                    p.detecciones, yolo_ejecutado = yolo.procesar(cuadro)
                p.senales, p.senales_ejecutado = senales.procesar(cuadro)
                p.carril = detector_carril.procesar(cuadro)
                obstaculos = analizar_obstaculos(cuadro, config["opencv_obstaculos"], config["zonas"], config["carril"])
                p.ocupacion_opencv = obstaculos.ocupacion
                p.bloqueo_opencv = obstaculos.bloqueada

            p.manual = nube.control_manual()

            # ==========================================================
            # 2. RAZONAR  y  3. ACTUAR
            # ==========================================================
            decision = agente.decidir(p)
            cliente.establecer_comando(decision.comando)

            # ==========================================================
            # 4. NUBE (solo se encola; el hilo de la nube hace el resto)
            # ==========================================================
            nube.considerar_evento(decision.senal, decision.confianza, decision.accion_nube, decision.modo)

            # ==========================================================
            # 5. Métricas, pantalla y teclado
            # ==========================================================
            ahora = time.monotonic()
            dt = ahora - ultimo_ciclo
            ultimo_ciclo = ahora
            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt) if fps > 0 else 1.0 / dt
            latencia = cliente.latencia_promedio_ms

            datos = DatosPantalla(
                decision=decision, detecciones=p.detecciones, senales=p.senales,
                senal_vista=agente.senal_vista, senal_confirmada=agente.senal_confirmada,
                modelo_senales=senales.mensaje, modelo_senales_ok=senales.disponible,
                carril=p.carril, obstaculos=obstaculos, zonas_ocupadas=agente.zonas_ocupadas,
                fps=fps, latencia_ms=latencia, enviados=cliente.enviados, acks=cliente.acks,
                perdidos=cliente.perdidos, carrito_responde=cliente.responde,
                vigilante=cliente.vigilante_activo, emergencia=emergencia, pausado=cliente.pausado,
                sin_auto=args.sin_auto, video_ok=p.video_ok, brillo=brillo, nitidez=nitidez,
                nube_activa=not args.sin_nube, nube_conectada=nube.conectado, nube_mensaje=nube.mensaje,
                nube_pendientes=nube.pendientes, modo_remoto=nube.modo_remoto,
            )
            interfaz.mostrar(cuadro if p.video_ok else None, datos)
            calibrador.leer()
            calibrador.mostrar_vistas(p.carril if p.video_ok else None, obstaculos)

            if registro is not None:
                vista = agente.senal_vista
                zonas = agente.zonas_ocupadas
                registro.escribir({
                    "tiempo_s": f"{ahora - inicio:.3f}",
                    "fps": f"{fps:.2f}",
                    "latencia_ms": "" if latencia is None else f"{latencia:.1f}",
                    "estado": decision.estado,
                    "comando": str(decision.comando),
                    "accion_nube": decision.accion_nube,
                    "modo": decision.modo,
                    "motivo": decision.motivo,
                    "regla": decision.regla,
                    "senal": decision.senal,
                    "confianza": f"{decision.confianza:.3f}",
                    "senal_vista": vista.clase if vista else "",
                    "conf_vista": f"{vista.confianza:.3f}" if vista else "",
                    "carril_ok": int(p.carril.detectado),
                    "error_carril": f"{p.carril.error:.3f}" if p.carril.detectado else "",
                    "emergencia": int(emergencia),
                    "video_ok": int(p.video_ok),
                    "brillo": f"{brillo:.1f}",
                    "nitidez": f"{nitidez:.1f}",
                    "yolo_ejecutado": int(yolo_ejecutado),
                    "senales_ejecutado": int(p.senales_ejecutado),
                    "num_detecciones": len(p.detecciones) + len(p.senales),
                    "detecciones": detecciones_a_texto(list(p.detecciones) + list(p.senales)),
                    "zona_izq": "OCUPADA" if zonas["IZQ"] else "LIBRE",
                    "zona_centro": "OCUPADA" if zonas["CENTRO"] else "LIBRE",
                    "zona_der": "OCUPADA" if zonas["DER"] else "LIBRE",
                    "nube_ok": "" if args.sin_nube else int(nube.conectado),
                    "pendientes": "" if args.sin_nube else nube.pendientes,
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
                guardar()

            if cv2.getWindowProperty(NOMBRE_VENTANA, cv2.WND_PROP_VISIBLE) < 1:
                break

    except KeyboardInterrupt:
        print("\n[MAIN] Interrumpido con Ctrl+C")
    finally:
        # Salida segura: DETENER varias veces, último evento a la nube y cierre
        print("[MAIN] Saliendo: enviando DETENER...")
        cliente.establecer_comando(Comando.detener())
        cliente.pausado = False
        cliente.cerrar()
        modo = decision.modo if decision is not None else "autonomo"
        nube.registrar("ninguna", 0.0, "detener", modo)
        nube.cerrar()
        lector.detener()
        if registro is not None:
            registro.cerrar()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
