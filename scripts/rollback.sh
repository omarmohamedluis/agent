#!/usr/bin/env bash
# Offline manual recovery. Explicit source release and optional data snapshot.
set -Eeuo pipefail
role=${1:-}; requested=${2:-}; shift 2 || true
case "$role" in client|server) ;; *) echo 'Usage: rollback.sh client|server RELEASE_DIRECTORY [--snapshot ARCHIVE]' >&2; exit 2;; esac
[[ $(id -u) == 0 && $(uname -s) == Linux ]] || { echo 'Recovery requires root on Linux' >&2; exit 1; }
prefix=/opt/omi
release=$(readlink -f -- "$requested")
[[ "$release" == "$prefix/releases/"* && -f "$release/.complete" ]] || { echo 'Choose a complete installed release under /opt/omi/releases' >&2; exit 2; }
[[ "$(basename "$release")" == *"-$role" ]] || { echo 'Release belongs to a different role' >&2; exit 2; }
restore=
while (($#)); do
    case "$1" in
        --snapshot) restore=$(readlink -f -- "${2:?}"); shift 2;;
        *) echo 'Unknown recovery argument' >&2; exit 2;;
    esac
done
if [[ -n "$restore" ]]; then
    [[ "$restore" == "/var/backups/omi/$role/"* && -f "$restore" ]] || { echo 'Snapshot must be under the role backup directory' >&2; exit 2; }
    tar -tzf "$restore" >/dev/null
fi
exec 9>/run/lock/omi-install.lock
flock -n 9 || { echo 'Installation/update already running' >&2; exit 1; }
source "$release/scripts/install-lib.sh"
source_dir=$release
data_dir=/var/lib/omi/$role
unit=omi-$role.service
unit_path=/etc/systemd/system/$unit
operation_id="recovery-$(date -u +%Y%m%dT%H%M%SZ)"
commit=$(cat "$release/.code-revision")
previous=
previous_satellite=
if [[ -L "$prefix/current" && -d "$prefix/current" ]]; then previous=$(readlink -f "$prefix/current"); fi
if [[ -L "$prefix/satellite/current" && -d "$prefix/satellite/current" ]]; then previous_satellite=$(readlink -f "$prefix/satellite/current"); fi
snapshot="/var/backups/omi/$role/$operation_id.tar.gz"
unit_backup="$prefix/releases/.unit-$operation_id"
no_start=0
stopped=0
trap 'code=$?; trap - EXIT; if ((code)); then if [[ "$stopped" == 1 ]]; then rollback_release || true; fi; status_write failed failed "Recovery failed (exit $code)" || true; fi; rm -f -- "$unit_backup"; exit "$code"' EXIT
restore_snapshot=$restore
activate_release
