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
