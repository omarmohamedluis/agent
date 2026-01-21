"""
Interfaz de Usuario (UI).
Gestiona la pantalla OLED, mostrando el estado del sistema, carga, errores
y la información de red. Se suscribe al heartbeat para actualizaciones.
"""
import json
import time
from pathlib import Path
from typing import Any, Dict
import sys

from heartbeat import (
    get_heartbeat_snapshot,
    register_heartbeat_listener,
    unregister_heartbeat_listener,
    start_heartbeat
)
from PIL import Image, ImageDraw, ImageFont
from net_com_handler import check_server_status
from logger import log_event, log_print
from display_manager import DisplayManager
from structure_manager import get_structure_manager

STRUCTURE_MANAGER = get_structure_manager()
BASE_DIR = Path(__file__).resolve().parents[1]  # reaches client/
ASSETS_PATH  = BASE_DIR / "web" / "utilities"

OLED_W, OLED_H = 128, 64
HEADER_H = 16

_standard_listener_registered = False

# -------- Hardware --------

_display_manager = DisplayManager(driver_name="ssd1306")
try:
    _display_manager.init()
except Exception as e:
    log_event("error", "ui", f"Fallo al iniciar display manager: {e}")

module_name = "omiclient.ui"

# -------- Carga de Assets --------

try:
    _FONT = ImageFont.truetype(str(ASSETS_PATH / "PixelOperator.ttf"), 14)
    _ICON_FONT = ImageFont.truetype(str(ASSETS_PATH / "lineawesome-webfont.ttf"), 16)
    _ICON  = Image.open(ASSETS_PATH / "omarpi.png")
except Exception as e:
    log_event("error", module_name, f"Fallo al cargar assets: {e}")
    _FONT = ImageFont.load_default()
    _ICON_FONT = ImageFont.load_default()
    _ICON = None


# -------- Lienzos (Canvases) --------
def _base_canvas() -> Image.Image:
    """Fondo negro con icono en la parte inferior (para Carga/Error/Apagado)."""
    img = Image.new("L", (OLED_W, OLED_H), 0)
    if _ICON:
        max_w, max_h = OLED_W, OLED_H - HEADER_H
        w, h = _ICON.size
        scale = min(max_w / w, max_h / h)
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        icon = _ICON.resize((nw, nh), Image.LANCZOS)
        x = (OLED_W - nw) // 2
        y = OLED_H - nh
        img.paste(icon, (x, y))
    return img

def _new_frame() -> Image.Image:
    """Frame completamente negro (sin icono)."""
    return Image.new("L", (OLED_W, OLED_H), 0)

