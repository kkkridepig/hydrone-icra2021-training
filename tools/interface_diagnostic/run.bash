#!/usr/bin/env bash
set -eo pipefail
diagnostic_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
export HYDRONE_ROOT="$diagnostic_root"
export HYDRONE_BACKEND="${HYDRONE_BACKEND:-ppu}"
source "$diagnostic_root/tools/server/setup.bash"
exec python "$diagnostic_root/tools/interface_diagnostic/diagnose.py" "$@"
