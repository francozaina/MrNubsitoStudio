#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
MR NUBSITO STUDIO v4.0 - Motor Escalable de Edición Automática (Voz IA + Assets)
================================================================================
USO POR CONSOLA:
  python "Mr Nubsito Studio.py" [video_input.mp4] [guion_video.txt o .json]

O simplemente ejecuta:
  python "Mr Nubsito Studio.py"
  (Buscará automáticamente 'video_input.mp4' y 'guion_video.txt' / 'guion_video.json')
================================================================================
"""

import asyncio
import json
import math
import os
import re
import subprocess
import sys
import unicodedata
import urllib.request
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# Bandera de Windows para evitar que se abran ventanas de terminal negras al invocar FFmpeg, yt-dlp, etc.
SUBPROCESS_NO_WINDOW = (
    subprocess.CREATE_NO_WINDOW if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0
)

try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    HAS_EDGE_TTS = False


# ==============================================================================
# 1. CONFIGURACIÓN GENERAL DEL MOTOR
# ==============================================================================
WIDTH = 1080
HEIGHT = 1920
TOP_HEIGHT = 620          # Mitad superior: Mr Nubsito (1080x620)
BOTTOM_HEIGHT = 1300      # Mitad inferior: Video de entrada (1080x1300)
FPS = 60
CPU_THREADS = 12

# MODO DE PANTALLA POR DEFECTO:
# "split" -> Mr Nubsito arriba (1080x620) + Video abajo (1080x1300) + Subs en la divisoria
# "full"  -> Video en pantalla completa (1080x1920) + Subs MrBeast en el centro-abajo
MODO_PANTALLA_DEFAULT = "split"

# Volumen de la voz de Mr Nubsito (1.0 = normal, 1.45 = +45% más fuerte y presente)
VOLUMEN_VOZ_YOUTUBER = 1.45

# Volumen del audio original del video de fondo (0.0 = muteado, 0.12 = 12% suave de fondo)
VOLUMEN_AUDIO_VIDEO_ORIGINAL = 0.12

# Motores de Voz (ElevenLabs opcional o Edge-TTS neuronal)
ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "pNInz6obpgDQGcFmaJgB")

EDGE_VOICE = "it-IT-GiuseppeMultilingualNeural"
EDGE_RATE = "+12%"
EDGE_PITCH = "+0Hz"

SUBTITLE_OFFSET_SEC = -0.02

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
GESTOS_DIR = ASSETS_DIR / "gestos"
EMOJIS_DIR = ASSETS_DIR / "emojis"
FONTS_DIR = ASSETS_DIR / "fonts"
SFX_DIR = ASSETS_DIR / "sfx"
LIKE_SUB_PATH = ASSETS_DIR / "Like&Sub.mp4"
TIMER_PATH = ASSETS_DIR / "timer.mp4"
DURACION_TIMER_SEG = 3.5
TEMP_DIR = BASE_DIR / "temp_build"
OUTPUT_DIR = BASE_DIR / "output_shorts"

DEFAULT_VIDEO_INPUT = BASE_DIR / "video_input.mp4"
DEFAULT_GUION_TXT = BASE_DIR / "guion_video.txt"
DEFAULT_GUION_JSON = BASE_DIR / "guion_video.json"


# ==============================================================================
# 2. ESTRUCTURAS DE DATOS
# ==============================================================================
@dataclass
class SegmentoVideoGuion:
    id_segmento: str
    texto_voz: str
    gesto_personaje: str = "hablando"        # "idle", "saludo", "hablando", "hablando2", "like" (o cualquier .mp4 en assets/gestos)
    t_inicio_video: Optional[float] = None   # Segundos en el video original (None = automático proporcional)
    t_fin_video: Optional[float] = None      # Segundos en el video original (None = automático proporcional)
    pausa_idle_final_seg: float = 0.18
    sfx_inicio: Optional[str] = None         # "ding", "shock", "tictac", "whoosh", None
    emoji: Optional[str] = None              # "diablo", "enojo", "fiesta", "lloro", "risa", "sorprendido", "sueno", None
    posicion_emoji: str = "derecha"          # "derecha", "izquierda", "centro", "arriba_der", "arriba_izq"
    flecha: Optional[str] = None             # "auto", "x,y", "x,y@mm:ss", zona ("izquierda"...), None. Máx 1 por video.
    mostrar_like_sub: bool = False           # True = superpone el video Like&Sub.mp4 sin fondo verde
    mostrar_timer: bool = False              # True = al terminar la pregunta, muestra timer.mp4 (3.5s) con cuenta atrás
    duracion_timer_seg: float = DURACION_TIMER_SEG


@dataclass
class ConfigGuion:
    titulo_superior: str = ""
    modo_pantalla: str = MODO_PANTALLA_DEFAULT
    archivo_salida: Optional[str] = None
    volumen_video_original: float = VOLUMEN_AUDIO_VIDEO_ORIGINAL
    palabras_verdes_extra: Set[str] = field(default_factory=set)
    palabras_rojas_extra: Set[str] = field(default_factory=set)
    segmentos: List[SegmentoVideoGuion] = field(default_factory=list)


@dataclass
class PalabraSubtitulo:
    texto: str
    inicio_seg: float
    fin_seg: float


# ==============================================================================
# 3. UTILIDADES DE NORMALIZACIÓN Y LECTOR UNIVERSAL DE GUIÓN (.TXT Y .JSON)
# ==============================================================================
PLANTILLA_GUION_GENERICA = """TITULO_SUPERIOR: Celular Nuevo Vs Un Ano Despues
MODO_PANTALLA: split
ARCHIVO_SALIDA: auto

