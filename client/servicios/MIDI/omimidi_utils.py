#!/usr/bin/env python3
from __future__ import annotations
import logging
from typing import Any, Dict, List, Optional
import ipaddress

LOGGER = logging.getLogger("omimidi.utils")

def validate_osc_path(path: str) -> bool:
    """Valida que una ruta OSC sea válida."""
    if not path:
        return False
    if not path.startswith("/"):
        return False
    return True

def validate_midi_config(config: Dict[str, Any]) -> List[str]:
    """Valida la configuración MIDI y retorna lista de errores."""
    errors = []
    
    # Validar puertos
    try:
        osc_port = int(config.get("osc_port", 0))
        if not (1 <= osc_port <= 65535):
            errors.append(f"Puerto OSC inválido: {osc_port}")
    except (TypeError, ValueError):
        errors.append("Puerto OSC debe ser un número")

    try:
        ui_port = int(config.get("ui_port", 0))
        if not (1 <= ui_port <= 65535):
            errors.append(f"Puerto UI inválido: {ui_port}")
    except (TypeError, ValueError):
        errors.append("Puerto UI debe ser un número")

    # Validar IPs
    osc_ips = config.get("osc_ips", [])
    if not isinstance(osc_ips, list):
        errors.append("osc_ips debe ser una lista")
    else:
        for ip in osc_ips:
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                errors.append(f"IP inválida: {ip}")

    # Validar rutas
    routes = config.get("routes", [])
    if not isinstance(routes, list):
        errors.append("routes debe ser una lista")
    else:
        seen_routes = set()
        for i, route in enumerate(routes):
            if not isinstance(route, dict):
                errors.append(f"Ruta {i} inválida: debe ser un objeto")
                continue
                
            # Validar tipo
            rtype = route.get("type")
            if rtype not in ("note", "cc"):
                errors.append(f"Ruta {i}: tipo inválido {rtype}")
                continue

            # Validar número según tipo
            if rtype == "note":
                try:
                    note = int(route["note"])
                    if not (0 <= note <= 127):
                        errors.append(f"Ruta {i}: nota fuera de rango (0-127)")
                except (KeyError, ValueError, TypeError):
                    errors.append(f"Ruta {i}: nota inválida")
            else:  # cc
                try:
                    cc = int(route["cc"])
                    if not (0 <= cc <= 127):
                        errors.append(f"Ruta {i}: CC fuera de rango (0-127)")
                except (KeyError, ValueError, TypeError):
                    errors.append(f"Ruta {i}: CC inválido")

            # Validar canal MIDI (opcional)
            channel = route.get("channel")
            if channel is not None:
                try:
                    ch = int(channel)
                    if not (0 <= ch <= 15):
                        errors.append(f"Ruta {i}: canal fuera de rango (0-15)")
                except (ValueError, TypeError):
                    errors.append(f"Ruta {i}: canal inválido")

            # Validar ruta OSC
            osc_path = route.get("osc", "")
            if not validate_osc_path(osc_path):
                errors.append(f"Ruta {i}: path OSC inválido {osc_path}")
            elif osc_path in seen_routes:
                errors.append(f"Ruta {i}: path OSC duplicado {osc_path}")
            else:
                seen_routes.add(osc_path)

            # Validar tipo de valor
            vtype = route.get("vtype", "float")
            if vtype not in ("float", "int", "bool", "const"):
                errors.append(f"Ruta {i}: tipo de valor inválido {vtype}")
            elif vtype == "const":
                try:
                    float(route.get("const", 0))
                except (ValueError, TypeError):
                    errors.append(f"Ruta {i}: valor constante inválido")

    return errors