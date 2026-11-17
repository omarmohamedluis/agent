#!/usr/bin/env bash
# Standalone bootstrap resolves the source to one immutable commit.
set -Eeuo pipefail
role=${1:-}
case "$role" in client|server) shift ;; *) echo 'Usage: install.sh client|server [--ref v3|COMMIT] [--update] [--no-start]' >&2; exit 2;; esac
ref=v3
operation_id=
args=()
while (($#)); do
    case "$1" in
        --ref) ref=${2:?Missing ref}; shift 2 ;;
        --operation-id) operation_id=${2:?Missing operation ID}; args+=("$1" "$2"); shift 2 ;;
        *) args+=("$1"); shift ;;
    esac
done
[[ "$ref" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$ && "$ref" != *..* ]] || { echo 'Invalid source ref' >&2; exit 2; }
[[ $(id -u) == 0 ]] || { echo 'Run this installer with sudo bash.' >&2; exit 1; }
[[ $(uname -s) == Linux ]] || { echo 'Production installation requires Linux.' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
if ! command -v curl >/dev/null || ! command -v python3 >/dev/null; then
    apt-get update
    apt-get install -y ca-certificates curl python3
fi
if [[ -z "$operation_id" ]]; then
    operation_id=$(python3 -c 'import uuid; print(uuid.uuid4())')
    args+=(--operation-id "$operation_id")
fi
[[ "$operation_id" =~ ^[A-Za-z0-9-]{1,80}$ ]] || { echo 'Invalid operation ID' >&2; exit 2; }
bootstrap_status() {
    python3 - "/var/lib/omi/$role/update-status.json" "$operation_id" "$1" "$2" <<'PY'
import json,os,pathlib,sys,time
path=pathlib.Path(sys.argv[1]);path.parent.mkdir(parents=True,exist_ok=True)
if sys.argv[3]=='failed':
    try:
        previous=json.loads(path.read_text())
        if previous.get('id')==sys.argv[2] and previous.get('status')=='failed': raise SystemExit(0)
    except (OSError,ValueError): pass
value=dict(id=sys.argv[2],status=sys.argv[3],phase='download',updated_at=time.time())
value['error' if sys.argv[3]=='failed' else 'detail']=sys.argv[4]
temp=path.with_suffix('.bootstrap.tmp')
with temp.open('w') as handle:
    json.dump(value,handle);handle.flush();os.fsync(handle.fileno())
os.replace(temp,path)
PY
    if [[ "$role" == server ]] && id omi >/dev/null 2>&1; then chown omi:omi "/var/lib/omi/$role/update-status.json"; fi
}
bootstrap=$(mktemp -d /tmp/omi-bootstrap.XXXXXXXX)
trap 'result=$?; trap - EXIT; if ((result)); then bootstrap_status failed "Source preparation failed (exit $result); current service was not stopped by the bootstrap" || true; fi; rm -rf -- "$bootstrap"; exit "$result"' EXIT
bootstrap_status running 'Resolving and downloading immutable source'
if [[ "$ref" =~ ^[a-f0-9]{40}$ ]]; then
    commit=$ref
else
    curl --proto '=https' --tlsv1.2 -fsSL --connect-timeout 20 --max-time 300 --retry 3 \
        "https://api.github.com/repos/omarmohamedluis/agent/commits/$ref" -o "$bootstrap/commit.json"
    commit=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha"])' "$bootstrap/commit.json")
fi
[[ "$commit" =~ ^[a-f0-9]{40}$ ]] || { echo 'Invalid GitHub commit response' >&2; exit 1; }
curl --proto '=https' --tlsv1.2 -fsSL --connect-timeout 20 --max-time 300 --retry 3 \
    "https://codeload.github.com/omarmohamedluis/agent/tar.gz/$commit" -o "$bootstrap/source.tar.gz"
python3 - "$bootstrap/source.tar.gz" "$bootstrap/source" <<'PY'
import pathlib, sys, tarfile
target = pathlib.Path(sys.argv[2]); target.mkdir()
with tarfile.open(sys.argv[1]) as archive:
    entries = archive.getmembers()
    roots = {pathlib.PurePosixPath(e.name).parts[0] for e in entries}
    if len(roots) != 1: raise SystemExit('Invalid archive root')
    for entry in entries:
        parts = pathlib.PurePosixPath(entry.name).parts
        if entry.name.startswith('/') or '..' in parts or entry.issym() or entry.islnk() or not (entry.isfile() or entry.isdir()):
            raise SystemExit('Unsafe source archive entry: ' + entry.name)
        if len(parts) < 2: continue
        entry.name = '/'.join(parts[1:])
        archive.extract(entry, target)
PY
bash "$bootstrap/source/scripts/install-release.sh" "$role" --source "$bootstrap/source" --commit "$commit" "${args[@]}"
