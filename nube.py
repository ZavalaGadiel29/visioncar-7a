"""
nube.py
-------
Conexión del carrito con la API en la nube (la de la carpeta api/).

Hace dos cosas, cada una en su propio HILO para no frenar el ciclo de visión:

1. ENVÍO DE EVENTOS  ->  POST {api_url}/api/v1/eventos
   - El ciclo principal solo mete eventos en una COLA (instantáneo).
   - Se manda un evento cuando cambia la acción o la señal, y cada 2 s
     aunque no cambie nada (para que el dashboard vea que el carrito vive).
   - Formato exacto del contrato:
       {"carrito_id","fecha","senal","confianza","accion","modo"}
   - Sin internet: los eventos se guardan en pendientes.jsonl (uno por
     línea) y se suben en lotes cuando vuelve la conexión.
   - Si la API rechaza un evento por datos inválidos (422), se guarda en
     rechazados.jsonl para revisarlo y no se reintenta para siempre.

2. CONTROL REMOTO  ->  GET {api_url}/api/v1/control/{carrito_id} cada 300 ms
   - Si la app puso modo "manual", el carrito obedece la acción manual.
   - Seguridad: en modo manual, si la nube no responde en 1 s -> DETENER.

El token (TOKEN_CARRITO) se lee del archivo .env, nunca del código.
"""

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime

from configuracion import cargar_env

SENALES = ("alto", "avanzar", "izquierda", "derecha", "ninguna", "peaton")
ACCIONES = ("avanzar", "detener", "girar_izq", "girar_der")
MODOS = ("autonomo", "manual")




