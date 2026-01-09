# OMI Agent Client

The OMI Agent Client is a Python-based application that runs on a Raspberry Pi (or similar Linux device). It acts as a bridge between local services (like MIDI) and a central server, providing a web-based dashboard for control and monitoring.

## Architecture

The client is built with **FastAPI** and follows a modular architecture:

-   **`client.py`**: The main entry point. Starts the Web Server, OLED UI, and background tasks.
-   **`core/`**: Core logic modules.
    -   `service_manager.py`: Manages independent service processes (start/stop).
    -   `display_manager.py`: Abstraction layer for OLED displays (supports SSD1306, Dummy).
    -   `system.py`: Provides system statistics (CPU, Temp, Network).
-   **`src/`**: Legacy modules (gradually being migrated/refactored).
    -   `NetComHandler.py`: Handles communication with the central server.
    -   `heartbeat.py`: Monitors system health.
-   **`servicios/`**: Independent services managed by the client.
    -   `MIDI/`: MIDI-to-OSC bridge service.
    -   `servicios.json`: Configuration registry for services.

## Web Dashboard

The client exposes a web dashboard at `http://<device-ip>:8000`.

-   **Dashboard**: View system status (CPU, Temp, IP) and control services.
-   **Settings**: Configure network settings (Static IP/DHCP) and reboot/shutdown the device.
-   **Service UI**: When a service (like MIDI) is active, its web interface is embedded in the dashboard.

## Usage

1.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
    *(Note: Requires `fastapi`, `uvicorn`, `jinja2`, `luma.oled`, `psutil`, `netifaces`, `mido`, `python-rtmidi`, `python-osc`)*

2.  **Run the Client**:
    ```bash
    python3 client.py
    ```

## Services

Services are defined in `client/servicios/servicios.json`. Each service is a standalone process that can be started or stopped via the dashboard.

### MIDI Service
Translates MIDI input events to OSC messages.
-   **Config**: `client/servicios/MIDI/OMIMIDI_map.json`
-   **Web UI**: Port 9001 (default)

## Development

-   **Static Files**: `client/static/` (CSS) and `client/utilities/` (Assets).
-   **Templates**: `client/templates/` (Jinja2 HTML).
