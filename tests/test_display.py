import subprocess
import sys
import threading
import time

import pytest

from omi.display import DisplayManager
from omi.display.drivers import SSD1306, UCTRONICS


class Bus:
    def __init__(self, port=1):
        self.commands = []
        self.blocks = []
        self.transfers = []
        self.closed = False

    def write_quick(self, address):
        self.address = address

    def write_byte_data(self, *args):
        self.commands.append(args)

    def write_i2c_block_data(self, *args):
        self.blocks.append(args)

    def i2c_rdwr(self, message):
        self.transfers.append(message)

    def close(self):
        self.closed = True


def test_import_without_optional_hardware_modules():
    result = subprocess.run([sys.executable, "-c", "import sys; import omi.display; assert 'smbus2' not in sys.modules; assert 'PIL' not in sys.modules"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_ssd1306_page_layout_and_bounded_blocks():
    Image = pytest.importorskip("PIL.Image")
    bus = Bus()
    driver = SSD1306(bus_factory=lambda port: bus)
    driver.open()
    image = Image.new("1", (128, 64))
    image.putpixel((0, 0), 1)
    image.putpixel((1, 7), 1)
    image.putpixel((2, 8), 1)
    driver.display(image)
    data = [value for _, control, block in bus.blocks for value in block]
    assert len(data) == 1024
    assert data[:3] == [1, 128, 0]
    assert data[130] == 1
    assert all(control == 0x40 and len(block) <= 32 for _, control, block in bus.blocks)
    driver.close()
    assert bus.closed


def test_uctronics_registers_rgb565_and_no_retry_after_bus_failure():
    Image = pytest.importorskip("PIL.Image")
    bus = Bus()
    driver = UCTRONICS(bus_factory=lambda port: bus, message_factory=lambda address, data: bytes(data))
    driver.open()
    driver.display(Image.new("RGB", (160, 80), (255, 255, 0)))
    assert bus.blocks[:4] == [(0x18, 0x2A, [0, 159]), (0x18, 0x2B, [24, 103]),
                              (0x18, 0x2C, [0, 0]), (0x18, 0x03, [0, 1])]
    assert len(bus.transfers) == 160
    assert bus.transfers[0] == bytes([0xFF, 0xE0] * 80)
    attempts = 0

    def fail(message):
        nonlocal attempts
        attempts += 1
        raise OSError("I2C unplugged")

    bus.i2c_rdwr = fail
    with pytest.raises(OSError, match="unplugged"):
        driver.display(Image.new("RGB", (160, 80)))
    assert attempts == 1
    assert bus.blocks[-1] == (0x18, 0x01, [0, 0])


def test_common_yellow_header_and_status():
    pytest.importorskip("PIL")
    from omi.display.presentation import render_status
    status = {"device_id": 3, "name": "Pi escenario", "connected": False,
              "state": {"health": "waiting", "detail": "MIDI desconectado"}}
    for width, height, header in ((128, 64, 16), (160, 80, 20)):
        image = render_status(status, width, height)
        assert image.size == (width, height)
        assert image.getpixel((width - 1, header - 1)) == (255, 255, 0)
        assert image.getpixel((width - 1, header)) == (0, 0, 0)


def test_detection_fallback_and_render_failure_isolated(monkeypatch):
    pytest.importorskip("PIL")
    monkeypatch.setenv("OMI_DISPLAY", "auto")
    finished = threading.Event()

    class Missing:
        name = "missing"

        def open(self):
            raise OSError("not installed")

        def close(self):
            pass

    class Unplugged:
        name, width, height = "test", 128, 64

        def open(self):
            pass

        def display(self, image):
            raise OSError("screen disconnected")

        def close(self):
            finished.set()

    manager = DisplayManager(factories=[Missing, Unplugged], interval=0.01)
    manager.start()
    manager.render({"name": "working agent"})
    assert finished.wait(2)
    manager.close()
    assert "screen disconnected" in manager.status()["error"]


def test_blocked_display_never_blocks_status_submission(monkeypatch):
    pytest.importorskip("PIL")
    monkeypatch.setenv("OMI_DISPLAY", "auto")
    entered, release = threading.Event(), threading.Event()

    class Slow:
        name, width, height = "slow", 128, 64

        def open(self):
            pass

        def display(self, image):
            entered.set()
            release.wait(2)

        def close(self):
            pass

    manager = DisplayManager(factories=[Slow], interval=0.01)
    manager.start()
    assert entered.wait(2)
    started = time.monotonic()
    for value in range(1000):
        manager.render({"value": value})
    assert time.monotonic() - started < 0.5
    assert manager._latest == {"value": 999}
    release.set()
    manager.close()
