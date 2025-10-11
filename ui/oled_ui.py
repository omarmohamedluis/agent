# ui/oled_ui.py
# OLED UI para SSD1306 128x64 (I2C 0x3C) con dos modos:
#  - LOADING: barra de carga + etiqueta de estado (set_progress)
#  - READY:   header 15 px (index | perfil | wifi) + 4 líneas (CPU, TEMP, WIFI, ETH)
#
# Requisitos:
#   pip install luma.oled pillow psutil netifaces

from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
from threading import Thread, Event
import time

import psutil
import netifaces
from PIL import Image, ImageDraw, ImageFont

# Hardware (fallback a NOOP si no hay OLED)
_HW_OK = True
try:
    from luma.core.interface.serial import i2c
    from luma.oled.device import ssd1306
except Exception:
    _HW_OK = False

# ----------------- Layout -----------------
OLED_W, OLED_H = 128, 64
HEADER_H, FOOTER_H = 15, 49  # 15 arriba, 49 abajo (4 líneas x LINE_HEIGHT)

# Barra superior: [0..15]=index, [16..111]=perfil (96px), [112..127]=icono
IDX_W, CENTER_W, ICON_W = 16, 96, 16

# Buscar carpeta 'utilitys'
_HERE = Path(__file__).resolve()
_CANDIDATES = [
    _HERE.parents[1] / "utilitys",  # agent/utilitys
    _HERE.parents[2] / "utilitys",  # por si estuviera un nivel más arriba
]
for _p in _CANDIDATES:
    if _p.exists():
        ASSETS = _p
        break
else:
    ASSETS = None

def _load_font(ttf_path: str | None, size: int):
    """Carga TTF con fallback seguro."""
    try:
        if ttf_path and Path(ttf_path).exists():
            return ImageFont.truetype(ttf_path, size)
    except Exception:
        pass
    try:
        return ImageFont.load_default()
    except Exception:
        try:
            return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", size)
        except Exception:
            return ImageFont.load_default()

FONT_MONO = str(ASSETS / "PixelOperator.ttf") if ASSETS else None
FONT_ICON = str(ASSETS / "lineawesome-webfont.ttf") if ASSETS else None

# Codepoints (ajusta si tu TTF usa otros)
ICON_WIFI       = "\uf1eb"
ICON_WIFI_SLASH = "\uf6ac"

# Tamaños
FONT_SIZE_HEADER = 17
FONT_SIZE_ICON   = 18
FONT_SIZE_FOOT   = 15
LINE_HEIGHT      = 12  # separacion entre líneas del footer

