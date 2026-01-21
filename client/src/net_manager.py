#!/usr/bin/env python3
"""
Gestor de Red (NetManager).
Se encarga de configurar las interfaces de red (IPs, Máscaras, Gateways, VLANs)
utilizando NetworkManager (nmcli) basándose en la configuración de `structure.json`.
"""
import json
import os
import sys
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Configuración de Rutas
BASE_DIR = Path(__file__).resolve().parents[1]
# Configuración de Logging
sys.path.append(str(BASE_DIR / "src"))
from logger import get_logger
LOGGER = get_logger("omiclient.net_manager")

STRUCTURE_PATH = BASE_DIR / "data" / "structure.json"

from structure_manager import get_structure_manager
STRUCTURE_MANAGER = get_structure_manager()


def load_structure() -> Dict[str, Any]:
    if not STRUCTURE_PATH.exists():
        return {}
    try:
        with STRUCTURE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        LOGGER.error(f"Error cargando structure.json: {e}")
        return {}

def _run_cmd(cmd: list[str], description: str) -> bool:
    """Ejecuta un comando y loguea el resultado. Retorna True si éxito."""
    cmd_str = " ".join(cmd)
    LOGGER.info(f"CMD ({description}): {cmd_str}")
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return True
    except subprocess.CalledProcessError as e:
        LOGGER.error(f"Error ejecutando comando '{cmd_str}': {e.stderr.decode().strip()}")
        return False

def _get_carrier_status(interface: str = "eth0") -> bool:
    """Verifica si la interfaz de red tiene portadora (cable conectado)."""
    try:
        path = Path(f"/sys/class/net/{interface}/carrier")
        if path.exists():
            return path.read_text().strip() == "1"
        return False
    except Exception:
        return False

def _get_nm_connection_details(con_name: str) -> Dict[str, str]:
    """
    Obtiene los detalles actuales de una conexión NM.
    Retorna un dict con 'ipv4.method', 'ipv4.addresses', 'ipv4.gateway', 'GENERAL.STATE'.
    """
    details = {}
    try:
        # Obtener campos específicos
        fields = ["ipv4.method", "ipv4.addresses", "ipv4.gateway", "GENERAL.STATE"]
        # nmcli -g field1,field2 con show name
        res = subprocess.run(
            ["nmcli", "-g", ",".join(fields), "con", "show", con_name],
            capture_output=True, text=True
        )
        if res.returncode == 0:
            # nmcli -g devuelve valores separados por lo que le pidamos, pero cuidado con valores vacíos
            # Mejor pedir uno a uno o usar -t -f
            pass
        
        # Enfoque más robusto: pedir todo en formato terco y parsear
        res = subprocess.run(
            ["nmcli", "-t", "-f", ",".join(fields), "con", "show", con_name],
            capture_output=True, text=True
        )
        if res.returncode == 0:
            lines = res.stdout.strip().split(":") # Ojo, ipv4.addresses puede tener :? No, en -t suele escapar
            # Pero ipv4.addresses puede ser multiple.
            # Vamos a simplificar: pedir uno a uno es más lento pero seguro.
            # O mejor, usar -g uno a uno.
            pass

        # Implementación simple y segura:
        for field in fields:
            r = subprocess.run(["nmcli", "-g", field, "con", "show", con_name], capture_output=True, text=True)
            if r.returncode == 0:
                details[field] = r.stdout.strip()
            else:
                details[field] = ""
                
    except Exception:
        pass
    return details

def _configure_nm_connection(con_name: str, mode: str, ip: str = None, mask: str = None, gateway: str = None) -> bool:
    """
    Configura una conexión NM existente con DHCP o Static IP.
    Verifica el estado actual antes de aplicar cambios.
    """
    
    # 1. Obtener estado actual
    current = _get_nm_connection_details(con_name)
    current_method = current.get("ipv4.method", "")
    current_ip = current.get("ipv4.addresses", "")
    current_gw = current.get("ipv4.gateway", "")
    
    # 2. Determinar configuración deseada
    desired_method = "auto" if mode == "dhcp" else "manual"
    desired_ip = ""
    desired_gw = gateway or ""
    
    if mode == "static":
        if ip and mask:
            # Calcular CIDR
            cidr_suffix = "24"
            if "." in str(mask):
                try:
                    import ipaddress
                    net = ipaddress.IPv4Network(f"0.0.0.0/{mask}")
                    cidr_suffix = str(net.prefixlen)
                except:
                    pass
            else:
                cidr_suffix = str(mask)
            desired_ip = f"{ip}/{cidr_suffix}"
        else:
            LOGGER.error(f"Modo estático para {con_name} pero faltan datos.")
            return False

    # 3. Comparar y Aplicar
    changes_needed = False
    
    # Normalizar para comparación
    # current_ip puede incluir gw o ser lista, nmcli es complejo.
    # Asumimos comparación simple de strings por ahora.
    
    if current_method != desired_method:
        changes_needed = True
    elif mode == "static":
        # En estático, comparar IP y GW
        # current_ip suele ser "192.168.1.100/24"
        if current_ip != desired_ip:
            changes_needed = True
        if current_gw != desired_gw:
            changes_needed = True
    
    if not changes_needed:
        LOGGER.info(f"Configuración de {con_name} ya es correcta ({mode}).")
        return False

    # Aplicar cambios
    STRUCTURE_MANAGER.set_busy("NET_CONFIG", f"Aplicando configuración {mode} a {con_name}...")
    
    cmd_args = ["nmcli", "con", "mod", con_name]
    desc = ""

    if mode == "static":
        # ipv4.method manual ipv4.addresses {ip} ipv4.gateway {gw}
        cmd_args.extend(["ipv4.method", "manual", "ipv4.addresses", desired_ip])
        if desired_gw:
            cmd_args.extend(["ipv4.gateway", desired_gw])
        else:
            cmd_args.extend(["ipv4.gateway", ""]) # Limpiar GW si no se pide
        desc = f"Configurando {con_name} (Static: {desired_ip})"
    else:
        # DHCP
        cmd_args.extend(["ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", ""])
        desc = f"Configurando {con_name} (DHCP)"

    return _run_cmd(cmd_args, desc)

