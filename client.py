# agent_listener.py
import socket, json
from pathlib import Path
import time

from jsonconfig import ensure_config
from heartbeat import UPDATEHB
# Ajusta este import según tu layout: si tu módulo es "ui/ui.py", usa: from ui.ui import ...
from ui import EstandardUse, LoadingUI, ErrorUI, UIShutdownProceess, UIOFF

SOFVERSION = "0.0.1"
BCAST_PORT = 37020
STRUCTURE_PATH = Path("agent_pi/data/structure.json")

LoadingUI(0,"INICIANDO")

time.sleep(1)

LoadingUI(30,"LEYENDO")

# Crea/valida el JSON
CFG = ensure_config(STRUCTURE_PATH, version=SOFVERSION)
snap = UPDATEHB(STRUCTURE_PATH)

LoadingUI(40,"CARGADO")



def listen_and_reply():
    # 1) Actualiza HB al inicio y pinta UI en modo "sin server" (wifi tachado)
    snap = UPDATEHB(STRUCTURE_PATH)
    try:
        LoadingUI(50,"ESPERANDO SERVER")
    except Exception:
        pass

    # 2) Socket para escuchar broadcast
    s_listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s_listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s_listen.bind(("", BCAST_PORT))
    s_listen.settimeout(0.5)  # para poder Ctrl+C
    print(f"[agent] escuchando broadcast en :{BCAST_PORT}")

    # 3) Socket para responder unicast
    s_reply = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        while True:
            try:
                data, addr = s_listen.recvfrom(4096)
            except socket.timeout:
                continue

            msg = data.decode("utf-8", "ignore")
            print(f"✓ broadcast recibido de {addr[0]}:{addr[1]} → {msg}")

            try:
                payload = json.loads(msg)
                if payload.get("type") == "DISCOVER":
                    server_ip  = payload.get("server_ip")
                    reply_port = int(payload.get("reply_port", 0))

                    # 4) Actualiza HB justo antes de responder
                    snap = UPDATEHB(STRUCTURE_PATH)

                    reply = {
                        "serial":  CFG["identity"]["serial"] or "pi-unknown",
                        "index":   CFG["identity"]["index"],
                        "name":    CFG["identity"]["name"],
                        "host":    CFG["identity"].get("host") or "unknown-host",
                        "version": CFG["version"]["version"],
                        "services": CFG["services"],  # lista tal cual
                        "heartbeat": {
                            "cpu":  snap["cpu"],
                            "temp": snap["temp"]
                        }
                    }

                    s_reply.sendto(json.dumps(reply).encode("utf-8"), (server_ip, reply_port))
                    print(f"→ estructura enviada a {server_ip}:{reply_port}")

                    # 5) Refresca UI como "online"
                    try:
                        EstandardUse(snap, server_online=True, json_path=STRUCTURE_PATH)
                    except Exception:
                        pass

            except Exception as e:
                print(f"[agent] error parseando/enviando: {e}")


    except KeyboardInterrupt:
        print("\n[agent] detenido por usuario.")
    finally:
        try: s_listen.close()
        except Exception: pass
        try: s_reply.close()
        except Exception: pass
        try: UIOFF()
        except Exception: pass

if __name__ == "__main__":
    listen_and_reply()




# from AppHandler import start_service, stop_service, get_active_service

# # arrancar
# start_service("standby")     # servicios/standby/service.py

# # cambiar a otro
# start_service("MIDI")        # para standby y lanza MIDI

# # consultar
# print("Activo:", get_active_service())

# # parar
# stop_service()
