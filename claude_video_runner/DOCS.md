# Personal Video Runner (Claude)

## One-time Claude Code login

The runner drives the Claude Code CLI headlessly using **your Claude
subscription**. There is no API key, and no per-video charge. Authentication is
the ordinary Claude Code browser login, performed once from a shell and stored in
a directory both add-ons can read.

Do the login **before** starting this add-on.

1. Install and start the **Advanced SSH & Web Terminal** add-on
   (`a0d7b954_ssh`) if you have not already, and open its terminal.
2. Create the shared credential directory and log in:

   ```sh
   mkdir -p /config/.claude-video-runner
   export CLAUDE_CONFIG_DIR=/config/.claude-video-runner
   claude
   ```

   If `claude` is not installed in the SSH add-on, install it first:

   ```sh
   npm install -g @anthropic-ai/claude-code
   ```

3. Complete the browser login that the CLI prints, then type `/exit`.
4. Confirm the credential file exists:

   ```sh
   ls -a /config/.claude-video-runner
   ```

   You should see `.credentials.json`.

5. Install and start **Personal Video Runner (Claude)**. Leave
   `claude_config_dir` at its default.

The same host directory appears as `/config/.claude-video-runner` inside the
Advanced SSH add-on and as `/homeassistant/.claude-video-runner` inside this
add-on. Both names point at the same files; the difference is only how each
add-on maps the Home Assistant configuration directory.

### Why the config mount, and not `~/.claude`

The Advanced SSH add-on's container filesystem is ephemeral. A plain `claude`
login there writes to `~/.claude` inside that container, and **that login is lost
every time the SSH add-on restarts or updates**. Pointing `CLAUDE_CONFIG_DIR` at
the Home Assistant configuration mount fixes that for both add-ons at once: the
login survives restarts, and the runner reads and refreshes the same tokens
headlessly.

The runner owns that directory at UID 10001 with mode `0700` so the headless CLI
can rotate its own OAuth tokens. The interactive login in the SSH add-on runs as
root, which can still read and write those files.

## Configuration

1. Keep `auto_discover_sensors` enabled to read every `sensor.*` and, when
   `include_binary_sensors` is enabled, every `binary_sensor.*` entity through
   Home Assistant's internal Core API.
2. Enable `allow_external_tts` only after accepting that generic narration text
   is sent to Edge TTS. Sensor names and values stay on the local visual cards
   and are never included in that narration.
3. Start the add-on. With `generate_personal_on_start` enabled, the first start
   publishes one real daily and one real weekly sensor story.
4. Keep `boot: auto` enabled so the internal scheduler survives host and add-on
   restarts.

`weekly_day` uses Monday `0` through Sunday `6`. Times use the Home Assistant
host timezone and 24-hour `HH:MM` format.

Set `auto_discover_sensors: false` only when you want the explicit
`entity_allowlist` mode. The runner reads all discovered states and histories,
then selects at most five informative readings for a one-minute video.

### Claude options

| Option | Default | Meaning |
| --- | --- | --- |
| `storyboard_provider` | `claude` | `claude` writes the storyboard with the local CLI. `offline` uses the deterministic template and never launches the CLI. |
| `claude_model` | `claude-opus-5` | Model passed to `claude --model`. Any model your subscription can reach is valid. |
| `claude_config_dir` | `/homeassistant/.claude-video-runner` | Shared credential directory. Must be an absolute path on a mount the add-on can write. |
| `claude_timeout_seconds` | `300` | Wall-clock budget for one storyboard call, 60–900. On timeout the run falls back to the offline storyboard. |
| `story_detail` | `personal` | See the privacy tradeoff below. |

### `story_detail`: what reaches the model

Both values keep everything on your hardware. Neither sends anything to a
third-party metered API, and the Supervisor token is scrubbed before any provider
call in either mode.

- **`personal` (default)** sends real sensor display names, values, and trend
  descriptions to the Claude Code CLI running inside this add-on, so the
  storyboard is genuinely about your week rather than a generic template. The
  request leaves the container only as far as your own Claude subscription
  carries it, under the same terms as any other Claude Code session you run.
- **`aggregate`** sends counts and category bands only — no entity names, no
  values, no identifiers. The result is a correct but far less specific story.

Choose `aggregate` if you would not paste a sensor name into a Claude Code
session by hand. Choose `personal` if you would.

## Storage

The add-on writes media only below `/share/personal_video_studio` and private
scheduler and audit data below its own `/data/personal_video_studio` volume. It
additionally maps the Home Assistant configuration directory, read-write, for the
single purpose of the shared credential store at `claude_config_dir`.

`/share` is shared across add-ons and is not a security boundary; any add-on
granted share access can read it. Install only trusted add-ons.

## Running next to the original add-on

This add-on and the original `personal_video_runner` can be installed and
enabled at the same time. They use different slugs and different private `/data`
volumes, and they publish into the same `/share/personal_video_studio` catalog
with distinct video IDs. The Personal Video Studio viewer needs no change to show
both.

If both add-ons are scheduled at the same minute they will contend for the render
lock; stagger `daily_time` and `weekly_time` by a few minutes, or set
`generate_personal_on_start: false` on one of them.

## Troubleshooting

- **`WARNING: ... is empty, so no Claude Code login is stored yet`** — the
  one-time login above has not run, or it wrote somewhere else. Re-run it with
  `CLAUDE_CONFIG_DIR` exported, then restart this add-on.
- **`FATAL: cannot create the Claude credential directory`** — `claude_config_dir`
  points somewhere the add-on cannot write. Return it to the default.
- **Storyboards are always generic** — check the log for a fallback reason. A
  missing login, an expired subscription, or a `claude_timeout_seconds` that is
  too low for the chosen model all fall back to the offline template rather than
  failing the run.
- **`FATAL: the 'claude' CLI is not on PATH`** — the image is broken. Reinstall
  the add-on, or set `storyboard_provider: offline` to keep publishing videos in
  the meantime.
