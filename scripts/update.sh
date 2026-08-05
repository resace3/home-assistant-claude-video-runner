#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
git status --porcelain | grep -q . && { echo "Refusing update with local changes" >&2; exit 1; }
git pull --ff-only
exec scripts/install.sh

