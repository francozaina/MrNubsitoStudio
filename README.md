# Mr Nubsito Studio v4.0 🎬

Motor escalable y modular de edición y automatización de videos cortos (Shorts/TikTok/Reels) con doblaje por Inteligencia Artificial, subtítulos dinámicos estilo MrBeast y reacción animada del YouTuber virtual arriba.

## 🚀 Características
- **Pantalla Dividida Inteligente (Split)**: YouTuber virtual en la parte superior reaccionando al video y gameplay/video original en la parte inferior.
- **Subtítulos Dinámicos MrBeast**: Subtítulos animados y sincronizados acústicamente palabra por palabra con colores de impacto (verde/rojo/amarillo).
- **Voz Neuronal IA**: Generación de voz con entonación expresiva y dinámica de YouTuber (Edge-TTS neuronal o ElevenLabs opcional) con cadena de masterización estilo micrófono de estudio (Shure SM7B).
- **Gestos Reales**: Detección dinámica de animaciones del YouTuber (`hablando`, `saludo`, `like`, `idle`) con reproducción fluida a sus 24 FPS nativos y continuidad entre segmentos sin cortes.
- **Assets Visuales y SFX**:
  - Emojis animados con rebote elástico (*pop-in*) y flotado suave.
  - Flecha roja señaladora direccionable (`centro`, `izquierda`, `derecha`, `arriba`, `abajo` o coordenadas porcentuales `x,y`).
  - Efectos de sonido sincronizados (`shock`, `whoosh`, `ding`, `tictac`, etc.).
  - Overlay de **Like & Subscribe** con eliminación de pantalla verde en tiempo real (*Chroma Key* + *Despill* verde).
- **Formato de Guion Universal**: Soporta formato simple de texto plano (`guion_video.txt`) separado por barras (`|`) o formato JSON.
- **Exportación Automática**: Nombra el archivo final según el título del guion (`TITULO_SUPERIOR`).

## 📁 Estructura del Proyecto
```
.
├── assets/
│   ├── emojis/           # Emojis PNG y flecha.png
│   ├── fonts/            # Tipografías (.ttf) estilo MrBeast
│   ├── gestos/           # Clips MP4 del YouTuber (hablando, idle, like, saludo)
│   ├── sfx/              # Efectos de sonido WAV/MP3
│   └── Like&Sub.mp4      # Animación con pantalla verde
├── guion_video.txt       # Guion activo
├── PROMPT_PARA_IA_GUIONES.txt # Prompt listo para que cualquier IA redacte guiones compatibles
├── Mr Nubsito Studio.py  # Script principal de renderizado y edición
└── video_input.mp4       # Video de entrada base
```

## 📝 Formato del Guion (`guion_video.txt`)
Cada línea del bloque `[GUION]` consta de 7 columnas:
```text
TIEMPO | GESTO | SFX | EMOJI | FLECHA | LIKE_SUB | TEXTO
```

Ejemplo:
```text
TITULO_SUPERIOR: MI NUEVO SHORT
MODO_PANTALLA: split
ARCHIVO_SALIDA: auto

[GUION]
auto | saludo   | shock  | sorprendido:derecha | centro  | no | ¡Miren lo que acaba de pasar en este video!
auto | hablando | whoosh | risa:izquierda      | ninguna | no | Al principio parecía sencillo, pero se descontroló totalmente.
auto | like     | ding   | fiesta:derecha      | ninguna | si | ¡Deja tu like y suscríbete para la segunda parte!
```

## 🛠️ Instalación y Uso
1. **Requisitos**:
   - Python 3.10+
   - FFmpeg instalado y disponible en el `PATH` del sistema.
2. **Dependencias**:
   ```bash
   pip install opencv-python pillow numpy edge-tts
   ```
3. **Ejecutar**:
   ```bash
   python "Mr Nubsito Studio.py"
   ```
   *(Buscará automáticamente `video_input.mp4` y `guion_video.txt` en el directorio)*
