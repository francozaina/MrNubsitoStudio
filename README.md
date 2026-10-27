# Mr Nubsito Studio v4.3 🎬

Motor escalable y modular de edición y automatización de videos cortos (Shorts/TikTok/Reels) y videos de Trivia con doblaje por Inteligencia Artificial, subtítulos dinámicos estilo MrBeast y reacción animada del YouTuber virtual arriba.

## 🚀 Características Principales
- **Duración 1:1 y Narración Continua sin Silencios**: El video editado final dura **exactamente lo mismo que el video original** a **velocidad real 1:1**. Cada bloque ajusta dinámicamente la cadencia de la voz para hablar de corrido sin huecos mudos y sin cortar nunca la última palabra.
- **Título Exclusivo para Nombre de Archivo (`TITULO_SUPERIOR`)**: El título del guion se utiliza automáticamente para darle nombre al archivo `.mp4` en `output_shorts/` sin tapar ni dibujarse encima del video.
- **21 Efectos de Sonido (`assets/sfx/`) con Soporte Combinado (`+`)**: Decodificación universal FFmpeg (`.mp3`, `.wav`, `.ogg`) con normalización y recorte de silencios. Permite combinar sonidos en un mismo bloque con `+` (ej. `whoosh+punch`, `camera_flash+fart`, `shock+xp_Error`).
- **Narración 100% Pura (Sin Call To Action)**: El prompt y las plantillas están diseñados para narrar exclusivamente lo que ocurre en el video hasta el último segundo, sin pedir likes ni suscripciones.
- **Modo Trivia con Reloj Cuenta Atrás (`timer.mp4`)**: Al terminar una pregunta de trivia (`TIMER: si`), muestra automáticamente el reloj cuenta atrás de **3.5 segundos** (con *Chroma Key* + *Despill* y su sonido sincronizado) mientras Mr Nubsito espera en modo `idle`.
- **Flecha Roja Inteligente (Máx. 1 por video)**: Seguimiento automático por flujo óptico (`auto` o `X,Y@MM:SS.s`) con detección de cortes de escena.

## 📁 Estructura del Proyecto
```
.
├── assets/
│   ├── emojis/           # Emojis PNG (sorprendido, risa, fiesta, diablo, enojo, lloro, sueno) y flecha.png
│   ├── fonts/            # Tipografías (.ttf) estilo MrBeast
│   ├── gestos/           # Clips MP4 del YouTuber (hablando, hablando2, idle, like, saludo)
│   ├── sfx/              # 21 Efectos de sonido WAV/MP3 (whoosh, punch, xp_Error, fart, cartoon_laugh, etc.)
│   ├── Like&Sub.mp4      # Animación opcional con pantalla verde
│   └── timer.mp4         # Reloj cuenta atrás de 3.5s para trivias con pantalla verde
├── guion_video.txt       # Guion activo
├── PROMPT_PARA_IA_GUIONES.txt # Prompt maestro para Gemini / ChatGPT
├── Mr Nubsito Studio.py  # Script principal y GUI
└── video_input.mp4       # Video de entrada base
```

## 📝 Formato del Guion (`guion_video.txt`)
Cada línea del bloque `[GUION]` consta de 8 columnas:
```text
TIEMPO | GESTO | SFX | EMOJI | FLECHA | LIKE_SUB | TIMER | TEXTO
```

Ejemplo:
```text
TITULO_SUPERIOR: Celular Dia 1 Vs Dia 365
MODO_PANTALLA: split
VOLUMEN_VIDEO_ORIGINAL: 0.22
ARCHIVO_SALIDA: auto

[GUION]
TIEMPO | GESTO | SFX | EMOJI | FLECHA | LIKE_SUB | TIMER | TEXTO
00:00 - 00:04 | hablando  | boxing_bell+awww  | fiesta:derecha        | ninguna       | no | no | ¡El primer día con celular nuevo lo apoyas suavecito en la almohada y hasta le das besitos!
00:04 - 00:08 | hablando2 | whoosh+punch      | sorprendido:izquierda | 30,52@00:06.1 | no | no | ¡Pero un año después lo lanzas desde lejos contra la cama, rebota fuerte y cae al piso!
00:08 - 00:12 | hablando  | camera_flash+fart | risa:derecha          | ninguna       | no | no | ¡Antes limpiabas la pantalla con un pañuelo especial y ahora le escupes encima usando tu propio buzo!
00:12 - 00:16 | hablando2 | shock+xp_Error    | diablo:izquierda      | ninguna       | no | no | ¡Al principio desenchufabas el cargador con cuidado, pero hoy tiras del cable desde la cama sin miedo!
```
