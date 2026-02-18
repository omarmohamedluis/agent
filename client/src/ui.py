"""
Interfaz de Usuario (UI) Simplificada.
Centraliza el dibujo y la visualización en la pantalla (OLED SSD1306 o Color UCTRONICS).
"""
import time
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional
from PIL import Image, ImageDraw, ImageFont

from heartbeat import (
    get_heartbeat_snapshot,
    register_heartbeat_listener,
    unregister_heartbeat_listener,
    start_heartbeat
)
from net_com_handler import check_server_status
from display_manager import DisplayManager
from structure_manager import get_structure_manager

# --- Configuración y Constantes ---
LOGGER = logging.getLogger("omiclient.ui")
STRUCTURE_MANAGER = get_structure_manager()
BASE_DIR = Path(__file__).resolve().parents[1]
ASSETS_PATH = BASE_DIR / "web" / "utilities"

HEADER_H = 16
# Colores
CLR_BLACK = (0, 0, 0)
CLR_WHITE = (255, 255, 255)
CLR_YELLOW = (255, 255, 0)
CLR_GREEN = (0, 255, 0)
CLR_BLUE = (0, 191, 255)
CLR_RED = (255, 0, 0)

# --- Estado Global de UI ---
_display_manager = DisplayManager(driver_name="ssd1306")
_standard_listener_registered = False
_font_cache = {}
_is_color = False

def _get_font(size: int, font_name: str = "PixelOperator.ttf") -> ImageFont.FreeTypeFont:
    key = f"{font_name}_{size}"
    if key in _font_cache: return _font_cache[key]
    try:
        f = ImageFont.truetype(str(ASSETS_PATH / font_name), size)
        _font_cache[key] = f
        return f
    except Exception:
        return ImageFont.load_default()

def _get_icon_font(size: int) -> ImageFont.FreeTypeFont:
    return _get_font(size, "lineawesome-webfont.ttf")

def _new_image() -> Image.Image:
    """Crea una imagen base según el tipo de pantalla."""
    w, h = _display_manager.width, _display_manager.height
    mode = "RGB" if _is_color else "1"
    return Image.new(mode, (w, h), (0,0,0) if _is_color else 0)

def init():
    """Inicializa el hardware de pantalla."""
    global _is_color
    try:
        _display_manager.init()
        _is_color = "uctronics" in _display_manager.driver_name.lower()
        LOGGER.info(f"UI inicializada: {_display_manager.driver_name} (Color: {_is_color})")
    except Exception as e:
        LOGGER.error(f"Fallo al inicializar pantalla: {e}")

# --- Funciones de Dibujo (Centralizadas) ---

def render_loading(percent: int, label: str) -> Image.Image:
    img = _new_image()
    draw = ImageDraw.Draw(img)
    w, h = img.size
    
    # Header area as progress bar
    bar_w = int((percent / 100.0) * w)
    clr_primary = CLR_YELLOW if _is_color else (255 if not _is_color else CLR_WHITE)
    
    # Draw bar
    draw.rectangle([0, 0, bar_w, HEADER_H], fill=clr_primary)
    draw.rectangle([0, 0, w - 1, HEADER_H], outline=clr_primary)
    
    # Text on top
    font = _get_font(12 if not _is_color else 14)
    txt = f"LOADING {percent}%"
    draw.text((2, 0), txt, font=font, fill=CLR_BLACK if bar_w > 60 else clr_primary)
    
    # Label
    y = HEADER_H + 4
    draw.text((2, y), (label or "")[:20], font=font, fill=clr_primary)
    return img

def render_message(label: str, is_error: bool = True) -> Image.Image:
    img = _new_image()
    draw = ImageDraw.Draw(img)
    w, h = img.size
    
    header_clr = CLR_RED if (is_error and _is_color) else CLR_WHITE
    txt_clr = CLR_WHITE if not _is_color else CLR_WHITE
    
    draw.rectangle([0, 0, w, HEADER_H], fill=header_clr)
    font_h = _get_font(12 if not _is_color else 14)
    draw.text((2, 0), "SYSTEM MSG", font=font_h, fill=CLR_BLACK if not _is_color else CLR_WHITE)
    
    font_b = _get_font(10 if not _is_color else 14)
    # Simple wrap
    words = (label or "").split()
    y = HEADER_H + 4
    line = ""
    for word in words:
        if draw.textbbox((0,0), line + word, font=font_b)[2] < w - 4:
            line += word + " "
        else:
            draw.text((2, y), line, font=font_b, fill=txt_clr)
            y += 12
            line = word + " "
    draw.text((2, y), line, font=font_b, fill=txt_clr)
    return img

