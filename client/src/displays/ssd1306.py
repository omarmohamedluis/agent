from PIL import Image
from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from .base import DisplayDriver

class SSD1306Driver(DisplayDriver):
    def __init__(self, width=128, height=64, port=1, address=0x3C):
        self._width = width
        self._height = height
        self._port = port
        self._address = address
        self._device = None

    def init(self):
        serial = i2c(port=self._port, address=self._address)
        self._device = ssd1306(serial, width=self._width, height=self._height)

    def clear(self):
        if self._device:
            self._device.clear()

    def display(self, image: Image.Image):
        if self._device:
            if image.size != (self._device.width, self._device.height):
                image = image.resize((self._device.width, self._device.height), Image.NEAREST)
            if image.mode != self._device.mode:
                image = image.convert(self._device.mode)
            self._device.display(image)

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height

    def cleanup(self):
        if self._device:
            self._device.cleanup()
            self._device = None

    # --- High Level Rendering ---

    def _get_font(self, size: int):
        from PIL import ImageFont
        try:
            from pathlib import Path
            fpath = Path(__file__).resolve().parents[2] / "web" / "utilities" / "PixelOperator.ttf"
            return ImageFont.truetype(str(fpath), size)
        except Exception:
            return ImageFont.load_default()

    def _get_icon_font(self, size: int):
        from PIL import ImageFont
        try:
            from pathlib import Path
            fpath = Path(__file__).resolve().parents[2] / "web" / "utilities" / "lineawesome-webfont.ttf"
            return ImageFont.truetype(str(fpath), size)
        except Exception:
            return ImageFont.load_default()

    def _get_logo(self):
        from PIL import Image
        try:
            from pathlib import Path
            fpath = Path(__file__).resolve().parents[2] / "web" / "utilities" / "omarpi.png"
            return Image.open(str(fpath))
        except Exception:
            return None

    def render_loading(self, percent: int, label: str):
        from PIL import ImageDraw, Image
        img = Image.new("1", (self._width, self._height), 0)
        
        # Draw Logo at bottom
        logo = self._get_logo()
        if logo:
            max_h = self._height - 16
            w, h = logo.size
            scale = min(self._width / w, max_h / h)
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            logo_res = logo.resize((nw, nh), Image.LANCZOS).convert("L")
            # Convert to 1 bit (monochrome)
            logo_res = logo_res.point(lambda x: 255 if x > 128 else 0, mode="1")
            img.paste(logo_res, ((self._width - nw) // 2, self._height - nh))

        draw = ImageDraw.Draw(img)
        font = self._get_font(14)
        
        # Header with inverted progress
        bar_w = int((percent / 100.0) * self._width)
        draw.rectangle([0, 0, self._width - 1, 15], outline=255)
        
        txt = label or f"LOADING {percent}%"
        tw, th = draw.textbbox((0, 0), txt, font=font)[2:]
        tx, ty = (self._width - tw) // 2, (16 - th) // 2
        
        # White text on black
        draw.text((tx, ty), txt, font=font, fill=255)
        
        if bar_w > 0:
            # Masked inversion for the bar
            bar_mask = Image.new("L", (self._width, 16), 0)
            ImageDraw.Draw(bar_mask).rectangle([0, 0, bar_w, 15], fill=255)
            
            temp_header = Image.new("L", (self._width, 16), 255)
            ImageDraw.Draw(temp_header).text((tx, ty), txt, font=font, fill=0)
            
            # Convert back to mode 1 for pasting
            temp_header = temp_header.convert("1")
            img.paste(temp_header, (0, 0), mask=bar_mask)

        self.display(img)

    def render_message(self, text: str, is_error: bool = False):
        from PIL import ImageDraw, Image
        img = Image.new("1", (self._width, self._height), 0)
        
        # Draw Logo at bottom
        logo = self._get_logo()
        if logo:
            max_h = self._height - 16
            w, h = logo.size
            scale = min(self._width / w, max_h / h)
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            logo_res = logo.resize((nw, nh), Image.LANCZOS).convert("L").point(lambda x: 255 if x > 128 else 0, mode="1")
            img.paste(logo_res, ((self._width - nw) // 2, self._height - nh))

        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, self._width, 15], fill=255)
        font_h = self._get_font(14)
        
        header_txt = "SYSTEM MSG" if not is_error else "ERROR"
        tw, th = draw.textbbox((0, 0), header_txt, font=font_h)[2:]
        draw.text(((self._width - tw)//2, (16 - th)//2), header_txt, font=font_h, fill=0)
        
        self.display(img)

    def render_standard(self, snapshot: dict, structure: dict, server_online: bool):
        from PIL import ImageDraw, Image
        img = Image.new("1", (self._width, self._height), 0)
        draw = ImageDraw.Draw(img)
        
        # 1. Header (Inverted)
        draw.rectangle([0, 0, self._width, 15], fill=255)
        f_h = self._get_font(12)
        
        idx = structure.get("identity", {}).get("index", "--")
        draw.text((2, 1), f"#{idx}", font=f_h, fill=0)
        
        services = structure.get("services", [])
        active_name = "STANDBY"
        for s in services:
            if s.get("running"):
                active_name = s.get("name", "ACTIVE")
                break
        
        svc_txt = active_name.upper()[:12]
        sw, sh = draw.textbbox((0, 0), svc_txt, font=f_h)[2:]
        draw.text(((self._width - sw)//2, (16 - sh)//2), svc_txt, font=f_h, fill=0)
        
        icon_f = self._get_icon_font(14)
        glyph = "\uf1eb" # Wifi
        iw, ih = draw.textbbox((0,0), glyph, font=icon_f)[2:]
        ix = self._width - iw - 2
        draw.text((ix, (16 - ih)//2), glyph, font=icon_f, fill=0)
        if not server_online:
            draw.line([(ix, 2), (ix+iw, 14)], fill=0, width=2)

        # 2. Body
        f_b = self._get_font(14)
        cpu = snapshot.get("cpu", 0)
        temp = snapshot.get("temp", 0)
        
        draw.text((4, 20), f"CPU: {cpu:.0f}%", font=f_b, fill=255)
        draw.text((4, 34), f"TEMP: {temp:.0f}C", font=f_b, fill=255)
        
        ip_val = "DISCONNECTED"
        ifaces = snapshot.get("ifaces", [])
        # Prio: eth, wlan, others
        sorted_if = sorted(ifaces, key=lambda x: (0 if "eth" in x.get("name","").lower() else 1 if "wlan" in x.get("name","").lower() else 2))
        if sorted_if:
            ip = sorted_if[0].get("ip") or "-"
            name = sorted_if[0].get("name", "").upper()[:4]
            ip_val = f"{name} {ip}"

        # Text wrap / trim for IP
        while draw.textbbox((0,0), ip_val, font=f_b)[2] > self._width - 4 and len(ip_val) > 10:
            ip_val = ip_val[:-1]
            
        draw.text((4, 48), ip_val, font=f_b, fill=255)
        
        self.display(img)
