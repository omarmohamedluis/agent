"""I2C protocols for the SSD1306 OLED and UCTRONICS RM0004 rack display.

The RM0004 register protocol and 700 us burst pacing are retained from the
previous OMI driver. A failed transfer raises immediately instead of retrying
forever on the display thread.
"""
from __future__ import annotations

import time
from typing import Any


class I2CDisplay:
    name = "i2c"
    width = 128
    height = 64

    def __init__(self, port: int = 1, address: int = 0, *, bus_factory=None):
        self.port, self.address = port, address
        self.bus: Any = None
        self.bus_factory = bus_factory

    def open(self):
        if self.bus_factory is None:
            from smbus2 import SMBus
            self.bus_factory = SMBus
        try:
            self.bus = self.bus_factory(self.port)
            self.bus.write_quick(self.address)
        except Exception:
            self.close()
            raise

    def close(self):
        if self.bus is not None:
            bus, self.bus = self.bus, None
            bus.close()


class SSD1306(I2CDisplay):
    name = "ssd1306"

    def __init__(self, port=1, address=0x3C, **kwargs):
        super().__init__(port, address, **kwargs)

    def open(self):
        super().open()
        try:
            # 128x64, horizontal addressing, internal charge pump, normal display.
            for command in (0xAE, 0xD5, 0x80, 0xA8, 0x3F, 0xD3, 0x00, 0x40,
                            0x8D, 0x14, 0x20, 0x00, 0xA1, 0xC8, 0xDA, 0x12,
                            0x81, 0xCF, 0xD9, 0xF1, 0xDB, 0x40, 0xA4, 0xA6, 0xAF):
                self.bus.write_byte_data(self.address, 0x00, command)
        except Exception:
            self.close()
            raise

    def display(self, image):
        image = image.resize((self.width, self.height)).convert("1", dither=0)
        pixels = image.load()
        for command in (0x21, 0, self.width - 1, 0x22, 0, 7):
            self.bus.write_byte_data(self.address, 0x00, command)
        data = [sum((1 << bit) if pixels[x, page * 8 + bit] else 0 for bit in range(8))
                for page in range(8) for x in range(self.width)]
        for offset in range(0, len(data), 32):
            self.bus.write_i2c_block_data(self.address, 0x40, data[offset:offset + 32])


class UCTRONICS(I2CDisplay):
    name = "uctronics"
    width, height = 160, 80

    def __init__(self, port=1, address=0x18, *, message_factory=None, **kwargs):
        super().__init__(port, address, **kwargs)
        self.message_factory = message_factory

    def _command(self, command, high, low):
        self.bus.write_i2c_block_data(self.address, command, [high, low])

    def display(self, image):
        if self.message_factory is None:
            from smbus2 import i2c_msg
            self.message_factory = i2c_msg.write
        image = image.resize((self.width, self.height)).convert("RGB")
        data = bytearray()
        for red, green, blue in image.getdata():
            value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
            data.extend((value >> 8, value & 0xFF))
        self._command(0x2A, 0, 159)
        self._command(0x2B, 24, 103)
        self._command(0x2C, 0, 0)
        self._command(0x03, 0, 1)
        self._command(0x01, 0, 1)
        try:
            for offset in range(0, len(data), 160):
                self.bus.i2c_rdwr(self.message_factory(self.address, data[offset:offset + 160]))
                time.sleep(0.0007)
        finally:
            self._command(0x01, 0, 0)
        self._command(0x03, 0, 1)