def render_standard(snapshot: Dict[str, Any], structure: Dict[str, Any], server_online: bool) -> Image.Image:
    img = _new_image()
    draw = ImageDraw.Draw(img)
    w, h = img.size
    
    header_bg = CLR_YELLOW if _is_color else 255
    header_fg = CLR_BLACK
    body_fg = CLR_WHITE if _is_color else 255
    
    # 1. Header
    draw.rectangle([0, 0, w, HEADER_H], fill=header_bg)
    
    f_h = _get_font(10 if not _is_color else 14)
    # Index
    idx = structure.get("identity", {}).get("index", "--")
    draw.text((2, 1), f"#{idx}", font=f_h, fill=header_fg)
    
    # Active App Name
    services = structure.get("services", [])
    active_name = "STANDBY"
    if isinstance(services, list):
        for s in services:
            if s.get("running"):
                active_name = s.get("name", "ACTIVE")
                break
    
    svc_txt = active_name.upper()[:10]
    sw = draw.textbbox((0, 0), svc_txt, font=f_h)[2]
    draw.text(((w - sw)//2, 1), svc_txt, font=f_h, fill=header_fg)
    
    # Wifi Icon
    icon_f = _get_icon_font(12 if not _is_color else 14)
    glyph = "\uf1eb"
    iw = draw.textbbox((0,0), glyph, font=icon_f)[2]
    ix = w - iw - 2
    draw.text((ix, 0), glyph, font=icon_f, fill=header_fg)
    if not server_online:
        draw.line([(ix, 0), (ix+iw, HEADER_H)], fill=header_fg, width=1)

    # 2. Body
    y = HEADER_H + 4
    line_h = 12 if not _is_color else 22
    f_b = _get_font(10 if not _is_color else 16)

    # CPU & TEMP
    cpu = snapshot.get("cpu", 0)
    temp = snapshot.get("temp", 0)
    
    clr_cpu = CLR_BLUE if not _is_color else (CLR_RED if cpu > 70 else CLR_BLUE)
    clr_tmp = CLR_BLUE if not _is_color else (CLR_RED if temp > 65 else CLR_BLUE)
    label_clr = CLR_WHITE if not _is_color else CLR_GREEN

    draw.text((2, y), "CPU:", font=f_b, fill=label_clr)
    draw.text((30 if not _is_color else 40, y), f"{cpu:.0f}%", font=f_b, fill=clr_cpu)
    
    draw.text((70 if not _is_color else 90, y), "TMP:", font=f_b, fill=label_clr)
    draw.text((100 if not _is_color else 130, y), f"{temp:.0f}C", font=f_b, fill=clr_tmp)
    
    # IP
    y += line_h
    ifaces = snapshot.get("ifaces", [])
    ip_val = "DISCONNECTED"
    for iface in ifaces:
        ip = iface.get("ip")
        if ip and not ip.startswith("127"):
            ip_val = ip
            break
            
    draw.text((2, y), "IP:", font=f_b, fill=label_clr)
    draw.text((20 if not _is_color else 30, y), ip_val, font=f_b, fill=CLR_BLUE if _is_color else body_fg)

    return img

# --- API Pública de UI ---

def update_ui(img: Image.Image):
    """Manda la imagen al hardware."""
    _display_manager.display(img)

def show_loading_ui(percent: int, label: str = ""):
    stop_standard_ui()
    img = render_loading(percent, label)
    update_ui(img)

def show_message_ui(label: str = "", is_error: bool = False):
    stop_standard_ui()
    img = render_message(label, is_error)
    update_ui(img)

def update_standard_ui(snapshot: Dict[str, Any]) -> None:
    # 1. Verificar Estado "Busy" del sistema
    structure = STRUCTURE_MANAGER.get_structure()
    sys_status = structure.get("system_status", {})
    if sys_status.get("is_busy"):
        msg = sys_status.get("busy_message") or "PROCESANDO..."
        img = render_loading(50, msg)
        update_ui(img)
        return

    # 2. Render normal
    server_online = check_server_status()
    img = render_standard(snapshot, structure, server_online)
    update_ui(img)

def _heartbeat_callback(snapshot: Dict[str, Any]):
    update_standard_ui(snapshot)

def start_standard_ui():
    global _standard_listener_registered
    if not _standard_listener_registered:
        register_heartbeat_listener(_heartbeat_callback)
        _standard_listener_registered = True
    update_standard_ui(get_heartbeat_snapshot())

def stop_standard_ui():
    global _standard_listener_registered
    if _standard_listener_registered:
        unregister_heartbeat_listener(_heartbeat_callback)
        _standard_listener_registered = False

def turn_ui_off():
    stop_standard_ui()
    _display_manager.clear()

# Inicializar al importar
init()
