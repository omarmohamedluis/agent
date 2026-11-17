"""Installed and development entry points. Hardware is composed only at launch."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import importlib
import json
import os
from pathlib import Path
import shutil
import sys

from omi import __version__
from omi.logging import configure_logging
from omi.store import Store


@contextmanager
def instance_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        stream.seek(0)
        if sys.platform == "win32":
            import msvcrt
            if not stream.read(1):
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        stream.close()
        raise RuntimeError("OMI is already running for this installation; stop systemd before manual development") from exc
    try:
        yield
    finally:
        if sys.platform == "win32":
            import msvcrt
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        stream.close()


def _parser(role: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"OMI v3 {role}")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("OMI_DATA_DIR", f"/var/lib/omi/{role}")))
    parser.add_argument("--host", default=os.environ.get("OMI_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("OMI_PORT", "8081" if role == "client" else "8080")))
    parser.add_argument("--simulate", action="store_true", help="Use explicit substitutes; never change host network or power")
    return parser


def agent_main() -> None:
    parser = _parser("client")
    parser.add_argument("--serial", help="Simulated identity (requires --simulate)")
    parser.add_argument("--server", help="Manual server URL; discovery remains the default")
    parser.add_argument("--endpoint", help="HTTP endpoint to advertise to the server")
    args = parser.parse_args()
    import uvicorn
    from omi.client.app import create_app
    from omi.client.runtime import ClientRuntime
    lock = args.data_dir / "instance.lock" if args.simulate else Path("/run/omi-client.lock")
    with ExitStack() as locks:
        locks.enter_context(instance_lock(lock))
        if lock != args.data_dir / "instance.lock":
            locks.enter_context(instance_lock(args.data_dir / "instance.lock"))
        settings = {"server_url": args.server} if args.server else {}
        runtime = ClientRuntime(args.data_dir, simulated=args.simulate, serial=args.serial,
                                endpoint=args.endpoint, port=args.port, settings=settings)
        configure_logging(args.data_dir, budget_mb=runtime.settings.log_budget_mb)
        uvicorn.run(create_app(runtime), host=args.host, port=args.port, access_log=False, timeout_graceful_shutdown=35)


def server_main() -> None:
    parser = _parser("server")
    parser.add_argument("--advertise", default=os.environ.get("OMI_ADVERTISE", ""), help="Server HTTP URL announced by UDP")
    parser.add_argument("--no-beacon", action="store_true")
    args = parser.parse_args()
    import uvicorn
    from omi.server.app import create_app
    with instance_lock(args.data_dir / "instance.lock"):
        configure_logging(args.data_dir)
        app = create_app(args.data_dir, simulated=args.simulate, advertise=args.advertise,
                         beacon_enabled=not args.no_beacon, http_port=args.port)
        uvicorn.run(app, host=args.host, port=args.port, access_log=False, timeout_graceful_shutdown=35)


def maintain_main() -> None:
    parser = argparse.ArgumentParser(description="OMI data maintenance; stop the service before destructive actions")
    parser.add_argument("--data-dir", type=Path, required=True)
    commands = parser.add_subparsers(dest="action", required=True)
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--role", choices=("client", "server"), required=True)
    doctor.add_argument("--offline", action="store_true", help="Check installation without changing network or contacting hardware")
    save = commands.add_parser("backup")
    save.add_argument("--output", type=Path)
    commands.add_parser("clear-logs")
    commands.add_parser("reset-state")
    install_reset = commands.add_parser("reset-installation")
    install_reset.add_argument("--confirm", action="store_true")
    factory = commands.add_parser("factory-reset")
    factory.add_argument("--confirm", action="store_true")
    factory.add_argument("--backup", type=Path)
    args = parser.parse_args()
    from omi.maintenance import backup, clear_logs, factory_reset, reset_state
    if args.action == "doctor":
        modules = ["omi.models", "omi.store", "omi.library", "omi.web", "fastapi", "uvicorn", "httpx"]
        modules += ["omi.client.app", "omi.services.host", "omi.network"] if args.role == "client" else ["omi.server.app"]
        for module in modules:
            importlib.import_module(module)
        report = {"ok": True, "version": __version__, "python": sys.version.split()[0], "role": args.role,
                  "data_dir": str(args.data_dir.resolve()), "offline": args.offline, "imports": modules}
        if args.role == "client" and sys.platform == "linux":
            report["networkmanager"] = shutil.which("nmcli")
            satellite_dir = Path(os.environ.get("OMI_SATELLITE_DIR", "/opt/omi/satellite/current"))
            try:
                from omi.services.satellite import prepared_command
                prepared_command(satellite_dir, args.data_dir / "runtime/satellite.json")
                report["satellite"] = "prepared pinned launcher OK"
            except RuntimeError as exc:
                report["satellite"] = str(exc)
                report["ok"] = False
            if not report["networkmanager"]:
                report["ok"] = False
        if (args.data_dir / "omi.sqlite3").exists():
            store = Store(args.data_dir / "omi.sqlite3")
            store.close()
            report["database"] = "integrity OK"
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["ok"]:
            raise SystemExit(1)
        return
    if args.action == "clear-logs":
        clear_logs(args.data_dir)
        print(json.dumps({"ok": True, "action": args.action}))
        return
    if args.action == "backup":
        store = Store(args.data_dir / "omi.sqlite3")
        try:
            saved = store.backup(args.output) if args.output else backup(store, args.data_dir / "backups")
            print(json.dumps({"ok": True, "backup": str(saved)}))
        finally:
            store.close()
        return
    with instance_lock(args.data_dir / "instance.lock"):
        store = Store(args.data_dir / "omi.sqlite3")
        try:
            result: dict = {"ok": True, "action": args.action}
            if args.action == "backup":
                saved = store.backup(args.output) if args.output else backup(store, args.data_dir / "backups")
                result["backup"] = str(saved)
            elif args.action == "reset-state":
                reset_state(store)
            elif args.action == "reset-installation":
                if not args.confirm:
                    raise ValueError("Pass --confirm to remove server enrollments and assignments")
                store.delete_prefix("server.")
            elif args.action == "factory-reset":
                saved = factory_reset(store, confirm=args.confirm, backup_dir=args.backup or args.data_dir / "backups")
                result["backup"] = str(saved) if saved else None
            print(json.dumps(result, ensure_ascii=False))
        finally:
            store.close()


if __name__ == "__main__":
    maintain_main()
