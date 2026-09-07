#!/usr/bin/env bash
set -eo pipefail
server_ws="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
source "$server_ws/tools/server/setup.bash"
exec python "$server_ws/tools/server/run.py" "$@"
