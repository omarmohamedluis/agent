# ui/oled_ui.py
from __future__ import annotations
from pathlib import Path
from dataclasses import dataclass
from threading import Thread, Event
import time

import psutil
import netifaces
from PIL import Image, ImageDraw, ImageFont, ImageChops
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306

# ----------------- Layout -----------------
OLED_W, OLED_H = 128, 64
HEADER_H = 15
LINE_HEIGHT = 12           # alto de cada línea del footer
STATS_Y_OFFSET = -3        # ajuste fino vertical de las 4 líneas

IDX_W, CENTER_W, ICON_W = 16, 96, 16

# ----------------- Assets -----------------
ASSETS = Path(__file__).resolve().parents[1] / "utilitys"
FONT_MONO = ASSETS / "PixelOperator.ttf"
FONT_ICON = ASSETS / "lineawesome-webfont.ttf"
SPLASH_IMG = ASSETS / "omarpi.png"

ICON_WIFI = "\uf1eb"       # presente en tu TTF

FONT_SIZE_HEADER = 17
FONT_SIZE_ICON   = 18
FONT_SIZE_FOOT   = 15

# ----------------- Datos -----------------
@dataclass
class HUDStats:
    cpu: int = 0
    temp_c: int = 0
    ip_wlan: str = "-"
    ip_eth: str = "-"

