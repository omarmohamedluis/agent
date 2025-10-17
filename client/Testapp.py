#!/usr/bin/env python3
# test_apphandler.py
from __future__ import annotations
import argparse
import sys
from pathlib import Path

# Importa el handler
from AppHandler import start_service, stop_service, get_active_service, BASE_SERVICES_DIR

def list_services() -> list[str]:
    base = Path(BASE_SERVICES_DIR)
    if not base.exists():
        return []
    out = []
    for p in base.iterdir():
        if p.is_dir() and (p / "service.py").exists():
            out.append(p.name)
    return sorted(out)

def cmd_start(name: str) -> int:
    if start_service(name):
        print(f"✓ start: {name}")
        return 0
    print(f"✗ no se pudo iniciar: {name}")
    return 1

def cmd_stop() -> int:
    if stop_service():
        print("✓ stop")
        return 0
    print("✗ no se pudo parar (o no había servicio)")
    return 1

def cmd_status() -> int:
    active = get_active_service()
    if active:
        print(f"estado: activo = {active}")
    else:
        print("estado: sin servicio activo")
    return 0

def cmd_restart(name: str) -> int:
    stop_service()
    return cmd_start(name)

def cmd_list() -> int:
    svcs = list_services()
    if not svcs:
        print("no hay servicios en 'servicios/*/service.py'")
        return 0
    print("servicios disponibles:")
    for s in svcs:
        print(" -", s)
    return 0

def repl() -> int:
    print("Mini CLI AppHandler (start/stop/status/restart/list/quit)")
    print("Escribe 'help' para ver comandos. Ctrl+C para salir.")
    try:
        while True:
            try:
                line = input("> ").strip()
            except EOFError:
                print()
                break
            if not line:
                continue
            parts = line.split()
            cmd = parts[0].lower()
            args = parts[1:]

            if cmd in ("quit", "exit", "q"):
                break
            if cmd in ("help", "h", "?"):
                print("Comandos:")
                print("  list")
                print("  status")
                print("  start <nombre>")
                print("  stop")
                print("  restart <nombre>")
                print("  quit")
                continue
            if cmd == "list":
                cmd_list()
                continue
            if cmd == "status":
                cmd_status()
                continue
            if cmd == "start":
                if not args:
                    print("uso: start <nombre>")
                    continue
                cmd_start(args[0])
                continue
            if cmd == "stop":
                cmd_stop()
                continue
            if cmd == "restart":
                if not args:
                    print("uso: restart <nombre>")
                    continue
                cmd_restart(args[0])
                continue

            print("comando no reconocido. Escribe 'help'.")
    except KeyboardInterrupt:
        print("\n(interrumpido)")
    finally:
        return 0

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="CLI de prueba para AppHandler")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="listar servicios disponibles")
    sub.add_parser("status", help="estado del servicio activo")

    p_start = sub.add_parser("start", help="iniciar un servicio")
    p_start.add_argument("name", help="nombre del servicio (carpeta en servicios/)")

    sub.add_parser("stop", help="detener el servicio activo")

    p_restart = sub.add_parser("restart", help="reiniciar un servicio")
    p_restart.add_argument("name", help="nombre del servicio")

    args = p.parse_args(argv)

    if args.cmd is None:
        # sin args → modo interactivo
        return repl()

    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "start":
        return cmd_start(args.name)
    if args.cmd == "stop":
        return cmd_stop()
    if args.cmd == "restart":
        return cmd_restart(args.name)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
