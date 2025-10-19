# test_ui_cycle.py


import time
from pathlib import Path

# ---- UI ----
from ui import (
    LoadingUI,
    ErrorUI,
    UISLOADINGProceess,
    EstandardUse,
    UIOFF,
)

# ---- Heartbeat (opcional: se arranca si está disponible) ----
STRUCTURE_PATH = Path(__file__).resolve().parent / "agent_pi" / "data" / "structure.json"
APP_VERSION = "0.0.1"

hb_running = False
def maybe_start_heartbeat():
    """Arranca heartbeat si el módulo está disponible y garantiza el JSON."""
    global hb_running
    try:
        # intentar crear/validar el JSON si está disponible
        try:
            from client.src.jsonconfig import ensure_config
        except Exception:
            try:
                from client.src.jsonconfig import ensure_config  # por si está en agent_pi/data/
            except Exception:
                ensure_config = None
        if ensure_config:
            ensure_config(STRUCTURE_PATH, version=APP_VERSION)

        from heartbeat import starthb, snapshot  # alias start/stop/snapshot
        starthb(STRUCTURE_PATH)
        hb_running = True

        # pequeña espera para que llene primeras métricas
        time.sleep(3)

        # devolver función que llama al snapshot real
        def _snap():
            return snapshot()
        return _snap
    except Exception:
        # fallback: snapshot dummy
        def _snap():
            return {"cpu": 37.5, "temp": 51.2, "ips": ["192.168.0.52"]}
        return _snap

def maybe_stop_heartbeat():
    if hb_running:
        try:
            from heartbeat import stophb
            stophb()
        except Exception:
            pass

get_snapshot = maybe_start_heartbeat()

# ---- Demos ----
def demo_loading(label="Cargando módulos"):
    for p in range(0, 101, 10):
        LoadingUI(p, label)
        time.sleep(0.12)

def demo_estandar(server_online=True):
    EstandardUse(get_snapshot(), server_online, json_path=STRUCTURE_PATH)
    time.sleep(2)

def demo_error(msg="ERROR"):
    ErrorUI(msg)
    time.sleep(2)

def demo_shutdown(label="Apagando"):
    for p in range(0, 101, 20):
        UIShutdownProceess(p, label)
        time.sleep(0.15)
    UIOFF()
    time.sleep(0.6)

def main():
    print("[demo] Ciclo de pruebas UI. Ctrl+C para salir.")
    try:
        while True:
            print("[demo] LoadingUI…")
            demo_loading("Inicializando")

            print("[demo] EstandardUse (online)…")
            demo_estandar(server_online=True)

            print("[demo] EstandardUse (offline)…")
            demo_estandar(server_online=False)

            print("[demo] ErrorUI…")
            demo_error("ERROR")

            print("[demo] Shutdown + OFF…")
            demo_shutdown("Shutdown")
    except KeyboardInterrupt:
        print("\n[demo] OFF y salida.")
        UIOFF()
        maybe_stop_heartbeat()

if __name__ == "__main__":
    main()
