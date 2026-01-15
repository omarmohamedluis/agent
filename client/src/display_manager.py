import logging
from typing import Optional
from PIL import Image
from displays.base import DisplayDriver
from displays.ssd1306 import SSD1306Driver
from displays.dummy import DummyDriver

LOGGER = logging.getLogger("omiclient.display_manager")

class DisplayManager:
    def __init__(self, driver_name: str = "ssd1306"):
        self.driver_name = driver_name
        self.driver: Optional[DisplayDriver] = None

    def init(self):
        LOGGER.info(f"Initializing DisplayManager with driver: {self.driver_name}")
        try:
            if self.driver_name == "ssd1306":
                self.driver = SSD1306Driver()
                self.driver.init()
            else:
                LOGGER.warning(f"Unknown driver '{self.driver_name}', falling back to Dummy")
                self.driver = DummyDriver()
                self.driver.init()
        except Exception as e:
            LOGGER.error(f"Failed to initialize driver '{self.driver_name}': {e}")
            LOGGER.info("Falling back to DummyDriver")
            self.driver = DummyDriver()
            self.driver.init()

    def display(self, image: Image.Image):
        if self.driver:
            self.driver.display(image)

    def clear(self):
        if self.driver:
            self.driver.clear()

    @property
    def width(self) -> int:
        return self.driver.width() if self.driver else 128

    @property
    def height(self) -> int:
        return self.driver.height() if self.driver else 64

    def cleanup(self):
        if self.driver:
            self.driver.cleanup()
