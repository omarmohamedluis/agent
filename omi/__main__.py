"""Run either role through python -m omi, useful for IDE debugging."""
import sys

from .cli import agent_main, maintain_main, server_main

if len(sys.argv) < 2 or sys.argv[1] not in ("client", "server", "maintain"):
    raise SystemExit("Usage: python -m omi {client|server|maintain} [options]")
role = sys.argv.pop(1)
{"client": agent_main, "server": server_main, "maintain": maintain_main}[role]()
