import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import io
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
if sys.platform == "win32":
    candidate = Path("C:/Program Files/Git/bin/bash.exe")
    if candidate.exists():
        BASH = str(candidate)


@pytest.mark.skipif(not BASH, reason="Bash is required")
def test_bash_syntax():
    paths = list((ROOT / "scripts").glob("*.sh")) + [ROOT / "deploy/omi-update"]
    for path in paths:
        result = subprocess.run([BASH, "-n", path.as_posix()], capture_output=True, text=True)
        assert result.returncode == 0, f"{path}: {result.stderr}"


def load_extractor():
    spec = importlib.util.spec_from_file_location("asar", ROOT / "scripts/extract-asar.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_asar(path, files, data=b""):
    encoded = json.dumps({"files": files}).encode()
    padding = (4 - (len(encoded) % 4)) % 4
    header_size = len(encoded) + padding + 8
    path.write_bytes(struct.pack("<IIII", 4, header_size, header_size - 4, len(encoded)) + encoded + b"\0" * padding + data)


def test_extract_production_asar_with_integrity_and_unpacked_native_module(tmp_path):
    archive = tmp_path / "app.asar"
    data = b"console.log('OMI')"
    native = tmp_path / "app.asar.unpacked/native.node"
    native.parent.mkdir()
    native.write_bytes(b"native")
    make_asar(archive, {"dist": {"files": {"main.js": {"size": len(data), "offset": "0", "integrity": {
        "algorithm": "SHA256", "hash": hashlib.sha256(data).hexdigest()}}}},
        "native.node": {"size": 6, "unpacked": True}}, data)
    load_extractor().extract(archive, tmp_path / "out")
    assert (tmp_path / "out/dist/main.js").read_bytes() == data
    assert (tmp_path / "out/native.node").read_bytes() == b"native"


@pytest.mark.parametrize("name", ["..", "../escape", "/escape", "foo\\bar"])
def test_asar_rejects_paths_outside_destination(tmp_path, name):
    archive = tmp_path / "app.asar"
    make_asar(archive, {name: {"size": 1, "offset": "0"}}, b"x")
    with pytest.raises(ValueError, match="Unsafe"):
        load_extractor().extract(archive, tmp_path / "out")


def test_asar_rejects_changed_file(tmp_path):
    archive = tmp_path / "app.asar"
    make_asar(archive, {"file": {"size": 1, "offset": "0", "integrity": {"algorithm": "SHA256", "hash": "wrong"}}}, b"x")
    with pytest.raises(ValueError, match="checksum"):
        load_extractor().extract(archive, tmp_path / "out")


@pytest.mark.skipif(not BASH, reason="Bash is required")
@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("restore", [False, True])
def test_release_transaction_preserves_data_and_recovers_failed_start(tmp_path, fails, restore):
    # Commands affect only a fixture directory; systemctl and link activation are
    # substituted. The real snapshot, copy, ordering and rollback functions run.
    fixture = tmp_path.as_posix()
    if sys.platform == "win32":
        fixture = "/" + fixture[0].lower() + fixture[2:]
    if restore:
        with tarfile.open(tmp_path / "restore.tar.gz", "w:gz") as archive:
            info = tarfile.TarInfo("preset")
            info.size = len(b"chosen-snapshot")
            archive.addfile(info, io.BytesIO(b"chosen-snapshot"))
    lib = (ROOT / "scripts/install-lib.sh").as_posix()
    python = Path(sys.executable).as_posix()
    script = f'''set -Eeuo pipefail
source '{lib}'
python3() {{ '{python}' "$@"; }}
base='{fixture}'
prefix="$base/opt"
data_dir="$base/data"
source_dir="$base/source"
unit=omi-client.service
role=client
operation_id=test-op
commit=test-commit
release="$prefix/new"
previous="$prefix/old"
unit_path="$base/unit"
unit_backup="$base/unit.backup"
snapshot="$base/backups/snapshot.tar.gz"
no_start=0
restore_snapshot={'"$base/restore.tar.gz"' if restore else ''}
stopped=0
mkdir -p "$release" "$previous" "$data_dir" "$source_dir/deploy/systemd"
printf 'user-preset' > "$data_dir/preset"
printf 'old-unit' > "$unit_path"
printf 'new-unit' > "$source_dir/deploy/systemd/$unit"
printf '%s' "$previous" > "$prefix/current"
printf 'old-commit' > "$previous/.code-revision"
status_write() {{ printf '%s:%s\\n' "$1" "$2" >> "$base/trace"; }}
atomic_link() {{ printf '%s' "$1" > "$2"; }}
systemctl() {{
    printf '%s\\n' "$*" >> "$base/trace"
    if [[ "$1" == start && $(cat "$prefix/current") == "$release" ]]; then
        printf 'new-schema-data' > "$data_dir/preset"
    fi
}}
wait_healthy() {{
    printf 'health:%s:%s\\n' "${{1:-$commit}}" "${{2:-$expected_target}}" >> "$base/trace"
    [[ "${{1:-$commit}}" == old-commit ]] && return 0
    return {1 if fails else 0}
}}
trap 'code=$?; if ((code)) && [[ "$stopped" == 1 ]]; then rollback_release; fi; exit "$code"' EXIT
activate_release
'''
    result = subprocess.run([BASH, "-c", script], capture_output=True, text=True)
    assert result.returncode == (1 if fails else 0), result.stderr
    trace = (tmp_path / "trace").read_text().splitlines()
    assert trace.index("running:stopping") < trace.index("stop omi-client.service") < trace.index("running:starting")
    assert (tmp_path / "backups/snapshot.tar.gz").is_file()
    with tarfile.open(tmp_path / "backups/snapshot.tar.gz") as archive:
        assert archive.extractfile("./preset").read() == b"user-preset"
    if fails:
        assert (tmp_path / "opt/current").read_text() == fixture + "/opt/old"
        assert (tmp_path / "data/preset").read_text() == "user-preset"
        assert (tmp_path / "data.failed-test-op/preset").read_text() == "new-schema-data"
        assert (tmp_path / "unit").read_text() == "old-unit"
        assert "health:old-commit:null" in trace
    else:
        assert (tmp_path / "opt/current").read_text() == fixture + "/opt/new"
        assert (tmp_path / "unit").read_text() == "new-unit"


@pytest.mark.skipif(not BASH, reason="Bash is required")
@pytest.mark.parametrize("unsafe", [False, True])
def test_bootstrap_resolves_immutable_commit_and_validates_source_before_execution(tmp_path, unsafe):
    commit = "a" * 40
    (tmp_path / "commit.json").write_text(json.dumps({"sha": commit}))
    script = b'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$OMI_FIXTURE_TRACE"\n'
    with tarfile.open(tmp_path / "source.tar.gz", "w:gz") as archive:
        info = tarfile.TarInfo("agent/scripts/install-release.sh" if not unsafe else "agent/../escape")
        info.size = len(script)
        archive.addfile(info, io.BytesIO(script))
    base = tmp_path.as_posix()
    if sys.platform == "win32":
        base = "/" + base[0].lower() + base[2:]
    python = Path(sys.executable).as_posix()
    bootstrap = (ROOT / "scripts/install.sh").as_posix()
    command = f'''set -Eeuo pipefail
export base='{base}'
export OMI_FIXTURE_TRACE="$base/trace"
id() {{ printf '0'; }}
uname() {{ printf 'Linux'; }}
python3() {{
    if [[ "$1" == - && "$2" == /var/lib/omi/client/update-status.json ]]; then
        shift 2
        '{python}' - "$base/update-status.json" "$@"
    else
        '{python}' "$@"
    fi
}}
curl() {{
    for argument in "$@"; do
        if [[ "$argument" == *'/commits/v3' ]]; then
            cp "$base/commit.json" "${{@: -1}}"
            return
        fi
    done
    [[ " $* " == *'/tar.gz/{commit} '* ]]
    cp "$base/source.tar.gz" "${{@: -1}}"
}}
export -f id uname python3 curl
bash '{bootstrap}' client --ref v3 --no-start
'''
    result = subprocess.run([BASH, "-c", command], capture_output=True, text=True)
    assert result.returncode == (1 if unsafe else 0), result.stderr
    if unsafe:
        assert not (tmp_path / "trace").exists()
        assert "Unsafe source archive" in result.stderr
    else:
        args = (tmp_path / "trace").read_text().splitlines()
        assert args[0] == "client"
        assert args[args.index("--commit") + 1] == commit
        assert "--no-start" in args
    status = json.loads((tmp_path / "update-status.json").read_text())
    assert status["status"] == ("failed" if unsafe else "running")


def test_systemd_bounds_restarts_and_does_not_download_on_start():
    for role, user in (("client", "root"), ("server", "omi")):
        unit = (ROOT / f"deploy/systemd/omi-{role}.service").read_text()
        assert f"User={user}" in unit
        assert "StartLimitBurst=3" in unit and "StartLimitIntervalSec=120" in unit
        assert "Restart=on-failure" in unit and "KillMode=control-group" in unit
        assert "curl" not in unit and "pip " not in unit and "git " not in unit
        assert f"--data-dir /var/lib/omi/{role}" in unit


def test_readiness_requires_prepared_commit_and_completed_recovery():
    spec = importlib.util.spec_from_file_location("verify_health", ROOT / "scripts/verify-health.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = {"preset_id": "show", "revision": "r1"}
    status = {"role": "client", "code_revision": "new", "booting": True, "busy": False,
              "state": {"health": "standby", "target": None}}
    assert not module.readiness({"role": "client"}, "client", "new", target)[0]
    assert not module.readiness(status, "client", "new", target)[0]
    status.update(booting=False)
    status["state"] = {"health": "error", "target": None, "detail": "MIDI restore failed"}
    assert not module.readiness(status, "client", "new", target)[0]
    status["state"] = {"health": "ready", "target": target}
    assert not module.readiness(status, "client", "old", target)[0]
    assert not module.readiness(status, "client", "new", None)[0]
    assert module.readiness(status, "client", "new", target)[0]
    status["state"]["health"] = "waiting"
    assert module.readiness(status, "client", "new", target)[0]
    status["busy"] = True
    assert not module.readiness(status, "client", "new", target)[0]
    assert module.readiness({"role": "server", "code_revision": "new"}, "server", "new")[0]


@pytest.mark.skipif(not BASH, reason="Bash is required")
@pytest.mark.parametrize("installed", [False, True])
def test_first_install_skips_absent_unit_but_existing_stop_errors_abort(tmp_path, installed):
    base = tmp_path.as_posix()
    if sys.platform == "win32":
        base = "/" + base[0].lower() + base[2:]
    lib = (ROOT / "scripts/install-lib.sh").as_posix()
    command = f'''set -Eeuo pipefail
source '{lib}'
base='{base}'
prefix="$base/opt"
release="$prefix/new"
previous={'"$prefix/old"' if installed else ''}
data_dir="$base/data"
source_dir="$base/source"
unit=omi-server.service
role=server
unit_path="$base/unit"
unit_backup="$base/unit.backup"
snapshot="$base/backups/snapshot.tar.gz"
operation_id=clean-test
commit=commit
no_start=0
mkdir -p "$release" "$data_dir" "$source_dir/deploy/systemd"
printf 'new-unit' > "$source_dir/deploy/systemd/$unit"
{'printf old-unit > "$unit_path"' if installed else ':'}
status_write() {{ :; }}
atomic_link() {{ printf '%s' "$1" > "$2"; }}
wait_healthy() {{ return 0; }}
systemctl() {{
    printf '%s\\n' "$*" >> "$base/trace"
    if [[ "$1" == stop ]]; then return 7; fi
}}
activate_release
'''
    result = subprocess.run([BASH, "-c", command], capture_output=True, text=True)
    assert result.returncode == (7 if installed else 0), result.stderr
    if installed:
        assert (tmp_path / "unit").read_text() == "old-unit"
        assert not (tmp_path / "backups/snapshot.tar.gz").exists()
    else:
        assert (tmp_path / "unit").read_text() == "new-unit"
        assert "stop omi-server.service" not in (tmp_path / "trace").read_text()


@pytest.mark.skipif(not BASH, reason="Bash is required")
def test_rollback_stop_failure_preserves_live_code_data_and_failure_status(tmp_path):
    base = tmp_path.as_posix()
    if sys.platform == "win32":
        base = "/" + base[0].lower() + base[2:]
    data = tmp_path / "data"
    data.mkdir()
    original_status = '{"id":"update-id","status":"failed","error":"new boot failed"}'
    (data / "update-status.json").write_text(original_status)
    (data / "omi.sqlite3").write_bytes(b"live database must stay here")
    (tmp_path / "current").write_text("new-code")
    (tmp_path / "snapshot.tar.gz").write_bytes(b"snapshot must stay here")
    lib = (ROOT / "scripts/install-lib.sh").as_posix()
    command = f'''set -Eeuo pipefail
source '{lib}'
base='{base}'
unit=omi-client.service
role=client
previous="$base/old"
prefix="$base"
data_dir="$base/data"
operation_id=update-id
snapshot="$base/snapshot.tar.gz"
unit_path="$base/unit"
unit_backup="$base/unit.backup"
status_write() {{ printf 'status:%s\\n' "$*" >> "$base/trace"; }}
atomic_link() {{ printf 'link:%s\\n' "$*" >> "$base/trace"; }}
mv() {{ printf 'mv:%s\\n' "$*" >> "$base/trace"; }}
tar() {{ printf 'tar:%s\\n' "$*" >> "$base/trace"; }}
systemctl() {{ printf 'systemctl:%s\\n' "$*" >> "$base/trace"; return 9; }}
if rollback_release; then exit 0; else exit 17; fi
'''
    result = subprocess.run([BASH, "-c", command], capture_output=True, text=True)
    assert result.returncode == 17, result.stderr
    assert (tmp_path / "trace").read_text().splitlines() == ["systemctl:stop omi-client.service"]
    assert (tmp_path / "current").read_text() == "new-code"
    assert (data / "omi.sqlite3").read_bytes() == b"live database must stay here"
    assert (data / "update-status.json").read_text() == original_status
    assert (tmp_path / "snapshot.tar.gz").read_bytes() == b"snapshot must stay here"
    assert not (tmp_path / "data.failed-update-id").exists()


@pytest.mark.skipif(not BASH, reason="Bash is required")
@pytest.mark.parametrize("observed", ["ready", "wrong_commit", "booting", "recovery_error", "wrong_target", "inactive"])
def test_rollback_verifies_previous_code_boot_and_restored_target(tmp_path, observed):
    saved_target = {"preset_id": "show", "revision": "applied-r1"}
    before = tmp_path / "before"
    before.mkdir()
    with sqlite3.connect(before / "omi.sqlite3") as connection:
        connection.execute("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO kv VALUES (?, ?)", ("execution", json.dumps({"target": saved_target})))
    (before / "preset").write_text("snapshot preset")
    (before / "update-status.json").write_text('{"id":"update-id","status":"running","phase":"stopping"}')
    with tarfile.open(tmp_path / "snapshot.tar.gz", "w:gz") as archive:
        archive.add(before, arcname=".")
    data = tmp_path / "data"
    data.mkdir()
    (data / "preset").write_text("failed new-code data")
    original_status = '{"id":"update-id","status":"failed","error":"new boot failed"}'
    (data / "update-status.json").write_text(original_status)
    old = tmp_path / "opt/old"
    old.mkdir(parents=True)
    (old / ".code-revision").write_text("old-commit")
    (tmp_path / "opt/current").write_text("new-code")
    (tmp_path / "unit").write_text("new-unit")
    (tmp_path / "unit.backup").write_text("old-unit")
    state = {"role": "client", "code_revision": "old-commit", "booting": False, "busy": False,
             "recovery_error": False, "state": {"health": "ready", "target": saved_target}}
    if observed == "wrong_commit":
        state["code_revision"] = "new-commit"
    elif observed == "booting":
        state["booting"] = True
    elif observed == "recovery_error":
        state["recovery_error"] = True
        state["state"].update(health="error", detail="Previous MIDI input failed to reopen")
    elif observed == "wrong_target":
        state["state"]["target"] = {"preset_id": "show", "revision": "wrong-r2"}
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            payload = json.dumps(state).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = tmp_path.as_posix()
    if sys.platform == "win32":
        base = "/" + base[0].lower() + base[2:]
    python = Path(sys.executable).as_posix()
    root = ROOT.as_posix()
    command = f'''set -Eeuo pipefail
source '{root}/scripts/install-lib.sh'
base='{base}'
prefix="$base/opt"
previous="$prefix/old"
data_dir="$base/data"
snapshot="$base/snapshot.tar.gz"
unit=omi-client.service
role=client
operation_id=update-id
unit_path="$base/unit"
unit_backup="$base/unit.backup"
source_dir='{root}'
commit=new-commit
expected_target='{{"preset_id":"incorrect-new-target","revision":"r2"}}'
python3() {{
    if [[ "$1" == */verify-health.py ]]; then
        printf '%s\\n' "$@" > "$base/health-args"
        '{python}' "$@" --port {server.server_port} --timeout 0.2
    else
        '{python}' "$@"
    fi
}}
atomic_link() {{ printf '%s' "$1" > "$2"; }}
systemctl() {{
    printf '%s\\n' "$*" >> "$base/trace"
    if [[ "$1" == is-active ]]; then return {3 if observed == "inactive" else 0}; fi
}}
if rollback_release; then
    result=0
else
    result=$?
    echo 'Automatic recovery failed; see INSTALL.md recovery.' >&2
fi
# Match the update cleanup: successful recovery still means the update failed.
status_write failed failed 'original update failure'
exit "$result"
'''
    try:
        result = subprocess.run([BASH, "-c", command], capture_output=True, text=True)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result.returncode == (0 if observed == "ready" else 1), result.stderr
    assert requests and all(path == "/api/status" for path in requests)
    if observed != "ready":
        assert "Automatic recovery failed" in result.stderr
    args = (tmp_path / "health-args").read_text().splitlines()
    assert args[args.index("--commit") + 1] == "old-commit"
    assert json.loads(args[args.index("--target") + 1]) == saved_target
    assert (tmp_path / "opt/current").read_text() == base + "/opt/old"
    assert (data / "preset").read_text() == "snapshot preset"
    assert (tmp_path / "data.failed-update-id/preset").read_text() == "failed new-code data"
    assert (tmp_path / "data.failed-update-id/update-status.json").read_text() == original_status
    assert (tmp_path / "unit").read_text() == "old-unit"
    update = json.loads((data / "update-status.json").read_text())
    assert update["status"] == "failed" and update["error"] == "original update failure"
