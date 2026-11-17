#!/usr/bin/env bash
# Run outside the OMI service cgroup: SSH terminal or omi-update wrapper.
set -Eeuo pipefail
role=${1:-}; shift || true
case "$role" in client|server) ;; *) echo 'Usage: update.sh client|server [--ref v3|COMMIT] [--operation-id ID]' >&2; exit 2;; esac
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec bash "$script_dir/install.sh" "$role" --update "$@"