class ClienteNube:
    def __init__(self, cfg_nube, desactivado=False):
        self.cfg = cfg_nube
        self.desactivado = desactivado
        cargar_env(cfg_nube.get("archivo_env", ".env"))
        self.api_url = os.getenv("API_URL", cfg_nube["api_url"]).rstrip("/")
        self.carrito_id = cfg_nube["carrito_id"]
        self.token = os.getenv("TOKEN_CARRITO", "")

        self._cola = queue.Queue(maxsize=5000)
        self._candado = threading.Lock()
        self._activo = False

        # Estado para la interfaz
        self.conectado = False
        self.mensaje = "nube desactivada (--sin-nube)" if desactivado else "conectando..."
        self.enviados = 0
        self.descartados = 0              # eventos perdidos porque la cola se llenó
        self._en_archivo = self._contar_pendientes()
        self._proximo_reintento = 0.0

        # Control remoto: (modo, accion, hora_monotonic_de_la_respuesta)
        self._control = None

        # Para decidir cuándo mandar un evento
        self._ultimo_evento = None        # (senal, accion, modo)
        self._hora_ultimo_evento = 0.0
        self._cambio_pendiente = False

        if not desactivado and not self.token:
            self.mensaje = "falta TOKEN_CARRITO en .env"
            print("[NUBE] Aviso: no hay TOKEN_CARRITO en .env; la API responderá 401.")

    # ==================================================================
    # Arranque y cierre
    # ==================================================================
    def iniciar(self):
        if self.desactivado:
            return
        self._activo = True
        threading.Thread(target=self._bucle_envio, daemon=True).start()
        threading.Thread(target=self._bucle_control, daemon=True).start()
        print(f"[NUBE] API: {self.api_url}  carrito: {self.carrito_id}")

    def cerrar(self, espera_s=2.0):
        """Da un momento para subir lo que queda; lo que no suba va a pendientes.jsonl."""
        if self.desactivado:
            return
        limite = time.monotonic() + espera_s
        while not self._cola.empty() and time.monotonic() < limite:
            time.sleep(0.1)
        self._activo = False
        restantes = self._sacar_de_cola(10_000)
        if restantes:
            self._guardar_pendientes(restantes)

    @property
    def pendientes(self):
        """Eventos que aún no llegan a la nube (en cola + en el archivo)."""
        return self._cola.qsize() + self._en_archivo

    # ==================================================================
    # Lo que usa el programa principal
    # ==================================================================
    def considerar_evento(self, senal, confianza, accion, modo):
        """
        Se llama en cada cuadro. Decide si toca mandar un evento:
        - cambió la señal, la acción o el modo, o
        - pasaron 'intervalo_evento_s' (2 s) desde el último.
        Para no saturar la API, entre dos eventos pasan al menos
        'intervalo_minimo_eventos_s'; un cambio que llega antes se manda
        en cuanto se cumple ese tiempo (con los datos más recientes).
        """
        if self.desactivado:
            return
        ahora = time.monotonic()
        clave = (senal, accion, modo)
        if clave != self._ultimo_evento:
            self._cambio_pendiente = True
        transcurrido = ahora - self._hora_ultimo_evento
        toca = (self._cambio_pendiente and transcurrido >= self.cfg["intervalo_minimo_eventos_s"]) \
            or transcurrido >= self.cfg["intervalo_evento_s"]
        if toca:
            self.registrar(senal, confianza, accion, modo)
            self._ultimo_evento = clave
            self._hora_ultimo_evento = ahora
            self._cambio_pendiente = False

    def registrar(self, senal, confianza, accion, modo):
        """Arma el evento con el formato exacto del contrato y lo mete a la cola."""
        if self.desactivado:
            return
        if senal not in SENALES or accion not in ACCIONES or modo not in MODOS:
            print(f"[NUBE] Evento fuera de contrato, no se envía: {senal} {accion} {modo}")
            return
        evento = {
            "carrito_id": self.carrito_id,
            "fecha": datetime.now().isoformat(timespec="seconds"),
            "senal": senal,
            "confianza": round(min(max(float(confianza), 0.0), 1.0), 3),
            "accion": accion,
            "modo": modo,
        }
        try:
            self._cola.put_nowait(evento)
        except queue.Full:
            self.descartados += 1

    def control_manual(self):
        """
        Devuelve (accion, motivo) si la app tiene el carrito en modo manual,
        o None si está en modo autónomo.
        """
        if self.desactivado or self._control is None:
            return None
        with self._candado:
            modo, accion, hora = self._control
        if modo != "manual":
            return None
        if time.monotonic() - hora > self.cfg["max_sin_respuesta_control_s"]:
            return "detener", "La nube no responde (>1 s): DETENER"
        return accion, ""

    @property
    def modo_remoto(self):
        if self._control is None:
            return "?"
        return self._control[0]

    # ==================================================================
    # HTTP (urllib, de la biblioteca estándar)
    # ==================================================================
    def _peticion(self, metodo, ruta, cuerpo=None, timeout=5.0):
        """Devuelve (codigo_http, datos_json). codigo 0 = no hubo conexión."""
        datos = None if cuerpo is None else json.dumps(cuerpo).encode("utf-8")
        peticion = urllib.request.Request(
            self.api_url + ruta, data=datos, method=metodo,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json",
                     "User-Agent": "visioncar-carrito"})
        try:
            with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
                texto = respuesta.read().decode("utf-8")
                return respuesta.status, (json.loads(texto) if texto else None)
        except urllib.error.HTTPError as error:
            try:
                return error.code, json.loads(error.read().decode("utf-8"))
            except (ValueError, OSError):
                return error.code, None
        except (urllib.error.URLError, OSError, ValueError):
            return 0, None

    # ==================================================================
    # Hilo 1: envío de eventos
    # ==================================================================
    def _bucle_envio(self):
        while self._activo:
            lote = self._sacar_de_cola(int(self.cfg["lote_maximo"]), espera=0.5)
            ahora = time.monotonic()
            if lote:
                # Si hay atrasados o estamos esperando para reintentar,
                # lo nuevo se forma detrás (así se conserva el orden).
                if self._en_archivo > 0 or ahora < self._proximo_reintento:
                    self._guardar_pendientes(lote)
                elif not self._enviar_lote(lote):
                    self._guardar_pendientes(lote)
            if self._en_archivo > 0 and time.monotonic() >= self._proximo_reintento:
                self._subir_pendientes()

    def _sacar_de_cola(self, maximo, espera=0.0):
        lote = []
        try:
            lote.append(self._cola.get(timeout=espera) if espera > 0 else self._cola.get_nowait())
            while len(lote) < maximo:
                lote.append(self._cola.get_nowait())
        except queue.Empty:
            pass
        return lote

    def _enviar_lote(self, lote):
        """
        True = ya no hay que reintentar (se guardó, o se rechazó por inválido).
        False = falló la conexión: reintentar más tarde.
        """
        cuerpo = lote[0] if len(lote) == 1 else lote
        codigo, respuesta = self._peticion("POST", "/api/v1/eventos", cuerpo,
                                           timeout=self.cfg["timeout_envio_s"])
        if 200 <= codigo < 300:
            self.enviados += len(lote)
            self._marcar(True, "nube OK")
            return True
        if codigo in (400, 413, 422):
            self._guardar_jsonl(self.cfg["archivo_rechazados"], lote)
            print(f"[NUBE] La API rechazó {len(lote)} evento(s) ({codigo}): {respuesta}")
            self._marcar(True, f"eventos rechazados ({codigo})")
            return True
        if codigo in (401, 403):
            self._marcar(False, f"token rechazado ({codigo}): revisa TOKEN_CARRITO")
        elif codigo == 429:
            self._marcar(True, "demasiadas peticiones (429), reintentando")
        elif codigo == 0:
            self._marcar(False, "sin conexion: guardando en pendientes")
        else:
            self._marcar(False, f"error del servidor ({codigo})")
        self._proximo_reintento = time.monotonic() + self.cfg["reintento_s"]
        return False

    def _subir_pendientes(self):
        """Sube pendientes.jsonl por lotes; lo que no se pueda subir se queda en el archivo."""
        ruta = self.cfg["archivo_pendientes"]
        eventos = []
        try:
            with open(ruta, "r", encoding="utf-8") as archivo:
                for linea in archivo:
                    try:
                        eventos.append(json.loads(linea))
                    except ValueError:
                        continue          # línea dañada (p. ej. apagón a mitad de escritura)
        except FileNotFoundError:
            self._en_archivo = 0
            return

        tamano = int(self.cfg["lote_pendientes"])
        subidos = 0
        while subidos < len(eventos) and self._activo:
            if not self._enviar_lote(eventos[subidos:subidos + tamano]):
                break
            subidos += tamano
        restantes = eventos[subidos:]
        temporal = ruta + ".tmp"
        with open(temporal, "w", encoding="utf-8") as archivo:
            for evento in restantes:
                archivo.write(json.dumps(evento, ensure_ascii=False) + "\n")
        os.replace(temporal, ruta)
        self._en_archivo = len(restantes)
        if subidos and not restantes:
            print(f"[NUBE] Se subieron todos los pendientes ({len(eventos)}).")

    def _guardar_pendientes(self, lote):
        self._guardar_jsonl(self.cfg["archivo_pendientes"], lote)
        self._en_archivo += len(lote)

    @staticmethod
    def _guardar_jsonl(ruta, lote):
        with open(ruta, "a", encoding="utf-8") as archivo:
            for evento in lote:
                archivo.write(json.dumps(evento, ensure_ascii=False) + "\n")

    def _contar_pendientes(self):
        try:
            with open(self.cfg["archivo_pendientes"], "r", encoding="utf-8") as archivo:
                return sum(1 for linea in archivo if linea.strip())
        except FileNotFoundError:
            return 0

    def _marcar(self, conectado, mensaje):
        self.conectado = conectado
        self.mensaje = mensaje

    # ==================================================================
    # Hilo 2: consulta del control remoto
    # ==================================================================
    def _bucle_control(self):
        ruta = f"/api/v1/control/{self.carrito_id}"
        while self._activo:
            inicio = time.monotonic()
            codigo, datos = self._peticion("GET", ruta, timeout=self.cfg["timeout_control_s"])
            if codigo == 200 and isinstance(datos, dict) and datos.get("modo") in MODOS:
                accion = datos.get("accion") if datos.get("accion") in ACCIONES else "detener"
                with self._candado:
                    self._control = (datos["modo"], accion, time.monotonic())
                if self._en_archivo == 0 and self._cola.empty():
                    self._marcar(True, "nube OK")
            elif codigo in (401, 403):
                self._marcar(False, f"token rechazado ({codigo}): revisa TOKEN_CARRITO")
            elif codigo == 0:
                self._marcar(False, "sin conexion con la API")
            espera = self.cfg["intervalo_control_s"] - (time.monotonic() - inicio)
            if espera > 0:
                time.sleep(espera)
