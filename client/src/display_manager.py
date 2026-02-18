import logging
import threading
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
        self._lock = threading.Lock()

    def init(self):
        LOGGER.info(f"Initializing DisplayManager (Target: {self.driver_name})")
        
        # Try Auto-detection if driver_name is default or explicit
        potential_drivers = []
        if self.driver_name == "ssd1306":
            potential_drivers = ["ssd1306", "uctronics"]
        elif self.driver_name == "uctronics":
            potential_drivers = ["uctronics", "ssd1306"]
        else:
            potential_drivers = [self.driver_name]

        for driver_id in potential_drivers:
            try:
                if driver_id == "ssd1306":
                    from displays.ssd1306 import SSD1306Driver
                    self.driver = SSD1306Driver()
                    self.driver.init()
                    self.driver_name = "ssd1306"
                    LOGGER.info("SSD1306 detected and initialized")
                    return
                elif driver_id == "uctronics":
                    from displays.uctronics import UCTRONICS_RM0004Driver
                    self.driver = UCTRONICS_RM0004Driver()
                    self.driver.init()
                    self.driver_name = "uctronics"
                    LOGGER.info("UCTRONICS RM0004 detected and initialized")
                    return
            except Exception as e:
                LOGGER.debug(f"Driver {driver_id} not available: {e}")

        # Fallback
        LOGGER.warning("No physical display detected, falling back to Dummy")
        self.driver = DummyDriver()
        self.driver.init()
        self.driver_name = "dummy"

    def display(self, image: Image.Image):
        with self._lock:
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
