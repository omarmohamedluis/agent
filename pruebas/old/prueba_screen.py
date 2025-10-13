from luma.core.interface.serial import i2c
from luma.oled.device import ssd1306
from PIL import Image, ImageDraw
import time

# --- Config ---
OLED_W, OLED_H = 128, 64
BAR_H = 64  # altura del rectángulo blanco

# --- Inicializar dispositivo ---
device = ssd1306(i2c(port=1, address=0x3C), width=OLED_W, height=OLED_H, rotate=0)

# --- Crear imagen negra ---
img = Image.new("1", (OLED_W, OLED_H), 0)
draw = ImageDraw.Draw(img)

# --- Dibujar franja blanca ---
draw.rectangle((0, 0, OLED_W - 1, BAR_H - 1), fill=1)

# --- Mostrar en pantalla ---
device.display(img)
print("⏳ Manteniendo imagen... pulsa Ctrl+C para salir.")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n👋 Saliendo, pantalla se mantiene con la última imagen.")