# ---------- Helpers de texto ----------
def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    """Devuelve (ancho, alto) del texto con el font dado."""
    try:
        x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
        return (x1 - x0, y1 - y0)
    except Exception:
        pass
    try:
        return font.getsize(text)
    except Exception:
        pass
    try:
        return (int(draw.textlength(text, font=font)), getattr(font, "size", 12))
    except Exception:
        return (len(text) * getattr(font, "size", 12) // 2, getattr(font, "size", 12))

def _vcenter_y(draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, box_h: int) -> int:
    """Calcula la Y para centrar verticalmente texto en box_h usando métricas reales."""
    try:
        ascent, descent = font.getmetrics()
        text_h = ascent + descent
        y = (box_h - text_h) // 2
        return max(0, y)
    except Exception:
        # Fallback: usa altura de bbox de una muestra
        _, h = _measure(draw, "Hg", font)
        return max(0, (box_h - h) // 2)

# ----------------- Datos -----------------
@dataclass
class HUDStats:
    cpu: int = 0
    temp_c: int = 0
    ip_wlan: str = "-"
    ip_eth: str = "-"

class _NoopDevice:
    def display(self, img): pass

class OledUI:
    def __init__(self, i2c_addr: int = 0x3C, i2c_port: int = 1,
                 refresh_s: float = 0.25, stats_every_s: float = 2.0):
        self._mode = "LOADING"  # LOADING | READY
        self._progress = 0
        self._label = "Cargando…"

        self._index = None      # int | None → ‘N’ si None
        self._profile = "standby"
        self._connected = False

        self._stats = HUDStats()

        self.refresh_s = refresh_s
        self.stats_every_s = stats_every_s
        self._stop = Event()
        self._th: Thread | None = None

        # Fuentes
        self.font_header = _load_font(FONT_MONO, FONT_SIZE_HEADER)
        self.font_icon   = _load_font(FONT_ICON, FONT_SIZE_ICON)
        self.font_foot   = _load_font(FONT_MONO, FONT_SIZE_FOOT)

        # Dispositivo
        if _HW_OK:
            try:
                serial = i2c(port=i2c_port, address=i2c_addr)
                self.dev = ssd1306(serial, width=OLED_W, height=OLED_H)
            except Exception:
                self.dev = _NoopDevice()
        else:
            self.dev = _NoopDevice()

    # -------- API llamada desde app.py --------
    def start_boot(self):
        self._mode = "LOADING"
        self._progress = 0
        self._label = "Cargando…"
        self._start_thread()

    def set_progress(self, percent: int, label: str | None = None):
        self._progress = max(0, min(100, int(percent)))
        if label is not None:
            self._label = label

    def set_ready(self, profile: str, index: int | None):
        self._profile = str(profile) if profile else "standby"
        self._index = index if (isinstance(index, int) or index is None) else None
        self._mode = "READY"

    def set_connection(self, is_connected: bool):
        self._connected = bool(is_connected)

    def set_stats(self, cpu: int, temp_c: int, ip_wlan: str, ip_eth: str):
        self._stats = HUDStats(cpu=int(cpu), temp_c=int(temp_c),
                               ip_wlan=ip_wlan or "-", ip_eth=ip_eth or "-")

    def stop(self):
        self._stop.set()
        if self._th and self._th.is_alive():
            self._th.join(timeout=1)

    # -------- Hilo de refresco --------
    def _start_thread(self):
        if self._th and self._th.is_alive():
            return
        self._render_loading()
        self._stop.clear()
        self._th = Thread(target=self._loop, daemon=True)
        self._th.start()

    def _loop(self):
        t_stats = 0.0
        self._collect_stats()
        while not self._stop.is_set():
            now = time.time()
            if now - t_stats >= self.stats_every_s:
                self._collect_stats()
                t_stats = now

            if self._mode == "LOADING":
                self._render_loading()
            else:
                self._render_hud()

            time.sleep(self.refresh_s)

    # -------- Datos del sistema --------
    def _collect_stats(self):
        try:
            cpu = int(psutil.cpu_percent(interval=None))
        except Exception:
            cpu = 0

        temp = 0
        try:
            temps = psutil.sensors_temperatures()
            for key in ("cpu-thermal", "cpu_thermal", "soc_thermal"):
                if key in temps and temps[key]:
                    temp = int(temps[key][0].current)
                    break
        except Exception:
            pass

        def _ip_cidr(iface: str) -> str:
            """Devuelve dirección IP con máscara /CIDR o '-'."""
            try:
                addrs = netifaces.ifaddresses(iface)
                if netifaces.AF_INET in addrs:
                    info = addrs[netifaces.AF_INET][0]
                    ip = info.get("addr", "-")
                    mask = info.get("netmask", None)
                    if mask:
                        bits = sum(bin(int(x)).count("1") for x in mask.split("."))
                        return f"{ip}/{bits}"
                    return ip
            except Exception:
                pass
            return "-"

        wlan = _ip_cidr("wlan0")
        eth  = _ip_cidr("eth0")

        self._stats = HUDStats(cpu=cpu, temp_c=temp, ip_wlan=wlan, ip_eth=eth)


    # -------- Dibujo --------
    def _new_canvas(self):
        img = Image.new("1", (OLED_W, OLED_H), 0)  # fondo negro
        draw = ImageDraw.Draw(img)
        return img, draw

    def _render_loading(self):
        img, draw = self._new_canvas()

        # Etiqueta centrada (blanco sobre fondo negro)
        label = self._label or ""
        if label:
            w, h = _measure(draw, label, self.font_header)
            draw.text(((OLED_W - w) // 2, 10), label, font=self.font_header, fill=1)

        # Barra central
        bar_w, bar_h = OLED_W - 16, 8
        x0 = 8
        y0 = (OLED_H // 2) - (bar_h // 2)
        draw.rectangle((x0, y0, x0 + bar_w, y0 + bar_h), outline=1, fill=0)
        if self._progress > 0:
            fill_w = int((bar_w - 2) * (self._progress / 100.0))
            draw.rectangle((x0 + 1, y0 + 1, x0 + 1 + fill_w, y0 + bar_h - 1), fill=1)

        self.dev.display(img)

    def _render_hud(self):
        img, draw = self._new_canvas()

        # ---------- HEADER (fondo blanco, texto negro) ----------
        draw.rectangle((0, 0, OLED_W, HEADER_H), fill=1)

        # y centradas por métrica
        y_header_txt = _vcenter_y(draw, self.font_header, HEADER_H)
        y_icon_txt   = _vcenter_y(draw, self.font_icon,   HEADER_H)

        # IZQ: índice (o 'N') dentro de 16x(HEADER_H)
        idx_txt = str(self._index) if (self._index is not None) else "N"
        draw.rectangle((1, 1, IDX_W - 2, HEADER_H - 2), outline=0, fill=1)  # borde negro
        draw.text((4, y_header_txt), idx_txt, font=self.font_header, fill=0)  # negro

        # CENTRO: perfil en MAYÚSCULAS (recorte “…” si no cabe)
        profile = (self._profile or "standby").upper()
        max_w = CENTER_W - 4
        w_prof, _ = _measure(draw, profile, self.font_header)
        if w_prof > max_w:
            while profile and _measure(draw, profile + "…", self.font_header)[0] > max_w:
                profile = profile[:-1]
            profile += "…"
            w_prof, _ = _measure(draw, profile, self.font_header)
        draw.text((16 + (CENTER_W - w_prof)//2, y_header_txt),
                  profile, font=self.font_header, fill=0)

        # DERECHA: icono Wi-Fi (negro)
        try:
            icon = ICON_WIFI if self._connected else ICON_WIFI_SLASH
            iw, _ = _measure(draw, icon, self.font_icon)
            draw.text((OLED_W - ICON_W + (ICON_W - iw)//2, y_icon_txt),
                      icon, font=self.font_icon, fill=0)
        except Exception:
            x = OLED_W - ICON_W + 2
            y = y_icon_txt if y_icon_txt > 0 else 2
            draw.arc((x, y, x+12, y+12), start=200, end=340, fill=0)
            draw.arc((x-2, y, x+14, y+12), start=200, end=340, fill=0)
            if not self._connected:
                draw.line((x, y+12, x+12, y), fill=0)

        # ---------- FOOTER (fondo negro, texto blanco) ----------
        y0 = HEADER_H
        lines = [
            f"CPU: {self._stats.cpu}%",
            f"TEMP: {self._stats.temp_c}°C",
            f"WIFI: {self._stats.ip_wlan or '-'}",
            f"ETH: {self._stats.ip_eth or '-'}",
        ]
        for i, txt in enumerate(lines):
            y = y0 + i * LINE_HEIGHT -3  # ajusta +2 si quieres subirlas/bajarlas
            draw.text((2, y), txt, font=self.font_foot, fill=1)

        self.dev.display(img)
