#!/usr/bin/env bash
set -Eeuo pipefail

OPTIONS_FILE="/data/options.json"
DEFAULT_CLAUDE_CONFIG_DIR="/homeassistant/.claude-video-runner"
# Must match GenerationConfig.claude_home_dir in src/video_runner/config.py.
RUNNER_HOME="/data/personal_video_studio/claude-home"

fail() {
  echo "FATAL: $*" >&2
  exit 1
}

# Reads a string option from the Supervisor-rendered add-on options. Falls back
# to the default for a missing file, malformed JSON, a missing key, or a blank
# value, so a bad option never turns into an unreadable shell error.
read_string_option() {
  python3 - "$OPTIONS_FILE" "$1" "$2" <<'PY'
import json
import sys

path, key, default = sys.argv[1], sys.argv[2], sys.argv[3]
value = default
try:
    with open(path, encoding="utf-8") as handle:
        candidate = json.load(handle).get(key)
    if isinstance(candidate, str) and candidate.strip():
        value = candidate.strip()
except (OSError, ValueError, AttributeError):
    pass
print(value)
PY
}

if ! command -v claude >/dev/null 2>&1; then
  fail "the 'claude' CLI is not on PATH inside this container.
The image is built with a pinned @anthropic-ai/claude-code install, so this
almost always means a corrupted or hand-modified image. Reinstall the add-on
from the repository, or rebuild the image, then start it again. Set
storyboard_provider: offline to keep generating videos without Claude."
fi

CLAUDE_CONFIG_DIR="$(read_string_option claude_config_dir "$DEFAULT_CLAUDE_CONFIG_DIR")"
case "$CLAUDE_CONFIG_DIR" in
  /*) ;;
  *) fail "claude_config_dir must be an absolute path, got '${CLAUDE_CONFIG_DIR}'." ;;
esac
export CLAUDE_CONFIG_DIR

install -d -m 0755 -o runner -g runner \
  /share/personal_video_studio \
  /share/personal_video_studio/daily \
  /share/personal_video_studio/weekly \
  /share/personal_video_studio/indexes
install -d -m 0700 -o runner -g runner \
  /share/personal_video_studio/temporary \
  /data/personal_video_studio

# The Claude CLI writes assorted state (caches, shell snapshots) under HOME and
# refreshes its own OAuth tokens inside CLAUDE_CONFIG_DIR, so both must be
# writable by the unprivileged runner after the gosu drop.
export HOME="$RUNNER_HOME"
install -d -m 0700 -o runner -g runner "$RUNNER_HOME"

# Require the parent to exist already. `install -d` would happily create a
# container-local /homeassistant if the homeassistant_config map were missing,
# which looks like success but silently detaches the runner from the credential
# store the Advanced SSH add-on writes to.
CLAUDE_CONFIG_PARENT="$(dirname "$CLAUDE_CONFIG_DIR")"
if [ ! -d "$CLAUDE_CONFIG_PARENT" ]; then
  fail "the parent directory '${CLAUDE_CONFIG_PARENT}' of claude_config_dir does not exist.
For the default /homeassistant/.claude-video-runner this means the
homeassistant_config map is not attached: reinstall or update the add-on so
Supervisor applies the current config.yaml. If you set claude_config_dir
yourself, point it at a path under an existing mount such as /homeassistant or
/data."
fi

if ! install -d -m 0700 -o runner -g runner "$CLAUDE_CONFIG_DIR" 2>/dev/null; then
  fail "cannot create the Claude credential directory '${CLAUDE_CONFIG_DIR}'.
It must live on a mount this add-on can write. The default
/homeassistant/.claude-video-runner requires the homeassistant_config map,
which this add-on declares; if you changed claude_config_dir, point it back at
a path under /homeassistant or /data."
fi
# The interactive login in the Advanced SSH add-on runs as root, so the token
# files land root-owned on the shared config mount. Hand the whole tree to the
# runner UID or the headless refresh cannot rewrite them.
chown -R runner:runner "$CLAUDE_CONFIG_DIR"

if [ -z "$(ls -A "$CLAUDE_CONFIG_DIR" 2>/dev/null)" ]; then
  echo "WARNING: ${CLAUDE_CONFIG_DIR} is empty, so no Claude Code login is stored yet." >&2
  echo "WARNING: run the one-time login described in the add-on documentation from the" >&2
  echo "WARNING: Advanced SSH & Web Terminal add-on. Until then the runner falls back to" >&2
  echo "WARNING: the offline storyboard and still publishes videos." >&2
fi

if [[ "${1:-scheduler}" == "scheduler" ]]; then
  video-runner prepare-addon \
    --options /data/options.json \
    --config-out /data/personal_video_studio/config.yaml \
    --schedule-out /data/personal_video_studio/schedule.json
  chown runner:runner \
    /data/personal_video_studio/config.yaml \
    /data/personal_video_studio/schedule.json
  exec gosu runner:runner video-runner scheduler \
    --config /data/personal_video_studio/config.yaml \
    --schedule /data/personal_video_studio/schedule.json
fi

exec gosu runner:runner video-runner "$@"
