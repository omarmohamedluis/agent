#!/usr/bin/env bash
# Sourcing this file has no effects. Functions support fixture-based testing.
set -Eeuo pipefail

status_write() {
    local status=$1 phase=$2 message=${3:-}
    python3 - "$data_dir/update-status.json" "$operation_id" "$status" "$phase" "$message" <<'PY'
import json, os, pathlib, sys, time
path = pathlib.Path(sys.argv[1]); path.parent.mkdir(parents=True, exist_ok=True)
value = dict(id=sys.argv[2], status=sys.argv[3], phase=sys.argv[4], updated_at=time.time())
value['error' if sys.argv[3] == 'failed' else 'detail'] = sys.argv[5]
temp = path.with_suffix('.tmp')
with temp.open('w') as handle:
    json.dump(value, handle); handle.flush(); os.fsync(handle.fileno())
os.replace(temp, path)
PY
    if [[ "$role" == server ]] && id omi >/dev/null 2>&1; then chown omi:omi "$data_dir/update-status.json"; fi
    printf '[%s] %s\n' "$phase" "$message"
}

atomic_link() {
    local target=$1 link=$2
    ln -sfn -- "$target" "$link.new"
    mv -Tf -- "$link.new" "$link"
}

wait_healthy() {
    local port=8081
    [[ "$role" == server ]] && port=8080
    python3 "$source_dir/scripts/verify-health.py" --role "$role" --port "$port" \
        --commit "$commit" --target "${expected_target:-null}" --timeout 180
    systemctl is-active --quiet "$unit"
}

rollback_release() {
    status_write running rollback 'Restoring previous release and pre-update data' || true
    systemctl stop "$unit" || true
    if [[ -n "$previous" ]]; then atomic_link "$previous" "$prefix/current" || return 1; fi
    if [[ -n "${previous_satellite:-}" ]]; then atomic_link "$previous_satellite" "$prefix/satellite/current" || return 1; fi
    if [[ -f "$snapshot" ]]; then
        failed_data="${data_dir}.failed-${operation_id}"
        mv -- "$data_dir" "$failed_data" || return 1
        mkdir -p -- "$data_dir" || return 1
        tar -xzf "$snapshot" -C "$data_dir" || return 1
        if [[ "$role" == server ]]; then chown -R omi:omi "$data_dir" || return 1; fi
    fi
    if [[ -f "$unit_backup" ]]; then cp -- "$unit_backup" "$unit_path" || return 1; fi
    systemctl daemon-reload || return 1
    if [[ -n "$previous" ]]; then
        systemctl reset-failed "$unit" || true
        systemctl start "$unit" || return 1
    fi
}

activate_release() {
    status_write running stopping 'Prepared release verified; stopping OMI cleanly'
    # A clean host has no OMI unit yet. Stopping a missing unit is an error in
    # systemd; existing installations must still fail if their stop fails.
    if [[ -n "$previous" || -f "$unit_path" ]]; then systemctl stop "$unit"; fi
    stopped=1
    mkdir -p -- "$(dirname "$snapshot")"
    chmod 700 "$(dirname "$snapshot")"
    # Cleanly stopped: snapshot includes a coherent database/WAL/runtime set.
    tar -czf "$snapshot" -C "$data_dir" .
    if [[ -n "${restore_snapshot:-}" ]]; then
        mv -- "$data_dir" "${data_dir}.before-$operation_id"
        mkdir -p -- "$data_dir"
        tar -xzf "$restore_snapshot" -C "$data_dir"
        if [[ "$role" == server ]]; then chown -R omi:omi "$data_dir"; fi
    fi
    expected_target=null
    if [[ "$role" == client ]]; then
        expected_target=$(python3 - "$data_dir/omi.sqlite3" <<'PY'
import json,pathlib,sqlite3,sys
path=pathlib.Path(sys.argv[1])
target=None
if path.is_file():
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        row=db.execute("SELECT value FROM kv WHERE key='execution'").fetchone()
        if row: target=json.loads(row[0]).get('target')
print(json.dumps(target,separators=(',',':')))
PY
)
    fi
    if [[ -f "$unit_path" ]]; then cp -- "$unit_path" "$unit_backup"; fi
    cp -- "$source_dir/deploy/systemd/$unit" "$unit_path"
    atomic_link "$release" "$prefix/current"
    if [[ "$role" == client && -f "$release/.satellite-release" ]]; then
        atomic_link "$(cat "$release/.satellite-release")" "$prefix/satellite/current"
    fi
    systemctl daemon-reload
    systemctl enable "$unit"
    systemctl reset-failed "$unit" || true
    if [[ "$no_start" == 1 ]]; then
        status_write succeeded installed 'Installed and enabled; start explicitly after review'
        stopped=0
        return
    fi
    status_write running starting 'Starting prepared OMI release'
    systemctl start "$unit"
    wait_healthy
    status_write succeeded ready "Release $commit running; snapshot: $snapshot"
    stopped=0
}
