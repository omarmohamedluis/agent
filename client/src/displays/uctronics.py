import time
import logging
from PIL import Image
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
            chunk_size = min(length - offset, BURST_MAX_LENGTH)
            msg = i2c_msg.write(self._address, data[offset:offset + chunk_size])
            self._bus.i2c_rdwr(msg)
            offset += chunk_size
            time.sleep(0.0007) # From C code usleep(700)

    def display(self, image: Image.Image):
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
