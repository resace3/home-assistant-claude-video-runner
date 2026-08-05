#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
command -v python3 >/dev/null || { echo "Python 3 is required" >&2; exit 1; }
python3 -c 'import sys; raise SystemExit(0 if (3,12) <= sys.version_info < (3,14) else "Python 3.12 or 3.13 is required")'
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip==26.1.2
.venv/bin/pip install --require-hashes -r requirements.lock
.venv/bin/pip install --no-deps .
install -d -m 0700 /data/personal_video_studio
install -d -m 0755 /share/personal_video_studio/{daily,weekly,indexes}
install -d -m 0700 /share/personal_video_studio/temporary
"$ROOT/.venv/bin/video-runner" rebuild-index
echo "Installed. Copy config.example.yaml to /data/personal_video_studio/config.yaml and edit privately."
