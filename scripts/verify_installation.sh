#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT/.venv/bin/video-runner" doctor --test-tts --config /data/personal_video_studio/config.yaml
"$ROOT/.venv/bin/video-runner" rebuild-index --config /data/personal_video_studio/config.yaml
test -r /share/personal_video_studio/indexes/all.json
"$ROOT/.venv/bin/python" -c 'import json; from pathlib import Path; payload=json.loads(Path("/share/personal_video_studio/indexes/all.json").read_text()); assert isinstance(payload, list)'
echo "Installation verification passed."
