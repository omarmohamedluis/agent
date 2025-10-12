#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
fill_rpi_config.py
- Solo requiere: --version
- La ruta del JSON está fija en CONFIG_PATH (al lado del script).
- Si no existe el JSON, se crea con una plantilla mínima.
- Reglas:
  * version.version = argumento --version
  * identity.index: si vacío -> 99 (si tiene valor, no tocar)
  * identity.name: si vacío -> "NONE" (si tiene valor, no tocar)
  * identity.serial: si vacío -> poner serial sistema; si poblado y NO coincide -> corregir y dejar log
  * server: preguntar por broadcast al puerto 37260 (SERVER_REPLY) y rellenar api_base/address si hay respuesta
  * network: SIEMPRE regenerar "interfaces" con lo detectado (borrar lo anterior)
              (solo incluir interfaces UP con IPv4 asignada)
  * services: si hay >1 enabled -> dejar SOLO standby.enabled = true; si solo hay 1 enabled, no tocar
"""

import argparse, json, os, sys, socket, time, uuid, subprocess, re, datetime

# ---------- Rutas fijas (editables) ----------
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH  = os.path.join(SCRIPT_DIR, "PiInfo.json")
LOG_DIR      = os.path.join(SCRIPT_DIR, "logs")

# ---------- Descubrimiento (broadcast) ----------
BROADCAST_PORT = 37260
BROADCAST_ADDR = "255.255.255.255"
UDP_TIMEOUT_S  = 1.0
UDP_RETRIES    = 3

# ---------- Utilidades de fichero ----------
def ensure_dir(p):
    os.makedirs(p, exist_ok=True)

def write_json(path, data):
    ensure_dir(os.path.dirname(path) or ".")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def read_or_create_config(path):
    if not os.path.exists(path):
        # Plantilla mínima
        tmpl = {
            "version": {"version": ""},
            "identity": {"index": 99, "name": "NONE", "serial": ""},
            "server": {"api_base": "", "address": ""},
            "network": {"interfaces": []},
            "services": [
                {"name": "standby", "enabled": True},
                {"name": "MIDI", "enabled": False},
                {"name": "companion-satellite", "enabled": False}
            ],
            "config": {
                "heartbeat_interval_s": 5,
                "log_level": "info",
                "topics": ["status", "metrics", "events"]
            }
        }
        write_json(path, tmpl)
        return tmpl
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def log_serial_mismatch(old_serial, new_serial):
    ensure_dir(LOG_DIR)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(LOG_DIR, f"serial_mismatch-{ts}.log")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"[{ts}] Serial en JSON: {old_serial}  !=  Serial del sistema: {new_serial}\n")
        f.write("Se ha actualizado el JSON para reflejar el serial correcto.\n")

# ---------- Identidad/RPi ----------
def get_rpi_serial():
    # /proc/cpuinfo
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.lower().startswith("serial"):
                    return line.split(":")[1].strip()
    except Exception:
        pass
    # Fallback device-tree
    try:
        out = subprocess.check_output(["cat", "/proc/device-tree/serial-number"], stderr=subprocess.DEVNULL)
        sn = out.decode(errors="ignore").strip()
        if sn:
            return sn
    except Exception:
        pass
    return "UNKNOWN"

def get_hostname():
    try:
        return socket.gethostname()
    except:
        return "UNKNOWN"

# ---------- Red local ----------
def iface_is_up(name: str) -> bool:
    try:
        with open(f"/sys/class/net/{name}/operstate", "r") as f:
            return f.read().strip().lower() == "up"
    except Exception:
        return False

def parse_ipv4_of(iface):
    try:
        out = subprocess.check_output(["ip", "-4", "addr", "show", "dev", iface], stderr=subprocess.DEVNULL).decode()
        m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)\/(\d+)", out)
        if not m:
            return None, None
        ip = m.group(1)
        cidr = int(m.group(2))
        mask_bits = (0xffffffff >> (32 - cidr)) << (32 - cidr)
        netmask = ".".join(str((mask_bits >> (i * 8)) & 0xff) for i in [3,2,1,0])
        return ip, netmask
    except Exception:
        return None, None

def get_default_gateway():
    try:
        out = subprocess.check_output(["ip", "route"], stderr=subprocess.DEVNULL).decode()
        # "default via 192.168.40.254 dev eth0 ..."
        m = re.search(r"^default\s+via\s+(\d+\.\d+\.\d+\.\d+)\s+dev\s+(\S+)", out, re.MULTILINE)
        if m:
            return m.group(1), m.group(2)  # (gateway_ip, iface_name)
    except Exception:
        pass
    return None, None

def get_dns_servers():
    servers = []
    try:
        with open("/etc/resolv.conf", "r") as f:
            for line in f:
                m = re.match(r"nameserver\s+(\d+\.\d+\.\d+\.\d+)", line.strip())
                if m:
                    servers.append(m.group(1))
    except Exception:
        pass
    return servers or ["1.1.1.1", "8.8.8.8"]

def detect_interfaces():
    """
    Devuelve solo interfaces que:
      - estén UP (operstate == 'up'), y
      - tengan IPv4 asignada.
    Ignora loopback, down, o sin IP.
    """
    sys_class_net = "/sys/class/net"
    ifaces = []

    try:
        all_names = [n for n in os.listdir(sys_class_net) if n != "lo"]
    except Exception:
        all_names = []

    for name in all_names:
        if not iface_is_up(name):
            continue  # ignora interfaces no conectadas / down
        ip, netmask = parse_ipv4_of(name)
        if not ip:  # ignora si no tiene IPv4
            continue

        # MAC
        mac = ""
        try:
            with open(os.path.join(sys_class_net, name, "address"), "r") as f:
                mac = f.read().strip().upper()
        except Exception:
            pass

        ifaces.append({
            "name": name,
            "mac": mac,
            "mode": "dhcp",  # no inferimos static aquí
            "ipv4": {
                "address": ip,
                "netmask": netmask,
                "gateway": "",
                "dns": []
            }
        })

    # gateway/dns globales
    gw_ip, gw_iface = get_default_gateway()
    dns = get_dns_servers()

    # asigna gateway/dns solo a la interfaz del default route (si existe en la lista)
    if gw_ip and gw_iface:
        for itf in ifaces:
            if itf["name"] == gw_iface:
                itf["ipv4"]["gateway"] = gw_ip
                itf["ipv4"]["dns"] = dns

    # opcional: añade DNS a las demás que queden sin dns
    for itf in ifaces:
        if not itf["ipv4"]["dns"]:
            itf["ipv4"]["dns"] = dns

    return ifaces

# ---------- Broadcast ----------
def udp_broadcast(message: dict, port=BROADCAST_PORT, retries=3, timeout=1.0):
    payload = json.dumps(message).encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(timeout)
        for _ in range(retries):
            try:
                s.sendto(payload, (BROADCAST_ADDR, port))
                data, addr = s.recvfrom(65535)
                try:
                    resp = json.loads(data.decode("utf-8", errors="ignore"))
                except Exception:
                    resp = None
                if isinstance(resp, dict):
                    resp["_from_ip"] = addr[0]
                    return resp
            except socket.timeout:
                continue
            except Exception:
                continue
    return None

def discover_server_via_broadcast(serial: str):
    base = {
        "msg_id": str(uuid.uuid4()),
        "ts": datetime.datetime.utcnow().isoformat() + "Z",
        "proto": 1
    }
    # 1) AGENT_QUERY
    q = dict(base)
    q["type"] = "AGENT_QUERY"
    q["identity"] = {"serial": serial, "hostname": get_hostname()}
    q["need"] = ["api_base", "ports"]

    resp = udp_broadcast(q)
    if isinstance(resp, dict) and resp.get("type") in ("SERVER_REPLY", "SERVER_AD"):
        server = resp.get("server", {})
        api_ip   = server.get("ip") or resp.get("_from_ip")
        api_port = server.get("api_port", 8443)
        api_base = server.get("api_base") or (f"https://{api_ip}:{api_port}" if api_ip else "")
        return {"ip": api_ip or "", "api_port": api_port, "api_base": api_base}

    # 2) AGENT_AD
    a = dict(base)
    a["type"] = "AGENT_AD"
    a["identity"] = {"serial": serial, "hostname": get_hostname()}
    a["observed"] = {"agent": "unknown", "ui": "unknown"}

    resp = udp_broadcast(a)
    if isinstance(resp, dict) and resp.get("type") in ("SERVER_REPLY", "SERVER_AD"):
        server = resp.get("server", {})
        api_ip   = server.get("ip") or resp.get("_from_ip")
        api_port = server.get("api_port", 8443)
        api_base = server.get("api_base") or (f"https://{api_ip}:{api_port}" if api_ip else "")
        return {"ip": api_ip or "", "api_port": api_port, "api_base": api_base}

    return None

# ---------- Normalizadores ----------
def normalize_version(conf, version_str):
    conf.setdefault("version", {})["version"] = version_str

def normalize_identity(conf):
    ident = conf.setdefault("identity", {})
    if ident.get("index") in (None, "", []):
        ident["index"] = 99
    if not ident.get("name"):
        ident["name"] = "NONE"

    sys_serial = get_rpi_serial()
    json_serial = ident.get("serial", "")
    if not json_serial:
        ident["serial"] = sys_serial
    elif json_serial != sys_serial:
        log_serial_mismatch(json_serial, sys_serial)
        ident["serial"] = sys_serial

def normalize_server(conf, serial):
    srv = conf.setdefault("server", {})
    info = discover_server_via_broadcast(serial)
    if info:
        srv["api_base"] = info.get("api_base", "")
        srv["address"]  = info.get("ip", "")
    else:
        srv.setdefault("api_base", srv.get("api_base", ""))
        srv.setdefault("address",  srv.get("address", ""))

def normalize_network(conf):
    net = conf.setdefault("network", {})
    net["interfaces"] = detect_interfaces()  # sobrescribe SIEMPRE (solo UP + con IPv4)

def normalize_services(conf):
    services = conf.get("services", [])
    enabled_count = sum(1 for s in services if s.get("enabled") is True)
    if enabled_count > 1:
        for s in services:
            s["enabled"] = (s.get("name") == "standby")

# ---------- Main ----------
def main():
    ap = argparse.ArgumentParser(description="Rellena el JSON de configuración de la Raspberry Pi (ruta fija en el script).")
    ap.add_argument("--version", required=True, help="Versión que se inyecta en version.version.")
    args = ap.parse_args()

    conf = read_or_create_config(CONFIG_PATH)

    normalize_version(conf, args.version)
    normalize_identity(conf)
    serial = conf.get("identity", {}).get("serial", "")
    normalize_server(conf, serial)
    normalize_network(conf)
    normalize_services(conf)

    write_json(CONFIG_PATH, conf)

    # Resumen
    print(f"OK → escrito: {CONFIG_PATH}")
    print(f"  version.version = {conf['version'].get('version','')}")
    print(f"  identity.index  = {conf['identity'].get('index')}")
    print(f"  identity.name   = {conf['identity'].get('name')}")
    print(f"  identity.serial = {conf['identity'].get('serial')}")
    print(f"  server.address  = {conf['server'].get('address','')}")
    print(f"  server.api_base = {conf['server'].get('api_base','')}")
    print(f"  interfaces      = {len(conf.get('network',{}).get('interfaces',[]))}")
    enabled = [s['name'] for s in conf.get('services',[]) if s.get('enabled') is True]
    print(f"  services enabled = {enabled}")

if __name__ == "__main__":
    main()