def _ensure_vlan_exists(vlan_id: int) -> bool:
    """Asegura que exista la conexión eth0.{vlan_id}."""
    con_name = f"eth0.{vlan_id}"
    if _run_cmd(["nmcli", "con", "show", con_name], f"Verificando {con_name}"):
        return True
    
    STRUCTURE_MANAGER.set_busy("NET_CONFIG", f"Creando VLAN {vlan_id}...")
    return _run_cmd(
        ["nmcli", "con", "add", "type", "vlan", "con-name", con_name, "dev", "eth0", "id", str(vlan_id)],
        f"Creando conexión VLAN {con_name}"
    )

def _delete_vlan_connection(con_name: str):
    STRUCTURE_MANAGER.set_busy("NET_CONFIG", f"Eliminando {con_name}...")
    _run_cmd(["nmcli", "con", "del", con_name], f"Eliminando {con_name}")

def _wait_for_ip(con_name: str, timeout: int = 10):
    """Espera a que la conexión obtenga una IP."""
    LOGGER.info(f"Esperando IP para {con_name} (máx {timeout}s)...")
    for i in range(timeout):
        STRUCTURE_MANAGER.set_busy("NET_CONFIG", f"Esperando IP ({i+1}/{timeout}s)...")
        try:
            res = subprocess.run(
                ["nmcli", "-g", "ip4.address", "con", "show", con_name], 
                capture_output=True, text=True
            )
            if res.returncode == 0 and res.stdout.strip():
                ip = res.stdout.strip()
                LOGGER.info(f"IP asignada a {con_name}: {ip}")
                return
        except Exception:
            pass
        time.sleep(1)
    LOGGER.warning(f"Timeout esperando IP para {con_name}.")

