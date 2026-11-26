"""
main.py
-------
Programa principal de VisionCar: une todo en el ciclo del agente.

    while True:
        1. PERCIBIR  -> cuadro, calidad, señales (modelo/best.pt), personas y
                        obstáculos (YOLO COCO), carril y obstáculos (OpenCV)
        2. RAZONAR   -> máquina de estados con prioridades (decision.py)
        3. ACTUAR    -> comando UDP (otro hilo lo reenvía cada 100 ms)
        4. NUBE      -> evento a la API (otro hilo, nunca frena este ciclo)
        5. Pantalla, registro CSV y teclado

Modelos: se corren los dos en cada cuadro. Si con los dos el programa iría
a menos de yolo.fps_minimo_dos_modelos (12 FPS), YOLO COCO (personas y
obstáculos) pasa a correr 1 de cada yolo.cada_n_cuadros_si_lento (2) cuadros.

Ejemplos:
    python main.py                               # carrito real + nube
    python main.py --sin-motores                 # prueba en la mesa: todo menos UDP
    python main.py --sin-nube                    # carrito real, sin internet
    python main.py --webcam --sin-auto --sin-nube --calibrar
    python main.py --video pista.mp4 --sin-auto --sin-nube
"""

import argparse
import dataclasses
import time

import cv2

from carril import DetectorCarril, pies_en_camino
from comunicacion import ClienteUDP, Comando
from configuracion import cargar_config, guardar_config, obtener_clave_udp
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
    parser.add_argument("--sin-motores", "--sin-auto", dest="sin_motores", action="store_true",
                        help="hace todo (video, detección, decisiones, nube) pero NO manda comandos UDP")
    parser.add_argument("--sin-nube", action="store_true", help="no conectarse a la API (sin internet)")
    parser.add_argument("--ip", default=None, help="IP del carrito (reemplaza la del config)")
    parser.add_argument("--calibrar", action="store_true", help="abrir las ventanas de calibración al iniciar")
    parser.add_argument("--sin-yolo", action="store_true", help="no cargar YOLO COCO (personas/obstáculos)")
    parser.add_argument("--sin-registro", action="store_true", help="no guardar el CSV de métricas")
    return parser.parse_args()


def ajustar_ritmo_personas(yolo, senales, ms_otros, cfg_yolo):
    """
    Decide si YOLO COCO (personas y obstáculos) corre en cada cuadro o 1 de cada 2.
    Devuelve el FPS estimado con los dos modelos en cada cuadro (o None si
    todavía no hay mediciones). Hay un margen de 1 FPS para que no cambie
    de un lado a otro a cada rato.
    """
    if ms_otros is None or yolo.ms_promedio is None:
        return None
    ms_total = ms_otros + yolo.ms_promedio + (senales.ms_promedio or 0.0)
    fps_estimado = 1000.0 / ms_total if ms_total > 0 else 999.0
    minimo = float(cfg_yolo["fps_minimo_dos_modelos"])
    normal = max(1, int(cfg_yolo["cada_n_cuadros"]))
    lento = max(normal, int(cfg_yolo["cada_n_cuadros_si_lento"]))
    if yolo.cada_n == normal and fps_estimado < minimo:
        yolo.cada_n = lento
        print(f"[MAIN] Con los dos modelos iría a {fps_estimado:.1f} FPS (< {minimo:.0f}): "
              f"COCO cada {lento} cuadros.")
    elif yolo.cada_n != normal and fps_estimado >= minimo + 1.0:
        yolo.cada_n = normal
        print(f"[MAIN] Con los dos modelos iría a {fps_estimado:.1f} FPS: COCO en cada cuadro.")
    return fps_estimado


