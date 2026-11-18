"""
configuracion.py
----------------
Carga y guarda el archivo config.json, y lee los secretos del archivo .env
(TOKEN_CARRITO, CLAVE_UDP). Los secretos NUNCA van en config.json.

Todos los parámetros del sistema viven en ese archivo; el resto de los
módulos reciben el diccionario ya cargado. Como Python pasa los
diccionarios "por referencia", si la ventana de calibración cambia un
valor, todos los módulos ven el valor nuevo inmediatamente.
"""

import json
import os


def cargar_config(ruta):
    """Lee el archivo JSON y lo devuelve como diccionario."""
    with open(ruta, "r", encoding="utf-8") as archivo:
        return json.load(archivo)


def guardar_config(config, ruta):
    """
    Guarda el diccionario en el archivo JSON.

    Primero se escribe en un archivo temporal y luego se reemplaza el
    original; así, si el programa se cierra a la mitad, no se corrompe
    la configuración.
    """
    ruta_temporal = ruta + ".tmp"
    with open(ruta_temporal, "w", encoding="utf-8") as archivo:
        json.dump(config, archivo, indent=2, ensure_ascii=False)
    os.replace(ruta_temporal, ruta)


def cargar_env(ruta=".env"):
    """Lee un archivo .env sencillo (NOMBRE=valor). No pisa variables ya existentes."""
    if not os.path.exists(ruta):
        return
    with open(ruta, "r", encoding="utf-8") as archivo:
        for linea in archivo:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            nombre, valor = linea.split("=", 1)
            os.environ.setdefault(nombre.strip(), valor.strip().strip('"').strip("'"))


def obtener_clave_udp(ruta_env=".env"):
    """
    Devuelve la clave compartida con el firmware (variable CLAVE_UDP del .env).
    Si falta, devuelve "" y quien la usa decide qué hacer.
    """
    cargar_env(ruta_env)
    return os.getenv("CLAVE_UDP", "").strip()