[GUION]
TIEMPO | GESTO | SFX | EMOJI | FLECHA | LIKE_SUB | TIMER | TEXTO
00:00 - 00:05 | hablando | crowd_shocked | fiesta:derecha | ninguna | no | no | ¡El primer día con celular nuevo lo besas y lo cuidas como si fuera de oro puro!
00:05 - 00:09 | hablando2 | awww+good_idea | risa:izquierda | ninguna | no | no | ¡Lo limpias suave con un pañuelo y hasta lo acuestas a dormir cómodo en tu propia almohada!
00:09 - 00:14 | hablando | punch+xp_Error | sorprendido:derecha | ninguna | no | no | ¡Pero cuando pasa un año entero lo tiras desde la puerta y rebota durísimo contra todo el piso!
00:14 - 00:18 | hablando2 | whoosh+cartoon_laugh | diablo:izquierda | 48,48@00:14.5 | no | no | ¡Después usas la pantalla con los dedos llenos de Cheetos picantes sin que te importe mancharlo!
00:18 - 00:22 | hablando | error+fart | enojo:derecha | ninguna | no | no | ¡Y cuando suena la alarma temprano lo revoleas directo contra la pared para seguir durmiendo!
"""


def _quitar_acentos(texto: str) -> str:
    """Convierte á->a, ñ->n, etc. para comparar nombres de archivos sin errores de codificación."""
    norm = unicodedata.normalize("NFD", texto)
    return "".join(c for c in norm if unicodedata.category(c) != "Mn")


def _parsear_tiempo_a_segundos(t_str: str) -> Optional[float]:
    t_str = str(t_str).strip()
    if not t_str or t_str.lower() in ("auto", "-", "none", "null"):
        return None
    if ":" in t_str:
        partes = t_str.split(":")
        try:
            if len(partes) == 2:
                return int(partes[0]) * 60.0 + float(partes[1].replace(",", "."))
            elif len(partes) == 3:
                return int(partes[0]) * 3600.0 + int(partes[1]) * 60.0 + float(partes[2].replace(",", "."))
        except ValueError:
            return None
    try:
        return float(t_str.replace(",", "."))
    except ValueError:
        return None


def _normalizar_gesto(gesto_raw: Optional[str]) -> str:
    if not gesto_raw:
        return "hablando"
    g = _quitar_acentos(str(gesto_raw).strip().lower()).replace(".mp4", "")
    if not g or g in ("ninguno", "none", "-", "auto"):
        return "hablando"

    # Mapeo inteligente de sinónimos o gestos de versiones anteriores a los assets reales:
    # Assets reales actuales: idle, saludo, hablando, like
    sinonimos = {
        "hola": "saludo",
        "inicio": "saludo",
        "bienvenida": "saludo",
        "despedida": "saludo",
        "normal": "hablando",
        "explicando": "hablando",
        "reaccion": "hablando",
        "sorpresa": "hablando",
        "sorprendido": "hablando",
        "sorprendido_senalando": "hablando",
        "mente_explotando": "hablando",
        "senalar": "hablando",
        "duda": "hablando",
        "hablando_2": "hablando2",
        "hablando 2": "hablando2",
        "hablar2": "hablando2",
        "reaccion2": "hablando2",
        "explicando2": "hablando2",
        "festejo": "like",
        "festejando_pro": "like",
        "pulgar": "like",
        "suscribete": "like",
        "aprobacion": "like",
        "silencio": "idle",
        "espera": "idle",
        "quieto": "idle",
    }
    if g in sinonimos:
        return sinonimos[g]
    return g


def _desglosar_sfx_lista(sfx_raw: Optional[str]) -> List[str]:
    if not sfx_raw:
        return []
    raw_clean = _quitar_acentos(str(sfx_raw).strip().lower())
    raw_clean = raw_clean.replace(".mp3", "").replace(".wav", "").replace(".ogg", "")
    if not raw_clean or raw_clean in ("ninguno", "ninguna", "none", "no", "false", "-", "sin_sfx"):
        return []

    sinonimos_sfx = {
        "sorpresa": "crowd_shocked",
        "asombro": "crowd_shocked",
        "multitud": "crowd_shocked",
        "campana": "boxing_bell",
        "bell": "boxing_bell",
        "boxeo": "boxing_bell",
        "round": "boxing_bell",
        "flash": "camera_flash",
        "destello": "camera_flash",
        "foto": "camera_shooter",
        "camara": "camera_shooter",
        "screenshot": "camera_shooter",
        "risa": "cartoon_laugh",
        "risas": "cartoon_laugh",
        "laugh": "cartoon_laugh",
        "dinero": "cash_ting",
        "caja": "cash_ting",
        "cash": "cash_ting",
        "moneda": "cash_ting",
        "robux": "cash_ting",
        "idea": "good_idea",
        "brillante": "good_idea",
        "correcto": "ding",
        "acierto": "ding",
        "festejo": "kids_yeyy",
        "celebracion": "kids_yeyy",
        "yey": "kids_yeyy",
        "ninos": "kids_yeyy",
        "corneta": "party_horn",
        "fiesta": "party_horn",
        "party": "party_horn",
        "golpe": "punch",
        "impacto": "punch",
        "pelea": "punch",
        "subida": "riser_suction",
        "riser": "riser_suction",
        "suction": "riser_suction",
        "tension": "riser_suction",
        "transicion": "whoosh",
        "viento": "whoosh",
        "windows": "xp_error",
        "xp": "xp_error",
        "bug": "xp_error",
        "fallo": "error",
        "fail": "xp_error",
        "pedo": "fart",
        "broma": "fart",
        "tierno": "awww",
        "triste": "awww",
        "aww": "awww",
    }

    partes = re.split(r"[+,;/]", raw_clean)
    resultado: List[str] = []
    for p in partes:
        item = p.strip().replace(" ", "_")
        if not item or item in ("ninguno", "ninguna", "none", "no", "-"):
            continue
        item_norm = sinonimos_sfx.get(item, item)
        if item_norm not in resultado:
            resultado.append(item_norm)
    return resultado


def _normalizar_sfx(sfx_raw: Optional[str]) -> Optional[str]:
    lista = _desglosar_sfx_lista(sfx_raw)
    return "+".join(lista) if lista else None


def _normalizar_emoji(emoji_raw: Optional[str]) -> Tuple[Optional[str], str]:
    """
    Devuelve (nombre_emoji, posicion).
    Ejemplo: 'emoji_risa.png:izquierda' -> ('risa', 'izquierda')
             'sueño' -> ('sueno', 'derecha')
    """
    if not emoji_raw:
        return None, "derecha"
    raw = str(emoji_raw).strip()
    posicion = "derecha"
    if ":" in raw:
        raw, pos_part = raw.split(":", 1)
        pos_clean = _quitar_acentos(pos_part.strip().lower())
        if pos_clean in ("izquierda", "derecha", "centro", "arriba", "abajo", "arriba_izq", "arriba_der"):
            posicion = pos_clean

    e = _quitar_acentos(raw.strip().lower())
    e = e.replace(".png", "").replace(".webp", "").replace(".jpg", "")
    if e.startswith("emoji_"):
        e = e[len("emoji_"):]

    if not e or e in ("ninguno", "ninguna", "none", "no", "false", "-"):
        return None, posicion

    # Si por error pusieron 'flecha' en la columna de emoji, lo ignoramos aquí (se maneja en flecha)
    if e == "flecha":
        return None, posicion

    sinonimos_emoji = {
        "diablito": "diablo",
        "malvado": "diablo",
        "enojado": "enojo",
        "furia": "enojo",
        "rabia": "enojo",
        "festejo": "fiesta",
        "celebracion": "fiesta",
        "llorando": "lloro",
        "triste": "lloro",
        "tristeza": "lloro",
        "risas": "risa",
        "riendo": "risa",
        "gracioso": "risa",
        "lol": "risa",
        "sorpresa": "sorprendido",
        "shock": "sorprendido",
        "impactado": "sorprendido",
        "dormido": "sueno",
        "aburrido": "sueno",
        "zzz": "sueno",
    }
    return sinonimos_emoji.get(e, e), posicion


def _normalizar_flecha(flecha_raw: Optional[str]) -> Optional[str]:
    """
    Normaliza la columna FLECHA. Formatos aceptados (v4.2 - Flecha Inteligente):
      - `ninguna` / `no` / `-`          -> sin flecha
      - `auto`                           -> detecta sola el objeto con más acción y lo sigue
      - `45,60`                          -> apunta al punto X=45% Y=60% del video y lo SIGUE (tracking)
      - `45,60@00:05.5`                  -> igual, pero aparece en el segundo 5.5 del video original
      - `45,60@1.2`                      -> aparece 1.2s después del inicio del segmento
      - `izquierda`/`derecha`/`arriba`/`abajo`/`centro` -> busca la acción en esa zona y la sigue
      - Agregar ` fija` (ej. `45,60 fija`) desactiva el seguimiento.
    """
    if not flecha_raw:
        return None
    f = _quitar_acentos(str(flecha_raw).strip().lower()).replace(".png", "")
    f = re.sub(r"\s+", " ", f).strip()
    if f.startswith("flecha:"):
        f = f.split(":", 1)[1].strip()
    elif f in ("flecha", "si", "yes", "true", "1", "activa"):
        return "auto"

    if not f or f in ("ninguna", "ninguno", "none", "no", "false", "-", "0"):
        return None
    # Quitar espacios alrededor de la coma y del '@' (ej. "45 , 60 @ 0:05")
    f = re.sub(r"\s*,\s*", ",", f)
    f = re.sub(r"\s*@\s*", "@", f)
    return f


def _prioridad_flecha(flecha: str) -> int:
    """Mayor = más precisa. Coordenadas con tiempo > coordenadas > auto con tiempo > zona/auto."""
    tiene_coord = bool(re.match(r"^\s*\d+(\.\d+)?,\d+(\.\d+)?", flecha))
    tiene_tiempo = "@" in flecha
    return (2 if tiene_coord else 0) + (1 if tiene_tiempo else 0)


def _limitar_a_una_flecha(segmentos: List["SegmentoVideoGuion"]) -> None:
    """Regla del canal: como máximo UNA flecha por video (la más precisa / importante)."""
    con_flecha = [i for i, s in enumerate(segmentos) if s.flecha]
    if len(con_flecha) <= 1:
        return
    elegido = max(con_flecha, key=lambda i: (_prioridad_flecha(segmentos[i].flecha or ""), -i))
    for i in con_flecha:
        if i != elegido:
            segmentos[i].flecha = None
    print(
        f"[*] Flecha: el guion pedía {len(con_flecha)} flechas; se usa solo 1 por video "
        f"(segmento {elegido + 1}: '{segmentos[elegido].flecha}')."
    )


def _normalizar_bool(val_raw: object) -> bool:
    if isinstance(val_raw, bool):
        return val_raw
    s = _quitar_acentos(str(val_raw).strip().lower())
    return s in ("si", "yes", "true", "1", "like_sub", "likesub", "activo", "activar", "si+timer")


def _normalizar_timer(val_raw: object) -> bool:
    if isinstance(val_raw, bool):
        return val_raw
    s = _quitar_acentos(str(val_raw).strip().lower()).replace(".mp4", "")
    return s in ("si", "yes", "true", "1", "timer", "reloj", "cuenta_atras", "countdown", "activo", "activar", "si+timer")


def _es_columna_timer_explicita(val_raw: str) -> bool:
    s = _quitar_acentos(val_raw.strip().lower()).replace(".mp4", "")
    return s in (
        "si", "no", "yes", "true", "false", "1", "0",
        "timer", "reloj", "cuenta_atras", "countdown",
        "ninguno", "ninguna", "none", "-", "activo", "activar",
    )


def _postprocesar_segmentos_trivia_y_gestos(
    segmentos: List[SegmentoVideoGuion],
    tuvo_columna_timer_explicita: bool,
) -> None:
    """
    1) Si el guion viene en formato de 7 columnas (sin columna TIMER explícita) y contiene
       preguntas de trivia (ej. SFX 'tictac' con pregunta '?', o dice 'tienes tres segundos',
       o el segmento siguiente empieza con '¡Tiempo!' / '¡Exacto!'), activa automáticamente
       el timer de 3.5s luego de la pregunta.
    2) Si existe 'hablando2.mp4' en assets/gestos, intercala automáticamente entre 'hablando'
       y 'hablando2' en los segmentos hablados para dar variedad natural y evitar repetición.
    """
    if not segmentos:
        return

    # 0. Máximo una flecha por video
    _limitar_a_una_flecha(segmentos)

    # 1. Detección inteligente de preguntas de trivia cuando no se especificó columna TIMER
    if not tuvo_columna_timer_explicita:
        for i, seg in enumerate(segmentos):
            if seg.mostrar_timer:
                continue
            txt_low = _quitar_acentos(seg.texto_voz.lower())
            tiene_pregunta = ("?" in seg.texto_voz) or ("¿" in seg.texto_voz)
            dice_segundos = any(
                k in txt_low
                for k in ("tres segundos", "3 segundos", "cinco segundos", "5 segundos", "tiempo para adivinar")
            )
            sig_es_respuesta = False
            if i + 1 < len(segmentos):
                sig_low = _quitar_acentos(segmentos[i + 1].texto_voz.lower())
                sig_es_respuesta = any(
                    sig_low.startswith(pref) or f"¡{pref}" in sig_low or f"!{pref}" in sig_low
                    for pref in ("tiempo", "exacto", "correcto", "es ", "la respuesta", "acertaste", "comenta tu respuesta")
                )

            if dice_segundos or (tiene_pregunta and (seg.sfx_inicio == "tictac" or sig_es_respuesta)):
                seg.mostrar_timer = True

    # 2. Intercalado inteligente entre 'hablando' y 'hablando2'
    tiene_hablando2_asset = (GESTOS_DIR / "hablando2.mp4").exists()
    if tiene_hablando2_asset:
        uso_hablando2_manual = any(s.gesto_personaje == "hablando2" for s in segmentos)
        if not uso_hablando2_manual:
            contador_hablando = 0
            for seg in segmentos:
                if seg.gesto_personaje == "hablando":
                    if contador_hablando % 2 == 1:
                        seg.gesto_personaje = "hablando2"
                    contador_hablando += 1
        else:
            for i in range(1, len(segmentos)):
                if (
                    segmentos[i].gesto_personaje == "hablando"
                    and segmentos[i - 1].gesto_personaje == "hablando"
                ):
                    segmentos[i].gesto_personaje = "hablando2"


def cargar_guion_video(ruta_guion: Path) -> ConfigGuion:
    """
    Lee cualquier guión en formato .txt (separado por '|') o .json (incluso si está
    pegado dentro de un .txt o envuelto en bloques ```json ... ```).
    """
    if not ruta_guion.exists():
        if DEFAULT_GUION_JSON.exists():
            ruta_guion = DEFAULT_GUION_JSON
        elif DEFAULT_GUION_TXT.exists():
            ruta_guion = DEFAULT_GUION_TXT
        else:
            DEFAULT_GUION_TXT.write_text(PLANTILLA_GUION_GENERICA.strip() + "\n", encoding="utf-8")
            print(f"[*] Se creó plantilla de guión en: {DEFAULT_GUION_TXT}")
            ruta_guion = DEFAULT_GUION_TXT

    contenido = ruta_guion.read_text(encoding="utf-8-sig").strip()

    # Limpiar posibles bloques markdown ```json ... ``` o ```txt ... ``` si fue copiado de una IA
    if contenido.startswith("```"):
        lineas_md = contenido.splitlines()
        if lineas_md and lineas_md[0].startswith("```"):
            lineas_md = lineas_md[1:]
        if lineas_md and lineas_md[-1].strip().startswith("```"):
            lineas_md = lineas_md[:-1]
        contenido = "\n".join(lineas_md).strip()

    config = ConfigGuion()

    # 1. ¿Es un archivo o bloque en formato JSON?
    if contenido.startswith("{"):
        datos = json.loads(contenido)
        config.titulo_superior = str(datos.get("titulo_superior", "") or "").strip()
        config.modo_pantalla = str(datos.get("modo_pantalla", MODO_PANTALLA_DEFAULT) or MODO_PANTALLA_DEFAULT).strip().lower()
        salida_raw = str(datos.get("archivo_salida", "") or "").strip()
        config.archivo_salida = None if salida_raw.lower() in ("", "auto", "none", "null") else salida_raw

        if "volumen_video_original" in datos:
            try:
                config.volumen_video_original = float(datos["volumen_video_original"])
            except (ValueError, TypeError):
                pass

        for w in datos.get("palabras_verdes", []):
            config.palabras_verdes_extra.add(str(w).strip().upper())
        for w in datos.get("palabras_rojas", []):
            config.palabras_rojas_extra.add(str(w).strip().upper())

        for idx, seg in enumerate(datos.get("segmentos", [])):
            # Soportar rango "tiempo": "00:00 - 00:04" o campos "tiempo_inicio" / "tiempo_fin"
            t_ini, t_fin = None, None
            if "tiempo" in seg and "-" in str(seg["tiempo"]):
                p1, p2 = str(seg["tiempo"]).split("-", 1)
                t_ini = _parsear_tiempo_a_segundos(p1)
                t_fin = _parsear_tiempo_a_segundos(p2)
            else:
                t_ini = _parsear_tiempo_a_segundos(seg.get("tiempo_inicio", ""))
                t_fin = _parsear_tiempo_a_segundos(seg.get("tiempo_fin", ""))

            gesto = _normalizar_gesto(seg.get("gesto", seg.get("pose_avatar_nubsito", "hablando")))
            sfx = _normalizar_sfx(seg.get("sfx", None))
            emoji, pos_emoji = _normalizar_emoji(seg.get("emoji", None))
            if "posicion_emoji" in seg and seg["posicion_emoji"]:
                pos_emoji = _quitar_acentos(str(seg["posicion_emoji"]).strip().lower())
            flecha = _normalizar_flecha(seg.get("flecha", None))
            like_sub = _normalizar_bool(seg.get("like_sub", seg.get("mostrar_like_sub", False)))
            timer_raw = seg.get("timer", seg.get("mostrar_timer", False))
            mostrar_timer = _normalizar_timer(timer_raw)
            if sfx == "timer":
                mostrar_timer = True
                sfx = "tictac"
            pausa = float(seg.get("pausa", 0.18))
            texto = str(seg.get("texto", seg.get("texto_voz", seg.get("texto_voz_tts", "")))).strip()

            if texto:
                config.segmentos.append(
                    SegmentoVideoGuion(
                        id_segmento=f"{len(config.segmentos) + 1:02d}_seg",
                        texto_voz=texto,
                        gesto_personaje=gesto,
                        t_inicio_video=t_ini,
                        t_fin_video=t_fin,
                        pausa_idle_final_seg=pausa,
                        sfx_inicio=sfx,
                        emoji=emoji,
                        posicion_emoji=pos_emoji,
                        flecha=flecha,
                        mostrar_like_sub=like_sub,
                        mostrar_timer=mostrar_timer,
                    )
                )
        tuvo_timer_json = any(("timer" in s or "mostrar_timer" in s) for s in datos.get("segmentos", []))
        _postprocesar_segmentos_trivia_y_gestos(config.segmentos, tuvo_timer_json)
        return config

    # 2. Formato Bloc de Notas (.txt separado por '|')
    en_guion = False
    tuvo_columna_timer_explicita = False
    for linea in contenido.splitlines():
        limpia = linea.strip()
        if not limpia or limpia.startswith("#") or limpia.startswith("//"):
            continue
        if limpia.upper() in ("[GUION]", "[GUIÓN]", "[SCRIPT]", "[SEGMENTOS]"):
            en_guion = True
            continue

        if not en_guion and ":" in limpia and "|" not in limpia:
            k, v = limpia.split(":", 1)
            k_up = _quitar_acentos(k.strip().upper())
            v_str = v.strip()
            if k_up in ("TITULO_SUPERIOR", "TITULO"):
                config.titulo_superior = "" if v_str.lower() in ("ninguno", "none", "-") else v_str
            elif k_up == "MODO_PANTALLA":
                config.modo_pantalla = v_str.lower()
            elif k_up in ("ARCHIVO_SALIDA", "SALIDA", "OUTPUT"):
                config.archivo_salida = None if v_str.lower() in ("", "auto", "none", "-") else v_str
            elif k_up == "VOLUMEN_VIDEO_ORIGINAL":
                try:
                    config.volumen_video_original = float(v_str.replace(",", "."))
                except ValueError:
                    pass
            elif k_up == "PALABRAS_VERDES":
                for w in v_str.split(","):
                    if w.strip():
                        config.palabras_verdes_extra.add(w.strip().upper())
            elif k_up == "PALABRAS_ROJAS":
                for w in v_str.split(","):
                    if w.strip():
                        config.palabras_rojas_extra.add(w.strip().upper())
            continue

        partes = [p.strip() for p in limpia.split("|")]
        if (
            len(partes) >= 2
            and _quitar_acentos(partes[0].upper()) in ("TIEMPO", "TIEMPOS", "TIME")
            and _quitar_acentos(partes[1].upper()) in ("GESTO", "GESTOS")
        ):
            continue
        t_ini, t_fin = None, None
        gesto = "hablando"
        sfx: Optional[str] = None
        emoji: Optional[str] = None
        pos_emoji = "derecha"
        flecha: Optional[str] = None
        like_sub = False
        mostrar_timer = False
        pausa = 0.18
        texto_voz = ""

        if len(partes) >= 8 and _es_columna_timer_explicita(partes[6]):
            # FORMATO COMPLETO v4.1 (CON TIMER EXPLÍCITO):
            # TIEMPO | GESTO | SFX | EMOJI | FLECHA | LIKE_SUB | TIMER | TEXTO
            tuvo_columna_timer_explicita = True
            rango_t, gesto_str, sfx_str, emoji_str, flecha_str, likesub_str, timer_str = partes[:7]
            texto_voz = " | ".join(partes[7:]).strip()
            if "-" in rango_t and rango_t.lower() != "-":
                t1, t2 = rango_t.split("-", 1)
                t_ini = _parsear_tiempo_a_segundos(t1)
                t_fin = _parsear_tiempo_a_segundos(t2)
            gesto = _normalizar_gesto(gesto_str)
            sfx = _normalizar_sfx(sfx_str)
            emoji, pos_emoji = _normalizar_emoji(emoji_str)
            flecha = _normalizar_flecha(flecha_str)
            like_sub = _normalizar_bool(likesub_str)
            mostrar_timer = _normalizar_timer(timer_str)
            if sfx == "timer":
                mostrar_timer = True
                sfx = "tictac"

        elif len(partes) >= 7:
            # FORMATO 7 COLUMNAS:
            # TIEMPO | GESTO | SFX | EMOJI | FLECHA | LIKE_SUB | TEXTO
            rango_t, gesto_str, sfx_str, emoji_str, flecha_str, likesub_str = partes[:6]
            texto_voz = " | ".join(partes[6:]).strip()
            if "-" in rango_t and rango_t.lower() != "-":
                t1, t2 = rango_t.split("-", 1)
                t_ini = _parsear_tiempo_a_segundos(t1)
                t_fin = _parsear_tiempo_a_segundos(t2)
            gesto = _normalizar_gesto(gesto_str)
            sfx = _normalizar_sfx(sfx_str)
            emoji, pos_emoji = _normalizar_emoji(emoji_str)
            flecha = _normalizar_flecha(flecha_str)
            like_sub = _normalizar_bool(likesub_str)
            if _normalizar_timer(likesub_str) and not like_sub:
                mostrar_timer = True
                tuvo_columna_timer_explicita = True
            elif "timer" in _quitar_acentos(likesub_str.lower()):
                mostrar_timer = True
                tuvo_columna_timer_explicita = True
            if sfx == "timer":
                mostrar_timer = True
                tuvo_columna_timer_explicita = True
                sfx = "tictac"

        elif len(partes) == 6:
            # TIEMPO | GESTO | SFX | EMOJI | LIKE_SUB | TEXTO
            rango_t, gesto_str, sfx_str, emoji_str, likesub_str, texto_voz = partes
            if "-" in rango_t and rango_t.lower() != "-":
                t1, t2 = rango_t.split("-", 1)
                t_ini = _parsear_tiempo_a_segundos(t1)
                t_fin = _parsear_tiempo_a_segundos(t2)
            gesto = _normalizar_gesto(gesto_str)
            sfx = _normalizar_sfx(sfx_str)
            emoji, pos_emoji = _normalizar_emoji(emoji_str)
            like_sub = _normalizar_bool(likesub_str)
            if _normalizar_timer(likesub_str) and not like_sub:
                mostrar_timer = True

        elif len(partes) == 5:
            # Compatibilidad v3.0: TIEMPO | GESTO | SFX | PAUSA_O_EMOJI | TEXTO
            rango_t, gesto_str, sfx_str, col4, texto_voz = partes
            if "-" in rango_t and rango_t.lower() != "-":
                t1, t2 = rango_t.split("-", 1)
                t_ini = _parsear_tiempo_a_segundos(t1)
                t_fin = _parsear_tiempo_a_segundos(t2)
            gesto = _normalizar_gesto(gesto_str)
            sfx = _normalizar_sfx(sfx_str)
            try:
                pausa = float(col4.replace(",", "."))
            except ValueError:
                emoji, pos_emoji = _normalizar_emoji(col4)

        elif len(partes) == 4:
            # TIEMPO | GESTO | SFX | TEXTO
            rango_t, gesto_str, sfx_str, texto_voz = partes
            if "-" in rango_t and rango_t.lower() != "-":
                t1, t2 = rango_t.split("-", 1)
                t_ini = _parsear_tiempo_a_segundos(t1)
                t_fin = _parsear_tiempo_a_segundos(t2)
            gesto = _normalizar_gesto(gesto_str)
            sfx = _normalizar_sfx(sfx_str)

        elif len(partes) == 3:
            # TIEMPO | GESTO | TEXTO
            rango_t, gesto_str, texto_voz = partes
            if "-" in rango_t and rango_t.lower() != "-":
                t1, t2 = rango_t.split("-", 1)
                t_ini = _parsear_tiempo_a_segundos(t1)
                t_fin = _parsear_tiempo_a_segundos(t2)
            gesto = _normalizar_gesto(gesto_str)

        elif len(partes) == 2:
            # GESTO | TEXTO
            gesto = _normalizar_gesto(partes[0])
            texto_voz = partes[1]
        else:
            # Solo TEXTO
            texto_voz = limpia

        if texto_voz:
            config.segmentos.append(
                SegmentoVideoGuion(
                    id_segmento=f"{len(config.segmentos) + 1:02d}_seg",
                    texto_voz=texto_voz,
                    gesto_personaje=gesto,
                    t_inicio_video=t_ini,
                    t_fin_video=t_fin,
                    pausa_idle_final_seg=pausa,
                    sfx_inicio=sfx,
                    emoji=emoji,
                    posicion_emoji=pos_emoji,
                    flecha=flecha,
                    mostrar_like_sub=like_sub,
                    mostrar_timer=mostrar_timer,
                )
            )

    _postprocesar_segmentos_trivia_y_gestos(config.segmentos, tuvo_columna_timer_explicita)
    return config


# ==============================================================================
# 4. TIPOGRAFÍA Y SUBTÍTULOS MRBEAST
# ==============================================================================
_FONT_CACHE: Dict[int, ImageFont.FreeTypeFont] = {}
_RUTA_FUENTE_ACTIVA: Optional[str] = None

PALABRAS_VERDES_BASE = {
    "CARO", "DÓLARES", "DOLARES", "MILLONES", "MIL", "DINERO", "PRECIO",
    "VALE", "PRO", "PROS", "VICTORIA", "GANAR", "GANÉ", "GRATIS", "ORO", " ROBUX",
}
PALABRAS_ROJAS_BASE = {
    "IMPOSIBLE", "LOCURA", "DIFÍCIL", "DIFICIL", "NUNCA", "SECRETO",
    "SUSCRÍBETE", "SUSCRIBETE", "SUSCRIBITE", "LIKE", "PELIGRO", "PROHIBIDO", "MUERTE", "ERROR",
}


def asegurar_fuente_mrbeast() -> Optional[str]:
    global _RUTA_FUENTE_ACTIVA
    if _RUTA_FUENTE_ACTIVA and os.path.exists(_RUTA_FUENTE_ACTIVA):
        return _RUTA_FUENTE_ACTIVA

    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    preferidas = ["MrBeast_Gaming_Font.ttf", "KomikaAxis.ttf", "KOMIKAX_.ttf", "MrBeast.ttf", "Burbank.ttf", "ObelixPro.ttf"]
    for nombre in preferidas:
        candidata = FONTS_DIR / nombre
        if candidata.exists():
            _RUTA_FUENTE_ACTIVA = str(candidata)
            return _RUTA_FUENTE_ACTIVA

    for archivo in sorted(FONTS_DIR.glob("*.[to]tf")):
        if archivo.stat().st_size > 10_000:
            _RUTA_FUENTE_ACTIVA = str(archivo)
            return _RUTA_FUENTE_ACTIVA

    destino_auto = FONTS_DIR / "MrBeast_Gaming_Font.ttf"
    urls_fuentes = [
        "https://github.com/google/fonts/raw/main/apache/luckiestguy/LuckiestGuy-Regular.ttf",
        "https://github.com/google/fonts/raw/main/ofl/bangers/Bangers-Regular.ttf",
    ]
    for url in urls_fuentes:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                datos = resp.read()
                if len(datos) > 20_000:
                    destino_auto.write_bytes(datos)
                    _RUTA_FUENTE_ACTIVA = str(destino_auto)
                    return _RUTA_FUENTE_ACTIVA
        except Exception:
            pass

    sistema = [
        "C:/Windows/Fonts/impact.ttf",
        "C:/Windows/Fonts/ariblk.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for s in sistema:
        if os.path.exists(s):
            _RUTA_FUENTE_ACTIVA = s
            return _RUTA_FUENTE_ACTIVA
    return None


def cargar_fuente(tamano: int) -> ImageFont.FreeTypeFont:
    if tamano in _FONT_CACHE:
        return _FONT_CACHE[tamano]
    ruta = asegurar_fuente_mrbeast()
    if ruta:
        try:
            _FONT_CACHE[tamano] = ImageFont.truetype(ruta, tamano)
            return _FONT_CACHE[tamano]
        except Exception:
            pass
    _FONT_CACHE[tamano] = ImageFont.load_default()
    return _FONT_CACHE[tamano]


def dibujar_texto_mrbeast(
    draw: ImageDraw.ImageDraw,
    pos_centro: Tuple[int, int],
    texto: str,
    fuente: ImageFont.FreeTypeFont,
    color_relleno: Tuple[int, int, int],
    grosor_borde: int = 10,
    offset_sombra: Tuple[int, int] = (6, 8),
) -> None:
    cx, cy = pos_centro
    sx, sy = offset_sombra
    draw.text(
        (cx + sx, cy + sy),
        texto,
        font=fuente,
        fill=(0, 0, 0, 235),
        anchor="mm",
        stroke_width=grosor_borde,
        stroke_fill=(0, 0, 0, 235),
    )
    draw.text(
        (cx, cy),
        texto,
        font=fuente,
        fill=color_relleno,
        anchor="mm",
        stroke_width=grosor_borde,
        stroke_fill=(0, 0, 0, 255),
    )


def renderizar_subtitulos_mrbeast(
    draw: ImageDraw.ImageDraw,
    palabras: List[PalabraSubtitulo],
    t_actual: float,
    y_centro: int = 625,
    palabras_verdes_extra: Optional[Set[str]] = None,
    palabras_rojas_extra: Optional[Set[str]] = None,
) -> None:
    if not palabras:
        return
    if t_actual < (palabras[0].inicio_seg - 0.06) or t_actual > (palabras[-1].fin_seg + 0.18):
        return

    idx_activa = 0
    for i, p in enumerate(palabras):
        if p.inicio_seg <= t_actual <= p.fin_seg:
            idx_activa = i
            break
        if t_actual > p.fin_seg:
            idx_activa = i

    tam_grupo = 2 if any(len(w.texto) >= 9 for w in palabras) else 3
    inicio_grupo = (idx_activa // tam_grupo) * tam_grupo
    grupo = palabras[inicio_grupo : inicio_grupo + tam_grupo]

    espaciado = 24
    items_render = []

    verdes = PALABRAS_VERDES_BASE | (palabras_verdes_extra or set())
    rojas = PALABRAS_ROJAS_BASE | (palabras_rojas_extra or set())

    for i_local, p in enumerate(grupo):
        es_activa = (inicio_grupo + i_local) == idx_activa
        txt_up = p.texto.upper()
        limpia = re.sub(r"[^A-ZÁÉÍÓÚÜÑ0-9]", "", txt_up)

        if es_activa:
            edad_palabra = max(0.0, t_actual - p.inicio_seg)
            factor_pop = 1.0 + 0.14 * math.exp(-edad_palabra * 14.0)
            tam_f = int(round(68 * factor_pop))
            if limpia in verdes:
                color = (46, 255, 95)
            elif limpia in rojas:
                color = (255, 75, 75)
            else:
                color = (255, 232, 0)
        else:
            tam_f = 60
            color = (255, 255, 255)

        fuente_p = cargar_fuente(tam_f)
        bbox = draw.textbbox((0, 0), txt_up, font=fuente_p, stroke_width=11)
        ancho_p = bbox[2] - bbox[0]
        items_render.append((txt_up, fuente_p, color, ancho_p, es_activa))

    ancho_total = sum(x[3] for x in items_render) + espaciado * max(0, len(items_render) - 1)
    x_cursor = (WIDTH - ancho_total) // 2

    for txt_up, fuente_p, color, ancho_p, es_activa in items_render:
        cx_p = x_cursor + ancho_p // 2
        cy_p = y_centro - 8 if es_activa else y_centro
        dibujar_texto_mrbeast(
            draw,
            (cx_p, cy_p),
            txt_up,
            fuente_p,
            color_relleno=color,
            grosor_borde=11,
            offset_sombra=(6, 8),
        )
        x_cursor += ancho_p + espaciado


# ==============================================================================
# 5. MOTOR DE VOZ Y ALINEACIÓN ACÚSTICA EXACTA
# ==============================================================================
def _peso_silabico_palabra(palabra: str) -> float:
    limpia = re.sub(r"[^a-záéíóúüñA-ZÁÉÍÓÚÜÑ0-9]", "", palabra)
    vocales = len(re.findall(r"[aeiouáéíóúüAEIOUÁÉÍÓÚÜ]+", limpia))
    peso = max(1.0, float(vocales) * 0.9 + len(limpia) * 0.12)
    if palabra.endswith((",", "...", ":", ";")):
        peso += 0.85
    elif palabra.endswith(("!", "?", ".")):
        peso += 1.10
    return peso


def detectar_limites_voz_real_wav(ruta_wav: Path) -> Tuple[float, float, float]:
    with wave.open(str(ruta_wav), "r") as wf:
        sr = wf.getframerate()
        n = wf.getnframes()
        dur_total = n / float(sr)
        audio = np.frombuffer(wf.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0

    if len(audio) == 0:
        return 0.0, dur_total, dur_total

    win = max(1, int(sr * 0.01))
    n_wins = len(audio) // win
    if n_wins < 2:
        return 0.0, dur_total, dur_total

    recorte = audio[: n_wins * win].reshape(n_wins, win)
    rms = np.sqrt(np.mean(recorte ** 2, axis=1))
    umbral = max(0.012, float(np.max(rms)) * 0.06)

    activos = np.where(rms > umbral)[0]
    if len(activos) == 0:
        return 0.0, dur_total, dur_total

    ini_seg = max(0.0, float(activos[0]) * 0.01 - 0.02)
    fin_seg = min(dur_total, float(activos[-1] + 1) * 0.01 + 0.03)
    return ini_seg, fin_seg, dur_total


def alinear_palabras_con_onda_real(
    texto: str,
    ruta_wav: Path,
    palabras_edge: List[PalabraSubtitulo],
) -> List[PalabraSubtitulo]:
    ini_voz, fin_voz, dur_total = detectar_limites_voz_real_wav(ruta_wav)
    dur_hablada = max(0.25, fin_voz - ini_voz)

    if palabras_edge:
        primero = palabras_edge[0].inicio_seg
        ultimo = palabras_edge[-1].fin_seg
        rango_raw = max(0.1, ultimo - primero)
        escala = dur_hablada / rango_raw
        calibradas: List[PalabraSubtitulo] = []
        for p in palabras_edge:
            t0 = ini_voz + (p.inicio_seg - primero) * escala + SUBTITLE_OFFSET_SEC
            t1 = ini_voz + (p.fin_seg - primero) * escala + SUBTITLE_OFFSET_SEC
            t0 = max(0.0, t0)
            t1 = max(t0 + 0.08, min(dur_total, t1))
            if calibradas and t0 > calibradas[-1].fin_seg and (t0 - calibradas[-1].fin_seg) < 0.18:
                calibradas[-1].fin_seg = t0
            calibradas.append(PalabraSubtitulo(texto=p.texto, inicio_seg=t0, fin_seg=t1))
        return calibradas

    tokens = texto.split()
    if not tokens:
        return []
    pesos = [_peso_silabico_palabra(tok) for tok in tokens]
    suma_pesos = sum(pesos)
    cursor = ini_voz
    resultado: List[PalabraSubtitulo] = []
    for tok, peso in zip(tokens, pesos):
        dur_p = (peso / suma_pesos) * dur_hablada
        t0 = max(0.0, cursor + SUBTITLE_OFFSET_SEC)
        t1 = min(dur_total, cursor + dur_p + SUBTITLE_OFFSET_SEC)
        resultado.append(PalabraSubtitulo(texto=tok, inicio_seg=t0, fin_seg=max(t0 + 0.08, t1)))
        cursor += dur_p
    return resultado


_REEMPLAZOS_FONETICOS_TTS = [
    (r"\bPCs\b", "PC"),
    (r"\bpcs\b", "PC"),
    (r"\bRoblox\b", "Róblox"),
    (r"\broblox\b", "Róblox"),
    (r"\bpros\b", "prós"),
    (r"\bPros\b", "Prós"),
    (r"\bPROS\b", "PRÓS"),
    (r"\bpro\b", "pró"),
    (r"\bPro\b", "Pró"),
    (r"\bPRO\b", "PRÓ"),
]

_PALABRAS_HYPE_YOUTUBER = {
    "mira", "mirá", "miren", "increíble", "imposible", "locura", "pero", "ahora",
    "suscríbete", "suscribite", "comenta", "comentá", "like", "secreto", "nunca",
    "jamás", "épico", "épica", "ganar", "perder", "cuidado", "rápido",
}


def _normalizar_fonetica_youtuber(texto: str) -> str:
    res = texto
    for patron, reemplazo in _REEMPLAZOS_FONETICOS_TTS:
        res = re.sub(patron, reemplazo, res)
    return res


def _dividir_clausulas_expresivas(texto: str) -> List[str]:
    partes = re.split(r"(?<=[!?.])\s+", texto.strip())
    return [p.strip() for p in partes if p.strip()]


def _prosodia_dinamica_clausula(
    clausula: str,
    idx: int,
    total: int,
    dur_objetivo_seg: Optional[float] = None,
    total_palabras_seg: int = 0,
) -> Tuple[str, str, str, float]:
    es_exclamacion = "!" in clausula or "¡" in clausula
    es_pregunta = "?" in clausula or "¿" in clausula
    palabras_low = set(re.findall(r"[a-záéíóúüñ]+", clausula.lower()))
    tiene_hype = bool(palabras_low & _PALABRAS_HYPE_YOUTUBER)

    pitch = EDGE_PITCH if EDGE_PITCH != "+8Hz" else "+0Hz"

    # Calcular densidad de palabras por segundo para adaptar ritmo base sin apurar ni dejar silencios
    wps = (total_palabras_seg / dur_objetivo_seg) if (dur_objetivo_seg and dur_objetivo_seg > 0.4) else 3.5
    if wps > 4.6:
        boost_rate = 10
        escala_pausa = 0.45
    elif wps > 3.9:
        boost_rate = 5
        escala_pausa = 0.65
    elif wps < 2.8:
        boost_rate = -3
        escala_pausa = 1.05
    else:
        boost_rate = 0
        escala_pausa = 0.85

    if es_exclamacion:
        if idx == 0:
            base_r, vol, pausa = 13, "+8%", 0.055
        elif idx == total - 1:
            base_r, vol, pausa = 14, "+10%", 0.060
        else:
            base_r, vol, pausa = 15, "+6%", 0.055
    elif es_pregunta:
        base_r, vol, pausa = 12, "+6%", 0.065
    else:
        base_r = 11 if tiene_hype else 9
        vol, pausa = "+0%", 0.065

    rate_final = max(0, min(32, base_r + boost_rate))
    return f"+{rate_final}%", pitch, vol, round(pausa * escala_pausa, 4)


def _recortar_silencio_clausula_pcm(
    pcm_float: np.ndarray,
    sr: int = 44100,
) -> Tuple[np.ndarray, float, float]:
    if len(pcm_float) == 0:
        return pcm_float, 0.0, 0.0
    win = max(1, int(sr * 0.005))
    n_wins = len(pcm_float) // win
    if n_wins < 2:
        dur = len(pcm_float) / float(sr)
        return pcm_float, 0.0, dur

    rms = np.sqrt(np.mean(pcm_float[: n_wins * win].reshape(n_wins, win) ** 2, axis=1))
    umbral = max(0.007, float(np.max(rms)) * 0.03)
    activos = np.where(rms > umbral)[0]
    if len(activos) == 0:
        dur = len(pcm_float) / float(sr)
        return pcm_float, 0.0, dur

    pad_ini = int(sr * 0.014)
    pad_fin = int(sr * 0.022)
    i0 = max(0, int(activos[0]) * win - pad_ini)
    i1 = min(len(pcm_float), int(activos[-1] + 1) * win + pad_fin)
    recortado = pcm_float[i0:i1].copy()

    fade_len = min(int(sr * 0.007), len(recortado) // 4)
    if fade_len > 1:
        recortado[:fade_len] *= np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
        recortado[-fade_len:] *= np.linspace(1.0, 0.0, fade_len, dtype=np.float32)

    return recortado, i0 / float(sr), len(recortado) / float(sr)


def _construir_cadena_atempo(factor: float) -> str:
    """Construye una cadena de filtros atempo válida para FFmpeg manteniendo el tono natural."""
    f = float(np.clip(factor, 0.50, 3.20))
    if abs(f - 1.0) < 0.012:
        return ""
    partes: List[str] = []
    while f > 2.0:
        partes.append("atempo=2.0000")
        f /= 2.0
    while f < 0.5:
        partes.append("atempo=0.5000")
        f /= 0.5
    if abs(f - 1.0) >= 0.010:
        partes.append(f"atempo={f:.4f}")
    return ",".join(partes)


def masterizar_voz_estudio_ffmpeg(
    ruta_entrada: Path,
    ruta_wav_salida: Path,
    factor_atempo: float = 1.0,
) -> None:
    cadena_atempo = _construir_cadena_atempo(factor_atempo)
    prefijo_atempo = f"{cadena_atempo}," if cadena_atempo else ""
    filtro_audio = (
        f"{prefijo_atempo}"
        "highpass=f=80,"
        "equalizer=f=135:width_type=o:width=1.1:g=2.4,"
        "equalizer=f=320:width_type=o:width=1.2:g=-1.2,"
        "equalizer=f=3150:width_type=o:width=1.3:g=-2.2,"
        "equalizer=f=5200:width_type=o:width=1.5:g=1.5,"
        "treble=g=2.0:f=8200,"
        "acompressor=threshold=-19dB:ratio=2.4:attack=10:release=110:makeup=5.0dB,"
        "alimiter=limit=0.98"
    )
    res = subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(ruta_entrada),
            "-af", filtro_audio,
            "-ar", "44100", "-ac", "1",
            str(ruta_wav_salida),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=SUBPROCESS_NO_WINDOW,
        check=False,
    )
    if res.returncode != 0 or not ruta_wav_salida.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(ruta_entrada), "-ar", "44100", "-ac", "1", str(ruta_wav_salida)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=SUBPROCESS_NO_WINDOW,
            check=False,
        )


def intentar_voz_elevenlabs(texto: str, ruta_mp3: Path) -> bool:
    if not ELEVENLABS_API_KEY:
        return False
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    payload = json.dumps({
        "text": _normalizar_fonetica_youtuber(texto),
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.32,
            "similarity_boost": 0.82,
            "style": 0.68,
            "use_speaker_boost": True,
        },
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "xi-api-key": ELEVENLABS_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            datos = resp.read()
            if len(datos) > 1000:
                ruta_mp3.write_bytes(datos)
                return True
    except Exception:
        pass
    return False


async def generar_voz_y_timestamps_segmento(
    texto: str,
    ruta_audio_salida: Path,
    dur_objetivo_seg: Optional[float] = None,
) -> Tuple[float, List[PalabraSubtitulo], float, float]:
    palabras_raw: List[PalabraSubtitulo] = []
    genero_audio = False
    ruta_wav = ruta_audio_salida.with_suffix(".wav")
    if ruta_audio_salida.exists():
        ruta_audio_salida.unlink()
    if ruta_wav.exists():
        ruta_wav.unlink()

    total_palabras_seg = len(texto.split())

    if ELEVENLABS_API_KEY:
        genero_audio = intentar_voz_elevenlabs(texto, ruta_audio_salida)
        if genero_audio:
            masterizar_voz_estudio_ffmpeg(ruta_audio_salida, ruta_wav)

    if not genero_audio and HAS_EDGE_TTS:
        try:
            clausulas_orig = _dividir_clausulas_expresivas(texto)
            if not clausulas_orig:
                clausulas_orig = [texto.strip()]

            sr = 44100
            bloques_pcm: List[np.ndarray] = []
            cursor_seg = 0.0

            for c_idx, cl_orig in enumerate(clausulas_orig):
                cl_tts = _normalizar_fonetica_youtuber(cl_orig)
                rate_c, pitch_c, vol_c, pausa_post = _prosodia_dinamica_clausula(
                    cl_orig, c_idx, len(clausulas_orig), dur_objetivo_seg, total_palabras_seg
                )
                tmp_mp3 = ruta_audio_salida.parent / f"{ruta_audio_salida.stem}_c{c_idx}.mp3"
                tmp_wav = ruta_audio_salida.parent / f"{ruta_audio_salida.stem}_c{c_idx}.wav"
                palabras_clausula: List[PalabraSubtitulo] = []

                try:
                    communicate = edge_tts.Communicate(
                        cl_tts,
                        EDGE_VOICE,
                        rate=rate_c,
                        pitch=pitch_c,
                        volume=vol_c,
                        boundary="WordBoundary",
                    )
                except TypeError:
                    communicate = edge_tts.Communicate(
                        cl_tts,
                        EDGE_VOICE,
                        rate=rate_c,
                        pitch=pitch_c,
                    )

                with open(tmp_mp3, "wb") as audio_file:
                    async for chunk in communicate.stream():
                        ctype = chunk.get("type")
                        if ctype == "audio":
                            audio_file.write(chunk["data"])
                        elif ctype == "WordBoundary":
                            ini = chunk["offset"] / 10_000_000.0
                            dur = chunk["duration"] / 10_000_000.0
                            palabras_clausula.append(
                                PalabraSubtitulo(
                                    texto=chunk["text"],
                                    inicio_seg=ini,
                                    fin_seg=ini + max(dur, 0.08),
                                )
                            )

                if tmp_mp3.exists() and tmp_mp3.stat().st_size > 0:
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", str(tmp_mp3), "-ar", str(sr), "-ac", "1", str(tmp_wav)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=SUBPROCESS_NO_WINDOW,
                        check=False,
                    )
                if tmp_wav.exists() and tmp_wav.stat().st_size > 0:
                    with wave.open(str(tmp_wav), "r") as wf_c:
                        pcm_c = (
                            np.frombuffer(wf_c.readframes(wf_c.getnframes()), dtype=np.int16).astype(np.float32)
                            / 32768.0
                        )
                    pcm_rec, offset_rec, dur_rec = _recortar_silencio_clausula_pcm(pcm_c, sr)

                    if "!" in cl_orig or "¡" in cl_orig:
                        progreso_c = np.linspace(0.0, 1.0, len(pcm_rec), dtype=np.float32)
                        env_punch = 1.0 + 0.09 * np.exp(-progreso_c * 3.2) + 0.05 * np.sin(progreso_c * math.pi)
                        pcm_rec = pcm_rec * env_punch

                    tokens_orig = cl_orig.split()
                    if palabras_clausula:
                        t_first = palabras_clausula[0].inicio_seg
                        t_last = palabras_clausula[-1].fin_seg
                        span_raw = max(0.08, t_last - t_first)
                        escala_c = max(0.10, dur_rec - 0.02) / span_raw
                        for w_i, p_w in enumerate(palabras_clausula):
                            txt_mostrar = tokens_orig[w_i] if w_i < len(tokens_orig) else p_w.texto
                            t0 = cursor_seg + max(0.0, (p_w.inicio_seg - t_first) * escala_c)
                            t1 = cursor_seg + min(dur_rec, max(0.08, (p_w.fin_seg - t_first) * escala_c))
                            palabras_raw.append(PalabraSubtitulo(texto=txt_mostrar, inicio_seg=t0, fin_seg=max(t0 + 0.07, t1)))

                    bloques_pcm.append(pcm_rec)
                    cursor_seg += dur_rec

                    if c_idx < len(clausulas_orig) - 1:
                        bloques_pcm.append(np.zeros(int(sr * pausa_post), dtype=np.float32))
                        cursor_seg += pausa_post

                for f_tmp in (tmp_mp3, tmp_wav):
                    if f_tmp.exists():
                        try:
                            f_tmp.unlink()
                        except OSError:
                            pass

            if bloques_pcm:
                audio_crudo = np.clip(np.concatenate(bloques_pcm), -0.98, 0.98)
                dur_cruda = len(audio_crudo) / float(sr)

                # Ajuste elástico de tempo por segmento:
                # Garantiza que la voz encaje de corrido en su ventana exacta sin cortar la última palabra
                # ni dejar huecos de silencio en blanco entre bloques.
                factor_atempo = 1.0
                if dur_objetivo_seg is not None and dur_objetivo_seg > 0.35 and dur_cruda > 0.15:
                    dur_ideal_voz = max(0.35, dur_objetivo_seg - 0.06)
                    if dur_cruda > dur_ideal_voz:
                        factor_atempo = float(np.clip(dur_cruda / dur_ideal_voz, 1.0, 2.40))
                    elif dur_cruda < dur_ideal_voz * 0.93:
                        factor_atempo = float(np.clip(dur_cruda / (dur_ideal_voz * 0.96), 0.80, 1.0))

                ruta_crudo_wav = ruta_audio_salida.parent / f"{ruta_audio_salida.stem}_raw.wav"
                with wave.open(str(ruta_crudo_wav), "w") as wf_raw:
                    wf_raw.setnchannels(1)
                    wf_raw.setsampwidth(2)
                    wf_raw.setframerate(sr)
                    wf_raw.writeframes((audio_crudo * 32767).astype(np.int16).tobytes())
                masterizar_voz_estudio_ffmpeg(ruta_crudo_wav, ruta_wav, factor_atempo=factor_atempo)
                if ruta_crudo_wav.exists():
                    try:
                        ruta_crudo_wav.unlink()
                    except OSError:
                        pass

                if abs(factor_atempo - 1.0) >= 0.010 and palabras_raw:
                    for p in palabras_raw:
                        p.inicio_seg /= factor_atempo
                        p.fin_seg /= factor_atempo

                genero_audio = ruta_wav.exists() and ruta_wav.stat().st_size > 0
        except Exception as e:
            print(f"[!] Error en Edge-TTS: {e}")

    if not ruta_wav.exists() or ruta_wav.stat().st_size == 0:
        tokens = texto.split()
        dur_estimada = dur_objetivo_seg if (dur_objetivo_seg and dur_objetivo_seg > 0.4) else max(1.8, len(tokens) * 0.28)
        sr = 44100
        t = np.linspace(0, dur_estimada, int(sr * dur_estimada), False)
        audio_sim = np.sin(2 * np.pi * 195 * t) * 0.25
        pcm = (audio_sim * 32767).astype(np.int16)
        with wave.open(str(ruta_wav), "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm.tobytes())

    with wave.open(str(ruta_wav), "r") as wf:
        duracion_real = wf.getnframes() / float(wf.getframerate())

    ini_voz, fin_voz, _ = detectar_limites_voz_real_wav(ruta_wav)
    if palabras_raw and not ELEVENLABS_API_KEY:
        palabras_alineadas: List[PalabraSubtitulo] = []
        for p in palabras_raw:
            t0 = max(0.0, p.inicio_seg + SUBTITLE_OFFSET_SEC)
            t1 = min(duracion_real, max(t0 + 0.07, p.fin_seg + SUBTITLE_OFFSET_SEC))
            if palabras_alineadas and t0 > palabras_alineadas[-1].fin_seg and (t0 - palabras_alineadas[-1].fin_seg) < 0.16:
                palabras_alineadas[-1].fin_seg = t0
            palabras_alineadas.append(PalabraSubtitulo(texto=p.texto, inicio_seg=t0, fin_seg=t1))
    else:
        palabras_alineadas = alinear_palabras_con_onda_real(texto, ruta_wav, palabras_raw)

    return duracion_real, palabras_alineadas, ini_voz, fin_voz


# ==============================================================================
# 6. MOTORES DE PERSONAJE, OVERLAYS (EMOJIS, FLECHA, LIKE&SUB) Y VIDEO
# ==============================================================================
class MotorPersonajeMrNubsitoV2:
    """
    Carga dinámicamente todos los videos de gestos en `assets/gestos/*.mp4`
    (ej. idle.mp4, saludo.mp4, hablando.mp4, like.mp4) respetando su FPS nativo.
    """

    def __init__(self, base_image_path: Path, gestos_dir: Path):
        self.gestos_dir = gestos_dir
        img_pil = Image.open(base_image_path).convert("RGB")
        self.base_img = self._resize_cover(img_pil, WIDTH, TOP_HEIGHT)
        self.base_np = np.array(self.base_img, dtype=np.uint8)

        yy, xx = np.meshgrid(
            np.arange(TOP_HEIGHT, dtype=np.float32),
            np.arange(WIDTH, dtype=np.float32),
            indexing="ij",
        )
        self.grid_x = xx
        self.grid_y = yy

        dist_head = ((xx - 535.0) / 225.0) ** 2 + ((yy - 235.0) / 195.0) ** 2
        self.mask_head = np.clip(1.0 - dist_head, 0.0, 1.0) ** 1.5

        dist_torso = ((xx - 520.0) / 360.0) ** 2 + ((yy - 540.0) / 220.0) ** 2
        self.mask_torso = np.clip(1.0 - dist_torso, 0.0, 1.0) ** 1.4

        self.clips_cache: Dict[str, List[np.ndarray]] = {}
        self.clips_fps: Dict[str, float] = {}
        self.gesto_previo: Optional[str] = None
        self.t_acumulado_gesto: float = 0.0
        self.t_cursor_hablando: Dict[str, float] = {"hablando": 0.0, "hablando2": 0.0}
        self._cargar_clips_mp4()

    @staticmethod
    def _resize_cover(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
        w, h = img.size
        scale = max(target_w / w, target_h / h)
        nw, nh = int(w * scale), int(h * scale)
        resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
        left = (nw - target_w) // 2
        top = (nh - target_h) // 2
        return resized.crop((left, top, left + target_w, top + target_h))

    def _cargar_clips_mp4(self) -> None:
        if not self.gestos_dir.exists():
            return
        for ruta_mp4 in sorted(self.gestos_dir.glob("*.mp4")):
            clave = _quitar_acentos(ruta_mp4.stem.strip().lower())
            cap = cv2.VideoCapture(str(ruta_mp4))
            fps_clip = cap.get(cv2.CAP_PROP_FPS) or 24.0
            frames = []
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                pil_f = self._resize_cover(Image.fromarray(frame_rgb), WIDTH, TOP_HEIGHT)
                frames.append(np.array(pil_f, dtype=np.uint8))
            cap.release()
            if frames:
                self.clips_cache[clave] = frames
                self.clips_fps[clave] = float(fps_clip)

        print(f"[*] Gestos de Mr Nubsito cargados: {list(self.clips_cache.keys())}")

    @staticmethod
    def _indice_ping_pong(frame_idx: int, total_frames: int) -> int:
        if total_frames <= 1:
            return 0
        ciclo = 2 * (total_frames - 1)
        pos = frame_idx % ciclo
        return pos if pos < total_frames else ciclo - pos

    def renderizar_frame(
        self,
        gesto_solicitado: str,
        tiempo_global: float,
        tiempo_en_segmento: float,
        nivel_voz_rms: float,
        esta_hablando: bool,
    ) -> Image.Image:
        gesto_norm = _normalizar_gesto(gesto_solicitado)

        # Mantener el gesto elegido durante todo el segmento sin cortar a 'idle'
        # en las micro-pausas entre palabras/sílabas.
        if gesto_norm in self.clips_cache:
            gesto_activo = gesto_norm
        elif "hablando" in self.clips_cache:
            gesto_activo = "hablando"
        else:
            gesto_activo = "idle"

        dt = 1.0 / float(FPS)

        # Si está en modo hablado ('hablando' o 'hablando2') y ambos assets existen,
        # cuando un clip termina su duración natural encadena automáticamente con el otro
        # para no repetir en loop en videos largos y mantener la continuidad del cursor.
        if gesto_activo in ("hablando", "hablando2"):
            tiene_ambos = ("hablando" in self.clips_cache) and ("hablando2" in self.clips_cache)
            if tiene_ambos:
                f_h1 = self.clips_cache["hablando"]
                f_h2 = self.clips_cache["hablando2"]
                dur_h1 = max(0.5, len(f_h1) / self.clips_fps.get("hablando", 24.0))
                dur_h2 = max(0.5, len(f_h2) / self.clips_fps.get("hablando2", 24.0))
                dur_ciclo_doble = dur_h1 + dur_h2

                t_cur = self.t_cursor_hablando.get(gesto_activo, 0.0)
                self.t_cursor_hablando[gesto_activo] = t_cur + dt
                t_mod = t_cur % dur_ciclo_doble

                if gesto_activo == "hablando":
                    if t_mod < dur_h1:
                        clip_usar = "hablando"
                        t_local = t_mod
                    else:
                        clip_usar = "hablando2"
                        t_local = t_mod - dur_h1
                else:
                    if t_mod < dur_h2:
                        clip_usar = "hablando2"
                        t_local = t_mod
                    else:
                        clip_usar = "hablando"
                        t_local = t_mod - dur_h2

                clip_frames = self.clips_cache[clip_usar]
                fps_c = self.clips_fps.get(clip_usar, 24.0)
                idx = min(len(clip_frames) - 1, max(0, int(round(t_local * fps_c))))
                self.gesto_previo = gesto_activo
                return Image.fromarray(clip_frames[idx], mode="RGB").convert("RGBA")

        if gesto_activo == self.gesto_previo:
            self.t_acumulado_gesto += dt
        else:
            self.gesto_previo = gesto_activo
            self.t_acumulado_gesto = 0.0

        clip_frames = (
            self.clips_cache.get(gesto_activo)
            or self.clips_cache.get("hablando")
            or self.clips_cache.get("idle")
        )
        if clip_frames:
            fps_c = self.clips_fps.get(gesto_activo, 24.0)
            idx_nativo = int(round(self.t_acumulado_gesto * fps_c))
            idx = self._indice_ping_pong(idx_nativo, len(clip_frames))
            return Image.fromarray(clip_frames[idx], mode="RGB").convert("RGBA")

        return self._deformar_malla_25d(gesto_activo, tiempo_global, tiempo_en_segmento, nivel_voz_rms)

    def _deformar_malla_25d(self, gesto: str, t_global: float, t_seg: float, rms: float) -> Image.Image:
        resp_pecho = 4.5 * math.sin(t_global * 2.6)
        idle_head_x = 3.5 * math.sin(t_global * 1.7)
        idle_head_y = 3.0 * math.cos(t_global * 2.6)
        impulso_voz = min(14.0, rms * 55.0)

        dx_head = idle_head_x * self.mask_head
        dy_head = (idle_head_y - impulso_voz * 0.85) * self.mask_head
        dy_torso = (resp_pecho - impulso_voz * 0.45) * self.mask_torso

        map_x = (self.grid_x - dx_head).astype(np.float32)
        map_y = (self.grid_y - dy_head - dy_torso).astype(np.float32)

        warped = cv2.remap(
            self.base_np,
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT_101,
        )
        return Image.fromarray(warped, mode="RGB").convert("RGBA")


class GestorAssetsVisuales:
    """
    Gestiona de forma escalable:
    1) Todos los PNGs de `assets/emojis/` (con soporte para ñ/acentos y animación pop-in).
    2) La flecha roja `assets/emojis/flecha.png` (con rotación automática y rebote hacia el objetivo).
    3) El video `assets/Like&Sub.mp4` con eliminación de pantalla verde (Chroma Key + Despill) y su audio.
    """

    def __init__(self, emojis_dir: Path, like_sub_path: Path, timer_path: Path = TIMER_PATH):
        self.emojis_cache: Dict[str, Image.Image] = {}
        self.flecha_base: Optional[Image.Image] = None
        self.likesub_frames: List[Image.Image] = []
        self.likesub_fps: float = 25.0
        self.likesub_audio_pcm: Optional[np.ndarray] = None

        self.timer_frames: List[Image.Image] = []
        self.timer_fps: float = 30.0
        self.timer_duracion: float = DURACION_TIMER_SEG
        self.timer_audio_pcm: Optional[np.ndarray] = None

        self._cargar_emojis_y_flecha(emojis_dir)
        self._cargar_like_sub_chromakey(like_sub_path)
        self._cargar_timer_chromakey(timer_path)

    def _cargar_emojis_y_flecha(self, emojis_dir: Path) -> None:
        if not emojis_dir.exists():
            return
        for ruta_png in sorted(emojis_dir.glob("*.png")):
            stem_norm = _quitar_acentos(ruta_png.stem.strip().lower())
            try:
                img = Image.open(ruta_png).convert("RGBA")
            except Exception:
                continue

            if stem_norm == "flecha":
                self.flecha_base = img
            else:
                clave_corta = stem_norm[len("emoji_"):] if stem_norm.startswith("emoji_") else stem_norm
                self.emojis_cache[clave_corta] = img
                self.emojis_cache[stem_norm] = img

        nombres_unicos = sorted({k for k in self.emojis_cache if not k.startswith("emoji_")})
        print(f"[*] Emojis cargados: {nombres_unicos} | Flecha roja: {'Sí' if self.flecha_base else 'No'}")

    def _cargar_like_sub_chromakey(self, like_sub_path: Path) -> None:
        if not like_sub_path.exists():
            return

        cap = cv2.VideoCapture(str(like_sub_path))
        self.likesub_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0

        # Recortamos la banda donde ocurre toda la animación del cartel (x=55..665, y=540..760 en 720x1280)
        # y la escalamos por 1.38x para que luzca nítida en el lienzo 1080x1920.
        x0, x1 = 55, 665
        y0, y1 = 540, 760
        escala = 1.38
        target_w = int(round((x1 - x0) * escala))
        target_h = int(round((y1 - y0) * escala))

        frames_rgba: List[Image.Image] = []
        while True:
            ret, frame_bgr = cap.read()
            if not ret:
                break
            recorte_bgr = frame_bgr[y0:y1, x0:x1]
            recorte_rgb = cv2.cvtColor(recorte_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)

            r = recorte_rgb[:, :, 0]
            g = recorte_rgb[:, :, 1]
            b = recorte_rgb[:, :, 2]
            max_rb = np.maximum(r, b)
            exceso_verde = g - max_rb

            # Rampa de transparencia suave para bordes anti-aliased sin halo verde
            alpha = np.clip(1.0 - (exceso_verde - 25.0) / 50.0, 0.0, 1.0)
            if float(np.max(alpha)) < 0.02:
                # Fotograma totalmente verde (vacío)
                frames_rgba.append(Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0)))
                continue

            # Despill de verde en bordes semitransparentes
            g_limpio = np.where(alpha < 0.99, np.minimum(g, max_rb), g)
            rgba_np = np.dstack([r, g_limpio, b, alpha * 255.0]).astype(np.uint8)
            img_rgba = Image.fromarray(rgba_np, mode="RGBA").resize((target_w, target_h), Image.Resampling.LANCZOS)
            frames_rgba.append(img_rgba)

        cap.release()

        # Recortar fotogramas vacíos del principio y final para que la animación sea inmediata
        no_vacios = [i for i, im in enumerate(frames_rgba) if im.getbbox() is not None]
        if no_vacios:
            ini_f = max(0, no_vacios[0] - 2)
            fin_f = min(len(frames_rgba), no_vacios[-1] + 3)
            self.likesub_frames = frames_rgba[ini_f:fin_f]

        # Extraer audio de Like&Sub.mp4 (clics de mouse y campanita)
        try:
            res = subprocess.run(
                ["ffmpeg", "-i", str(like_sub_path), "-f", "s16le", "-ac", "1", "-ar", "44100", "-"],
                capture_output=True,
                creationflags=SUBPROCESS_NO_WINDOW,
                check=False,
            )
            if res.returncode == 0 and len(res.stdout) > 0:
                pcm_full = np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0
                if no_vacios:
                    t_ini_audio = max(0.0, (no_vacios[0] - 2) / self.likesub_fps)
                    idx_sample = int(t_ini_audio * 44100)
                    self.likesub_audio_pcm = pcm_full[idx_sample:]
                else:
                    self.likesub_audio_pcm = pcm_full
        except Exception:
            pass

        print(f"[*] Overlay Like&Sub cargado (Chroma Key activo): {len(self.likesub_frames)} frames útiles")

    def _cargar_timer_chromakey(self, timer_path: Path) -> None:
        if not timer_path.exists():
            return

        cap = cv2.VideoCapture(str(timer_path))
        self.timer_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        w_vid = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1080)
        h_vid = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1920)

        # Recortar la región donde se ubica el reloj en timer.mp4 (x=420..660, y=1355..1635 en 1080x1920)
        # y escalarla por 1.25x para que se vea grande, nítida y llamativa en pantalla.
        x0 = max(0, int(round(w_vid * (420.0 / 1080.0))))
        x1 = min(w_vid, int(round(w_vid * (660.0 / 1080.0))))
        y0 = max(0, int(round(h_vid * (1355.0 / 1920.0))))
        y1 = min(h_vid, int(round(h_vid * (1635.0 / 1920.0))))
        escala = 1.25
        target_w = max(32, int(round((x1 - x0) * escala)))
        target_h = max(32, int(round((y1 - y0) * escala)))

        frames_rgba: List[Image.Image] = []
        while True:
            ret, frame_bgr = cap.read()
            if not ret:
                break
            recorte_bgr = frame_bgr[y0:y1, x0:x1]
            recorte_rgb = cv2.cvtColor(recorte_bgr, cv2.COLOR_BGR2RGB).astype(np.float32)

            r = recorte_rgb[:, :, 0]
            g = recorte_rgb[:, :, 1]
            b = recorte_rgb[:, :, 2]
            max_rb = np.maximum(r, b)
            exceso_verde = g - max_rb

            alpha = np.clip(1.0 - (exceso_verde - 25.0) / 50.0, 0.0, 1.0)
            if float(np.max(alpha)) < 0.02:
                frames_rgba.append(Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0)))
                continue

            g_limpio = np.where(alpha < 0.99, np.minimum(g, max_rb), g)
            rgba_np = np.dstack([r, g_limpio, b, alpha * 255.0]).astype(np.uint8)
            img_rgba = Image.fromarray(rgba_np, mode="RGBA").resize((target_w, target_h), Image.Resampling.LANCZOS)
            frames_rgba.append(img_rgba)

        cap.release()

        if frames_rgba:
            self.timer_frames = frames_rgba
            self.timer_duracion = DURACION_TIMER_SEG

        # Extraer audio del timer (tic-tac / cuenta regresiva de timer.mp4)
        try:
            res = subprocess.run(
                ["ffmpeg", "-i", str(timer_path), "-f", "s16le", "-ac", "1", "-ar", "44100", "-"],
                capture_output=True,
                creationflags=SUBPROCESS_NO_WINDOW,
                check=False,
            )
            if res.returncode == 0 and len(res.stdout) > 0:
                self.timer_audio_pcm = np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception:
            pass

        print(
            f"[*] Overlay Timer de Trivia cargado (Chroma Key activo): "
            f"{len(self.timer_frames)} frames ({self.timer_duracion:.1f}s)"
        )

    def dibujar_emoji(
        self,
        lienzo: Image.Image,
        nombre_emoji: Optional[str],
        posicion: str,
        t_seg: float,
        dur_seg: float,
        es_split: bool,
    ) -> None:
        if not nombre_emoji:
            return
        clave = _quitar_acentos(nombre_emoji.strip().lower())
        img_base = self.emojis_cache.get(clave)
        if img_base is None:
            return

        # Animación de entrada elástica (pop-in), flotado suave y salida limpia
        dur_mostrar = min(dur_seg - 0.05, 3.2)
        if t_seg < 0.05 or t_seg > dur_mostrar:
            return

        if t_seg < 0.25:
            p_in = (t_seg - 0.05) / 0.20
            escala_anim = 0.2 + 0.92 * math.sin(p_in * math.pi * 0.65)
            alpha_factor = min(1.0, p_in * 1.6)
        elif t_seg > dur_mostrar - 0.22:
            p_out = max(0.0, (dur_mostrar - t_seg) / 0.22)
            escala_anim = 0.6 + 0.4 * p_out
            alpha_factor = p_out
        else:
            escala_anim = 1.0 + 0.06 * math.sin(t_seg * 7.5)
            alpha_factor = 1.0

        tam_base = 230
        tam_actual = max(24, int(round(tam_base * escala_anim)))
        emoji_img = img_base.resize((tam_actual, tam_actual), Image.Resampling.LANCZOS)

        # Pequeño balanceo en ángulo
        angulo = 7.0 * math.sin(t_seg * 5.5)
        emoji_img = emoji_img.rotate(angulo, resample=Image.Resampling.BICUBIC, expand=True)

        if alpha_factor < 0.99:
            r, g, b, a = emoji_img.split()
            a = a.point(lambda p: int(p * alpha_factor))
            emoji_img = Image.merge("RGBA", (r, g, b, a))

        # Coordenadas según posición elegida (sin tapar subtítulos en y=637)
        y_base_top = TOP_HEIGHT + 230 if es_split else 360
        pos_map = {
            "derecha": (WIDTH - 210, y_base_top),
            "izquierda": (210, y_base_top),
            "centro": (WIDTH // 2, y_base_top + 140),
            "arriba_der": (WIDTH - 200, 180),
            "arriba_izq": (200, 180),
            "abajo": (WIDTH - 210, HEIGHT - 420),
        }
        cx, cy = pos_map.get(posicion, pos_map["derecha"])
        cy += int(round(8.0 * math.sin(t_seg * 6.0)))

        ew, eh = emoji_img.size
        lienzo.alpha_composite(emoji_img, dest=(max(0, cx - ew // 2), max(0, cy - eh // 2)))

    def dibujar_flecha_inteligente(
        self,
        lienzo: Image.Image,
        plan: Optional["PlanFlecha"],
        f_idx: int,
        es_split: bool,
    ) -> None:
        """
        Dibuja la flecha roja siguiendo el plan de tracking precalculado:
        - Aparece con un 'pop' elástico, señala con rebote y se desvanece al final.
        - Siempre apunta al objetivo (que se mueve con la acción del video).
        - Se coloca del lado con más espacio libre para no tapar la acción.
        """
        if plan is None or self.flecha_base is None:
            return
        k = f_idx - plan.frame_inicio
        if k < 0 or k >= len(plan.xs):
            return

        y_video_top = TOP_HEIGHT if es_split else 0
        h_video = BOTTOM_HEIGHT if es_split else HEIGHT
        tx = float(plan.xs[k])
        ty = float(plan.ys[k]) + y_video_top
        n = len(plan.xs)
        t_local = k / float(FPS)
        t_rest = (n - 1 - k) / float(FPS)

        # Animación: pop elástico de entrada (0.22s) + fade/encogido de salida (0.18s)
        if t_local < 0.22:
            p = t_local / 0.22
            c1 = 1.70158
            ease_back = 1.0 + (c1 + 1.0) * (p - 1.0) ** 3 + c1 * (p - 1.0) ** 2
            escala = 0.2 + 0.8 * ease_back
        else:
            escala = 1.0
        alpha = 1.0
        if t_rest < 0.18:
            q = max(0.0, t_rest / 0.18)
            alpha = q
            escala *= 0.75 + 0.25 * q

        # Dirección de la cola (desde la punta hacia atrás), suavizada en el plan
        cola_x = float(plan.cola_x[k])
        cola_y = float(plan.cola_y[k])
        norma = math.hypot(cola_x, cola_y) or 1.0
        cola_x, cola_y = cola_x / norma, cola_y / norma

        # La punta del PNG apunta a la IZQUIERDA (180°). Vector punta = -cola.
        dx, dy = -cola_x, -cola_y
        angulo_deseado = math.degrees(math.atan2(-dy, dx))
        angulo_rot = angulo_deseado - 180.0

        tam = max(24, int(round(210 * escala)))
        flecha_scaled = self.flecha_base.resize((tam, tam), Image.Resampling.LANCZOS)
        flecha_rot = flecha_scaled.rotate(angulo_rot, resample=Image.Resampling.BICUBIC, expand=True)
        if alpha < 0.999:
            a = flecha_rot.getchannel("A").point(lambda v: int(v * alpha))
            flecha_rot.putalpha(a)

        # Rebote rítmico de "señalar" a lo largo del eje (la punta casi toca el objetivo)
        rebote = 22.0 * abs(math.sin(t_local * 9.0))
        dist = (0.5 * tam) + 34.0 + rebote
        cx = tx + cola_x * dist
        cy = ty + cola_y * dist

        fw, fh = flecha_rot.size
        y_min = y_video_top
        y_max = y_video_top + h_video - fh
        pos_x = int(round(max(0, min(WIDTH - fw, cx - fw / 2.0))))
        pos_y = int(round(max(y_min, min(y_max, cy - fh / 2.0))))
        lienzo.alpha_composite(flecha_rot, dest=(pos_x, pos_y))

    def dibujar_like_sub(
        self,
        lienzo: Image.Image,
        activo: bool,
        t_seg: float,
        dur_seg: float,
    ) -> None:
        if not activo or not self.likesub_frames:
            return

        total_f = len(self.likesub_frames)
        dur_nativa = total_f / self.likesub_fps

        # Si el segmento es más corto que la animación nativa, adaptamos la velocidad
        # para que se vea el ciclo completo (Like -> Subscribe -> Campana) dentro del segmento.
        if dur_seg < dur_nativa:
            progreso = min(1.0, max(0.0, t_seg / max(0.1, dur_seg)))
            idx = min(total_f - 1, int(round(progreso * (total_f - 1))))
        else:
            idx = int(round(t_seg * self.likesub_fps))
            if idx >= total_f:
                return

        frame_overlay = self.likesub_frames[idx]
        ow, oh = frame_overlay.size
        pos_x = (WIDTH - ow) // 2
        pos_y = HEIGHT - oh - 140
        lienzo.alpha_composite(frame_overlay, dest=(pos_x, pos_y))

    def dibujar_timer(
        self,
        lienzo: Image.Image,
        activo: bool,
        t_en_timer: float,
        dur_timer: float,
        es_split: bool,
    ) -> None:
        if not activo or not self.timer_frames:
            return
        if t_en_timer < 0.0 or t_en_timer > dur_timer:
            return

        total_f = len(self.timer_frames)
        progreso = min(1.0, max(0.0, t_en_timer / max(0.1, dur_timer)))
        idx = min(total_f - 1, int(round(progreso * (total_f - 1))))

        frame_timer = self.timer_frames[idx]
        if t_en_timer < 0.16:
            p_in = t_en_timer / 0.16
            escala_anim = 0.55 + 0.52 * math.sin(p_in * math.pi * 0.65)
            nw = max(24, int(round(frame_timer.width * escala_anim)))
            nh = max(24, int(round(frame_timer.height * escala_anim)))
            frame_timer = frame_timer.resize((nw, nh), Image.Resampling.LANCZOS)
        elif t_en_timer > dur_timer - 0.12:
            p_out = max(0.0, (dur_timer - t_en_timer) / 0.12)
            escala_anim = 0.65 + 0.35 * p_out
            nw = max(24, int(round(frame_timer.width * escala_anim)))
            nh = max(24, int(round(frame_timer.height * escala_anim)))
            frame_timer = frame_timer.resize((nw, nh), Image.Resampling.LANCZOS)

        ow, oh = frame_timer.size
        cx = WIDTH // 2
        cy = 1480 if es_split else 960
        pos_x = max(0, min(WIDTH - ow, cx - ow // 2))
        pos_y = max(0, min(HEIGHT - oh, cy - oh // 2))
        lienzo.alpha_composite(frame_timer, dest=(pos_x, pos_y))


class LectorVideoReferencia:
    """
    Carga el video de entrada interpolado a 60 FPS reales (con detección de cortes de escena)
    y permite obtener el fotograma exacto con interpolación sub-frame en cualquier
    segundo `t_video`, redimensionado a (1080x1300) para modo Split o (1080x1920) para Full.
    """

    def __init__(self, ruta_video: Path, target_w: int, target_h: int):
        if not ruta_video.exists():
            raise FileNotFoundError(f"No se encontró el video de entrada: {ruta_video}")
        self.ruta_video = ruta_video
        self.target_w = target_w
        self.target_h = target_h
        self.fps_origen = float(FPS)
        self.frames: List[np.ndarray] = []

        print(f"[*] Procesando y convirtiendo '{ruta_video.name}' a {FPS} FPS fluidos...")

        filtro_vf = (
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h},"
            f"framerate=fps={FPS}:interp_start=0:interp_end=255:scene=8.2"
        )
        cmd_ff = [
            "ffmpeg",
            "-v", "error",
            "-i", str(ruta_video),
            "-vf", filtro_vf,
            "-pix_fmt", "rgb24",
            "-f", "rawvideo",
            "-",
        ]
        try:
            proc = subprocess.Popen(
                cmd_ff,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                creationflags=SUBPROCESS_NO_WINDOW,
            )
            bytes_por_frame = target_w * target_h * 3
            if proc.stdout:
                while True:
                    buf = proc.stdout.read(bytes_por_frame)
                    if len(buf) < bytes_por_frame:
                        break
                    frame_np = np.frombuffer(buf, dtype=np.uint8).reshape((target_h, target_w, 3)).copy()
                    self.frames.append(frame_np)
            proc.wait()
        except Exception:
            self.frames.clear()

        # Fallback a OpenCV si FFmpeg no pudo decodificar el archivo
        if not self.frames:
            cap = cv2.VideoCapture(str(ruta_video))
            self.fps_origen = cap.get(cv2.CAP_PROP_FPS) or 30.0
            while True:
                ret, frame_bgr = cap.read()
                if not ret:
                    break
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                h, w, _ = frame_rgb.shape
                scale = max(target_w / float(w), target_h / float(h))
                nw, nh = int(round(w * scale)), int(round(h * scale))
                resized = cv2.resize(
                    frame_rgb,
                    (nw, nh),
                    interpolation=cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR,
                )
                x0 = (nw - target_w) // 2
                y0 = (nh - target_h) // 2
                crop = resized[y0 : y0 + target_h, x0 : x0 + target_w]
                self.frames.append(crop)
            cap.release()

        if not self.frames:
            raise RuntimeError(f"El archivo de video {ruta_video} no pudo leerse.")
        self.duracion_total = len(self.frames) / float(self.fps_origen)
        print(f"[*] Video listo a {self.fps_origen:.0f} FPS: {len(self.frames)} frames ({self.duracion_total:.2f}s)")

    def obtener_frame_en_segundo(self, t_seg: float) -> Image.Image:
        pos = max(0.0, min(float(len(self.frames) - 1), t_seg * self.fps_origen))
        i0 = int(pos)
        frac = pos - i0

        if frac < 0.02 or i0 >= len(self.frames) - 1:
            return Image.fromarray(self.frames[i0], mode="RGB")

        f0 = self.frames[i0]
        f1 = self.frames[i0 + 1]
        # Si hay un cambio brusco de escena entre f0 y f1, no mezclar para evitar fantasma en el corte
        diff_escena = float(np.mean(np.abs(f0[::8, ::8].astype(np.int16) - f1[::8, ::8].astype(np.int16))))
        if diff_escena > 28.0:
            return Image.fromarray(f0 if frac < 0.5 else f1, mode="RGB")

        mezcla = cv2.addWeighted(f0, 1.0 - frac, f1, frac, 0.0)
        return Image.fromarray(mezcla, mode="RGB")


# ==============================================================================
# 6b. FLECHA INTELIGENTE: DETECCIÓN DE LA ACCIÓN + SEGUIMIENTO (TRACKING) DEL OBJETIVO
# ==============================================================================
DURACION_FLECHA_SEG = 2.2          # Tiempo que la flecha permanece en pantalla
UMBRAL_CORTE_ESCENA = 30.0         # Diferencia media de gris que se considera cambio de escena

_ZONAS_FLECHA: Dict[str, Tuple[float, float]] = {
    "centro": (0.50, 0.52),
    "izquierda": (0.27, 0.52),
    "derecha": (0.73, 0.52),
    "arriba": (0.50, 0.30),
    "abajo": (0.50, 0.74),
    "arriba_izq": (0.27, 0.30),
    "arriba_der": (0.73, 0.30),
    "abajo_izq": (0.27, 0.74),
    "abajo_der": (0.73, 0.74),
}


@dataclass
class PlanFlecha:
    frame_inicio: int        # Frame (dentro del segmento) en el que aparece la flecha
    xs: np.ndarray           # X del objetivo por frame (px dentro del área de video)
    ys: np.ndarray           # Y del objetivo por frame (px dentro del área de video)
    cola_x: np.ndarray       # Dirección de la cola de la flecha (desde la punta hacia atrás)
    cola_y: np.ndarray
    descripcion: str = ""


def _parsear_espec_flecha(espec: str) -> Dict[str, object]:
    s = _quitar_acentos(espec.strip().lower())
    fija = bool(re.search(r"\bfija\b|\bestatica\b|\bquieta\b", s))
    s = re.sub(r"\b(fija|estatica|quieta|seguir|sigue|tracking)\b", "", s).strip()

    parte_obj, parte_t = s, None
    if "@" in s:
        parte_obj, parte_t = s.split("@", 1)
    parte_obj = parte_obj.strip()

    res: Dict[str, object] = {
        "modo": "auto", "px": None, "py": None, "es_pixel": False,
        "zona": None, "t_abs": None, "t_rel": None, "fija": fija,
    }
    m = re.match(r"^(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)$", parte_obj)
    if m:
        res["modo"] = "punto"
        res["px"], res["py"] = float(m.group(1)), float(m.group(2))
        res["es_pixel"] = res["px"] > 100.0 or res["py"] > 100.0
    elif parte_obj in _ZONAS_FLECHA:
        res["modo"] = "zona"
        res["zona"] = parte_obj

    if parte_t:
        pt = parte_t.strip().rstrip("s").strip()
        if ":" in pt:
            res["t_abs"] = _parsear_tiempo_a_segundos(pt)
        else:
            try:
                res["t_rel"] = float(pt.replace(",", "."))
            except ValueError:
                pass
    return res


def _suavizar_1d(valores: np.ndarray, sigma: float) -> np.ndarray:
    if len(valores) < 3 or sigma <= 0:
        return valores.astype(np.float64)
    radio = max(1, int(round(sigma * 3)))
    xk = np.arange(-radio, radio + 1, dtype=np.float64)
    kernel = np.exp(-(xk ** 2) / (2.0 * sigma ** 2))
    kernel /= kernel.sum()
    padded = np.pad(valores.astype(np.float64), (radio, radio), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def planificar_flecha_inteligente(
    lector: "LectorVideoReferencia",
    espec: str,
    t_video_inicio_seg: float,
    dur_seg: float,
) -> Optional[PlanFlecha]:
    """
    Calcula (antes de renderizar el segmento) dónde y cuándo aparece la flecha y la
    trayectoria del objetivo cuadro a cuadro:
      1) Analiza el movimiento del segmento (compensando paneos de cámara) para encontrar
         el MOMENTO de mayor acción (si el guion no fijó un segundo con '@').
      2) Encuentra el OBJETO en movimiento (auto/zona) o ajusta el punto pedido por el guion
         al objeto en movimiento más cercano.
      3) Sigue al objetivo con flujo óptico Lucas-Kanade y suaviza la trayectoria.
      4) Evita cruzar cortes de escena (la flecha nunca 'salta' a otra toma).
    """
    frames = lector.frames
    if not frames or dur_seg <= 0.25:
        return None
    fps_v = float(lector.fps_origen)
    H, W = frames[0].shape[:2]
    n_frames_video = len(frames)
    e = _parsear_espec_flecha(espec)

    def idx_video(t_seg_rel: float) -> int:
        return int(max(0, min(n_frames_video - 1, round((t_video_inicio_seg + t_seg_rel) * fps_v))))

    # ---- 1. Mapa de movimiento del segmento (1/4 de resolución, ~30 análisis por segundo) ----
    ESC = 0.25
    sw, sh = max(16, int(W * ESC)), max(16, int(H * ESC))

    def gris(idx: int, ancho: int, alto: int) -> np.ndarray:
        g = cv2.cvtColor(cv2.resize(frames[idx], (ancho, alto), interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY)
        return g

    paso_t = 1.0 / 30.0
    t_list = np.arange(0.0, dur_seg, paso_t)
    if len(t_list) < 3:
        t_list = np.array([0.0, dur_seg * 0.5, max(0.0, dur_seg - 0.01)])
    grises = [cv2.GaussianBlur(gris(idx_video(t), sw, sh), (3, 3), 0).astype(np.float32) for t in t_list]

    mapas: List[np.ndarray] = []
    cortes: List[bool] = []
    energia: List[float] = []
    for a, b in zip(grises[:-1], grises[1:]):
        if float(np.mean(np.abs(a - b))) > UMBRAL_CORTE_ESCENA:
            mapas.append(np.zeros_like(a))
            cortes.append(True)
            energia.append(0.0)
            continue
        try:
            (sx, sy), _resp = cv2.phaseCorrelate(a, b)
        except cv2.error:
            sx, sy = 0.0, 0.0
        if abs(sx) > sw * 0.25 or abs(sy) > sh * 0.25:
            sx, sy = 0.0, 0.0
        M = np.float32([[1, 0, sx], [0, 1, sy]])
        a_alineado = cv2.warpAffine(a, M, (sw, sh), borderMode=cv2.BORDER_REPLICATE)
        d = np.abs(b - a_alineado)
        d = np.maximum(0.0, d - float(np.median(d)) - 4.0)
        margen = int(max(abs(sx), abs(sy))) + 2
        d[:margen, :] = 0
        d[-margen:, :] = 0
        d[:, :margen] = 0
        d[:, -margen:] = 0
        mapas.append(d)
        cortes.append(False)
        energia.append(float(d.sum()))
    t_pares = t_list[1:]
    energia_np = np.array(energia, dtype=np.float64)

    # ---- 2. Ventana temporal de la flecha (siempre dentro de UNA sola toma, sin cruzar cortes) ----
    MIN_VISIBLE = 0.8
    tiempos_corte = [float(t_p) for t_p, es_c in zip(t_pares, cortes) if es_c]
    limites = [0.0] + tiempos_corte + [dur_seg]
    tomas = [(limites[i], limites[i + 1]) for i in range(len(limites) - 1) if limites[i + 1] - limites[i] > 0.05]
    if not tomas:
        tomas = [(0.0, dur_seg)]

    def toma_de(t: float) -> Tuple[float, float]:
        for (a_t, b_t) in tomas:
            if a_t <= t < b_t:
                return a_t, b_t
        return tomas[-1]

    L_max = min(DURACION_FLECHA_SEG, max(0.5, dur_seg - 0.1))
    origen_tiempo = "auto (momento de mayor acción)"
    t_pedido: Optional[float] = None
    if e["t_abs"] is not None:
        t_pedido = float(e["t_abs"]) - t_video_inicio_seg
        origen_tiempo = f"guion @{float(e['t_abs']):.2f}s del video"
    elif e["t_rel"] is not None:
        t_pedido = float(e["t_rel"])
        origen_tiempo = f"guion +{float(e['t_rel']):.2f}s del segmento"

    if t_pedido is not None:
        t_pedido = max(0.05, min(dur_seg - 0.3, t_pedido))
        a_t, b_t = toma_de(t_pedido)
        t0 = max(t_pedido, a_t + 0.03)
        if b_t - t0 < MIN_VISIBLE:
            if b_t - a_t >= MIN_VISIBLE:
                t0 = max(a_t + 0.03, b_t - MIN_VISIBLE)   # Adelantar dentro de la misma toma
            else:
                # Toma demasiado corta: usar la toma siguiente (o la anterior si es la última)
                idx_t = tomas.index((a_t, b_t))
                alternativa = tomas[idx_t + 1] if idx_t + 1 < len(tomas) else (tomas[idx_t - 1] if idx_t > 0 else (a_t, b_t))
                a_t, b_t = alternativa
                t0 = a_t + 0.03
        L = max(0.3, min(L_max, b_t - t0 - paso_t))
    else:
        mejor = (0.05, L_max, -1.0)
        hay_tomas_largas = any((b_t - a_t) >= MIN_VISIBLE + 0.1 for (a_t, b_t) in tomas)
        for (a_t, b_t) in tomas:
            largo_toma = b_t - a_t
            if largo_toma < 0.35 or (hay_tomas_largas and largo_toma < MIN_VISIBLE + 0.1):
                continue
            for t_cand in np.arange(a_t + 0.03, max(a_t + 0.04, b_t - min(MIN_VISIBLE, largo_toma) + 1e-6), paso_t):
                L_c = min(L_max, b_t - t_cand - paso_t)
                if L_c < min(MIN_VISIBLE, largo_toma * 0.9):
                    continue
                dentro = (t_pares > t_cand) & (t_pares <= t_cand + L_c)
                if not np.any(dentro):
                    continue
                # Energía media de acción * factor de legibilidad (la flecha debe verse ~1.6s o más)
                score = float(energia_np[dentro].mean()) * min(1.0, L_c / 1.6)
                if score > mejor[2]:
                    mejor = (float(t_cand), float(L_c), score)
        t0, L, _ = mejor
        if mejor[2] < 0:
            a_t, b_t = max(tomas, key=lambda tb: tb[1] - tb[0])
            t0, L = a_t + 0.03, max(0.3, min(L_max, b_t - a_t - 0.05))

    # ---- 3. Selección del objetivo inicial ----
    dentro_ini = (t_pares > t0) & (t_pares <= t0 + min(0.6, L))
    acc = np.zeros((sh, sw), dtype=np.float32)
    n_usados = 0
    for m_d, ok in zip(mapas, dentro_ini):
        if ok:
            acc += m_d
            n_usados += 1
    acc = cv2.GaussianBlur(acc, (0, 0), 4.0)
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    hay_movimiento = float(acc.max()) > 3.0 * max(1, n_usados)

    def peso_gauss(cx: float, cy: float, sigma_px: float) -> np.ndarray:
        return np.exp(-(((xx - cx) ** 2) + ((yy - cy) ** 2)) / (2.0 * sigma_px ** 2))

    modo = str(e["modo"])
    if modo == "punto":
        if e["es_pixel"]:
            px_n, py_n = float(e["px"]) / W, float(e["py"]) / H
        else:
            px_n, py_n = float(e["px"]) / 100.0, float(e["py"]) / 100.0
        px_n, py_n = min(0.98, max(0.02, px_n)), min(0.98, max(0.02, py_n))
        x0_n, y0_n = px_n, py_n
        if hay_movimiento and not e["fija"]:
            ponderado = acc * peso_gauss(px_n * sw, py_n * sh, 0.10 * sw)
            if float(ponderado.max()) > 0.25 * float(acc.max()):
                iy, ix = np.unravel_index(int(np.argmax(ponderado)), ponderado.shape)
                x0_n, y0_n = (ix + 0.5) / sw, (iy + 0.5) / sh
    else:
        zona = str(e["zona"] or "centro")
        zx, zy = _ZONAS_FLECHA.get(zona, _ZONAS_FLECHA["centro"])
        sigma_rel = 0.24 if modo == "zona" else 0.45
        x0_n, y0_n = zx, zy
        if hay_movimiento:
            ponderado = acc * peso_gauss(zx * sw, zy * sh, sigma_rel * sw)
            # Evitar bordes extremos (marcas de agua / interfaz)
            ponderado[: int(sh * 0.06), :] = 0
            ponderado[int(sh * 0.94):, :] = 0
            iy, ix = np.unravel_index(int(np.argmax(ponderado)), ponderado.shape)
            x0_n, y0_n = (ix + 0.5) / sw, (iy + 0.5) / sh

    # ---- 4. Seguimiento cuadro a cuadro con flujo óptico (1/2 resolución) ----
    n_out = max(2, int(round(L * FPS)))
    ESC_T = 0.5
    tw, th = max(16, int(W * ESC_T)), max(16, int(H * ESC_T))
    radio = max(18.0, 0.045 * tw)
    pt = np.array([x0_n * tw, y0_n * th], dtype=np.float32)

    def detectar_puntos(g: np.ndarray, centro: np.ndarray) -> Optional[np.ndarray]:
        mascara = np.zeros_like(g)
        cv2.circle(mascara, (int(centro[0]), int(centro[1])), int(radio), 255, -1)
        return cv2.goodFeaturesToTrack(g, maxCorners=40, qualityLevel=0.01, minDistance=3, mask=mascara)

    idx_prev = idx_video(t0)
    g_prev = gris(idx_prev, tw, th)
    puntos = None if e["fija"] else detectar_puntos(g_prev, pt)
    seguimiento_activo = not bool(e["fija"])
    atraer_a_movimiento = seguimiento_activo
    fuerza_atraccion = 0.06 if modo in ("auto", "zona") else 0.035

    xs_l: List[float] = []
    ys_l: List[float] = []
    for k in range(n_out):
        t_rel = t0 + k / float(FPS)
        idx = idx_video(t_rel)
        if seguimiento_activo and idx != idx_prev:
            g = gris(idx, tw, th)
            if float(np.mean(np.abs(g.astype(np.int16) - g_prev.astype(np.int16)))) > UMBRAL_CORTE_ESCENA:
                seguimiento_activo = False  # Corte de escena: congelar posición
            else:
                if puntos is not None and len(puntos) > 0:
                    nuevos, estado, _err = cv2.calcOpticalFlowPyrLK(
                        g_prev, g, puntos, None, winSize=(21, 21), maxLevel=3,
                        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03),
                    )
                    if nuevos is not None and estado is not None:
                        ok = estado.reshape(-1) == 1
                        if int(ok.sum()) >= 3:
                            vec = (nuevos[ok] - puntos[ok]).reshape(-1, 2)
                            # Separar movimiento de cámara (paneo global) del movimiento propio del objeto
                            try:
                                (gsx, gsy), _r = cv2.phaseCorrelate(g_prev.astype(np.float32), g.astype(np.float32))
                            except cv2.error:
                                gsx, gsy = 0.0, 0.0
                            rel = np.linalg.norm(vec - np.array([gsx, gsy], dtype=np.float32), axis=1)
                            propios = rel > 1.2
                            if int(propios.sum()) >= 3 and float(propios.mean()) >= 0.2:
                                desp = np.median(vec[propios], axis=0)
                            else:
                                desp = np.median(vec, axis=0)
                            pt = pt + desp.astype(np.float32)
                            # Conservar solo los puntos que se mueven igual que el objetivo
                            coherentes = np.linalg.norm(vec - desp, axis=1) < 2.5
                            puntos = nuevos[ok].reshape(-1, 2)[coherentes].reshape(-1, 1, 2)
                            dist = np.linalg.norm(puntos.reshape(-1, 2) - pt, axis=1)
                            puntos = puntos[dist < radio * 1.8].reshape(-1, 1, 2)
                        else:
                            puntos = None

                # Atraer suavemente el objetivo hacia el movimiento real cercano (cámara compensada)
                if atraer_a_movimiento and len(mapas) > 0:
                    ip = int(np.argmin(np.abs(t_pares - t_rel)))
                    mp = mapas[ip]
                    cxq, cyq = pt[0] * (sw / tw), pt[1] * (sh / th)
                    rq = radio * (sw / tw) * 2.2
                    x1, x2 = int(max(0, cxq - rq)), int(min(sw, cxq + rq))
                    y1, y2 = int(max(0, cyq - rq)), int(min(sh, cyq + rq))
                    if x2 > x1 and y2 > y1:
                        ventana = mp[y1:y2, x1:x2]
                        masa = float(ventana.sum())
                        if masa > 60.0:
                            vy_idx, vx_idx = np.mgrid[y1:y2, x1:x2]
                            mx = float((ventana * vx_idx).sum() / masa) * (tw / sw)
                            my = float((ventana * vy_idx).sum() / masa) * (th / sh)
                            pt = pt + fuerza_atraccion * (np.array([mx, my], dtype=np.float32) - pt)

                if puntos is None or len(puntos) < 6:
                    puntos = detectar_puntos(g, pt)
                g_prev = g
            idx_prev = idx

        pt[0] = float(min(tw - 4, max(4, pt[0])))
        pt[1] = float(min(th - 4, max(4, pt[1])))
        xs_l.append(float(pt[0]) / ESC_T)
        ys_l.append(float(pt[1]) / ESC_T)

    xs = _suavizar_1d(np.array(xs_l), 5.0)
    ys = _suavizar_1d(np.array(ys_l), 5.0)
    margen_px = 30.0
    xs = np.clip(xs, margen_px, W - margen_px)
    ys = np.clip(ys, margen_px, H - margen_px)

    # ---- 5. Orientación: la flecha se ubica del lado con más espacio (estable, sin girar loca) ----
    mx_med, my_med = float(np.median(xs)), float(np.median(ys))
    hx = float(np.clip((W * 0.5 - mx_med) / (W * 0.15), -1.0, 1.0))
    vy = -0.65 if my_med > H * 0.33 else 0.65   # Arriba del objetivo, salvo que esté muy arriba
    if abs(hx) < 0.25:
        hx = 0.35 if mx_med < W * 0.5 else -0.35
    norma = math.hypot(hx, vy) or 1.0
    cola_x = np.full(len(xs), hx / norma)
    cola_y = np.full(len(xs), vy / norma)

    desc = (
        f"modo={modo}{' (fija)' if e['fija'] else ' + seguimiento'} | aparece {t_video_inicio_seg + t0:.2f}s "
        f"del video ({origen_tiempo}) | dura {L:.2f}s | objetivo inicial ({x0_n * 100:.0f}%, {y0_n * 100:.0f}%)"
    )
    return PlanFlecha(
        frame_inicio=int(round(t0 * FPS)),
        xs=xs, ys=ys, cola_x=cola_x, cola_y=cola_y,
        descripcion=desc,
    )


# ==============================================================================
# 7. UTILIDADES DE AUDIO, SFX Y RENDERIZADO PRINCIPAL
# ==============================================================================
def preparar_assets_base() -> None:
    for carpeta in [ASSETS_DIR, GESTOS_DIR, EMOJIS_DIR, FONTS_DIR, SFX_DIR, TEMP_DIR, OUTPUT_DIR]:
        carpeta.mkdir(parents=True, exist_ok=True)
    asegurar_fuente_mrbeast()

    base_img_path = ASSETS_DIR / "mr_nubsito_base.png"
    if not base_img_path.exists():
        img = Image.new("RGBA", (1080, 620), (24, 20, 48, 255))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([260, 120, 680, 620], radius=50, fill=(35, 35, 45, 255), outline=(210, 40, 40, 255), width=16)
        draw.rounded_rectangle([240, 360, 820, 650], radius=90, fill=(0, 115, 230, 255))
        draw.polygon([(390, 95), (690, 110), (680, 390), (380, 380)], fill=(245, 195, 25, 255), outline=(160, 120, 10, 255))
        draw.ellipse([475, 195, 505, 235], fill=(40, 35, 30, 255))
        draw.ellipse([585, 195, 615, 235], fill=(40, 35, 30, 255))
        draw.arc([465, 230, 625, 330], start=15, end=165, fill=(40, 35, 30, 255), width=14)
        img.save(base_img_path)


def calcular_envolvente_rms(ruta_wav: Path, fps: int) -> np.ndarray:
    with wave.open(str(ruta_wav), "r") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    muestras_por_frame = max(1, int(sr / fps))
    total_video_frames = int(math.ceil(len(audio) / muestras_por_frame))
    rms_por_frame = np.zeros(total_video_frames, dtype=np.float32)

    for i in range(total_video_frames):
        chunk = audio[i * muestras_por_frame : (i + 1) * muestras_por_frame]
        if len(chunk) > 0:
            rms_por_frame[i] = float(np.sqrt(np.mean(chunk ** 2)))
    return rms_por_frame


class GestorAudioSFX:
    """
    Carga y decodifica todos los sonidos de `assets/sfx` (.mp3, .wav, .ogg) a PCM 44.1kHz mono,
    recorta silencios muertos al inicio/final y normaliza el pico para una mezcla limpia.
    Soporta combinaciones múltiples separadas por `+` (ej. `punch+cartoon_laugh`).
    """

    def __init__(self, sfx_dir: Path, sr: int = 44100):
        self.sfx_dir = sfx_dir
        self.sr = sr
        self.cache_pcm: Dict[str, np.ndarray] = {}
        self.mapa_archivos: Dict[str, Path] = {}
        self._indexar_directorio()

    def _indexar_directorio(self) -> None:
        if not self.sfx_dir.exists():
            return
        # Preferir .mp3/.ogg o .wav según disponibilidad; si hay duplicados, priorizar el archivo más reciente/completo
        for archivo in sorted(self.sfx_dir.glob("*.*")):
            if archivo.suffix.lower() in (".wav", ".mp3", ".ogg"):
                clave = _quitar_acentos(archivo.stem.strip().lower()).replace(" ", "_").replace("-", "_")
                if clave not in self.mapa_archivos or archivo.suffix.lower() == ".mp3":
                    self.mapa_archivos[clave] = archivo

    def obtener_pcm(self, nombre_sfx: Optional[str], max_dur_seg: float = 3.2) -> Optional[np.ndarray]:
        if not nombre_sfx:
            return None
        clave_norm = _normalizar_sfx(nombre_sfx) or _quitar_acentos(nombre_sfx.strip().lower()).replace(" ", "_")
        if "+" in clave_norm:
            clave_norm = clave_norm.split("+")[0]

        if clave_norm not in self.cache_pcm:
            ruta = self.mapa_archivos.get(clave_norm)
            if ruta is None or not ruta.exists():
                return None
            try:
                res = subprocess.run(
                    [
                        "ffmpeg", "-v", "error",
                        "-i", str(ruta),
                        "-f", "s16le", "-ac", "1", "-ar", str(self.sr),
                        "-",
                    ],
                    capture_output=True,
                    creationflags=SUBPROCESS_NO_WINDOW,
                    check=False,
                )
                if res.returncode != 0 or not res.stdout:
                    return None
                pcm = np.frombuffer(res.stdout, dtype=np.int16).astype(np.float32) / 32768.0
                if len(pcm) == 0:
                    return None

                # Recortar silencio inicial/final para que el impacto suene a tiempo
                abs_pcm = np.abs(pcm)
                pico = float(np.max(abs_pcm))
                if pico > 1e-4:
                    idx_activos = np.where(abs_pcm > max(0.004, pico * 0.025))[0]
                    if len(idx_activos) > 0:
                        i0 = max(0, int(idx_activos[0]) - int(self.sr * 0.008))
                        i1 = min(len(pcm), int(idx_activos[-1]) + int(self.sr * 0.025))
                        pcm = pcm[i0:i1]
                    pcm = (pcm / pico) * 0.85

                self.cache_pcm[clave_norm] = pcm.astype(np.float32)
            except Exception:
                return None

        pcm_base = self.cache_pcm.get(clave_norm)
        if pcm_base is None or len(pcm_base) == 0:
            return None

        max_samples = int(round(max_dur_seg * self.sr))
        if len(pcm_base) > max_samples > 0:
            recorte = pcm_base[:max_samples].copy()
            fade_n = min(int(self.sr * 0.04), len(recorte) // 4)
            if fade_n > 1:
                recorte[-fade_n:] *= np.linspace(1.0, 0.0, fade_n, dtype=np.float32)
            return recorte
        return pcm_base


def mezclar_voz_sfx_y_likesub(
    ruta_voz_wav: Path,
    duracion_total_seg: float,
    nombre_sfx: Optional[str],
    incluir_audio_likesub: bool,
    likesub_audio_pcm: Optional[np.ndarray],
    ruta_salida_wav: Path,
    gestor_sfx: Optional[GestorAudioSFX] = None,
    tiene_emoji: bool = False,
    tiene_flecha: bool = False,
    incluir_timer: bool = False,
    t_inicio_timer_seg: float = 0.0,
    dur_timer_seg: float = DURACION_TIMER_SEG,
    timer_audio_pcm: Optional[np.ndarray] = None,
) -> None:
    sr = 44100
    n_total = max(1, int(round(duracion_total_seg * sr)))
    mezcla = np.zeros(n_total, dtype=np.float32)

    with wave.open(str(ruta_voz_wav), "r") as wf:
        voz_pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        n_copy = min(n_total, len(voz_pcm))
        if n_copy > 0:
            voz_fuerte = np.tanh(voz_pcm[:n_copy] * VOLUMEN_VOZ_YOUTUBER)
            fade_out = min(int(sr * 0.015), n_copy // 4)
            if fade_out > 1:
                voz_fuerte[-fade_out:] *= np.linspace(1.0, 0.0, fade_out, dtype=np.float32)
            mezcla[:n_copy] += voz_fuerte

    if gestor_sfx is not None:
        lista_sfx = _desglosar_sfx_lista(nombre_sfx)
        if incluir_timer:
            lista_sfx = [s for s in lista_sfx if s != "tictac"]

        # Soporta 1 o más SFX por segmento (ej. `punch+cartoon_laugh` escalonados)
        for idx_s, sfx_item in enumerate(lista_sfx):
            offset_seg = 0.0 if idx_s == 0 else min(max(0.18, duracion_total_seg * 0.36), max(0.0, duracion_total_seg - 0.45))
            idx_ini_s = max(0, min(n_total, int(round(offset_seg * sr))))
            n_disp_s = n_total - idx_ini_s
            if n_disp_s <= 0:
                continue
            sfx_pcm = gestor_sfx.obtener_pcm(sfx_item, max_dur_seg=min(2.6, max(0.5, duracion_total_seg * 0.80)))
            if sfx_pcm is not None and len(sfx_pcm) > 0:
                n_s = min(n_disp_s, len(sfx_pcm))
                ganancia = 0.30 if idx_s == 0 else 0.26
                mezcla[idx_ini_s : idx_ini_s + n_s] += sfx_pcm[:n_s] * ganancia

        # Pop sutil automático al aparecer un emoji o flecha si no estaba ya en la lista
        if (tiene_emoji or tiene_flecha) and "pop" not in lista_sfx:
            pop_pcm = gestor_sfx.obtener_pcm("pop", max_dur_seg=0.45)
            if pop_pcm is not None and len(pop_pcm) > 0:
                idx_pop = min(n_total, int(round(0.06 * sr)))
                n_pop = min(n_total - idx_pop, len(pop_pcm))
                if n_pop > 0:
                    mezcla[idx_pop : idx_pop + n_pop] += pop_pcm[:n_pop] * 0.16

    if incluir_timer:
        idx_ini_t = max(0, min(n_total, int(round(t_inicio_timer_seg * sr))))
        n_disp = n_total - idx_ini_t
        if n_disp > 0:
            if timer_audio_pcm is not None and len(timer_audio_pcm) > 0:
                n_t = min(n_disp, len(timer_audio_pcm))
                mezcla[idx_ini_t : idx_ini_t + n_t] += timer_audio_pcm[:n_t] * 0.92
            elif gestor_sfx is not None:
                tt_pcm = gestor_sfx.obtener_pcm("tictac", max_dur_seg=dur_timer_seg)
                if tt_pcm is not None and len(tt_pcm) > 0:
                    n_target = min(n_disp, int(round(dur_timer_seg * sr)))
                    reps = int(math.ceil(n_target / float(len(tt_pcm))))
                    tt_rep = np.tile(tt_pcm, reps)[:n_target]
                    mezcla[idx_ini_t : idx_ini_t + n_target] += tt_rep * 0.45

    if incluir_audio_likesub and likesub_audio_pcm is not None and len(likesub_audio_pcm) > 0:
        n_ls = min(n_total, len(likesub_audio_pcm))
        mezcla[:n_ls] += likesub_audio_pcm[:n_ls] * 0.32

    mezcla = np.clip(mezcla, -0.98, 0.98)
    pcm_out = (mezcla * 32767).astype(np.int16)
    with wave.open(str(ruta_salida_wav), "w") as wf_out:
        wf_out.setnchannels(1)
        wf_out.setsampwidth(2)
        wf_out.setframerate(sr)
        wf_out.writeframes(pcm_out.tobytes())


def detectar_encoder_nvidia() -> str:
    try:
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            creationflags=SUBPROCESS_NO_WINDOW,
            check=False,
        )
        if "h264_nvenc" in res.stdout:
            return "h264_nvenc"
    except FileNotFoundError:
        pass
    return "libx264"


def _asignar_tiempos_reales_segmentos(
    segmentos: List[SegmentoVideoGuion],
    dur_video_origen: float,
) -> List[Tuple[int, float]]:
    """
    Garantiza que los segmentos cubran de forma continua de `0.0` a `dur_video_origen`
    sin huecos mudos y que la suma de frames coincida al frame exacto con el video original.
    """
    n_seg = len(segmentos)
    total_frames_video = max(1, int(round(dur_video_origen * FPS)))
    if n_seg == 0:
        return []

    pesos: List[float] = []
    for seg in segmentos:
        if seg.t_inicio_video is not None and seg.t_fin_video is not None and seg.t_fin_video > seg.t_inicio_video:
            t_ini = min(dur_video_origen, max(0.0, seg.t_inicio_video))
            t_fin = min(dur_video_origen, max(t_ini + 0.25, seg.t_fin_video))
            pesos.append(max(0.4, t_fin - t_ini))
        else:
            n_pal = max(3, len(seg.texto_voz.split()))
            extra_t = seg.duracion_timer_seg if seg.mostrar_timer else 0.0
            pesos.append(max(1.2, (n_pal / 3.5) + extra_t))

    suma_pesos = max(0.1, sum(pesos))
    frames_asignados: List[int] = []
    acumulado = 0
    for i, p in enumerate(pesos):
        if i == n_seg - 1:
            f_seg = max(1, total_frames_video - acumulado)
        else:
            f_seg = max(1, int(round((p / suma_pesos) * total_frames_video)))
            if acumulado + f_seg >= total_frames_video:
                f_seg = max(1, total_frames_video - acumulado - (n_seg - 1 - i))
        frames_asignados.append(f_seg)
        acumulado += f_seg

    diff = total_frames_video - sum(frames_asignados)
    if diff != 0:
        frames_asignados[-1] = max(1, frames_asignados[-1] + diff)

    cursor_t = 0.0
    resultado: List[Tuple[int, float]] = []
    for seg, f_seg in zip(segmentos, frames_asignados):
        dur_seg = f_seg / float(FPS)
        seg.t_inicio_video = cursor_t
        seg.t_fin_video = min(dur_video_origen, cursor_t + dur_seg)
        cursor_t += dur_seg
        resultado.append((f_seg, dur_seg))

    return resultado


async def editar_video_con_guion(ruta_video: Path, ruta_guion: Path) -> Path:
    print("=" * 74)
    print(" MR NUBSITO STUDIO v4.3 - SINCRONIZACIÓN CONTINUA 1:1 + SFX COMPLETOS")
    print("=" * 74)

    preparar_assets_base()
    config = cargar_guion_video(ruta_guion)
    if not config.segmentos:
        raise ValueError(f"El guión '{ruta_guion.name}' no contiene segmentos válidos.")

    es_split = (config.modo_pantalla != "full")
    alto_video = BOTTOM_HEIGHT if es_split else HEIGHT

    lector_video = LectorVideoReferencia(ruta_video, WIDTH, alto_video)
    motor_personaje = MotorPersonajeMrNubsitoV2(ASSETS_DIR / "mr_nubsito_base.png", GESTOS_DIR) if es_split else None
    gestor_assets = GestorAssetsVisuales(EMOJIS_DIR, LIKE_SUB_PATH, TIMER_PATH)
    gestor_sfx = GestorAudioSFX(SFX_DIR)

    dur_video_input = float(lector_video.duracion_total)
    tiempos_segmentos = _asignar_tiempos_reales_segmentos(config.segmentos, dur_video_input)

    datos_segmentos = []
    archivos_audio_seg: List[Path] = []

    # 1. Generar todas las voces ajustadas al tramo exacto de cada escena, sin huecos ni cortes de última palabra
    for idx, (seg, (total_frames_seg, dur_seg_exacta)) in enumerate(zip(config.segmentos, tiempos_segmentos)):
        if seg.mostrar_timer:
            dur_timer = min(seg.duracion_timer_seg, max(0.8, dur_seg_exacta - 1.1))
            dur_voz_objetivo = max(0.6, dur_seg_exacta - dur_timer - 0.08)
        else:
            dur_timer = 0.0
            dur_voz_objetivo = dur_seg_exacta

        info_assets = (
            f"{seg.t_inicio_video:.1f}s-{seg.t_fin_video:.1f}s ({dur_seg_exacta:.2f}s) | "
            f"gesto={seg.gesto_personaje} | sfx={seg.sfx_inicio or '-'} | "
            f"emoji={seg.emoji or '-'} | flecha={seg.flecha or '-'} | "
            f"timer={f'Sí({dur_timer:.1f}s)' if seg.mostrar_timer else 'No'}"
        )
        print(f"[+] Segmento [{idx + 1}/{len(config.segmentos)}] ({info_assets}): {seg.texto_voz}")
        ruta_mp3 = TEMP_DIR / f"{seg.id_segmento}.mp3"
        dur_voz, palabras_sub, ini_voz, fin_voz = await generar_voz_y_timestamps_segmento(
            seg.texto_voz,
            ruta_mp3,
            dur_objetivo_seg=dur_voz_objetivo,
        )

        if seg.mostrar_timer:
            t_ini_timer: Optional[float] = min(max(0.3, dur_voz + 0.05), max(0.3, dur_seg_exacta - dur_timer))
        else:
            t_ini_timer = None

        ruta_voz_wav = ruta_mp3.with_suffix(".wav")
        rms_env = calcular_envolvente_rms(ruta_voz_wav, FPS)

        ruta_mix_wav = TEMP_DIR / f"{seg.id_segmento}_mix.wav"
        mezclar_voz_sfx_y_likesub(
            ruta_voz_wav=ruta_voz_wav,
            duracion_total_seg=dur_seg_exacta,
            nombre_sfx=seg.sfx_inicio,
            incluir_audio_likesub=seg.mostrar_like_sub,
            likesub_audio_pcm=gestor_assets.likesub_audio_pcm,
            ruta_salida_wav=ruta_mix_wav,
            gestor_sfx=gestor_sfx,
            tiene_emoji=bool(seg.emoji),
            tiene_flecha=bool(seg.flecha),
            incluir_timer=seg.mostrar_timer,
            t_inicio_timer_seg=t_ini_timer or 0.0,
            dur_timer_seg=dur_timer,
            timer_audio_pcm=gestor_assets.timer_audio_pcm,
        )
        archivos_audio_seg.append(ruta_mix_wav)

        datos_segmentos.append(
            [seg, total_frames_seg, dur_seg_exacta, palabras_sub, rms_env, ini_voz, fin_voz, t_ini_timer, dur_timer]
        )

    dur_total_locucion = sum(item[2] for item in datos_segmentos)
    print(f"[*] Duración video original: {dur_video_input:.2f}s | Duración sincronizada total: {dur_total_locucion:.2f}s")

    # 2. Unir el audio maestro de la voz + SFX + audio original del video de fondo con duración exacta
    ruta_voz_maestra = TEMP_DIR / "voz_maestra.wav"
    bloques_pcm = []
    for ruta_w in archivos_audio_seg:
        with wave.open(str(ruta_w), "r") as wf:
            bloques_pcm.append(wf.readframes(wf.getnframes()))

    with wave.open(str(ruta_voz_maestra), "w") as wf_master:
        wf_master.setnchannels(1)
        wf_master.setsampwidth(2)
        wf_master.setframerate(44100)
        bytes_target = int(round(dur_video_input * 44100)) * 2
        bytes_escritos = 0
        for b in bloques_pcm:
            if bytes_escritos + len(b) > bytes_target:
                b = b[: bytes_target - bytes_escritos]
            wf_master.writeframes(b)
            bytes_escritos += len(b)
            if bytes_escritos >= bytes_target:
                break
        if bytes_escritos < bytes_target:
            wf_master.writeframes(b"\x00" * (bytes_target - bytes_escritos))

    ruta_audio_final = TEMP_DIR / "audio_final_combinado.wav"
    if config.volumen_video_original > 0.001:
        res_mix = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(ruta_voz_maestra),
                "-t", f"{dur_video_input:.3f}",
                "-i", str(ruta_video),
                "-filter_complex",
                f"[1:a]volume={config.volumen_video_original}[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.98:level=disabled[aout]",
                "-map", "[aout]",
                "-t", f"{dur_video_input:.3f}",
                "-ar", "44100", "-ac", "1",
                str(ruta_audio_final),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=SUBPROCESS_NO_WINDOW,
            check=False,
        )
        if res_mix.returncode != 0 or not ruta_audio_final.exists():
            ruta_audio_final = ruta_voz_maestra
    else:
        ruta_audio_final = ruta_voz_maestra

    # 3. Renderizar video final con FFmpeg usando TITULO_SUPERIOR exclusivamente para el nombre del archivo .mp4
    if config.titulo_superior:
        titulo_limpio = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", config.titulo_superior).strip().rstrip(".")
        nombre_out = titulo_limpio or config.archivo_salida or f"editado_{ruta_video.stem}.mp4"
    else:
        nombre_out = config.archivo_salida or f"editado_{ruta_video.stem}.mp4"

    if not nombre_out.lower().endswith(".mp4"):
        nombre_out += ".mp4"
    ruta_mp4_final = OUTPUT_DIR / nombre_out
    encoder = detectar_encoder_nvidia()
    print(f"[*] Renderizando con {encoder} ({WIDTH}x{HEIGHT} a {FPS} FPS | Duración: {dur_video_input:.2f}s | Archivo: {nombre_out})...")

    if encoder == "h264_nvenc":
        args_codec = ["-c:v", "h264_nvenc", "-preset", "p4", "-b:v", "12M", "-pix_fmt", "yuv420p"]
    else:
        args_codec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-threads", str(CPU_THREADS), "-pix_fmt", "yuv420p"]

    cmd_ffmpeg = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{WIDTH}x{HEIGHT}",
        "-pix_fmt", "rgb24",
        "-r", str(FPS),
        "-i", "-",
        "-i", str(ruta_audio_final),
        *args_codec,
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{dur_video_input:.3f}",
        str(ruta_mp4_final),
    ]

    proceso_ffmpeg = subprocess.Popen(
        cmd_ffmpeg,
        stdin=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=SUBPROCESS_NO_WINDOW,
    )

    tiempo_global = 0.0

    for seg, total_frames_seg, dur_seg_exacta, palabras_sub, rms_env, ini_voz, fin_voz, t_ini_timer, dur_timer in datos_segmentos:
        dur_hablada_seg = float(t_ini_timer) if (seg.mostrar_timer and t_ini_timer is not None) else dur_seg_exacta

        # Flecha inteligente (máx. 1 por video): se planifica una vez al inicio del segmento
        plan_flecha: Optional[PlanFlecha] = None
        if seg.flecha and gestor_assets.flecha_base is not None:
            t_vid_ini_seg = tiempo_global
            try:
                plan_flecha = planificar_flecha_inteligente(lector_video, seg.flecha, t_vid_ini_seg, dur_hablada_seg)
                if plan_flecha is not None:
                    print(f"[*] Flecha inteligente: {plan_flecha.descripcion}")
            except Exception as exc_flecha:
                print(f"[!] No se pudo planificar la flecha ({exc_flecha}); se omite.")
                plan_flecha = None

        for f_idx in range(total_frames_seg):
            t_seg = f_idx / float(FPS)
            rms_val = float(rms_env[f_idx]) if f_idx < len(rms_env) else 0.0
            esta_hablando = (ini_voz <= t_seg <= fin_voz) and (rms_val > 0.008)

            en_timer = bool(
                seg.mostrar_timer
                and t_ini_timer is not None
                and (t_ini_timer <= t_seg <= t_ini_timer + dur_timer)
            )
            t_en_timer = (t_seg - t_ini_timer) if (t_ini_timer is not None) else 0.0

            # Mientras corre la cuenta regresiva del timer, Mr Nubsito pasa a 'idle' esperando la respuesta.
            if seg.mostrar_timer and t_ini_timer is not None and t_seg >= (t_ini_timer - 0.03):
                gesto_frame = "idle"
            elif esta_hablando or t_seg <= fin_voz + 0.15:
                gesto_frame = seg.gesto_personaje
            else:
                gesto_frame = "idle"

            # VELOCIDAD REAL 1:1 CONTINUA: El video de fondo avanza SIEMPRE de forma continua
            # a exactamente 1 segundo real por segundo de video (al compás de tiempo_global).
            t_video_actual = min(dur_video_input - 0.001, tiempo_global)
            frame_video_pil = lector_video.obtener_frame_en_segundo(t_video_actual)

            lienzo = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 255))

            if es_split and motor_personaje is not None:
                frame_top = motor_personaje.renderizar_frame(
                    gesto_solicitado=gesto_frame,
                    tiempo_global=tiempo_global,
                    tiempo_en_segmento=t_seg,
                    nivel_voz_rms=rms_val,
                    esta_hablando=esta_hablando,
                )
                lienzo.paste(frame_top, (0, 0))
                lienzo.paste(frame_video_pil, (0, TOP_HEIGHT))

                # Overlays visuales (Emoji, Flecha Roja, Timer de Trivia y Like&Sub sin fondo verde)
                gestor_assets.dibujar_emoji(lienzo, seg.emoji, seg.posicion_emoji, t_seg, dur_hablada_seg, es_split=True)
                gestor_assets.dibujar_flecha_inteligente(lienzo, plan_flecha, f_idx, es_split=True)
                gestor_assets.dibujar_timer(lienzo, en_timer, t_en_timer, dur_timer, es_split=True)
                gestor_assets.dibujar_like_sub(lienzo, seg.mostrar_like_sub, t_seg, dur_seg_exacta)

                draw_lienzo = ImageDraw.Draw(lienzo)
                draw_lienzo.line([(0, TOP_HEIGHT), (WIDTH, TOP_HEIGHT)], fill=(0, 0, 0, 255), width=10)

                # NOTA: TITULO_SUPERIOR NO se dibuja en pantalla (se usa exclusivamente para el nombre del archivo .mp4)
                renderizar_subtitulos_mrbeast(
                    draw_lienzo,
                    palabras_sub,
                    t_seg,
                    y_centro=TOP_HEIGHT + 12,
                    palabras_verdes_extra=config.palabras_verdes_extra,
                    palabras_rojas_extra=config.palabras_rojas_extra,
                )
            else:
                lienzo.paste(frame_video_pil, (0, 0))

                gestor_assets.dibujar_emoji(lienzo, seg.emoji, seg.posicion_emoji, t_seg, dur_hablada_seg, es_split=False)
                gestor_assets.dibujar_flecha_inteligente(lienzo, plan_flecha, f_idx, es_split=False)
                gestor_assets.dibujar_timer(lienzo, en_timer, t_en_timer, dur_timer, es_split=False)
                gestor_assets.dibujar_like_sub(lienzo, seg.mostrar_like_sub, t_seg, dur_seg_exacta)

                draw_lienzo = ImageDraw.Draw(lienzo)
                renderizar_subtitulos_mrbeast(
                    draw_lienzo,
                    palabras_sub,
                    t_seg,
                    y_centro=1350,
                    palabras_verdes_extra=config.palabras_verdes_extra,
                    palabras_rojas_extra=config.palabras_rojas_extra,
                )

            if proceso_ffmpeg.stdin:
                proceso_ffmpeg.stdin.write(lienzo.convert("RGB").tobytes())

            tiempo_global += 1.0 / FPS

    if proceso_ffmpeg.stdin:
        proceso_ffmpeg.stdin.close()
    proceso_ffmpeg.wait()

    print("=" * 74)
    print(f"[EXITO] Video editado guardado en: {ruta_mp4_final}")
    print("=" * 74)
    return ruta_mp4_final


# ==============================================================================
# 8. DESCARGA CON YT-DLP E INTERFAZ GRÁFICA (APLICACIÓN DE ESCRITORIO)
# ==============================================================================
def descargar_video_youtube(url: str) -> Path:
    """
    Descarga el video de YouTube usando exactamente el comando:
      yt-dlp -S res,ext:mp4:m4a -o "video_input.%(ext)s" "URL_DEL_VIDEO"
    """
    import shutil

    url_limpia = url.strip()
    if not url_limpia:
        raise ValueError("La URL del video está vacía.")

    # Eliminar video_input anterior para asegurar que descargue el video nuevo
    for existente in BASE_DIR.glob("video_input.*"):
        try:
            existente.unlink()
        except OSError:
            pass

    ejecutable_ytdlp = shutil.which("yt-dlp")
    if ejecutable_ytdlp:
        cmd_base = [ejecutable_ytdlp]
    else:
        cmd_base = [sys.executable, "-m", "yt_dlp"]

    cmd = [
        *cmd_base,
        "-S", "res,ext:mp4:m4a",
        "-o", "video_input.%(ext)s",
        url_limpia,
    ]

    print("=" * 74)
    print(f"[*] Descargando video con yt-dlp: {url_limpia}")
    print(f"    Comando: yt-dlp -S res,ext:mp4:m4a -o \"video_input.%(ext)s\" \"{url_limpia}\"")
    print("=" * 74)

    proc = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=SUBPROCESS_NO_WINDOW,
    )
    if proc.stdout:
        for linea in proc.stdout:
            linea_s = linea.rstrip()
            if linea_s:
                print(f"[yt-dlp] {linea_s}")
    proc.wait()

    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp terminó con código de error {proc.returncode}. Verifica el link de YouTube.")

    if DEFAULT_VIDEO_INPUT.exists():
        return DEFAULT_VIDEO_INPUT

    descargados = sorted(BASE_DIR.glob("video_input.*"))
    if descargados:
        return descargados[0]

    raise FileNotFoundError("No se encontró el archivo 'video_input.mp4' tras la descarga.")


def asegurar_acceso_directo_con_icono() -> None:
    """Crea o actualiza el acceso directo 'Mr Nubsito Studio.lnk' con el icono logo.ico en la carpeta."""
    if sys.platform != "win32":
        return
    lnk_path = BASE_DIR / "Mr Nubsito Studio.lnk"
    ico_path = BASE_DIR / "logo.ico"
    py_path = BASE_DIR / "Mr Nubsito Studio.py"
    if not ico_path.exists() or not py_path.exists():
        return
    try:
        import shutil
        pyw_exe = shutil.which("pythonw") or shutil.which("python") or sys.executable
        ps_cmd = (
            f"$ws = New-Object -ComObject WScript.Shell; "
            f"$s = $ws.CreateShortcut('{str(lnk_path)}'); "
            f"$s.TargetPath = '{str(pyw_exe)}'; "
            f"$s.Arguments = '\"{str(py_path)}\"'; "
            f"$s.WorkingDirectory = '{str(BASE_DIR)}'; "
            f"$s.IconLocation = '{str(ico_path)},0'; "
            f"$s.Description = 'Mr Nubsito Studio - Editor de Shorts'; "
            f"$s.Save()"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            creationflags=SUBPROCESS_NO_WINDOW,
            check=False,
        )
    except Exception:
        pass


def iniciar_aplicacion_gui() -> None:
    # 1. Configurar AppUserModelID para que Windows muestre logo.ico en la barra de tareas en lugar del de Python
    if sys.platform == "win32":
        try:
            import ctypes
            myappid = "mrnubsito.studio.editor.v4"
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # 0 = SW_HIDE
        except Exception:
            pass

    asegurar_acceso_directo_con_icono()

    import queue
    import threading
    import tkinter as tk
    from tkinter import messagebox, scrolledtext

    root = tk.Tk()
    root.title("Mr Nubsito Studio v4.3 - Editor Automático de Shorts")
    root.geometry("920x760")
    root.minsize(780, 640)
    root.configure(bg="#14161f")

    # 2. Cargar logo.ico en la barra de título de la ventana y en la barra de tareas
    ruta_ico = BASE_DIR / "logo.ico"
    if ruta_ico.exists():
        try:
            root.iconbitmap(default=str(ruta_ico))
        except Exception:
            try:
                root.iconbitmap(str(ruta_ico))
            except Exception:
                pass
        try:
            from PIL import ImageTk
            img_ico = Image.open(str(ruta_ico))
            root.tk.call("wm", "iconphoto", root._w, ImageTk.PhotoImage(img_ico))
        except Exception:
            pass

    cola_logs: "queue.Queue[str]" = queue.Queue()
    editando_lock = threading.Lock()

    # Estilos y colores
    BG_MAIN = "#14161f"
    BG_CARD = "#1e2230"
    BG_INPUT = "#10121a"
    FG_TEXT = "#f3f4f6"
    FG_MUTED = "#9ca3af"
    ACCENT_YELLOW = "#facc15"
    ACCENT_GREEN = "#22c55e"
    ACCENT_BLUE = "#3b82f6"

    # Encabezado
    header_frame = tk.Frame(root, bg=BG_MAIN, padx=20, pady=12)
    header_frame.pack(fill=tk.X)

    lbl_titulo = tk.Label(
        header_frame,
        text="MR NUBSITO STUDIO",
        font=("Segoe UI", 18, "bold"),
        fg=ACCENT_YELLOW,
        bg=BG_MAIN,
    )
    lbl_titulo.pack(side=tk.LEFT)

    lbl_sub = tk.Label(
        header_frame,
        text="Pegá el link del Short y el guión para generar el video automáticamente",
        font=("Segoe UI", 10),
        fg=FG_MUTED,
        bg=BG_MAIN,
        padx=12,
    )
    lbl_sub.pack(side=tk.LEFT, pady=(6, 0))

    # 1. Campo de Link de YouTube
    url_card = tk.Frame(root, bg=BG_CARD, padx=16, pady=12, highlightthickness=1, highlightbackground="#2e3446")
    url_card.pack(fill=tk.X, padx=20, pady=(0, 10))

    lbl_url = tk.Label(
        url_card,
        text="1. Link del Video de YouTube / Short:",
        font=("Segoe UI", 11, "bold"),
        fg=FG_TEXT,
        bg=BG_CARD,
    )
    lbl_url.pack(anchor="w", pady=(0, 6))

    url_row = tk.Frame(url_card, bg=BG_CARD)
    url_row.pack(fill=tk.X)

    entry_url = tk.Entry(
        url_row,
        font=("Consolas", 11),
        bg=BG_INPUT,
        fg="#ffffff",
        insertbackground="#ffffff",
        relief=tk.FLAT,
        highlightthickness=1,
        highlightbackground="#374151",
        highlightcolor=ACCENT_YELLOW,
    )
    entry_url.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=7, padx=(0, 8))

    def pegar_url() -> None:
        try:
            texto_clip = root.clipboard_get().strip()
            entry_url.delete(0, tk.END)
            entry_url.insert(0, texto_clip)
        except Exception:
            pass

    def copiar_prompt_con_link() -> None:
        url_actual = entry_url.get().strip()
        dur_info = "auto (detectada al ver el video)"
        if DEFAULT_VIDEO_INPUT.exists():
            try:
                cap_info = cv2.VideoCapture(str(DEFAULT_VIDEO_INPUT))
                fps_i = cap_info.get(cv2.CAP_PROP_FPS) or 30.0
                fc_i = cap_info.get(cv2.CAP_PROP_FRAME_COUNT) or 0
                cap_info.release()
                if fc_i > 0 and fps_i > 0:
                    dur_s = fc_i / fps_i
                    mins = int(dur_s // 60)
                    secs = int(dur_s % 60)
                    dur_info = f"{mins:02d}:{secs:02d} ({dur_s:.1f} segundos)"
            except Exception:
                pass

        ruta_p = BASE_DIR / "PROMPT_PARA_IA_GUIONES.txt"
        plantilla = ""
        if ruta_p.exists():
            try:
                plantilla = ruta_p.read_text(encoding="utf-8-sig")
            except Exception:
                pass

        if not plantilla:
            plantilla = PLANTILLA_GUION_GENERICA

        link_usar = url_actual if url_actual else "[PEGA AQUÍ EL LINK DEL VIDEO DE YOUTUBE]"
        texto_copiar = plantilla
        if "[PEGA_AQUÍ_EL_LINK_DEL_VIDEO]" in texto_copiar:
            texto_copiar = texto_copiar.replace("[PEGA_AQUÍ_EL_LINK_DEL_VIDEO]", link_usar)
        elif "[PEGA AQUÍ EL LINK DEL VIDEO DE YOUTUBE]" in texto_copiar:
            texto_copiar = texto_copiar.replace("[PEGA AQUÍ EL LINK DEL VIDEO DE YOUTUBE]", link_usar)
        else:
            texto_copiar = f"LINK DEL VIDEO DE YOUTUBE:\n{link_usar}\n\n" + texto_copiar

        if "[DURACION_EXACTA_VIDEO]" in texto_copiar:
            texto_copiar = texto_copiar.replace("[DURACION_EXACTA_VIDEO]", dur_info)

        root.clipboard_clear()
        root.clipboard_append(texto_copiar)

        btn_copiar_prompt.configure(text="✅ ¡Copiado con Link!", bg=ACCENT_GREEN, fg="#052e16")
        root.after(2500, lambda: btn_copiar_prompt.configure(text="📋 Copiar Prompt para Gemini (con Link)", bg="#4f46e5", fg="#ffffff"))

    btn_pegar_url = tk.Button(
        url_row,
        text="Pegar Link",
        command=pegar_url,
        font=("Segoe UI", 9, "bold"),
        bg="#2d3348",
        fg=FG_TEXT,
        activebackground="#3e4663",
        activeforeground="#ffffff",
        relief=tk.FLAT,
        padx=12,
        pady=6,
        cursor="hand2",
    )
    btn_pegar_url.pack(side=tk.LEFT, padx=(0, 6))

    btn_copiar_prompt = tk.Button(
        url_row,
        text="📋 Copiar Prompt para Gemini (con Link)",
        command=copiar_prompt_con_link,
        font=("Segoe UI", 9, "bold"),
        bg="#4f46e5",
        fg="#ffffff",
        activebackground="#4338ca",
        activeforeground="#ffffff",
        relief=tk.FLAT,
        padx=14,
        pady=6,
        cursor="hand2",
    )
    btn_copiar_prompt.pack(side=tk.LEFT)

    # 2. Campo de Guión
    guion_card = tk.Frame(root, bg=BG_CARD, padx=16, pady=12, highlightthickness=1, highlightbackground="#2e3446")
    guion_card.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))

    guion_top_row = tk.Frame(guion_card, bg=BG_CARD)
    guion_top_row.pack(fill=tk.X, pady=(0, 6))

    lbl_guion = tk.Label(
        guion_top_row,
        text="2. Guión del Video:",
        font=("Segoe UI", 11, "bold"),
        fg=FG_TEXT,
        bg=BG_CARD,
    )
    lbl_guion.pack(side=tk.LEFT)

    txt_guion = scrolledtext.ScrolledText(
        guion_card,
        font=("Consolas", 10),
        bg=BG_INPUT,
        fg="#e5e7eb",
        insertbackground="#ffffff",
        relief=tk.FLAT,
        height=13,
        wrap=tk.WORD,
        highlightthickness=1,
        highlightbackground="#374151",
        highlightcolor=ACCENT_YELLOW,
        padx=10,
        pady=10,
    )
    txt_guion.pack(fill=tk.BOTH, expand=True)

    # Cargar el guion actual o la plantilla por defecto
    if DEFAULT_GUION_TXT.exists():
        try:
            contenido_inicial = DEFAULT_GUION_TXT.read_text(encoding="utf-8-sig").strip()
        except Exception:
            contenido_inicial = PLANTILLA_GUION_GENERICA.strip()
    else:
        contenido_inicial = PLANTILLA_GUION_GENERICA.strip()
    txt_guion.insert("1.0", contenido_inicial)

    def restaurar_plantilla() -> None:
        txt_guion.delete("1.0", tk.END)
        txt_guion.insert("1.0", PLANTILLA_GUION_GENERICA.strip())

    def pegar_guion() -> None:
        try:
            clip = root.clipboard_get().strip()
            if clip:
                txt_guion.delete("1.0", tk.END)
                txt_guion.insert("1.0", clip)
        except Exception:
            pass

    btn_copiar_prompt_g = tk.Button(
        guion_top_row,
        text="📋 Copiar Prompt para Gemini",
        command=copiar_prompt_con_link,
        font=("Segoe UI", 9, "bold"),
        bg="#4f46e5",
        fg="#ffffff",
        activebackground="#4338ca",
        activeforeground="#ffffff",
        relief=tk.FLAT,
        padx=10,
        pady=3,
        cursor="hand2",
    )
    btn_copiar_prompt_g.pack(side=tk.RIGHT, padx=(6, 0))

    btn_plantilla = tk.Button(
        guion_top_row,
        text="Cargar Plantilla Ejemplo",
        command=restaurar_plantilla,
        font=("Segoe UI", 9),
        bg="#2d3348",
        fg=FG_TEXT,
        activebackground="#3e4663",
        activeforeground="#ffffff",
        relief=tk.FLAT,
        padx=10,
        pady=3,
        cursor="hand2",
    )
    btn_plantilla.pack(side=tk.RIGHT, padx=(6, 0))

    btn_pegar_guion = tk.Button(
        guion_top_row,
        text="Reemplazar con Portapapeles",
        command=pegar_guion,
        font=("Segoe UI", 9, "bold"),
        bg="#2d3348",
        fg=FG_TEXT,
        activebackground="#3e4663",
        activeforeground="#ffffff",
        relief=tk.FLAT,
        padx=10,
        pady=3,
        cursor="hand2",
    )
    btn_pegar_guion.pack(side=tk.RIGHT)

    # 3. Botón EDITAR y Barra de Acciones
    acciones_frame = tk.Frame(root, bg=BG_MAIN, padx=20, pady=4)
    acciones_frame.pack(fill=tk.X)

    btn_editar = tk.Button(
        acciones_frame,
        text="🎬  EDITAR VIDEO",
        font=("Segoe UI", 13, "bold"),
        bg=ACCENT_GREEN,
        fg="#052e16",
        activebackground="#16a34a",
        activeforeground="#ffffff",
        relief=tk.FLAT,
        padx=28,
        pady=10,
        cursor="hand2",
    )
    btn_editar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

    def abrir_carpeta_salida() -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(OUTPUT_DIR))
        except Exception:
            pass

    btn_carpeta = tk.Button(
        acciones_frame,
        text="📂 Abrir Carpeta Output",
        command=abrir_carpeta_salida,
        font=("Segoe UI", 10, "bold"),
        bg=ACCENT_BLUE,
        fg="#ffffff",
        activebackground="#2563eb",
        activeforeground="#ffffff",
        relief=tk.FLAT,
        padx=16,
        pady=10,
        cursor="hand2",
    )
    btn_carpeta.pack(side=tk.RIGHT)

    # 4. Consola de Progreso en Vivo
    log_card = tk.Frame(root, bg=BG_CARD, padx=14, pady=8, highlightthickness=1, highlightbackground="#2e3446")
    log_card.pack(fill=tk.X, padx=20, pady=(6, 16))

    lbl_estado = tk.Label(
        log_card,
        text="Estado: Listo para editar",
        font=("Segoe UI", 9, "bold"),
        fg=ACCENT_YELLOW,
        bg=BG_CARD,
    )
    lbl_estado.pack(anchor="w", pady=(0, 4))

    txt_logs = scrolledtext.ScrolledText(
        log_card,
        font=("Consolas", 9),
        bg="#0b0d13",
        fg="#a7f3d0",
        relief=tk.FLAT,
        height=7,
        state=tk.DISABLED,
    )
    txt_logs.pack(fill=tk.X)

    class RedireccionadorStdout:
        def __init__(self, q: "queue.Queue[str]"):
            self.q = q

        def write(self, texto: str) -> None:
            if texto:
                self.q.put(texto)

        def flush(self) -> None:
            pass

    def actualizar_consola() -> None:
        while not cola_logs.empty():
            msg = cola_logs.get_nowait()
            txt_logs.configure(state=tk.NORMAL)
            txt_logs.insert(tk.END, msg)
            txt_logs.see(tk.END)
            txt_logs.configure(state=tk.DISABLED)
        root.after(80, actualizar_consola)

    def ejecutar_flujo_edicion() -> None:
        if not editando_lock.acquire(blocking=False):
            return

        url_video = entry_url.get().strip()
        texto_guion_actual = txt_guion.get("1.0", tk.END).strip()

        if not texto_guion_actual:
            messagebox.showwarning("Guión vacío", "Por favor pega un guión antes de presionar Editar.")
            editando_lock.release()
            return

        btn_editar.configure(state=tk.DISABLED, text="⏳ PROCESANDO Y RENDERIZANDO...", bg="#4b5563", fg="#ffffff")
        lbl_estado.configure(text="Estado: Trabajando... (No cierres la ventana)", fg=ACCENT_YELLOW)
        txt_logs.configure(state=tk.NORMAL)
        txt_logs.delete("1.0", tk.END)
        txt_logs.configure(state=tk.DISABLED)

        def tarea_background() -> None:
            stdout_orig = sys.stdout
            stderr_orig = sys.stderr
            sys.stdout = RedireccionadorStdout(cola_logs)
            sys.stderr = RedireccionadorStdout(cola_logs)
            try:
                # 1. Guardar el guión escrito/pegado en guion_video.txt
                DEFAULT_GUION_TXT.write_text(texto_guion_actual + "\n", encoding="utf-8")
                print(f"[*] Guión guardado en: {DEFAULT_GUION_TXT.name}")

                # 2. Si el usuario ingresó un link de YouTube, descargarlo con yt-dlp
                if url_video:
                    ruta_video_usar = descargar_video_youtube(url_video)
                else:
                    if not DEFAULT_VIDEO_INPUT.exists():
                        raise FileNotFoundError(
                            "No ingresaste un link de YouTube y tampoco existe 'video_input.mp4' en la carpeta."
                        )
                    print(f"[*] Sin link nuevo: usando '{DEFAULT_VIDEO_INPUT.name}' existente en la carpeta.")
                    ruta_video_usar = DEFAULT_VIDEO_INPUT

                # 3. Ejecutar el motor de edición tal como funciona normalmente
                ruta_final = asyncio.run(editar_video_con_guion(ruta_video_usar, DEFAULT_GUION_TXT))

                def on_success() -> None:
                    lbl_estado.configure(
                        text=f"¡Listo! Video exportado en: output_shorts/{ruta_final.name}",
                        fg=ACCENT_GREEN,
                    )
                    btn_editar.configure(state=tk.NORMAL, text="🎬  EDITAR VIDEO", bg=ACCENT_GREEN, fg="#052e16")
                    messagebox.showinfo(
                        "¡Video Terminado!",
                        f"El video se generó con éxito en:\n\n{ruta_final}",
                    )

                root.after(0, on_success)

            except Exception as exc:
                print(f"\n[ERROR] {exc}\n")

                def on_error(err_msg: str = str(exc)) -> None:
                    lbl_estado.configure(text=f"Error: {err_msg}", fg="#ef4444")
                    btn_editar.configure(state=tk.NORMAL, text="🎬  EDITAR VIDEO", bg=ACCENT_GREEN, fg="#052e16")
                    messagebox.showerror("Error durante la edición", err_msg)

                root.after(0, on_error)
            finally:
                sys.stdout = stdout_orig
                sys.stderr = stderr_orig
                editando_lock.release()

        hilo = threading.Thread(target=tarea_background, daemon=True)
        hilo.start()

    btn_editar.configure(command=ejecutar_flujo_edicion)
    root.after(80, actualizar_consola)
    root.mainloop()


if __name__ == "__main__":
    # Si se pasan argumentos por consola, corre en modo CLI directo;
    # si se ejecuta sin argumentos (doble clic o `python "Mr Nubsito Studio.py"`), abre la aplicación gráfica.
    if len(sys.argv) > 1:
        video_arg = Path(sys.argv[1]).resolve()
        guion_arg = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else DEFAULT_GUION_TXT
        asyncio.run(editar_video_con_guion(video_arg, guion_arg))
    else:
        iniciar_aplicacion_gui()
