import time
import logging
import threading
from PIL import Image, ImageDraw, ImageFont
from smbus2 import SMBus, i2c_msg
from .base import DisplayDriver

LOGGER = logging.getLogger("omiclient.displays.uctronics")

# Registers from st7735.h
I2C_ADDRESS = 0x18
BURST_MAX_LENGTH = 160

X_COORDINATE_REG = 0x2A
Y_COORDINATE_REG = 0x2B
CHAR_DATA_REG = 0x2C
SCAN_DIRECTION_REG = 0x36
WRITE_DATA_REG = 0x00
BURST_WRITE_REG = 0x01
SYNC_REG = 0x03

ST7735_XSTART = 0
ST7735_YSTART = 24
ST7735_WIDTH = 160
ST7735_HEIGHT = 80

class UCTRONICS_RM0004Driver(DisplayDriver):
    def __init__(self, port=1, address=I2C_ADDRESS):
        self._port = port
        self._address = address
        self._bus = None
        self._width = ST7735_WIDTH
        self._height = ST7735_HEIGHT
        self._lock = threading.Lock()
        self._font_cache = {}

    def init(self):
        try:
            self._bus = SMBus(self._port)
            # Test connection
            self._bus.write_quick(self._address)
            LOGGER.info(f"UCTRONICS RM0004 initialized at address 0x{self._address:02X}")
            self.clear()
        except Exception as e:
            LOGGER.error(f"Failed to initialize UCTRONICS RM0004: {e}")
            raise

    def _write_command(self, cmd, high, low):
        msg = [cmd, high, low]
        self._bus.write_i2c_block_data(self._address, msg[0], msg[1:])

    def _set_address_window(self, x0, y0, x1, y1):
        self._write_command(X_COORDINATE_REG, x0 + ST7735_XSTART, x1 + ST7735_XSTART)
        self._write_command(Y_COORDINATE_REG, y0 + ST7735_YSTART, y1 + ST7735_YSTART)
        self._write_command(CHAR_DATA_REG, 0x00, 0x00)
        self._write_command(SYNC_REG, 0x00, 0x01)

    def clear(self):
        if not self._bus:
            return
        self.fill_rectangle(0, 0, self._width, self._height, 0x0000)
        self._write_command(SYNC_REG, 0x00, 0x01)

    def fill_rectangle(self, x, y, w, h, color):
        if x >= self._width or y >= self._height:
            return
        if x + w > self._width:
            w = self._width - x
        if y + h > self._height:
            h = self._height - y

        self._set_address_window(x, y, x + w - 1, y + h - 1)
        
        # Prepare high and low bytes for the color
        high = (color >> 8) & 0xFF
        low = color & 0xFF
        row_data = bytes([high, low] * w)
        
        self._write_command(BURST_WRITE_REG, 0x00, 0x01)
        for _ in range(h):
            self._burst_transfer(row_data)
        self._write_command(BURST_WRITE_REG, 0x00, 0x00)

    def _burst_transfer(self, data):
        length = len(data)
        offset = 0
        while offset < length:
            try:
                chunk_size = min(length - offset, BURST_MAX_LENGTH)
                msg = i2c_msg.write(self._address, data[offset:offset + chunk_size])
                self._bus.i2c_rdwr(msg)
                offset += chunk_size
                time.sleep(0.0007) # From C code usleep(700)
            except Exception as e:
                LOGGER.error(f"I2C Burst Error: {e}")
                # Try to re-init bus if it fails
                try:
                    self._bus.close()
                    self._bus = SMBus(self._port)
                except:
                    pass
                time.sleep(0.01)
                continue

    def display(self, image: Image.Image):
        with self._lock:
            if not self._bus:
                return
            
            # Resize if necessary
            if image.size != (self._width, self._height):
                image = image.resize((self._width, self._height), Image.LANCZOS)
            
            # Convert to RGB565
            image = image.convert("RGB")
            pixels = image.load()
            
            data = bytearray()
            for y in range(self._height):
                for x in range(self._width):
                    r, g, b = pixels[x, y]
                    # RGB888 to RGB565
                    color = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
                    data.append((color >> 8) & 0xFF)
                    data.append(color & 0xFF)
            
            self._set_address_window(0, 0, self._width - 1, self._height - 1)
            self._write_command(BURST_WRITE_REG, 0x00, 0x01)
            self._burst_transfer(data)
            self._write_command(BURST_WRITE_REG, 0x00, 0x00)
            self._write_command(SYNC_REG, 0x00, 0x01)

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height

    def cleanup(self):
        if self._bus:
            self._bus.close()
            self._bus = None

    # --- High Level Rendering ---

    def _get_font(self, size: int):
        from PIL import ImageFont
        key = f"P_{size}"
        if key in self._font_cache: return self._font_cache[key]
        try:
            from pathlib import Path
            fpath = Path(__file__).resolve().parents[2] / "web" / "utilities" / "PixelOperator.ttf"
            self._font_cache[key] = ImageFont.truetype(str(fpath), size)
            return self._font_cache[key]
        except Exception:
            return ImageFont.load_default()

    def _get_icon_font(self, size: int):
        from PIL import ImageFont
        key = f"I_{size}"
        if key in self._font_cache: return self._font_cache[key]
        try:
            from pathlib import Path
            fpath = Path(__file__).resolve().parents[2] / "web" / "utilities" / "lineawesome-webfont.ttf"
            self._font_cache[key] = ImageFont.truetype(str(fpath), size)
            return self._font_cache[key]
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
        img = Image.new("RGB", (self._width, self._height), (0, 0, 0))
        
        # Draw Logo at bottom
        logo = self._get_logo()
        if logo:
            max_h = self._height - 20
            w, h = logo.size
            scale = min(self._width / w, max_h / h)
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            logo_res = logo.resize((nw, nh), Image.LANCZOS).convert("RGB")
            img.paste(logo_res, ((self._width - nw) // 2, self._height - nh))

        draw = ImageDraw.Draw(img)
        CLR_YELLOW = (255, 255, 0)
        CLR_BLACK = (0, 0, 0)
        CLR_WHITE = (255, 255, 255)
        
        font = self._get_font(18)
        txt = label or f"LOADING {percent}%"
        
        tw, th = draw.textbbox((0, 0), txt, font=font)[2:]
        tx, ty = (self._width - tw) // 2, (20 - th) // 2
        
        # Header with progress bar
        bar_w = int((percent / 100.0) * self._width)
        if bar_w > 0:
            draw.rectangle([0, 0, bar_w, 19], fill=CLR_YELLOW)
        
        # Draw text (White on black base, black on yellow bar)
        draw.text((tx, ty), txt, font=font, fill=CLR_WHITE)
        
        if bar_w > 0:
            # Masking for dual color text
            bar_mask = Image.new("L", (self._width, 20), 0)
            ImageDraw.Draw(bar_mask).rectangle([0, 0, bar_w, 19], fill=255)
            
            temp_header = Image.new("RGB", (self._width, 20), CLR_YELLOW)
            ImageDraw.Draw(temp_header).text((tx, ty), txt, font=font, fill=CLR_BLACK)
            img.paste(temp_header, (0, 0), mask=bar_mask)

        self.display(img)

    def render_message(self, text: str, is_error: bool = False):
        img = Image.new("RGB", (self._width, self._height), (0, 0, 0))
        
        # Draw Logo at bottom
        logo = self._get_logo()
        if logo:
            max_h = self._height - 20
            w, h = logo.size
            scale = min(self._width / w, max_h / h)
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            logo_res = logo.resize((nw, nh), Image.LANCZOS).convert("RGB")
            img.paste(logo_res, ((self._width - nw) // 2, self._height - nh))

        draw = ImageDraw.Draw(img)
        CLR_RED = (255, 0, 0)
        CLR_WHITE = (255, 255, 255)
        CLR_BLACK = (0, 0, 0)
        
        header_clr = CLR_RED if is_error else CLR_WHITE
        draw.rectangle([0, 0, self._width, 19], fill=header_clr)
        
        font_h = self._get_font(18)
        header_txt = "SYSTEM MSG" if not is_error else "ERROR"
        tw, th = draw.textbbox((0, 0), header_txt, font=font_h)[2:]
        draw.text(((self._width - tw)//2, (20 - th)//2), header_txt, font=font_h, fill=CLR_BLACK)
        
        self.display(img)

    def render_standard(self, snapshot: dict, structure: dict, server_online: bool):
        img = Image.new("RGB", (self._width, self._height), (0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        CLR_YELLOW = (255, 255, 0)
        CLR_BLACK = (0, 0, 0)
        CLR_WHITE = (255, 255, 255)
        CLR_GREEN = (0, 255, 0)
        CLR_BLUE = (0, 191, 255)
        CLR_RED = (255, 0, 0)

        # 1. Header (Yellow)
        draw.rectangle([0, 0, self._width, 19], fill=CLR_YELLOW)
        f_h = self._get_font(18)
        
        idx = structure.get("identity", {}).get("index", "--")
        draw.text((4, 0), f"#{idx}", font=f_h, fill=CLR_BLACK)
        
        services = structure.get("services", [])
        active_name = "STANDBY"
        for s in services:
            if s.get("running"):
                active_name = s.get("name", "ACTIVE")
                break
        
        if not server_online:
            svc_txt = "OFFLINE"
        else:
            svc_txt = active_name.upper()[:12]
            
        sw, sh = draw.textbbox((0, 0), svc_txt, font=f_h)[2:]
        draw.text(((self._width - sw)//2, (20 - sh)//2), svc_txt, font=f_h, fill=CLR_BLACK)
        
        icon_f = self._get_icon_font(20)
        glyph = "\uf1eb" 
        iw, ih = draw.textbbox((0,0), glyph, font=icon_f)[2:]
        ix = self._width - iw - 4
        draw.text((ix, (20 - ih)//2), glyph, font=icon_f, fill=CLR_BLACK)
        if not server_online:
            draw.line([(ix, 2), (ix+iw, 18)], fill=CLR_RED, width=2)

        # 2. Body
        f_b = self._get_font(18)
        cpu = snapshot.get("cpu", 0)
        temp = snapshot.get("temp", 0)
        
        y = 22
        line_h = 20
        # CPU
        draw.text((4, y), "CPU:", font=f_b, fill=CLR_GREEN)
        cpu_val = f"{cpu:.0f}%" if cpu is not None else "--"
        clr_cpu = CLR_RED if (cpu is not None and cpu > 70) else CLR_BLUE
        draw.text((50, y), cpu_val, font=f_b, fill=clr_cpu)
        
        # VLAN (if active)
        vlan = snapshot.get("active_vlan")
        if vlan is not None:
            v_label = "VL:"
            draw.text((95, y), v_label, font=f_b, fill=CLR_GREEN)
            draw.text((125, y), f"{vlan}", font=f_b, fill=CLR_BLUE)
        
        # TMP
        draw.text((4, y + line_h), "TMP:", font=f_b, fill=CLR_GREEN)
        temp_val = f"{temp:.0f}C" if temp is not None else "--"
        clr_tmp = CLR_RED if (temp is not None and temp > 65) else CLR_BLUE
        draw.text((50, y + line_h), temp_val, font=f_b, fill=clr_tmp)
        
        # NET
        main_nic = snapshot.get("main_nic")
        main_nic_ip = snapshot.get("main_nic_ip")
        
        if main_nic:
            name = (main_nic or "").upper()[:4]
            ip_str = main_nic_ip or "-"
            draw.text((4, y + line_h * 2), f"{name}:", font=f_b, fill=CLR_GREEN)
            draw.text((50, y + line_h * 2), ip_str, font=f_b, fill=CLR_BLUE)
        else:
            # Fallback
            ifaces = snapshot.get("ifaces", [])
            primary = None
            for iface in ifaces:
                ip = iface.get("ip")
                if ip and not ip.startswith("127"):
                    primary = iface
                    break
            
            if primary:
                name = (primary.get("name") or "").upper()[:4]
                ip_str = primary.get("ip") or "-"
                draw.text((4, y + line_h * 2), f"{name}:", font=f_b, fill=CLR_GREEN)
                draw.text((50, y + line_h * 2), ip_str, font=f_b, fill=CLR_BLUE)
            else:
                draw.text((4, y + line_h * 2), "NET:", font=f_b, fill=CLR_GREEN)
                draw.text((50, y + line_h * 2), "-", font=f_b, fill=CLR_BLUE)
        
        self.display(img)
