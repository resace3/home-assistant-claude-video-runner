#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
rm -rf -- "$ROOT/.venv"
echo "Runtime removed. Private configuration and completed videos were preserved."
echo "Delete /data/personal_video_studio and /share/personal_video_studio manually only if intentional."

