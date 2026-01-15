"""
Módulo de Red para OMIMIDI.
Gestiona la verificación de conectividad (ping), escaneo de subredes
y descubrimiento de dispositivos OSC.
"""
#!/usr/bin/env python3
from __future__ import annotations
import subprocess
import concurrent.futures
import ipaddress
import socket
from typing import List, Dict, Any, Tuple

from omimidi_logger import get_logger

LOGGER = get_logger("omimidi.ping")

def is_reachable(ip: str, timeout: float = 0.5) -> bool:
    """
    Comprueba si una IP es alcanzable usando el comando ping del sistema.
    Timeout por defecto: 0.5s (ajustado para WiFi/RPi).
    """
    try:
        # -c 1: enviar 1 paquete
        # -W timeout: esperar respuesta (segundos)
        # -n: sin resolución DNS
        res = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), "-n", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return res.returncode == 0
    except Exception:
        return False

def scan_subnet(base_ip: str) -> List[str]:
    """
    Escanea una subred /24 buscando hosts activos.
    Usa un pool de hilos limitado para no saturar la RPi.
    """
    prefix = ".".join(base_ip.split(".")[:-1]) + "."
    found = []
    
    def check_ip(i):
        target = f"{prefix}{i}"
        # Timeout 0.5s para balancear velocidad y fiabilidad
        if is_reachable(target, timeout=0.5):
            return target
        return None

    # Limitamos a 20 workers para evitar "Resource temporarily unavailable"
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(check_ip, i) for i in range(1, 255)]
        for future in concurrent.futures.as_completed(futures):
            res = future.result()
            if res:
                found.append(res)
    return sorted(found)

def ping_osc_targets(ips: List[str], callback=None) -> List[Dict[str, Any]]:
    """
    Verifica una lista de IPs objetivo para OSC.
    Maneja direcciones de broadcast realizando un escaneo de subred.
    Devuelve una lista de resultados con estado de conectividad.
    Si se proporciona callback, se llama con cada resultado individual: callback(result_dict).
    """
    LOGGER.info(f"Iniciando ping_osc_targets para: {ips}")
    results = []
    targets_to_process: List[Tuple[str, bool, bool]] = [] # (ip, is_discovered, already_checked)

    for ip_str in ips:
        try:
            ipaddress.ip_address(ip_str)
            # Detección simple de broadcast (termina en .255 o es global)
            if ip_str.endswith(".255") or ip_str == "255.255.255.255":
                LOGGER.info(f"Detectado broadcast: {ip_str}, iniciando escaneo...")
                broadcast_msg = {
                    "ip": ip_str,
                    "type": "broadcast",
                    "info": "Detectado broadcast, iniciando descubrimiento..."
                }
                results.append(broadcast_msg)
                if callback: callback(broadcast_msg)
                
                discovered = scan_subnet(ip_str)
                LOGGER.info(f"Escaneo completado. Encontrados: {discovered}")
                for d_ip in discovered:
                    # Los descubiertos ya fueron verificados en scan_subnet
                    targets_to_process.append((d_ip, True, True)) 
            else:
                # IPs directas se verificarán explícitamente
                targets_to_process.append((ip_str, False, False))
        except ValueError:
            LOGGER.warning(f"IP inválida: {ip_str}")
            # Si no es una IP válida, la marcamos para intentar ping igual (o fallar)
            targets_to_process.append((ip_str, False, False))

    # Deduplicar por IP para no pinguear lo mismo varias veces
    seen_ips = set()
    unique_targets = []
    for t in targets_to_process:
        if t[0] not in seen_ips:
            seen_ips.add(t[0])
            unique_targets.append(t)

    LOGGER.info(f"Procesando {len(unique_targets)} objetivos únicos...")
    for ip, is_discovered, already_checked in unique_targets:
        res = None
        if already_checked:
            # Si viene del escaneo, ya sabemos que responde
            res = {"ip": ip, "ok": True, "discovered": is_discovered}
        else:
            # Verificación explícita con timeout generoso (1.0s) para targets directos
            LOGGER.debug(f"Haciendo ping a {ip}...")
            reachable = is_reachable(ip, timeout=1.0)
            LOGGER.debug(f"Ping a {ip}: {'OK' if reachable else 'FAIL'}")
            if not reachable:
                res = {
                    "ip": ip,
                    "ok": False,
                    "error": "IP no alcanzable",
                    "discovered": is_discovered
                }
            else:
                res = {"ip": ip, "ok": True, "discovered": is_discovered}
        
        if res:
            results.append(res)
            if callback: callback(res)

    LOGGER.info("ping_osc_targets finalizado.")
    return results
