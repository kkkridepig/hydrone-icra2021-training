#!/usr/bin/env bash
# Run from the image's base shell. Never install torch or GPU runtime packages.
set -eo pipefail
server_ws="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
ppu_python="${HYDRONE_PPU_PYTHON:-python}"
[[ -z "${VIRTUAL_ENV:-}" ]] || { echo 'Deactivate any old venv first.' >&2; exit 1; }
mkdir -p "$server_ws/logs/server"
"$ppu_python" "$server_ws/tools/server/probe_ppu.py" > "$server_ws/logs/server/ppu-before.json"
[[ ! -e "$server_ws/.venvs/hydrone-ppu" ]] || { echo 'Existing environment preserved; inspect it before recreating.' >&2; exit 1; }
"$ppu_python" -m venv --system-site-packages "$server_ws/.venvs/hydrone-ppu"
source "$server_ws/.venvs/hydrone-ppu/bin/activate"
# A constraints file prevents dependency resolution from replacing vendor torch.
python - <<'PY' > "$server_ws/logs/server/ppu-protected-constraints.txt"
import importlib.metadata as m
for package in ('torch', 'torchvision', 'torchaudio', 'numpy'):
    try: print(package + '==' + m.version(package))
    except m.PackageNotFoundError: pass
PY
python -m pip install --index-url https://mirrors.aliyun.com/pypi/simple/ \
  -c "$server_ws/logs/server/ppu-protected-constraints.txt" \
  -r "$server_ws/tools/server/requirements-ppu.txt"
python "$server_ws/tools/server/probe_ppu.py" > "$server_ws/logs/server/ppu-after.json"
python - "$server_ws" <<'PY'
import json, sys
from pathlib import Path
root=Path(sys.argv[1]) / 'logs/server'
a=json.loads((root/'ppu-before.json').read_text()); b=json.loads((root/'ppu-after.json').read_text())
assert (a['torch'],a['torch_path'],a['cuda_interface']) == (b['torch'],b['torch_path'],b['cuda_interface']), 'PPU torch changed'
PY
python -m pip freeze > "$server_ws/logs/server/ppu-pip-freeze.txt"
echo PPU_ENV_OK
