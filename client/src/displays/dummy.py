from PIL import Image
from .base import DisplayDriver
import logging

LOGGER = logging.getLogger("omiclient.displays.dummy")

class DummyDriver(DisplayDriver):
    def __init__(self, width=128, height=64):
        self._width = width
        self._height = height

    def init(self):
        LOGGER.info(f"DummyDisplay initialized ({self._width}x{self._height})")

    def clear(self):
        LOGGER.debug("DummyDisplay cleared")

    def display(self, image: Image.Image):
        LOGGER.debug(f"DummyDisplay received image: {image.size}")

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height

    def cleanup(self):
        LOGGER.info("DummyDisplay cleanup")
