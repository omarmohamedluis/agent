# ui/ui.py

from __future__ import annotations
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import json


DEFAULT_JSON_PATH = Path("agent_pi/data/structure.json")

OLED_W, OLED_H = 128, 64
HEADER_H = 16

# -------- Hardware --------

from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306

serial = i2c(port=1, address=0x3C)
_device = ssd1306(serial, width=OLED_W, height=OLED_H)

# -------- Carga estricta de fuentes e icono --------

_FONT = ImageFont.truetype("utilitys/PixelOperator.ttf",14)
_ICON_FONT = ImageFont.truetype("utilitys/lineawesome-webfont.ttf",16)
_ICON  = Image.open("utilitys/omarpi.png")


# -------- Lienzos --------
def _base_canvas() -> Image.Image:
    """Fondo negro con icono abajo (para Loading/Error/Shutdown)."""
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

# -------- Headers --------
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

    glyph = "\uf1eb"  # usamos el normal y lo tachamos si no ok
    fill = 0 if inverted else 255
    gw, gh = draw.textbbox((0, 0), glyph, font=_ICON_FONT)[2:]
    x = OLED_W - gw - 2
    y = max(0, (HEADER_H - gh) // 2)
    draw.text((x, y), glyph, font=_ICON_FONT, fill=fill)
    if not ok:
        # Diagonal del recuadro del glyph
        x0, y0 = x, y
        x1, y1 = x + gw, y + gh
        draw.line([(x0, y0), (x1, y1)], fill=fill, width=2)

def _draw_header_text_left_center_right_inverted(img: Image.Image, left:str, center:str, right_wifi_ok: bool):
    """Header blanco, texto negro."""
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, OLED_W - 1, HEADER_H - 1], fill=255)
    # LEFT
    l_text = left or ""
    l_tw, l_th = draw.textbbox((0,0), l_text, font=_FONT)[2:]
    draw.text((2, max(0, (HEADER_H - l_th)//2)), l_text, font=_FONT, fill=0)
    # RIGHT (icono negro)
    _draw_wifi_icon(draw, ok=right_wifi_ok, inverted=True)
    # CENTER
    c_text = center or ""
    c_tw, c_th = draw.textbbox((0,0), c_text, font=_FONT)[2:]
    cx = max(2, (OLED_W - c_tw)//2)
    cy = max(0, (HEADER_H - c_th)//2)
    draw.text((cx, cy), c_text, font=_FONT, fill=0)

# -------- Útiles --------
def _read_json_simple(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _get_enabled_service(services_list):
    if isinstance(services_list, list):
        for s in services_list:
            if s.get("enabled"):
                return s.get("name") or ""
    return ""

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
    # if not _HW_OK or _device is None:
    #     print("[OLED:NOOP] frame renderizado")
    #     return
    if img.size != (_device.width, _device.height):
        img = img.resize((_device.width, _device.height), Image.NEAREST)
    if img.mode != _device.mode:
        img = img.convert(_device.mode)
    _device.display(img)

# -------- API pública --------
def LoadingUI(percent: int, label: str = "Cargando"):
    """Pantalla de carga: barra en header que invierte el texto; icono abajo."""
    img = _base_canvas()
    _draw_header_with_progress(img, percent, label)
    _display(img)

def ErrorUI(label: str = "ERROR"):
    """Header blanco + texto ERROR (o label), icono abajo."""
    img = _base_canvas()
    _draw_header_error(img, label)
    _display(img)

def UIShutdownProceess(percent: int, label: str = "Apagando"):
    """Igual que LOADING, separado por semántica (con icono)."""
    img = _base_canvas()
    _draw_header_with_progress(img, percent, label)
    _display(img)

def EstandardUse(snapshot: dict, server_online: bool, json_path: Path = DEFAULT_JSON_PATH):
    """Header blanco/negro y footer con CPU/TEMP y NET (WIFI/ETH ip/cidr)."""
    img = _new_frame()

    # Header invertido: fondo blanco, texto negro
    data = _read_json_simple(json_path)
    index = data.get("identity", {}).get("index", None)
    index_label = f"#{index if index is not None else '--'}"
    services = data.get("services", [])
    service_active = _get_enabled_service(services)
    service_label = (service_active or "").upper()
    _draw_header_text_left_center_right_inverted(img, index_label, service_label, right_wifi_ok=server_online)

    # Footer
    draw = ImageDraw.Draw(img)
    cpu  = snapshot.get("cpu")
    temp = snapshot.get("temp")
    ifaces = snapshot.get("ifaces") or []

    # Elegir interfaz principal: primero Wi-Fi; si no hay, la primera
    primary = None
    for x in ifaces:
        if _is_wifi_iface(x.get("iface", "")):
            primary = x
            break
    if primary is None:
        primary = ifaces[0] if ifaces else None

    if primary:
        iface_name = primary.get("iface") or ""
        if _is_wifi_iface(iface_name):
            kind = "WIFI"
        elif _is_eth_iface(iface_name):
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

def UIOFF():
    """Apaga visualmente la OLED (pantalla completamente negra)."""
    img = Image.new("L", (OLED_W, OLED_H), 0)
    _display(img)
