#!/usr/bin/env python3
import json
import os
import sys
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

# Setup Paths
BASE_DIR = Path(__file__).resolve().parents[1]
# Setup Logging
sys.path.append(str(BASE_DIR / "src"))
from logger import get_logger
LOGGER = get_logger("omimidi.net_manager")

STRUCTURE_PATH = BASE_DIR / "data" / "structure.json"

def load_structure() -> Dict[str, Any]:
    if not STRUCTURE_PATH.exists():
        return {}
    try:
        with STRUCTURE_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        LOGGER.error(f"Error cargando structure.json: {e}")
        return {}

def _run_cmd(cmd: list[str], description: str):
    """Ejecuta un comando y loguea el resultado (Mockup por ahora)."""
    cmd_str = " ".join(cmd)
    LOGGER.info(f"CMD ({description}): {cmd_str}")
    # subprocess.run(cmd, check=False) # Descomentar para real

def update_nics():
    """
    Lee structure.json y aplica la configuración de red inteligente.
    Maneja VLANs globales y por servicio.
    """
    LOGGER.info("Iniciando actualización de configuración de red (update_nics)...")

    structure = load_structure()
    network = structure.get("network", {})
    desired = network.get("desired", {})
    services = structure.get("services", [])

    # 1. Configuración Global de NIC (eth0)
    mode = desired.get("mode", "dhcp")
    vlan_from_service = desired.get("vlan_from_service", False)
    global_vlan = desired.get("vlan")

    LOGGER.info(f"Modo de red: {mode.upper()}")
    if mode == "static":
        LOGGER.info(f"Aplicando IP Estática: {desired.get('ip')} / {desired.get('mask')}")
        LOGGER.info(f"Gateway: {desired.get('gateway')}")
    
    # TODO: Aquí iría la lógica real de 'ip addr add ...' para eth0

    # 2. Lógica de VLAN Inteligente
    target_vlan = None
    source = "Global"

    if vlan_from_service:
        # Buscar servicio activo
        active_svc = next((svc for svc in services if svc.get("enabled")), None)
        if active_svc:
            # Si el servicio tiene VLAN definida, úsala
            svc_vlan = active_svc.get("vlan")
            if svc_vlan is not None:
                target_vlan = svc_vlan
                source = f"Servicio ({active_svc.get('name')})"
            else:
                LOGGER.info(f"Servicio activo ({active_svc.get('name')}) no requiere VLAN.")
        else:
            LOGGER.info("'Desde servicio' activo pero no hay servicio habilitado. Usando global si existe.")
            target_vlan = global_vlan
    else:
        target_vlan = global_vlan

    # 3. Aplicación de Cambios de VLAN
    # En un sistema real, aquí comprobaríamos qué VLANs existen con 'ip -json link show'
    # Para este mockup, asumimos que siempre "aplicamos" el estado deseado.
    
    if target_vlan:
        LOGGER.info(f"🎯 VLAN Objetivo: {target_vlan} (Origen: {source})")
        
        # Lógica de cambio:
        # 1. Borrar cualquier VLAN existente en eth0 que NO sea la target
        #    (Simplificación: Borrar todas las vlan de eth0 y crear la nueva)
        #    _run_cmd(["ip", "link", "del", "eth0.OLD_VLAN"], "Limpiando VLANs antiguas")
        
        # 2. Crear la nueva VLAN
        _run_cmd(
            ["sudo", "ip", "link", "add", "link", "eth0", "name", f"eth0.{target_vlan}", "type", "vlan", "id", str(target_vlan)],
            f"Creando interfaz VLAN {target_vlan}"
        )
        _run_cmd(
            ["sudo", "ip", "addr", "add", f"192.168.{target_vlan}.10/24", "dev", f"eth0.{target_vlan}"],
            f"Asignando IP a VLAN {target_vlan}"
        )
        _run_cmd(
            ["sudo", "ip", "link", "set", "dev", f"eth0.{target_vlan}", "up"],
            f"Levantando interfaz VLAN {target_vlan}"
        )
    else:
        LOGGER.info("⚪ No se requiere VLAN activa.")
        # Lógica de limpieza: Asegurar que no haya VLANs colgadas
        # _run_cmd(["ip", "link", "del", "eth0.ANY"], "Limpiando todas las VLANs")

    LOGGER.info("✅ Configuración de red finalizada.")

if __name__ == "__main__":
    # If run directly, configure logging first (since client.py didn't run)
    from logger import configure_logging
    configure_logging()
    update_nics()
