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
    gesto_personaje: str = "hablando"        # "idle", "saludo", "hablando", "like" (o cualquier .mp4 en assets/gestos)
    t_inicio_video: Optional[float] = None   # Segundos en el video original (None = automático proporcional)
    t_fin_video: Optional[float] = None      # Segundos en el video original (None = automático proporcional)
    pausa_idle_final_seg: float = 0.18
    sfx_inicio: Optional[str] = None         # "ding", "shock", "tictac", "whoosh", None
    emoji: Optional[str] = None              # "diablo", "enojo", "fiesta", "lloro", "risa", "sorprendido", "sueno", None
    posicion_emoji: str = "derecha"          # "derecha", "izquierda", "centro", "arriba_der", "arriba_izq"
    flecha: Optional[str] = None             # "centro", "izquierda", "derecha", "arriba", "abajo", "x,y", None
    mostrar_like_sub: bool = False           # True = superpone el video Like&Sub.mp4 sin fondo verde


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
PLANTILLA_GUION_GENERICA = """TITULO_SUPERIOR: [Título corto y llamativo de máximo 4-5 palabras, o déjalo vacío]
MODO_PANTALLA: split
ARCHIVO_SALIDA: auto

[GUION]
TIEMPO | GESTO | SFX | EMOJI | FLECHA | LIKE_SUB | TEXTO
auto | saludo | shock | sorprendido | centro | no | ¡Texto del gancho inicial!
auto | hablando | whoosh | risa:izquierda | ninguna | no | Texto del desarrollo del video.
auto | like | ding | fiesta | ninguna | si | ¡Texto final pidiendo like y suscripción! Call to action agresiva, deci que a los 10 mil subs sortearas robux.
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


def _normalizar_sfx(sfx_raw: Optional[str]) -> Optional[str]:
    if not sfx_raw:
        return None
    s = _quitar_acentos(str(sfx_raw).strip().lower())
    s = s.replace(".wav", "").replace(".mp3", "")
    if not s or s in ("ninguno", "ninguna", "none", "no", "false", "-", "sin_sfx"):
        return None
    return s


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
    if not flecha_raw:
        return None
    f = _quitar_acentos(str(flecha_raw).strip().lower()).replace(".png", "")
    if f.startswith("flecha:"):
        f = f.split(":", 1)[1].strip()
    elif f == "flecha" or f in ("si", "true", "1", "activa"):
        return "centro"

    if not f or f in ("ninguna", "ninguno", "none", "no", "false", "-", "0"):
        return None
    return f


def _normalizar_bool(val_raw: object) -> bool:
    if isinstance(val_raw, bool):
        return val_raw
    s = _quitar_acentos(str(val_raw).strip().lower())
    return s in ("si", "yes", "true", "1", "like_sub", "likesub", "activo", "activar")


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
                    )
                )
        return config

    # 2. Formato Bloc de Notas (.txt separado por '|')
    en_guion = False
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
        pausa = 0.18
        texto_voz = ""

        if len(partes) >= 7:
            # FORMATO COMPLETO v4.0:
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
                )
            )

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


def _prosodia_dinamica_clausula(clausula: str, idx: int, total: int) -> Tuple[str, str, str, float]:
    es_exclamacion = "!" in clausula or "¡" in clausula
    es_pregunta = "?" in clausula or "¿" in clausula
    palabras_low = set(re.findall(r"[a-záéíóúüñ]+", clausula.lower()))
    tiene_hype = bool(palabras_low & _PALABRAS_HYPE_YOUTUBER)

    pitch = EDGE_PITCH if EDGE_PITCH != "+8Hz" else "+0Hz"

    if es_exclamacion:
        if idx == 0:
            return "+13%", pitch, "+8%", 0.065
        elif idx == total - 1:
            return "+14%", pitch, "+10%", 0.075
        else:
            return "+15%", pitch, "+6%", 0.070
    elif es_pregunta:
        return "+12%", pitch, "+6%", 0.080
    else:
        rate_narrativo = "+11%" if tiene_hype else "+9%"
        return rate_narrativo, pitch, "+0%", 0.085


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

    pad_ini = int(sr * 0.018)
    pad_fin = int(sr * 0.026)
    i0 = max(0, int(activos[0]) * win - pad_ini)
    i1 = min(len(pcm_float), int(activos[-1] + 1) * win + pad_fin)
    recortado = pcm_float[i0:i1].copy()

    fade_len = min(int(sr * 0.008), len(recortado) // 4)
    if fade_len > 1:
        recortado[:fade_len] *= np.linspace(0.0, 1.0, fade_len, dtype=np.float32)
        recortado[-fade_len:] *= np.linspace(1.0, 0.0, fade_len, dtype=np.float32)

    return recortado, i0 / float(sr), len(recortado) / float(sr)


def masterizar_voz_estudio_ffmpeg(ruta_entrada: Path, ruta_wav_salida: Path) -> None:
    filtro_audio = (
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
        check=False,
    )
    if res.returncode != 0 or not ruta_wav_salida.exists():
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(ruta_entrada), "-ar", "44100", "-ac", "1", str(ruta_wav_salida)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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
) -> Tuple[float, List[PalabraSubtitulo], float, float]:
    palabras_raw: List[PalabraSubtitulo] = []
    genero_audio = False
    ruta_wav = ruta_audio_salida.with_suffix(".wav")
    if ruta_audio_salida.exists():
        ruta_audio_salida.unlink()
    if ruta_wav.exists():
        ruta_wav.unlink()

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
                    cl_orig, c_idx, len(clausulas_orig)
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
                ruta_crudo_wav = ruta_audio_salida.parent / f"{ruta_audio_salida.stem}_raw.wav"
                with wave.open(str(ruta_crudo_wav), "w") as wf_raw:
                    wf_raw.setnchannels(1)
                    wf_raw.setsampwidth(2)
                    wf_raw.setframerate(sr)
                    wf_raw.writeframes((audio_crudo * 32767).astype(np.int16).tobytes())
                masterizar_voz_estudio_ffmpeg(ruta_crudo_wav, ruta_wav)
                if ruta_crudo_wav.exists():
                    try:
                        ruta_crudo_wav.unlink()
                    except OSError:
                        pass
                genero_audio = ruta_wav.exists() and ruta_wav.stat().st_size > 0
        except Exception as e:
            print(f"[!] Error en Edge-TTS: {e}")

    if not ruta_wav.exists() or ruta_wav.stat().st_size == 0:
        tokens = texto.split()
        dur_estimada = max(1.8, len(tokens) * 0.28)
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

        # Si el gesto se mantiene igual (incluso entre dos segmentos seguidos con 'hablando'),
        # continuar el tiempo acumulado sin reiniciar el video para evitar cortes.
        if gesto_activo == self.gesto_previo:
            self.t_acumulado_gesto += 1.0 / float(FPS)
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

    def __init__(self, emojis_dir: Path, like_sub_path: Path):
        self.emojis_cache: Dict[str, Image.Image] = {}
        self.flecha_base: Optional[Image.Image] = None
        self.likesub_frames: List[Image.Image] = []
        self.likesub_fps: float = 25.0
        self.likesub_audio_pcm: Optional[np.ndarray] = None

        self._cargar_emojis_y_flecha(emojis_dir)
        self._cargar_like_sub_chromakey(like_sub_path)

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

    def dibujar_flecha(
        self,
        lienzo: Image.Image,
        direccion_o_coord: Optional[str],
        t_seg: float,
        dur_seg: float,
        es_split: bool,
    ) -> None:
        if not direccion_o_coord or self.flecha_base is None:
            return

        dur_mostrar = min(dur_seg - 0.05, 3.5)
        if t_seg < 0.05 or t_seg > dur_mostrar:
            return

        y_video_top = TOP_HEIGHT if es_split else 0
        h_video = BOTTOM_HEIGHT if es_split else HEIGHT

        # Determinar el punto objetivo (tx, ty) que queremos señalar y el ángulo de la flecha.
        # Nota: En `assets/emojis/flecha.png`, la punta de la flecha apunta hacia la IZQUIERDA (180°).
        dir_clean = _quitar_acentos(direccion_o_coord.strip().lower())
        angulo_rot = -25.0  # Por defecto apunta en diagonal hacia abajo-izquierda al centro
        tx, ty = WIDTH // 2, y_video_top + int(h_video * 0.52)
        offset_dir = (1.0, -0.45)  # Dirección desde la punta hacia la cola de la flecha

        if "," in dir_clean:
            try:
                px_str, py_str = dir_clean.split(",", 1)
                px = float(px_str.strip())
                py = float(py_str.strip())
                if px <= 100.0 and py <= 100.0:
                    tx = int(WIDTH * (px / 100.0))
                    ty = y_video_top + int(h_video * (py / 100.0))
                else:
                    tx, ty = int(px), int(py)
                if tx < WIDTH // 2:
                    angulo_rot = -20.0
                    offset_dir = (1.0, -0.35)
                else:
                    angulo_rot = -160.0
                    offset_dir = (-1.0, -0.35)
            except ValueError:
                pass
        elif dir_clean == "izquierda":
            tx, ty = int(WIDTH * 0.25), y_video_top + int(h_video * 0.52)
            angulo_rot = 0.0  # Punta hacia la izquierda
            offset_dir = (1.0, 0.0)
        elif dir_clean == "derecha":
            tx, ty = int(WIDTH * 0.75), y_video_top + int(h_video * 0.52)
            angulo_rot = 180.0  # Punta hacia la derecha
            offset_dir = (-1.0, 0.0)
        elif dir_clean == "arriba":
            tx, ty = WIDTH // 2, y_video_top + int(h_video * 0.28)
            angulo_rot = -90.0  # En PIL rotate(-90) lleva la punta izquierda hacia ARRIBA
            offset_dir = (0.0, 1.0)
        elif dir_clean == "abajo":
            tx, ty = WIDTH // 2, y_video_top + int(h_video * 0.78)
            angulo_rot = 90.0   # En PIL rotate(90) lleva la punta izquierda hacia ABAJO
            offset_dir = (0.0, -1.0)
        else:
            # "centro"
            tx, ty = WIDTH // 2, y_video_top + int(h_video * 0.52)
            angulo_rot = -25.0
            offset_dir = (0.90, -0.42)

        tam_flecha = 230
        flecha_scaled = self.flecha_base.resize((tam_flecha, tam_flecha), Image.Resampling.LANCZOS)
        flecha_rot = flecha_scaled.rotate(angulo_rot, resample=Image.Resampling.BICUBIC, expand=True)

        # Rebote rítmico de "señalar" a lo largo del eje de la flecha
        rebote = 28.0 * abs(math.sin(t_seg * 9.5))
        dist_centro_flecha = 135.0 + rebote
        cx = int(round(tx + offset_dir[0] * dist_centro_flecha))
        cy = int(round(ty + offset_dir[1] * dist_centro_flecha))

        fw, fh = flecha_rot.size
        pos_x = max(0, min(WIDTH - fw, cx - fw // 2))
        pos_y = max(0, min(HEIGHT - fh, cy - fh // 2))
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
            proc = subprocess.Popen(cmd_ff, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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


def _resolver_ruta_sfx(nombre_sfx: Optional[str]) -> Optional[Path]:
    if not nombre_sfx or not SFX_DIR.exists():
        return None
    objetivo = _quitar_acentos(nombre_sfx.strip().lower())
    for archivo in SFX_DIR.glob("*.*"):
        if archivo.suffix.lower() in (".wav", ".mp3", ".ogg"):
            if _quitar_acentos(archivo.stem.strip().lower()) == objetivo:
                return archivo
    return None


def mezclar_voz_sfx_y_likesub(
    ruta_voz_wav: Path,
    duracion_total_seg: float,
    nombre_sfx: Optional[str],
    incluir_audio_likesub: bool,
    likesub_audio_pcm: Optional[np.ndarray],
    ruta_salida_wav: Path,
) -> None:
    sr = 44100
    n_total = int(round(duracion_total_seg * sr))
    mezcla = np.zeros(n_total, dtype=np.float32)

    with wave.open(str(ruta_voz_wav), "r") as wf:
        voz_pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        n_copy = min(n_total, len(voz_pcm))
        # Amplificar la voz con saturación suave para que suene fuerte sin distorsionar
        voz_fuerte = np.tanh(voz_pcm[:n_copy] * VOLUMEN_VOZ_YOUTUBER)
        mezcla[:n_copy] += voz_fuerte

    ruta_sfx = _resolver_ruta_sfx(nombre_sfx)
    if ruta_sfx and ruta_sfx.exists():
        try:
            with wave.open(str(ruta_sfx), "r") as wf_s:
                sfx_pcm = np.frombuffer(wf_s.readframes(wf_s.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
                n_sfx = min(n_total, len(sfx_pcm))
                mezcla[:n_sfx] += sfx_pcm[:n_sfx] * 0.36
        except Exception:
            pass

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
        res = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True, check=False)
        if "h264_nvenc" in res.stdout:
            return "h264_nvenc"
    except FileNotFoundError:
        pass
    return "libx264"


async def editar_video_con_guion(ruta_video: Path, ruta_guion: Path) -> Path:
    print("=" * 74)
    print(" MR NUBSITO STUDIO v4.0 - EDICIÓN ESCALABLE CON ASSETS Y VOZ IA")
    print("=" * 74)

    preparar_assets_base()
    config = cargar_guion_video(ruta_guion)
    if not config.segmentos:
        raise ValueError(f"El guión '{ruta_guion.name}' no contiene segmentos válidos.")

    es_split = (config.modo_pantalla != "full")
    alto_video = BOTTOM_HEIGHT if es_split else HEIGHT

    lector_video = LectorVideoReferencia(ruta_video, WIDTH, alto_video)
    motor_personaje = MotorPersonajeMrNubsitoV2(ASSETS_DIR / "mr_nubsito_base.png", GESTOS_DIR) if es_split else None
    gestor_assets = GestorAssetsVisuales(EMOJIS_DIR, LIKE_SUB_PATH)

    datos_segmentos = []
    archivos_audio_seg: List[Path] = []

    # 1. Generar todas las voces, timestamps MrBeast y mezcla de SFX por segmento
    for idx, seg in enumerate(config.segmentos):
        info_assets = (
            f"gesto={seg.gesto_personaje} | sfx={seg.sfx_inicio or '-'} | "
            f"emoji={seg.emoji or '-'} | flecha={seg.flecha or '-'} | like&sub={'Sí' if seg.mostrar_like_sub else 'No'}"
        )
        print(f"[+] Segmento [{idx + 1}/{len(config.segmentos)}] ({info_assets}): {seg.texto_voz}")
        ruta_mp3 = TEMP_DIR / f"{seg.id_segmento}.mp3"
        dur_voz, palabras_sub, ini_voz, fin_voz = await generar_voz_y_timestamps_segmento(seg.texto_voz, ruta_mp3)

        total_frames_seg = max(1, int(round((dur_voz + seg.pausa_idle_final_seg) * FPS)))
        dur_seg_exacta = total_frames_seg / float(FPS)

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
        )
        archivos_audio_seg.append(ruta_mix_wav)

        datos_segmentos.append([seg, total_frames_seg, dur_seg_exacta, palabras_sub, rms_env, ini_voz, fin_voz])

    # 2. Repartir automáticamente los tiempos del video de entrada para segmentos con tiempo 'auto'
    dur_total_locucion = sum(item[2] for item in datos_segmentos)
    cursor_tiempo_loc = 0.0
    for item in datos_segmentos:
        seg: SegmentoVideoGuion = item[0]
        dur_seg = item[2]
        if seg.t_inicio_video is None or seg.t_fin_video is None:
            frac_ini = cursor_tiempo_loc / max(0.1, dur_total_locucion)
            frac_fin = (cursor_tiempo_loc + dur_seg) / max(0.1, dur_total_locucion)
            seg.t_inicio_video = frac_ini * lector_video.duracion_total
            seg.t_fin_video = frac_fin * lector_video.duracion_total
        cursor_tiempo_loc += dur_seg

    # 3. Unir el audio maestro de la voz + audio original del video de fondo
    ruta_voz_maestra = TEMP_DIR / "voz_maestra.wav"
    bloques_pcm = []
    for ruta_w in archivos_audio_seg:
        with wave.open(str(ruta_w), "r") as wf:
            bloques_pcm.append(wf.readframes(wf.getnframes()))
    with wave.open(str(ruta_voz_maestra), "w") as wf_master:
        wf_master.setnchannels(1)
        wf_master.setsampwidth(2)
        wf_master.setframerate(44100)
        for b in bloques_pcm:
            wf_master.writeframes(b)

    ruta_audio_final = TEMP_DIR / "audio_final_combinado.wav"
    if config.volumen_video_original > 0.001:
        res_mix = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(ruta_voz_maestra),
                "-stream_loop", "-1", "-i", str(ruta_video),
                "-filter_complex",
                f"[1:a]volume={config.volumen_video_original}[bg];[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.98:level=disabled[aout]",
                "-map", "[aout]",
                "-ar", "44100", "-ac", "1",
                str(ruta_audio_final),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if res_mix.returncode != 0 or not ruta_audio_final.exists():
            ruta_audio_final = ruta_voz_maestra
    else:
        ruta_audio_final = ruta_voz_maestra

    # 4. Renderizar video final con FFmpeg usando el título del guión como nombre de archivo
    if config.titulo_superior:
        titulo_limpio = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", config.titulo_superior).strip().rstrip(".")
        nombre_out = titulo_limpio or config.archivo_salida or f"editado_{ruta_video.stem}.mp4"
    else:
        nombre_out = config.archivo_salida or f"editado_{ruta_video.stem}.mp4"

    if not nombre_out.lower().endswith(".mp4"):
        nombre_out += ".mp4"
    ruta_mp4_final = OUTPUT_DIR / nombre_out
    encoder = detectar_encoder_nvidia()
    print(f"[*] Renderizando con {encoder} ({WIDTH}x{HEIGHT} a {FPS} FPS | Modo: {config.modo_pantalla.upper()})...")

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
        "-shortest",
        str(ruta_mp4_final),
    ]

    proceso_ffmpeg = subprocess.Popen(cmd_ffmpeg, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    tiempo_global = 0.0

    for seg, total_frames_seg, dur_seg_exacta, palabras_sub, rms_env, ini_voz, fin_voz in datos_segmentos:
        t_vid_ini = float(seg.t_inicio_video or 0.0)
        t_vid_fin = float(seg.t_fin_video or lector_video.duracion_total)

        for f_idx in range(total_frames_seg):
            t_seg = f_idx / float(FPS)
            progreso = f_idx / float(max(1, total_frames_seg - 1))
            rms_val = float(rms_env[f_idx]) if f_idx < len(rms_env) else 0.0
            esta_hablando = (ini_voz <= t_seg <= fin_voz) and (rms_val > 0.008)

            t_video_actual = t_vid_ini + progreso * (t_vid_fin - t_vid_ini)
            frame_video_pil = lector_video.obtener_frame_en_segundo(t_video_actual)

            lienzo = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 255))

            if es_split and motor_personaje is not None:
                frame_top = motor_personaje.renderizar_frame(
                    gesto_solicitado=seg.gesto_personaje,
                    tiempo_global=tiempo_global,
                    tiempo_en_segmento=t_seg,
                    nivel_voz_rms=rms_val,
                    esta_hablando=esta_hablando,
                )
                lienzo.paste(frame_top, (0, 0))
                lienzo.paste(frame_video_pil, (0, TOP_HEIGHT))

                # Overlays visuales (Emoji, Flecha Roja, Like&Sub sin fondo verde)
                gestor_assets.dibujar_emoji(lienzo, seg.emoji, seg.posicion_emoji, t_seg, dur_seg_exacta, es_split=True)
                gestor_assets.dibujar_flecha(lienzo, seg.flecha, t_seg, dur_seg_exacta, es_split=True)
                gestor_assets.dibujar_like_sub(lienzo, seg.mostrar_like_sub, t_seg, dur_seg_exacta)

                draw_lienzo = ImageDraw.Draw(lienzo)
                draw_lienzo.line([(0, TOP_HEIGHT), (WIDTH, TOP_HEIGHT)], fill=(0, 0, 0, 255), width=10)

                if config.titulo_superior:
                    fuente_t = cargar_fuente(50)
                    dibujar_texto_mrbeast(
                        draw_lienzo,
                        (WIDTH // 2, TOP_HEIGHT + 78),
                        config.titulo_superior.upper(),
                        fuente_t,
                        (255, 255, 255),
                        9,
                        (5, 6),
                    )

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

                gestor_assets.dibujar_emoji(lienzo, seg.emoji, seg.posicion_emoji, t_seg, dur_seg_exacta, es_split=False)
                gestor_assets.dibujar_flecha(lienzo, seg.flecha, t_seg, dur_seg_exacta, es_split=False)
                gestor_assets.dibujar_like_sub(lienzo, seg.mostrar_like_sub, t_seg, dur_seg_exacta)

                draw_lienzo = ImageDraw.Draw(lienzo)
                if config.titulo_superior:
                    fuente_t = cargar_fuente(58)
                    dibujar_texto_mrbeast(
                        draw_lienzo,
                        (WIDTH // 2, 180),
                        config.titulo_superior.upper(),
                        fuente_t,
                        (255, 255, 255),
                        10,
                        (5, 7),
                    )
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


def iniciar_aplicacion_gui() -> None:
    import queue
    import threading
    import tkinter as tk
    from tkinter import messagebox, scrolledtext

    root = tk.Tk()
    root.title("Mr Nubsito Studio v4.0 - Editor Automático de Shorts")
    root.geometry("920x760")
    root.minsize(780, 640)
    root.configure(bg="#14161f")

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
    btn_pegar_url.pack(side=tk.RIGHT)

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
