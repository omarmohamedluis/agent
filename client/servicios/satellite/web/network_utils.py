#!/usr/bin/env python3
import subprocess
import concurrent.futures
import ipaddress
import socket
from typing import List, Dict, Any, Tuple
import logging

LOGGER = logging.getLogger("satellite.network")

def is_reachable(ip: str, timeout: float = 0.5) -> bool:
    """Comprueba si una IP es alcanzable usando ping."""
    try:
        res = subprocess.run(
            ["ping", "-c", "1", "-W", str(timeout), "-n", ip],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return res.returncode == 0
    except Exception:
        return False

def scan_subnet(base_ip: str) -> List[str]:
    """Escanea una subred /24 buscando hosts activos."""
    try:
        prefix = ".".join(base_ip.split(".")[:-1]) + "."
        found = []
        
        def check_ip(i):
            target = f"{prefix}{i}"
            if is_reachable(target, timeout=0.5):
                return target
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(check_ip, i) for i in range(1, 255)]
            for future in concurrent.futures.as_completed(futures):
                res = future.result()
                if res:
                    found.append(res)
        return sorted(found)
    except Exception as e:
        LOGGER.error(f"Error escaneando subred: {e}")
        return []

def ping_targets(ips: List[str], callback=None) -> List[Dict[str, Any]]:
    """Verifica una lista de IPs objetivo. Maneja broadcast."""
    results = []
    targets_to_process: List[Tuple[str, bool, bool]] = [] 

    for ip_str in ips:
        ip_str = ip_str.strip()
        if not ip_str: continue
        try:
            # Detección de broadcast
            if ip_str.endswith(".255") or ip_str == "255.255.255.255":
                broadcast_msg = {"ip": ip_str, "type": "broadcast", "info": "Detectado broadcast, escaneando..."}
                results.append(broadcast_msg)
                if callback: callback(broadcast_msg)
                
                discovered = scan_subnet(ip_str)
                for d_ip in discovered:
                    targets_to_process.append((d_ip, True, True)) 
            else:
                targets_to_process.append((ip_str, False, False))
        except Exception:
            targets_to_process.append((ip_str, False, False))

    seen_ips = set()
    unique_targets = []
    for t in targets_to_process:
        if t[0] not in seen_ips:
            seen_ips.add(t[0])
            unique_targets.append(t)

    for ip, is_discovered, already_checked in unique_targets:
        if already_checked:
            res = {"ip": ip, "ok": True, "discovered": is_discovered}
        else:
            reachable = is_reachable(ip, timeout=1.0)
            res = {"ip": ip, "ok": reachable, "discovered": is_discovered}
            if not reachable: res["error"] = "No alcanzable"
        
        results.append(res)
        if callback: callback(res)

    return results
