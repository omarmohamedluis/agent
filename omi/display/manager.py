from __future__ import annotations

import copy
import logging
import os
import threading
import time

from .drivers import SSD1306, UCTRONICS

LOGGER = logging.getLogger(__name__)


class DisplayManager:
    """Detect and render on a daemon thread, with a single latest-state slot.

    A missing display is normal. A transfer failure disables it until restart;
    neither detection nor I2C writes run in an event loop or MIDI callback.
    """

    def __init__(self, *, factories=None, interval: float = 0.5):
        self._factories = factories
        self.interval = interval
        self.driver = None
        self.error: str | None = None
        self._latest: dict = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        if os.environ.get("OMI_DISPLAY", "auto").lower() in ("none", "off", "0"):
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="omi-display", daemon=True)
        self._thread.start()

    def render(self, status: dict):
        with self._lock:
            self._latest = copy.deepcopy(status)
        self._wake.set()

    def _detect(self):
        preferred = os.environ.get("OMI_DISPLAY", "auto").lower()
        factories = self._factories
        if factories is None:
            available = {"ssd1306": SSD1306, "uctronics": UCTRONICS}
            factories = [available[preferred]] if preferred in available else [UCTRONICS, SSD1306]
        for factory in factories:
            driver = None
            try:
                driver = factory()
                driver.open()
                self.driver = driver
                self.error = None
                LOGGER.info("Display detected: %s", driver.name)
                return
            except Exception as error:
                if driver:
                    try:
                        driver.close()
                    except Exception:
                        pass
                self.error = str(error)
        LOGGER.info("No optional display detected (%s)", self.error or "none configured")

    def _run(self):
        try:
            self._detect()
            if self.driver is None:
                return
            from .presentation import render_status
            from .metrics import LocalMetrics
            metrics = LocalMetrics()
            while not self._stop.is_set():
                with self._lock:
                    status = dict(self._latest)
                status["display_metrics"] = metrics.sample()
                self.driver.display(render_status(status, self.driver.width, self.driver.height,
                                                  page=int(time.monotonic() // 3)))
                self._wake.clear()
                self._wake.wait(self.interval)
                # Limit rendering even if status producers continually wake us.
                if self._wake.is_set():
                    self._stop.wait(self.interval)
        except Exception as error:
            self.error = str(error)
            LOGGER.warning("Optional display disabled: %s", error)
        finally:
            if self.driver is not None:
                try:
                    self.driver.close()
                except Exception:
                    LOGGER.debug("Display close failed", exc_info=True)
                self.driver = None

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def status(self):
        return {"driver": self.driver.name if self.driver else None, "error": self.error}