def texto_ajuste(cfg_carril, pd, decision):
    """Líneas para la tecla K: parámetros del control y diferencia actual de ruedas."""
    comando = decision.comando
    if comando.nombre == "MOVER":
        dif = f"{comando.p1 - comando.p2:+.0f}  ({comando.p1:.0f} / {comando.p2:.0f})"
    else:
        dif = f"- ({comando.nombre})"
    return [
        f"kp {cfg_carril['kp']}   kd {cfg_carril['kd']}   suavizado {cfg_carril['suavizado_error']}",
        f"max dif {cfg_carril['max_diferencia_ruedas']}   max cambio/cuadro {cfg_carril['max_cambio_por_cuadro']}",
        f"error filtrado {pd.ultimo_error_filtrado:+.2f}   angulo {pd.ultimo_angulo:+.2f}   "
        f"i {pd.termino_i:+.2f}",
        f"error control {pd.ultimo_error_control:+.2f}   giro {pd.modo_giro}",
        f"dif ruedas (izq-der): {dif}",
    ]


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
    # config.json -> yolo -> usar_coco = false: NO se carga COCO. Peatones = clase
    # 'peaton' de best_v2.pt; obstáculos = solo el detector de bordes de OpenCV.
    usar_coco = bool(config["yolo"].get("usar_coco", True))
    if not usar_coco:
        print("[MAIN] usar_coco = false: sin modelo COCO (peatones con best_v2.pt, obstaculos con OpenCV).")
    if not args.sin_yolo and usar_coco:
        from percepcion_yolo import PercepcionYOLO
        yolo = PercepcionYOLO(config["yolo"], config["zonas"])
    from percepcion_yolo import TIPO_PEATON, DetectorSenales
    senales = DetectorSenales(config["senales"], config["zonas"])
    detector_carril = DetectorCarril(config["carril"])
    agente = AgenteDecision(config)

    # La clave UDP es un secreto: se lee de .env (CLAVE_UDP), no de config.json
    clave_udp = obtener_clave_udp(config["nube"].get("archivo_env", ".env"))
    if not clave_udp and not args.sin_motores:
        raise SystemExit("Falta CLAVE_UDP en el archivo .env (copia .env.example como .env y ponla). "
                         "Para probar sin mover el carrito usa --sin-motores.")
    cliente = ClienteUDP(config["auto"]["ip"], config["auto"]["puerto_udp"], clave_udp,
                         config["comunicacion"], simulado=args.sin_motores)
    if args.sin_motores:
        print("[MAIN] --sin-motores: NO se mandan comandos UDP al carrito (todo lo demás funciona).")
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
    ms_otros = None            # ms por ciclo de todo lo que NO es esperar video ni los modelos
    fps_estimado = None        # FPS que habría corriendo los dos modelos en cada cuadro
    ciclos_medidos = 0         # los primeros ciclos son lentos (se abre la ventana): no cuentan
    inicio = time.monotonic()
    ultimo_ciclo = inicio
    decision = None
    # Cuadros negros/vacíos (brillo < brillo_cuadro_vacio): con solo_linea se ignoran,
    # se repite el último comando y ese tiempo no cuenta como "sin línea".
    solo_linea = bool(config["carril"].get("solo_linea", False))
    vacio_desde = None         # desde cuándo llegan cuadros vacíos seguidos
    ultimo_carril = None       # último resultado del carril (para la pantalla en un cuadro vacío)
    aviso_calibracion, aviso_hasta = "", 0.0
    mostrar_ajuste = False     # tecla K: kp, kd, suavizado y diferencia de ruedas en pantalla
    debug_guia = False         # tecla D: candidatos a guion de la línea guía y su motivo

    print("[MAIN] Listo. ESPACIO=emergencia  P=pausar envío  C=calibrar asfalto/amarillo  "
          "T=trackbars  K=ver ajuste PD  D=debug guia  G=guardar  Q=salir")

    try:
        while True:
            # ==========================================================
            # 1. PERCIBIR
            # ==========================================================
            cuadro, antiguedad, _ = lector.obtener_cuadro(tiempo_espera=0.2)
            inicio_proceso = time.perf_counter()
            ms_modelos = 0.0
            p = EntradaPercepcion(ancho_imagen=config["video"]["ancho"], alto_imagen=config["video"]["alto"],
                                  emergencia=emergencia)

            if cuadro is None:
                p.video_ok, p.motivo_video = False, "Sin video (aun no llega ningun cuadro)"
            elif antiguedad > config["video"]["max_antiguedad_cuadro_s"]:
                p.video_ok, p.motivo_video = False, f"Video congelado ({antiguedad:.1f} s sin cuadros)"
            else:
                p.video_ok = True

            brillo = nitidez = 0.0
            obstaculos = ResultadoObstaculos()
            todas_detecciones = []     # incluye las "fuera de pista" (solo para dibujar)
            yolo_ejecutado = False
            if agente.pedir_reinicio_carril:
                detector_carril.reiniciar()
                agente.pedir_reinicio_carril = False

            cuadro_vacio = False
            if p.video_ok:
                p.imagen_confiable, p.motivo_imagen, brillo, nitidez = evaluar_calidad(cuadro, config["calidad_imagen"])
                cc = config["calidad_imagen"]
                # Borrosa (con brillo suficiente): decision.py la tolera max_borrosa_s si va en movimiento
                p.imagen_borrosa = (not p.imagen_confiable and brillo >= cc["brillo_minimo"]
                                    and nitidez < cc["nitidez_minima"])
                if (solo_linea and brillo < float(cc.get("brillo_cuadro_vacio", 15)) and not emergencia
                        and decision is not None):
                    ahora_v = time.monotonic()
                    if vacio_desde is None:
                        vacio_desde = ahora_v
                    # Si duran demasiado ya no es un cuadro suelto: se trata como imagen oscura (DETENER)
                    cuadro_vacio = ahora_v - vacio_desde <= float(cc.get("max_cuadros_vacios_s", 1.0))
                elif vacio_desde is not None:
                    # Llegó un cuadro bueno: el tiempo de los vacíos no cuenta como "sin línea"
                    detector_carril.compensar_pausa(time.monotonic() - vacio_desde)
                    vacio_desde = None
                if cuadro_vacio and ultimo_carril is not None:
                    p.carril = ultimo_carril

            if p.video_ok and not cuadro_vacio:
                t0 = time.perf_counter()
                del_modelo, p.senales_ejecutado = senales.procesar(cuadro)
                # 'peaton' del modelo propio NO es señal: va aparte (regla de peatón)
                p.senales = [d for d in del_modelo if d.tipo != TIPO_PEATON]
                peatones_modelo = [d for d in del_modelo if d.tipo == TIPO_PEATON]
                if yolo is not None:
                    p.detecciones, yolo_ejecutado = yolo.procesar(cuadro)
                ms_modelos = (time.perf_counter() - t0) * 1000.0
                # Bifurcaciones: la señal reciente manda (izquierda/derecha = esa rama,
                # avanzar = la más recta). GIRO_DIRIGIDO: seguir SOLO la orilla de ese lado.
                detector_carril.preferencia_temporal = agente.preferencia_carril()
                detector_carril.orilla_forzada = agente.orilla_forzada()
                p.carril = detector_carril.procesar(cuadro)
                ultimo_carril = p.carril
                # Personas y obstáculos solo cuentan si sus pies están sobre el asfalto
                # y en la mitad de abajo de la imagen. Los demás se ignoran (gris).
                todas_detecciones = list(p.detecciones) + peatones_modelo
                cd = config["decision"]
                if cd.get("solo_en_pista", True):
                    for d in todas_detecciones:
                        en_camino, motivo, d.en_camino_forzado = pies_en_camino(
                            cuadro, d.caja, config["carril"], int(cd["radio_pies_px"]),
                            cd["altura_minima_pies"], p.carril)
                        d.fuera_pista = not en_camino
                        d.motivo_ignorado = "" if en_camino else motivo
                    p.detecciones = [d for d in p.detecciones if not d.fuera_pista]
                    p.peatones_fuera = [d for d in peatones_modelo if d.fuera_pista]   # solo para avisar
                    peatones_modelo = [d for d in peatones_modelo if not d.fuera_pista]
                p.peatones_modelo = peatones_modelo
                obstaculos = analizar_obstaculos(cuadro, config["opencv_obstaculos"], config["zonas"], config["carril"])
                p.ocupacion_opencv = obstaculos.ocupacion
                p.bloqueo_opencv = obstaculos.bloqueada

            p.manual = nube.control_manual()

            # ==========================================================
            # 2. RAZONAR  y  3. ACTUAR
            # ==========================================================
            if cuadro_vacio:
                # Cuadro negro/vacío: NO se decide nada; el hilo UDP sigue mandando el último comando
                decision = dataclasses.replace(
                    decision, motivo=f"Cuadro vacio (brillo {brillo:.0f}): repito el ultimo comando")
            else:
                decision = agente.decidir(p)
                cliente.establecer_comando(decision.comando)   # con --sin-motores no sale nada por UDP

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
                decision=decision, detecciones=todas_detecciones, senales=p.senales,
                senal_vista=agente.senal_vista, senal_confirmada=agente.senal_confirmada,
                modelo_senales=senales.mensaje, modelo_senales_ok=senales.disponible,
                carril=p.carril, obstaculos=obstaculos, zonas_ocupadas=agente.zonas_ocupadas,
                fps=fps, latencia_ms=latencia, enviados=cliente.enviados, acks=cliente.acks,
                perdidos=cliente.perdidos, carrito_responde=cliente.responde,
                vigilante=cliente.vigilante_activo, emergencia=emergencia, pausado=cliente.pausado,
                sin_auto=args.sin_motores, video_ok=p.video_ok, brillo=brillo, nitidez=nitidez,
                nube_activa=not args.sin_nube, nube_conectada=nube.conectado, nube_mensaje=nube.mensaje,
                nube_pendientes=nube.pendientes, modo_remoto=nube.modo_remoto,
                senal_activa=agente.senal_activa,
                altura_activacion=config["decision"]["altura_activacion_senal"],
                personas_cada_n=yolo.cada_n if yolo is not None else 0, fps_estimado=fps_estimado,
                aviso=aviso_calibracion if ahora < aviso_hasta else "",
                ajuste=texto_ajuste(config["carril"], agente.pd, decision) if mostrar_ajuste else None,
                debug_guia=debug_guia,
            )
            interfaz.mostrar(cuadro if p.video_ok else None, datos)
            calibrador.leer()
            calibrador.mostrar_vistas(p.carril if p.video_ok else None, obstaculos)

            if registro is not None:
                vista = agente.senal_vista
                cmd = decision.comando
                if cmd.nombre == "MOVER":
                    ruedas = (f"{cmd.p1:.0f}", f"{cmd.p2:.0f}")
                elif cmd.nombre == "DETENER":
                    ruedas = ("0", "0")
                else:
                    ruedas = ("", "")
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
                    "modo_carril": p.carril.modo,
                    "emergencia": int(emergencia),
                    "video_ok": int(p.video_ok),
                    "brillo": f"{brillo:.1f}",
                    "nitidez": f"{nitidez:.1f}",
                    "yolo_ejecutado": int(yolo_ejecutado),
                    "senal_activa": agente.senal_activa or "",
                    "personas_cada_n": yolo.cada_n if yolo is not None else "",
                    "memoria_linea": f"{p.carril.segundos_sin_linea:.2f}",
                    "angulo_linea": f"{p.carril.angulo_linea:.3f}",
                    "termino_i": f"{agente.pd.termino_i:.3f}",
                    "rueda_izq": ruedas[0],
                    "rueda_der": ruedas[1],
                    "senales_ejecutado": int(p.senales_ejecutado),
                    "num_detecciones": len(p.detecciones) + len(p.peatones_modelo) + len(p.senales),
                    "detecciones": detecciones_a_texto(list(p.detecciones) + list(p.peatones_modelo) + list(p.senales)),
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
                if cuadro is not None and p.video_ok:
                    aviso_calibracion = detector_carril.calibrar(cuadro)
                    aviso_hasta = time.monotonic() + 4.0
                    calibrador.sincronizar()
                    print(f"[MAIN] {aviso_calibracion}")
                else:
                    print("[MAIN] No se puede calibrar: no hay video")
            elif tecla in (ord("k"), ord("K")):
                mostrar_ajuste = not mostrar_ajuste
            elif tecla in (ord("d"), ord("D")):
                debug_guia = not debug_guia
                print(f"[MAIN] Debug de la linea guia: {'ON' if debug_guia else 'OFF'}")
            elif tecla in (ord("t"), ord("T")):
                calibrador.alternar()
            elif tecla in (ord("g"), ord("G")):
                guardar()

            if cv2.getWindowProperty(NOMBRE_VENTANA, cv2.WND_PROP_VISIBLE) < 1:
                break

            # Ritmo de YOLO COCO: se estima el FPS con los DOS modelos en cada
            # cuadro, contando TODO el ciclo (percepción, decisión, pantalla,
            # CSV y teclado) menos la espera del video. Si es menor al mínimo,
            # COCO corre cada 2 cuadros. Se mide al final para incluir la pantalla.
            if p.video_ok:
                ciclos_medidos += 1
                ms_ciclo = (time.perf_counter() - inicio_proceso) * 1000.0
                otros = max(0.0, ms_ciclo - ms_modelos)
                if ciclos_medidos > 10:
                    ms_otros = otros if ms_otros is None else 0.9 * ms_otros + 0.1 * otros
            if yolo is not None and ciclos_medidos >= 30:
                fps_estimado = ajustar_ritmo_personas(yolo, senales, ms_otros, config["yolo"])

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
