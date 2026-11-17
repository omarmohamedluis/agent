"""One presentation for both displays; the SSD1306 maps it to monochrome."""
from pathlib import Path

YELLOW = (255, 255, 0)
ASSETS = Path(__file__).with_name("assets")


def render_status(status: dict, width: int, height: int, *, page: int = 0):
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (width, height), "black")
    draw = ImageDraw.Draw(image)
    header_height = 16 if height == 64 else 20
    try:
        font = ImageFont.truetype(str(ASSETS / "PixelOperator.ttf"), 16 if width == 128 else 18)
    except OSError:
        font = ImageFont.load_default()
    state = status.get("state") or {}
    operation = state.get("operation") or {}
    info = state.get("service_info") or {}
    network = state.get("network") or {}
    health = str(state.get("health", "standby"))
    service = str(info.get("service") or status.get("service") or health).upper()
    identity = status.get("device_id")
    header = f"#{identity if identity is not None else '--'} {service}"
    draw.rectangle((0, 0, width - 1, header_height - 1), fill=YELLOW)

    def fitted(text):
        text = str(text).replace("\n", " ")
        while text and draw.textlength(text, font=font) > width - 6:
            text = text[:-1]
        return text

    draw.text((3, 0), fitted(header), fill="black", font=font)
    connection = "CENTRAL OK" if status.get("connected") else "AUTONOMO"
    if status.get("discrepancy"):
        connection = "DISCREPANCIA"
    if operation.get("status") in ("accepted", "running"):
        lines = (operation.get("phase", ""), state.get("detail", ""), connection)
    elif health == "error" or status.get("discrepancy") or page % 2 == 0:
        address = network.get("address") or network.get("ip") or next(iter(network.get("addresses", [])), "") or status.get("management_ip") or ""
        lines = (status.get("name") or status.get("hostname") or "OMI Agent",
                 state.get("detail") or address or health, connection)
    else:
        metrics = status.get("display_metrics") or {}
        temperature = metrics.get("temperature")
        cpu = metrics.get("cpu")
        metrics_text = f"CPU {cpu:.0f}%" if cpu is not None else "CPU --"
        metrics_text += f" {temperature:.0f}C" if temperature is not None else " --C"
        address = status.get("management_ip") or network.get("address") or next(iter(network.get("addresses", [])), "") or "Sin IP"
        vlan = network.get("vlan")
        address_text = f"V{vlan} {address}" if vlan is not None else address
        lines = (status.get("preset_name") or "STANDBY", address_text, metrics_text)
    line_height = (height - header_height) // 3
    for index, line in enumerate(lines):
        draw.text((3, header_height + index * line_height), fitted(line), fill="white", font=font)
    return image