# ----------------- UI -----------------
class OledUI:
    def __init__(self, i2c_addr: int = 0x3C, i2c_port: int = 1,
                 refresh_s: float = 0.25, stats_every_s: float = 2.0):
        self._mode = "LOADING"      # LOADING | READY
        self._progress = 0
        self._label = "Cargando…"   # App podrá cambiarlo, pero en loading mostramos fijo “CARGANDO…”
        self._loading_text_fixed = "CARGANDO…"

        self._index: int | None = None
        self._profile = "standby"
        self._connected = False
        self._stats = HUDStats()

        # Splash (se mantiene durante toda la carga)
        self._splash_img: Image.Image | None = None

        self.refresh_s = refresh_s
        self.stats_every_s = stats_every_s
        self._stop = Event()
        self._th: Thread | None = None

        self.font_header = ImageFont.truetype(str(FONT_MONO), FONT_SIZE_HEADER)
        self.font_icon   = ImageFont.truetype(str(FONT_ICON), FONT_SIZE_ICON)
        self.font_foot   = ImageFont.truetype(str(FONT_MONO), FONT_SIZE_FOOT)

        self.dev = ssd1306(i2c(port=i2c_port, address=i2c_addr), width=OLED_W, height=OLED_H)

        # Carga splash si existe
        try:
            if SPLASH_IMG.exists():
                img = Image.open(SPLASH_IMG).convert("1")
                if img.size != (OLED_W, OLED_H):
                    img = img.resize((OLED_W, OLED_H))
                self._splash_img = img
        except Exception:
            self._splash_img = None

    # -------- API --------
    def start_boot(self):
        self._mode = "LOADING"
        self._progress = 0

        self._start_thread()

    def set_progress(self, percent: int, label: str | None = None):
        self._progress = max(0, min(100, int(percent)))
        # Podemos aceptar label para logs, pero en pantalla usamos fijo “CARGANDO…”
        if label is not None:
            self._label = label

    def set_ready(self, profile: str, index: int | None):
        self._profile = (profile or "standby")
        self._index = index if (isinstance(index, int) or index is None) else None
        self._mode = "READY"

    def set_connection(self, is_connected: bool):
        self._connected = bool(is_connected)

    def stop(self):
        self._stop.set()
        if self._th and self._th.is_alive():
            self._th.join(timeout=1)

    # -------- Loop --------
    def _start_thread(self):
        if self._th and self._th.is_alive():
            return
        # Render inicial
        self._render_loading()
        self._stop.clear()
        self._th = Thread(target=self._loop, daemon=True)
        self._th.start()

    def _loop(self):
        last_stats = 0.0
        self._collect_stats()
        while not self._stop.is_set():
            now = time.time()
            if now - last_stats >= self.stats_every_s:
                self._collect_stats()
                last_stats = now

            if self._mode == "LOADING":
                self._render_loading()
            else:
                self._render_hud()

            time.sleep(self.refresh_s)

    # -------- Datos del sistema --------
    def _collect_stats(self):
        cpu = int(psutil.cpu_percent(interval=None))

        temp_c = 0
        try:
            temps = psutil.sensors_temperatures()
            for key in ("cpu-thermal", "cpu_thermal", "soc_thermal"):
                if key in temps and temps[key]:
                    temp_c = int(temps[key][0].current)
                    break
        except Exception:
            pass

        def ip_cidr(iface: str) -> str:
            try:
                addrs = netifaces.ifaddresses(iface)
                if netifaces.AF_INET not in addrs:
                    return "-"
                info = addrs[netifaces.AF_INET][0]
                ip = info.get("addr", "-")
                mask = info.get("netmask")
                if not ip or ip == "0.0.0.0":
                    return "-"
                if not mask:
                    return ip
                bits = sum(bin(int(x)).count("1") for x in mask.split("."))
                return f"{ip}/{bits}"
            except Exception:
                return "-"

        self._stats = HUDStats(
            cpu=cpu,
            temp_c=temp_c,
            ip_wlan=ip_cidr("wlan0"),
            ip_eth=ip_cidr("eth0"),
        )

    # -------- Helpers de dibujo --------
    @staticmethod
    def _new_canvas():
        img = Image.new("1", (OLED_W, OLED_H), 0)  # negro
        return img, ImageDraw.Draw(img)

    @staticmethod
    def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont):
        x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
        return x1 - x0, y1 - y0

    @staticmethod
    def _vcenter_y(font: ImageFont.ImageFont, box_h: int) -> int:
        ascent, descent = font.getmetrics()
        return max(0, (box_h - (ascent + descent)) // 2)

    @staticmethod
    def _elide_to_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> str:
        w, _ = OledUI._text_size(draw, text, font)
        if w <= max_w:
            return text
        while text and OledUI._text_size(draw, text + "…", font)[0] > max_w:
            text = text[:-1]
        return (text + "…") if text else "…"

    # -------- Render --------
    def _render_loading(self):
        # Base: splash si existe; si no, negro
        if self._splash_img is not None:
            base = self._splash_img.copy()
        else:
            base = Image.new("1", (OLED_W, OLED_H), 0)  # negro

        # Header-progreso (independiente) con inversión de texto por XOR
        header = Image.new("1", (OLED_W, HEADER_H), 0)

        # a) Relleno del progreso (blanco en la parte llena)
        fill_w = max(0, min(OLED_W, int(OLED_W * (self._progress / 100.0))))
        if fill_w > 0:
            fill = Image.new("1", (OLED_W, HEADER_H), 0)
            ImageDraw.Draw(fill).rectangle((0, 0, fill_w - 1, HEADER_H - 1), fill=1)
            header = ImageChops.logical_xor(header, fill)

        # b) Texto del header: usamos fijo “CARGANDO…” en MAYÚSCULAS y lo recortamos si hace falta
        text_layer = Image.new("1", (OLED_W, HEADER_H), 0)
        dtext = ImageDraw.Draw(text_layer)
        label = self._loading_text_fixed.upper()
        label = self._elide_to_width(dtext, label, self.font_header, OLED_W - 4)
        tw, th = self._text_size(dtext, label, self.font_header)
        y = self._vcenter_y(self.font_header, HEADER_H)
        dtext.text(((OLED_W - tw) // 2, y), label, font=self.font_header, fill=1)

        # c) XOR del texto con el header: invierte las letras donde hay fill
        header = ImageChops.logical_xor(header, text_layer)

        # d) Componer sobre el splash
        base.paste(header, (0, 0))

        self.dev.display(base)

    def _render_hud(self):
        img, draw = self._new_canvas()

        # ---------- HEADER (fondo blanco, texto negro) ----------
        draw.rectangle((0, 0, OLED_W, HEADER_H), fill=1)
        y_header = self._vcenter_y(self.font_header, HEADER_H)
        y_icon   = self._vcenter_y(self.font_icon,   HEADER_H)

        # IZQ: índice (negro sobre blanco)
        idx_txt = str(self._index) if (self._index is not None) else "N"
        draw.rectangle((1, 1, IDX_W - 2, HEADER_H - 2), outline=0, fill=1)
        draw.text((4, y_header), idx_txt, font=self.font_header, fill=0)

        # CENTRO: perfil MAYÚSCULAS con recorte “…” a CENTER_W
        profile = (self._profile or "standby").upper()
        profile = self._elide_to_width(draw, profile, self.font_header, CENTER_W - 4)
        w_prof, _ = self._text_size(draw, profile, self.font_header)
        draw.text((IDX_W + (CENTER_W - w_prof) // 2, y_header),
                  profile, font=self.font_header, fill=0)

        # DERECHA: icono Wi-Fi
        iw, ih = self._text_size(draw, ICON_WIFI, self.font_icon)
        x_icon = OLED_W - ICON_W + (ICON_W - iw) // 2
        draw.text((x_icon, y_icon), ICON_WIFI, font=self.font_icon, fill=0)

        # Desconectado: barra diagonal elegante
        if not self._connected:
            offset_y = 2
            thickness = 2
            x0 = x_icon - 1
            y0 = y_icon + ih - offset_y
            x1 = x_icon + iw + 1
            y1 = y_icon + offset_y
            for t in range(thickness):
                draw.line((x0, y0 - t, x1, y1 - t), fill=0)

        # ---------- FOOTER (negro, texto blanco) ----------
        lines = [
            f"CPU: {self._stats.cpu}%",
            f"TEMP: {self._stats.temp_c}°C",
            f"WIFI: {self._stats.ip_wlan}",
            f"ETH: {self._stats.ip_eth}",
        ]
        base_y = HEADER_H + STATS_Y_OFFSET
        for i, txt in enumerate(lines):
            draw.text((2, base_y + i * LINE_HEIGHT), txt, font=self.font_foot, fill=1)

        self.dev.display(img)
