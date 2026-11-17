#!/usr/bin/env bash
set -Eeuo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$source_dir/scripts/install-lib.sh"
role=${1:-}; shift || true
case "$role" in client|server) ;; *) echo 'Role must be client or server' >&2; exit 2;; esac
commit=local
update=0
no_start=0
operation_id=$(python3 -c 'import uuid; print(uuid.uuid4())')
while (($#)); do
    case "$1" in
        --source) source_dir=${2:?}; shift 2;;
        --commit) commit=${2:?}; shift 2;;
        --update) update=1; shift;;
        --no-start) no_start=1; shift;;
        --operation-id) operation_id=${2:?}; shift 2;;
        *) echo "Unknown installation argument: $1" >&2; exit 2;;
    esac
done
[[ "$commit" =~ ^[a-f0-9]{40}$ ]] || { echo 'Use scripts/install.sh to resolve an immutable source commit.' >&2; exit 2; }
[[ "$operation_id" =~ ^[A-Za-z0-9-]{1,80}$ ]] || { echo 'Invalid operation ID' >&2; exit 2; }
[[ $(id -u) == 0 && $(uname -s) == Linux ]] || { echo 'Installation requires root on Linux' >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo 'A running systemd is required' >&2; exit 1; }
source /etc/os-release
[[ "$ID" == debian || "$ID" == raspbian ]] || { echo 'Supported systems: Raspberry Pi OS 64-bit and Debian 12+' >&2; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
arch=$(dpkg --print-architecture)
[[ "$arch" == arm64 || "$arch" == amd64 ]] || { echo "Unsupported architecture: $arch" >&2; exit 1; }
if [[ "$role" == client ]]; then
    command -v nmcli >/dev/null || { echo 'NetworkManager is required; enable it before installation.' >&2; exit 1; }
fi
prefix=/opt/omi
data_dir=/var/lib/omi/$role
unit=omi-$role.service
unit_path=/etc/systemd/system/$unit
mkdir -p "$prefix/releases" "$data_dir" /etc/omi /var/backups/omi/"$role"
exec 9>/run/lock/omi-install.lock
flock -n 9 || { echo 'Another OMI installation/update is running' >&2; exit 1; }
if [[ -f /etc/omi/role && $(cat /etc/omi/role) != "$role" ]]; then
    echo 'This host is already installed for another OMI role.' >&2; exit 1
fi
for cron_user in root "${SUDO_USER:-root}"; do
    if command -v crontab >/dev/null && crontab -u "$cron_user" -l 2>/dev/null | grep -E 'start_agent\.sh|client/client\.py|omi-agent' >/dev/null; then
        echo "Remove the old OMI startup line from crontab -u $cron_user -e, then retry." >&2; exit 1
    fi
done
if [[ "$role" == client ]]; then
    if systemctl is-active --quiet satellite.service || systemctl is-enabled --quiet satellite.service; then
        echo 'Stop and disable the standalone satellite.service before installing OMI.' >&2; exit 1
    fi
fi
previous=
previous_satellite=
if [[ -L "$prefix/current" && -d "$prefix/current" ]]; then previous=$(readlink -f "$prefix/current"); fi
if [[ -L "$prefix/satellite/current" && -d "$prefix/satellite/current" ]]; then previous_satellite=$(readlink -f "$prefix/satellite/current"); fi
[[ "$update" == 0 || -n "$previous" ]] || { echo 'No installed release to update' >&2; exit 1; }
release="$prefix/releases/$commit-$role"
snapshot="/var/backups/omi/$role/$(date -u +%Y%m%dT%H%M%SZ)-$operation_id.tar.gz"
unit_backup="$prefix/releases/.unit-$operation_id"
stopped=0
cleanup() {
    result=$?
    trap - EXIT
    if ((result)); then
        if [[ "$stopped" == 1 ]]; then rollback_release || echo 'Automatic recovery failed; see INSTALL.md recovery.' >&2; fi
        status_write failed failed "Installation/update failed (exit $result); previous release: ${previous:-none}" || true
    fi
    rm -f -- "$unit_backup"
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT TERM
status_write running packages 'Preparing system packages while current release remains running'
export DEBIAN_FRONTEND=noninteractive
packages=(ca-certificates curl python3 python3-pip python3-venv tar xz-utils util-linux sudo iproute2)
if [[ "$role" == client ]]; then
    packages+=(build-essential pkg-config libasound2-dev libusb-1.0-0 libudev1 libfontconfig1 i2c-tools)
fi
apt-get update
apt-get install -y --no-install-recommends "${packages[@]}"
if [[ "$role" == server ]]; then
    id omi >/dev/null 2>&1 || useradd --system --home-dir /var/lib/omi --shell /usr/sbin/nologin omi
    chown -R omi:omi "$data_dir"
fi
if [[ ! -f "$release/.complete" ]]; then
    [[ ! -e "$release" ]] || { echo "Incomplete release exists: $release; inspect and move it aside before retry." >&2; exit 1; }
    mkdir -p "$release"
    cp -a "$source_dir/." "$release/"
    printf '%s\n' "$commit" > "$release/.code-revision"
    printf '{"commit":"%s"}\n' "$commit" > "$release/omi/_build.json"
    chmod +x "$release"/scripts/*.sh
    status_write running python 'Installing locked Python dependencies into the new release'
    python3 -m venv "$release/venv"
    "$release/venv/bin/python" -m pip install --disable-pip-version-check -r "$release/requirements-$role.lock"
    "$release/venv/bin/python" -m pip install --disable-pip-version-check --no-deps --no-build-isolation "$release"
    "$release/venv/bin/python" -m pip check
    if [[ "$role" == client ]]; then
        status_write running satellite 'Preparing pinned Companion Satellite and bundled runtimes'
        bash "$release/scripts/install-satellite.sh" "$prefix/satellite" --prepare-only
        python3 - "$release/deploy/satellite-lock.json" "$arch" "$prefix/satellite" > "$release/.satellite-release" <<'PY'
import json,sys
lock=json.load(open(sys.argv[1]))
print(f'{sys.argv[3]}/{lock["version"]}-{lock["build"]}-{sys.argv[2]}')
PY
    fi
    check_dir=$(mktemp -d /tmp/omi-check.XXXXXXXX)
    if [[ "$role" == client ]]; then
        OMI_SATELLITE_DIR=$(cat "$release/.satellite-release") "$release/venv/bin/omi-maintain" --data-dir "$check_dir" doctor --role "$role" --offline
    else
        "$release/venv/bin/omi-maintain" --data-dir "$check_dir" doctor --role "$role" --offline
    fi
    rm -rf -- "$check_dir"
    entry=omi-agent
    [[ "$role" == server ]] && entry=omi-server
    "$release/venv/bin/$entry" --help >/dev/null
    touch "$release/.complete"
fi
printf '%s\n' "$role" > /etc/omi/role
if [[ ! -f /etc/omi/omi.env ]]; then install -m 640 "$release/deploy/omi.env" /etc/omi/omi.env; fi
install -m 755 "$release/deploy/omi-update" /usr/local/sbin/omi-update
if [[ "$role" == server ]]; then
    printf 'omi ALL=(root) NOPASSWD: /usr/local/sbin/omi-update server *\n' > /etc/sudoers.d/omi-update
    chmod 440 /etc/sudoers.d/omi-update
    visudo -cf /etc/sudoers.d/omi-update
fi
activate_release
echo "OMI $role ready. Code: $release. Data: $data_dir"
