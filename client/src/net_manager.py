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
from typing import Any, Dict, Optional

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

def _connection_exists(con_name: str) -> bool:
    """Verifica si una conexión de NetworkManager existe."""
    # nmcli con show {name} retorna 0 si existe, >0 si no
    return _run_cmd(["nmcli", "con", "show", con_name], f"Verificando existencia de {con_name}")

def _get_carrier_status(interface: str = "eth0") -> bool:
    """Verifica si la interfaz de red tiene portadora (cable conectado)."""
    try:
        path = Path(f"/sys/class/net/{interface}/carrier")
        if path.exists():
            return path.read_text().strip() == "1"
        return False
    except Exception:
        return False

def update_nics():
    """
    Lee structure.json y aplica la configuración de red usando NetworkManager (nmcli).
    Maneja VLANS globales y por servicio.
    """
    STRUCTURE_MANAGER.set_busy("NET_CONFIG", "Configurando Red...")
    LOGGER.info("Iniciando actualización de configuración de red (nmcli)...")

    structure = load_structure()
    network = structure.get("network", {})
    desired = network.get("desired", {})
    services = structure.get("services", [])

    # 0. Verificar Portadora (¿Cable conectado?)
    if not _get_carrier_status("eth0"):
        LOGGER.warning("⚠️ No se detecta portadora en eth0 (¿Cable desconectado?). Saltando configuración de red para evitar tiempos de espera.")
        STRUCTURE_MANAGER.clear_busy("NET_CONFIG")
        return False

    # 1. Configuración Global de NIC (eth0)
    global_mode = desired.get("mode", "dhcp")
    global_ip = desired.get("ip")
    global_mask = desired.get("mask")
    global_gateway = desired.get("gateway")
    
    vlan_from_service = desired.get("vlan_from_service", False)
    global_vlan = desired.get("vlan")

    # 2. Determinar VLAN Objetivo y Fuente de Configuración IP
    target_vlan = None
    ip_config_source = "Global"
    
    active_ip_mode = global_mode
    active_ip = global_ip
    active_mask = global_mask
    active_gateway = global_gateway

    if not vlan_from_service:
        target_vlan = global_vlan
        # Usar configuración IP de VLAN global si está disponible
        if target_vlan:
            # Usar vlan_mode explícito si está disponible, por defecto dhcp
            active_ip_mode = desired.get("vlan_mode", "dhcp")
            
            if active_ip_mode == "static":
                active_ip = desired.get("vlan_ip")
                active_mask = desired.get("vlan_mask")
                active_gateway = desired.get("vlan_gateway")
            else:
                active_ip = None
                active_mask = None
                active_gateway = None
    else:
        active_svc = next((svc for svc in services if svc.get("enabled")), None)
        if active_svc:
            svc_vlan = active_svc.get("vlan")
            if svc_vlan is not None:
                target_vlan = svc_vlan
                ip_config_source = f"Servicio ({active_svc.get('name')})"
                active_ip_mode = active_svc.get("ip_mode", "dhcp")
                active_ip = active_svc.get("ip")
                active_mask = active_svc.get("mask")
                active_gateway = active_svc.get("gateway")
            else:
                LOGGER.info(f"Servicio activo ({active_svc.get('name')}) no define VLAN. Modo Estricto: Sin VLAN.")
                target_vlan = None
                ip_config_source = "Global (Fallback)"
        else:
            LOGGER.info("'Desde servicio' activo pero no hay servicio. Sin VLAN.")
            target_vlan = None

    # 3. Aplicar Configuración con nmcli
    changes_made = False
    
    # A) Configurar eth0 base (Solo si es necesario)
    # Buscamos la conexión base
    con_name = "omi-eth0"
    base_con_exists = False
    
    # Intentar encontrar una conexión existente de tipo ethernet para eth0
    try:
        result = subprocess.run(["nmcli", "-t", "-f", "NAME,TYPE,DEVICE", "con", "show"], capture_output=True, text=True)
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split(":")
                if len(parts) >= 2:
                    c_name, c_type = parts[0], parts[1]
                    if c_type == "802-3-ethernet" or c_type == "ethernet":
                         if c_name == "omi-eth0":
                             con_name = "omi-eth0"
                             base_con_exists = True
                             break
                         elif c_name.startswith("Ethernet connection") or c_name == "Wired connection 1":
                             con_name = c_name
                             base_con_exists = True
    except Exception as e:
        LOGGER.error(f"Error buscando conexiones existentes: {e}")

    if not base_con_exists:
        LOGGER.info(f"Creando conexión base {con_name}")
        _run_cmd(
            ["nmcli", "con", "add", "type", "ethernet", "con-name", con_name, "ifname", "eth0"],
            f"Creando {con_name}"
        )
        changes_made = True
    
    # Configurar eth0 solo si hay cambios en la definición
    # TODO: Podríamos verificar la config actual antes de aplicar, pero _configure_nm_connection es rápido.
    # Lo importante es no hacer 'con up' si ya está arriba y configurada.
    if _configure_nm_connection(con_name, active_ip_mode, active_ip, active_mask, active_gateway):
        changes_made = True
        _run_cmd(["nmcli", "con", "up", con_name], f"Levantando {con_name}")
        if active_ip_mode != "static" and not target_vlan:
             _wait_for_ip(con_name)
    else:
        # Si no hubo cambios de config, verificamos si está activa
        # Si no está activa, la levantamos.
        # nmcli -t -f GENERAL.STATE con show {name}
        try:
            res = subprocess.run(["nmcli", "-t", "-f", "GENERAL.STATE", "con", "show", con_name], capture_output=True, text=True)
            if "activated" not in res.stdout:
                 _run_cmd(["nmcli", "con", "up", con_name], f"Levantando {con_name} (estaba inactiva)")
        except:
            pass

    # B) Configurar VLAN (si existe)
    if target_vlan:
        vlan_con_name = f"eth0.{target_vlan}"
        LOGGER.info(f"🎯 VLAN Objetivo: {target_vlan} (Origen: {ip_config_source})")
        
        if not _connection_exists(vlan_con_name):
            # Crear VLAN
            _run_cmd(
                ["nmcli", "con", "add", "type", "vlan", "con-name", vlan_con_name, "dev", "eth0", "id", str(target_vlan)],
                f"Creando conexión VLAN {vlan_con_name}"
            )
            changes_made = True
        
        # Configurar IP en la VLAN
        if _configure_nm_connection(vlan_con_name, active_ip_mode, active_ip, active_mask, active_gateway):
             changes_made = True
             _run_cmd(["nmcli", "con", "up", vlan_con_name], f"Levantando {vlan_con_name}")
             if active_ip_mode != "static":
                _wait_for_ip(vlan_con_name)
        else:
             # Verificar si está activa
             try:
                res = subprocess.run(["nmcli", "-t", "-f", "GENERAL.STATE", "con", "show", vlan_con_name], capture_output=True, text=True)
                if "activated" not in res.stdout:
                     _run_cmd(["nmcli", "con", "up", vlan_con_name], f"Levantando {vlan_con_name} (estaba inactiva)")
             except:
                pass
             
    else:
        LOGGER.info("⚪ No se requiere VLAN activa.")
        # Limpieza de VLANs residuales
        try:
            result = subprocess.run(["nmcli", "-t", "-f", "NAME", "con", "show"], capture_output=True, text=True)
            if result.returncode == 0:
                for name in result.stdout.splitlines():
                    if name.startswith("eth0.") and (not target_vlan or name != f"eth0.{target_vlan}"):
                        LOGGER.info(f"Limpiando conexión residual: {name}")
                        _run_cmd(["nmcli", "con", "del", name], f"Eliminando {name}")
                        changes_made = True
        except Exception as e:
            LOGGER.error(f"Error listando conexiones para limpieza: {e}")
    
    if changes_made:
        LOGGER.info("✅ Configuración de red actualizada.")
        # Forzar actualización de heartbeat para reflejar cambios en UI/Estructura inmediatamente
        try:
            from heartbeat import force_update_interfaces
            force_update_interfaces()
        except Exception as e:
            LOGGER.error(f"Fallo al forzar actualización de heartbeat: {e}")
    else:
        LOGGER.info("✅ Configuración de red verificada (sin cambios).")
        
    STRUCTURE_MANAGER.clear_busy("NET_CONFIG")
    return changes_made

