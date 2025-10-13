# agent_listener.py
import socket, json
from pathlib import Path

from jsonconfig import ensure_config
from heartbeat import starthb, stophb, snapshothb  # <- añadimos snapshot

SOFVERSION = "0.0.1"
BCAST_PORT = 37020
STRUCTURE_PATH = Path("agent_pi/data/structure.json")

# Crea/valida el JSON y arranca el heartbeat (en hilo)
CFG = ensure_config(STRUCTURE_PATH, version=SOFVERSION)
starthb(STRUCTURE_PATH)

def listen_and_reply():
    # Socket para escuchar broadcast
    s_listen = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s_listen.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s_listen.bind(("", BCAST_PORT))
    print(f"[agent] escuchando broadcast en :{BCAST_PORT}")

    # Socket para responder unicast
    s_reply = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    while True:
        data, addr = s_listen.recvfrom(4096)
        msg = data.decode("utf-8", "ignore")
        print(f"✓ broadcast recibido de {addr[0]}:{addr[1]} → {msg}")

        try:
            payload = json.loads(msg)
            if payload.get("type") == "DISCOVER":
                server_ip  = payload.get("server_ip")
                reply_port = int(payload.get("reply_port", 0))

                hb = snapshothb()  # {"cpu":..., "temp":..., "ips":[...]}

                reply = {
                    "type": "STRUCTURE",
                    "serial": CFG["identity"]["serial"] or "pi-unknown",
                    "index": CFG["identity"]["index"],
                    "name":  CFG["identity"]["name"],
                    "version": CFG["version"]["version"],
                    "services": CFG["services"],             # lista tal cual
                    "heartbeat": {
                        "cpu": hb["cpu"],                    # % (float o None)
                        "temp": hb["temp"],                  # ºC (float o None)
                        "ip": hb["ips"]                      # array de IPs actuales
                    }
                }

                s_reply.sendto(json.dumps(reply).encode("utf-8"), (server_ip, reply_port))
                print(f"→ estructura enviada a {server_ip}:{reply_port}")
        except Exception as e:
            print(f"[agent] error parseando/enviando: {e}")

if __name__ == "__main__":
    try:
        listen_and_reply()
    except KeyboardInterrupt:
        stophb()
        print("\n[agent] detenido.")
