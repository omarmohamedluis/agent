import importlib.util
from pathlib import Path


def checker():
    path = Path(__file__).resolve().parents[1] / "scripts/verify-health.py"
    spec = importlib.util.spec_from_file_location("omi_verify_health", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.readiness


def test_update_verification_lock_allows_installer_but_not_ordinary_busy():
    ready = checker()
    status = {"role": "client", "code_revision": "sha", "booting": False, "busy": True,
              "update_verifying": True, "state": {"busy": True, "health": "standby", "target": None}}
    assert ready(status, "client", "sha")[0]
    status["update_verifying"] = False
    assert not ready(status, "client", "sha")[0]


def test_standby_historical_error_kept_without_rejecting_successful_recovery():
    ready = checker()
    status = {"role": "client", "code_revision": "sha", "booting": False, "busy": False,
              "recovery_error": False, "state": {"health": "error", "target": None, "detail": "Old MIDI failure"}}
    assert ready(status, "client", "sha")[0]
    status["recovery_error"] = True
    assert not ready(status, "client", "sha")[0]