# -------- Cabeceras (Headers) --------
def _draw_header_with_progress(img: Image.Image, percent: int, label: str):
    percent = max(0, min(100, int(percent)))
    draw = ImageDraw.Draw(img)
    text = label or ""
    tw, th = draw.textbbox((0, 0), text, font=_FONT)[2:]
    tx = max(2, (OLED_W - tw) // 2)
    ty = max(0, (HEADER_H - th) // 2)
    draw.text((tx, ty), text, font=_FONT, fill=255)
    bar_w = int((percent / 100.0) * OLED_W)
    if bar_w > 0:
        draw.rectangle([0, 0, bar_w - 1, HEADER_H - 1], fill=255)
        text_layer = Image.new("L", (OLED_W, HEADER_H), 0)
        ImageDraw.Draw(text_layer).text((tx, ty), text, font=_FONT, fill=255)
        bar_mask = Image.new("L", (OLED_W, HEADER_H), 0)
        ImageDraw.Draw(bar_mask).rectangle([0, 0, bar_w - 1, HEADER_H - 1], fill=255)
        masked = Image.new("L", (OLED_W, HEADER_H), 0)
        masked.paste(text_layer, (0, 0), mask=bar_mask)
        img.paste(0, (0, 0, OLED_W, HEADER_H), mask=masked)

def _draw_header_error(img: Image.Image, label: str):
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, OLED_W - 1, HEADER_H - 1], fill=255)
    text = label or "ERROR"
    tw, th = draw.textbbox((0, 0), text, font=_FONT)[2:]
    tx = max(2, (OLED_W - tw) // 2)
    ty = max(0, (HEADER_H - th) // 2)
    draw.text((tx, ty), text, font=_FONT, fill=0)

def _draw_wifi_icon(draw: ImageDraw.ImageDraw, ok: bool, inverted: bool):

    glyph = "\uf1eb"  # usar normal y tachar si no ok
    fill = 0 if inverted else 255
    gw, gh = draw.textbbox((0, 0), glyph, font=_ICON_FONT)[2:]
    x = OLED_W - gw - 2
    y = max(0, (HEADER_H - gh) // 2)
    draw.text((x, y), glyph, font=_ICON_FONT, fill=fill)
    if not ok:
        # Línea diagonal cruzando la caja del glifo
        x0, y0 = x, y
        x1, y1 = x + gw, y + gh
        draw.line([(x0, y0), (x1, y1)], fill=fill, width=2)

def _draw_header_text_left_center_right_inverted(img: Image.Image, left:str, center:str, right_wifi_ok: bool):
    """Cabecera blanca, texto negro."""
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, OLED_W - 1, HEADER_H - 1], fill=255)
    # IZQUIERDA
    l_text = left or ""
    l_tw, l_th = draw.textbbox((0,0), l_text, font=_FONT)[2:]
    draw.text((2, max(0, (HEADER_H - l_th)//2)), l_text, font=_FONT, fill=0)
    # DERECHA (icono negro)
    _draw_wifi_icon(draw, ok=right_wifi_ok, inverted=True)
    # CENTRO
    c_text = center or ""
    c_tw, c_th = draw.textbbox((0,0), c_text, font=_FONT)[2:]
    cx = max(2, (OLED_W - c_tw)//2)
    cy = max(0, (HEADER_H - c_th)//2)
    draw.text((cx, cy), c_text, font=_FONT, fill=0)

# -------- Utilidades --------

def _is_wifi_iface(name: str) -> bool:
    if not name:
        return False
    n = name.lower()
    return n.startswith("wl") or n.startswith("wlan") or n.startswith("wifi")

def _is_eth_iface(name: str) -> bool:
    if not name:
        return False
    n = name.lower()
    return n.startswith("eth") or n.startswith("en")

def _display(img: Image.Image):
    _display_manager.display(img)


def _get_current_app_name() -> str:
    try:
        svc = STRUCTURE_MANAGER.get_active_service()
        if svc:
            return svc.get("name", "DESCONOCIDO").upper()
    except Exception:
        pass
    return "STANDBY"


def _get_connection_status() -> bool:
    return check_server_status()

def _standard_ui_listener(snapshot: Dict[str, Any]) -> None:
    update_standard_ui(snapshot)


# -------- API Pública --------
def show_loading_ui(percent: int, label: str = ""):
    stop_standard_ui()
    """Pantalla de carga: barra en cabecera con texto invertido; icono abajo."""
    img = _base_canvas()
    _draw_header_with_progress(img, percent, label)
    _display(img)

def show_message_ui(label: str = ""):
    stop_standard_ui()
    """Cabecera blanca + texto ERROR (o etiqueta), icono abajo."""
    img = _base_canvas()
    _draw_header_error(img, label)
    _display(img)


def show_error_ui(label: str = "ERROR", times: int = 3, interval: float = 0.25) -> None:
    """Muestra ErrorUI con parpadeo simple."""
    times = max(1, int(times))
    delay = max(0.05, float(interval))
    for _ in range(times):
        show_message_ui(label)
        time.sleep(delay)
        turn_ui_off()
        time.sleep(delay)
    show_message_ui(label)


def update_standard_ui(snapshot: Dict[str, Any]) -> None:
    """Cabecera blanco/negro y pie con CPU/TEMP y RED (WIFI/ETH ip/cidr)."""
    
    # Verificar Estado Ocupado primero
    structure = STRUCTURE_MANAGER.get_structure()
    sys_status = structure.get("system_status", {})
    if sys_status.get("is_busy"):
        msg = sys_status.get("busy_message") or "PROCESANDO..."
        # Mostrar UI de carga en lugar del frame estándar
        # Usamos una versión simplificada aquí para evitar recursión si show_loading_ui llama a stop_standard_ui
        # De hecho show_loading_ui llama a stop_standard_ui que desregistra este listener!
        # Así que no podemos llamar a show_loading_ui directamente si queremos seguir escuchando.
        # Solo dibujamos el frame de carga aquí.
        img = _base_canvas()
        _draw_header_with_progress(img, 50, msg) # 50% como "ocupado" genérico
        _display(img)
        return

    img = _new_frame()

    # Cabecera Invertida: fondo blanco, texto negro
    index = structure.get("identity", {}).get("index", None)
    index_label = f"#{index if index is not None else '--'}"
    app_label = (_get_current_app_name() or "").strip().upper() or "--"
    server_online = _get_connection_status()
    _draw_header_text_left_center_right_inverted(
        img,
        index_label,
        app_label,
        right_wifi_ok=server_online,
    )

    # Pie (Footer)
    draw = ImageDraw.Draw(img)
    cpu  = snapshot.get("cpu")
    temp = snapshot.get("temp")
    ifaces = snapshot.get("ifaces") or []

    # Elegir interfaz primaria con prioridad:
    # 1. Ethernet Física (eth0)
    # 2. WiFi (wlan0)
    # 3. VLAN (eth0.X)
    # 4. Otros
    primary = None
    
    def _get_prio(iface):
        name = iface.get("name", "").lower()
        if _is_eth_iface(name) and "." not in name: return 0
        if _is_wifi_iface(name): return 1
        if _is_eth_iface(name) and "." in name: return 2
        return 3

    if ifaces:
        sorted_ifaces = sorted(ifaces, key=lambda x: (_get_prio(x), x.get("name", "")))
        primary = sorted_ifaces[0]

    if primary:
        iface_name = primary.get("name") or ""
        if _is_wifi_iface(iface_name):
            kind = "WIFI"
        elif _is_eth_iface(iface_name):
            if "." in iface_name:
                kind = "VLAN"
            else:
                kind = "ETH"
        else:
            kind = "NET"
        ip_cidr = primary.get("cidr") or primary.get("ip") or "-"
        ip_text = f"{kind} {ip_cidr if ip_cidr else '-'}"
    else:
        ip_text = "NET -"

    y = HEADER_H + 0
    draw.text((2, y),      f"CPU: {('--' if cpu  is None else f'{cpu:.0f}%')}", font=_FONT, fill=255)
    draw.text((2, y + 12), f"TEMP:{('--' if temp is None else f'{temp:.0f}C')}", font=_FONT, fill=255)

    tw, _ = draw.textbbox((0, 0), ip_text, font=_FONT)[2:]
    while tw > (OLED_W - 4) and len(ip_text) > 4:
        ip_text = ip_text[:-2] + "…"
        tw, _ = draw.textbbox((0, 0), ip_text, font=_FONT)[2:]
    draw.text((2, y + 24), ip_text, font=_FONT, fill=255)

    _display(img)

def turn_ui_off():
    stop_standard_ui()
    """Apaga visualmente el OLED (pantalla completamente negra)."""
    _display_manager.clear()
    log_event("info", module_name, "Pantalla OLED apagada")


def start_standard_ui(ensure_heartbeat: bool = True) -> None:
    """Registra la UI estándar para recibir actualizaciones del heartbeat."""
    global _standard_listener_registered

    log_print("info", module_name, "Iniciando UI Estándar")

    if ensure_heartbeat:
        start_heartbeat(start_active=True)

    if not _standard_listener_registered:
        register_heartbeat_listener(_standard_ui_listener)
        _standard_listener_registered = True

    snapshot = get_heartbeat_snapshot()
    update_standard_ui(snapshot)


def stop_standard_ui() -> None:
    """Elimina la suscripción de la UI estándar y apaga la pantalla."""
    global _standard_listener_registered

    if _standard_listener_registered:
        unregister_heartbeat_listener(_standard_ui_listener)
        _standard_listener_registered = False
        log_print("info", module_name, "Suscripción UI eliminada del heartbeat")

log_print("info", module_name, "OLED inicializado y listo")