def update_nics():
    """
    Lógica Principal de Configuración de Red.
    """
    STRUCTURE_MANAGER.set_busy("NET_CONFIG", "Iniciando gestión de red...")
    LOGGER.info("Iniciando actualización de configuración de red...")

    # 1. Verificar Cable
    STRUCTURE_MANAGER.set_busy("NET_CONFIG", "Verificando cable...")
    if not _get_carrier_status("eth0"):
        LOGGER.warning("⚠️ No se detecta portadora en eth0. Abortando configuración de red.")
        STRUCTURE_MANAGER.clear_busy("NET_CONFIG")
        return False

    structure = load_structure()
    network = structure.get("network", {})
    desired = network.get("desired", {})
    services = structure.get("services", [])

    # 2. Determinar VLAN Activa
    # Variables objetivo
    target_vlan_id: Optional[int] = None
    target_mode = "dhcp"
    target_ip = ""
    target_mask = ""
    target_gw = ""
    
    vlan_from_service = desired.get("vlan_from_service", False)
    
    if not vlan_from_service:
        # Configuración Global
        if desired.get("vlan"):
            target_vlan_id = int(desired.get("vlan"))
            target_mode = desired.get("vlan_mode", "dhcp")
            target_ip = desired.get("vlan_ip")
            target_mask = desired.get("vlan_mask")
            target_gw = desired.get("vlan_gateway")
            LOGGER.info(f"Usando VLAN Global: {target_vlan_id} ({target_mode})")
    else:
        # Configuración desde Servicio
        active_svc = next((svc for svc in services if svc.get("enabled")), None)
        if active_svc:
            if active_svc.get("vlan_active") and active_svc.get("vlan"):
                target_vlan_id = int(active_svc.get("vlan"))
                target_mode = active_svc.get("vlan_mode", "dhcp") # Ojo: InfoClient usa vlan_mode/vlan_ip para la VLAN
                target_ip = active_svc.get("vlan_ip")
                target_mask = active_svc.get("vlan_mask")
                target_gw = active_svc.get("vlan_gateway")
                LOGGER.info(f"Usando VLAN de Servicio ({active_svc.get('name')}): {target_vlan_id} ({target_mode})")
            else:
                LOGGER.info(f"Servicio activo ({active_svc.get('name')}) no requiere VLAN.")
        else:
            LOGGER.info("Sin servicio activo. No se aplicará VLAN.")

    # 3. Gestionar Interfaces VLAN
    # Listar conexiones existentes eth0.*
    existing_vlans = []
    try:
        res = subprocess.run(["nmcli", "-t", "-f", "NAME", "con", "show"], capture_output=True, text=True)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if line.startswith("eth0."):
                    existing_vlans.append(line)
    except Exception:
        pass

    # A) Crear/Mantener Target VLAN
    active_con_name = None
    if target_vlan_id:
        active_con_name = f"eth0.{target_vlan_id}"
        _ensure_vlan_exists(target_vlan_id)
    
    # B) Borrar VLANs sobrantes
    for vlan_con in existing_vlans:
        if vlan_con != active_con_name:
            _delete_vlan_connection(vlan_con)

    # 4. Configurar Interfaz Activa (VLAN o Eth0 base?)
    # El usuario dijo: "una vez sepa que VLAN ha de configurar... comprueba si el interface et0.vlan existe... configura... comprueba ip"
    # ¿Y qué pasa con eth0 base? El script original configuraba eth0 base TAMBIÉN.
    # Asumiremos que eth0 base siempre se configura con los parámetros globales "network.desired" (no vlan_*).
    
    # Configurar eth0 base (siempre)
    eth0_mode = desired.get("mode", "dhcp")
    eth0_ip = desired.get("ip")
    eth0_mask = desired.get("mask")
    eth0_gw = desired.get("gateway")
    
    # Buscar conexión existente para eth0
    con_name = "omi-eth0"
    try:
        # nmcli -t -f NAME,DEVICE con show
        res = subprocess.run(["nmcli", "-t", "-f", "NAME,DEVICE,TYPE", "con", "show"], capture_output=True, text=True)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = line.split(":")
                if len(parts) >= 3:
                    c_name, c_device, c_type = parts[0], parts[1], parts[2]
                    # Si es ethernet y está asociada a eth0 o es una "Wired connection" genérica sin dispositivo pero compatible
                    if (c_type == "802-3-ethernet" or c_type == "ethernet"):
                        if c_device == "eth0":
                            con_name = c_name
                            LOGGER.info(f"Usando conexión existente para eth0: {con_name}")
                            break
                        elif c_name == "Ethernet connection 1" and not c_device:
                             # Fallback común si no está activa
                             con_name = c_name
                             break
    except Exception:
        pass

    # Asegurar conexión base
    if not _run_cmd(["nmcli", "con", "show", con_name], f"Verificando {con_name}"):
         _run_cmd(["nmcli", "con", "add", "type", "ethernet", "con-name", con_name, "ifname", "eth0"], f"Creando {con_name}")

    # Configurar y Levantar eth0
    eth0_changed = _configure_nm_connection(con_name, eth0_mode, eth0_ip, eth0_mask, eth0_gw)
    eth0_active = "activated" in _get_nm_connection_details(con_name).get("GENERAL.STATE", "")
    
    if eth0_changed or not eth0_active:
        STRUCTURE_MANAGER.set_busy("NET_CONFIG", f"Levantando {con_name}...")
        # Usamos Popen para no bloquear (equivalente a --wait 0 manual)
        LOGGER.info(f"CMD (Async): nmcli con up {con_name}")
        subprocess.Popen(["nmcli", "con", "up", con_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Configurar VLAN (si aplica)
    if active_con_name:
        vlan_changed = _configure_nm_connection(active_con_name, target_mode, target_ip, target_mask, target_gw)
        vlan_active = "activated" in _get_nm_connection_details(active_con_name).get("GENERAL.STATE", "")
        
        if vlan_changed or not vlan_active:
            STRUCTURE_MANAGER.set_busy("NET_CONFIG", f"Levantando {active_con_name}...")
            LOGGER.info(f"CMD (Async): nmcli con up {active_con_name}")
            subprocess.Popen(["nmcli", "con", "up", active_con_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
        # Siempre esperamos/verificamos IP para la VLAN activa, por si acaso
        _wait_for_ip(active_con_name)

    STRUCTURE_MANAGER.clear_busy("NET_CONFIG")
    LOGGER.info("✅ Gestión de red finalizada.")
    
    # Forzar actualización de heartbeat
    try:
        from heartbeat import force_update_interfaces
        force_update_interfaces()
    except:
        pass
    
    return True

if __name__ == "__main__":
    from logger import configure_logging
    configure_logging()
    update_nics()