def _wait_for_ip(con_name: str, timeout: int = 10):
    """Espera a que la conexión obtenga una IP (DHCP)."""
    LOGGER.info(f"Esperando IP para {con_name} (máx {timeout}s)...")
    for i in range(timeout):
        try:
            # nmcli -g ip4.address con show {name}
            # Si no está activa o no tiene IP, devuelve vacío o error
            res = subprocess.run(
                ["nmcli", "-g", "ip4.address", "con", "show", con_name], 
                capture_output=True, text=True
            )
            if res.returncode == 0 and res.stdout.strip():
                ip = res.stdout.strip()
                LOGGER.info(f"IP asignada a {con_name}: {ip}")
                STRUCTURE_MANAGER.set_busy("NET_CONFIG", f"Red Configurada: {ip}")
                return
        except Exception:
            pass
        
        STRUCTURE_MANAGER.set_busy("NET_CONFIG", f"Obteniendo IP para {con_name} ({i+1}/{timeout}s)...")
        time.sleep(1)
    
    LOGGER.warning(f"Timeout esperando IP para {con_name}. Continuando...")

def _configure_nm_connection(con_name: str, mode: str, ip: str = None, mask: str = None, gateway: str = None) -> bool:
    """Configura una conexión NM existente con DHCP o Static IP."""
    changed = False
    
    # Obtener configuración actual (simplificado, asumimos que siempre aplicamos para asegurar estado)
    # En producción podríamos leer 'nmcli -t -f ipv4.method con show {name}' para evitar re-aplicar.
    
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
            
            full_ip = f"{ip}/{cidr_suffix}"
            
            # Comandos para estática
            # ipv4.method manual
            # ipv4.addresses {ip}
            # ipv4.gateway {gw}
            
            cmds = [
                ["nmcli", "con", "mod", con_name, "ipv4.method", "manual"],
                ["nmcli", "con", "mod", con_name, "ipv4.addresses", full_ip]
            ]
            if gateway:
                cmds.append(["nmcli", "con", "mod", con_name, "ipv4.gateway", gateway])
            
            for cmd in cmds:
                if _run_cmd(cmd, f"Configurando {con_name} (Static)"):
                    changed = True
        else:
            LOGGER.error(f"Modo estático para {con_name} pero faltan datos.")
            return False
    else:
        # DHCP
        # ipv4.method auto
        if _run_cmd(["nmcli", "con", "mod", con_name, "ipv4.method", "auto"], f"Configurando {con_name} (DHCP)"):
            changed = True
            # Limpiar estáticas previas si las hubiera
            _run_cmd(["nmcli", "con", "mod", con_name, "ipv4.addresses", ""], "Limpiando IPs estáticas")
            _run_cmd(["nmcli", "con", "mod", con_name, "ipv4.gateway", ""], "Limpiando Gateway")

    return changed

def cleanup_vlans():
    """
    Limpia las interfaces de structure.json pero MANTIENE la configuración del OS (NM profiles).
    """
    LOGGER.info("🧹 Limpiando estado de interfaces en structure.json (Persistiendo NM profiles)...")
    try:
        STRUCTURE_MANAGER.update_network_interfaces([])
        LOGGER.info("Estado de interfaces limpiado.")
    except Exception as e:
        LOGGER.error(f"Error limpiando interfaces en structure: {e}")

if __name__ == "__main__":
    from logger import configure_logging
    configure_logging()
    update_nics()
