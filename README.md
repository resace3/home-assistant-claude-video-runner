# Home Assistant Claude Video Runner

A privacy-first, deterministic MoviePy pipeline for one-minute daily and weekly
Home Assistant data stories. The public repository contains only generic code
and synthetic fixtures. The supervised add-on can automatically read every sensor
and binary-sensor entity, rank the most useful local patterns, and turn them into
animated charts, comparisons, progress visuals, and practical reflections.

Storyboards are written by the **Claude Code CLI running inside the add-on on the
user's own Claude subscription**. There is no API key to paste anywhere, no
metered per-video charge, and no third-party model vendor in the path.

> This is not a medical device. Generated observations are descriptive, not medical advice. Review the privacy implications of `story_detail` before enabling `personal`.

## Architecture

```mermaid
flowchart LR
  A[Automatically discovered HA sensors] -->|SUPERVISOR_TOKEN stays here| B[Local collector]
  B --> C[Disclosure DTO shaped by story_detail]
  C --> D{Claude Code CLI or offline template}
  D -->|claude -p, subscription login| E[Strict Pydantic validation]
  D -->|any failure| E
  E --> F[Libby TTS at 1.0x]
  F --> G[Local MoviePy and FFmpeg]
  G --> H[Private staging and validation]
  H -->|manifest last| I[/share/personal_video_studio]
  J[(CLAUDE_CONFIG_DIR on the HA config mount)] -.->|read and refresh| D
  K[Advanced SSH add-on] -.->|one-time browser login| J
```

The Supervisor token exists only during the collector phase in an in-memory HTTP
client. The client is closed, the environment value and token-aware logging
filter are destroyed, and provider/TTS modules are imported only after that scrub
boundary. It is never written, logged, sent to a model or TTS service, passed on
a command line, put into Docker metadata, or exposed to a browser.

The browser-safe manifest contains titles, dates, filenames, duration, and a
short safe description. Model, voice, checksum, fallback reason, and category
audit records stay under private `/data`. Raw states and prompts are never
written into the shared catalog.

## Authentication: a subscription, not an API key

The runner shells out to `claude -p`. That CLI authenticates with the same
browser login a normal Claude Code session uses, so:

- There is no API key, and nothing to paste into add-on options.
- There is no per-video cost. Usage draws on the Claude subscription that is
  already paid for, under its normal limits.
- Nothing is sent to a third-party metered API in either `story_detail` mode.

The credential store is shared between two add-ons through `CLAUDE_CONFIG_DIR` on
the Home Assistant **configuration** mount:

| Add-on | Path to the same directory |
| --- | --- |
| Advanced SSH & Web Terminal (`a0d7b954_ssh`) | `/config/.claude-video-runner` |
| Personal Video Runner (Claude) | `/homeassistant/.claude-video-runner` |

This placement also fixes a latent problem. The SSH add-on's container
filesystem is ephemeral, so a plain `~/.claude` login there is **lost whenever
that add-on restarts or updates**. Putting `CLAUDE_CONFIG_DIR` on the
configuration mount makes one login survive restarts for both add-ons.

## Home Assistant installation

Do the one-time login first, then install the add-on.

1. Open the terminal of the **Advanced SSH & Web Terminal** add-on and run:

   ```sh
   mkdir -p /config/.claude-video-runner
   export CLAUDE_CONFIG_DIR=/config/.claude-video-runner
   claude
   ```

   If `claude` is not present in that add-on, install it first with
   `npm install -g @anthropic-ai/claude-code`.

2. Complete the browser login the CLI prints, then `/exit`. Verify with
   `ls -a /config/.claude-video-runner`; `.credentials.json` should exist.

3. Add this repository to the Home Assistant add-on store, install **Personal
   Video Runner (Claude)**, review its options, and explicitly enable
   `allow_external_tts` before starting it.

4. Start the add-on. Leave `claude_config_dir` at its default.

The add-on owns Python, MoviePy, FFmpeg, FFprobe, Node.js, the Claude Code CLI,
Libby TTS, and its scheduler. It does not depend on a host package install, and
it never stops, restarts, uninstalls, or weakens the SSH add-on.

With `generate_personal_on_start` enabled, the first start publishes one real
daily and one real weekly video from the instance's sensors. Recurring jobs run
inside the add-on and survive restarts.

A real scheduled run receives `SUPERVISOR_TOKEN` from Supervisor at runtime. The
runner uses it with `http://supervisor/core/api/states` and the history API, then
scrubs it before storyboard, TTS, or rendering work. Never paste that token into
configuration.

## Installing beside the original runner

This add-on has its own slug (`claude_video_runner`) and its own private `/data`
volume, so it installs and runs alongside the existing `personal_video_runner`
add-on. Both publish into the same `/share/personal_video_studio` catalog with
distinct video IDs, so **the existing Personal Video Studio viewer shows videos
from both runners with zero viewer changes**.

Stagger the two schedules by a few minutes; see [scheduling](docs/SCHEDULING.md).

## Commands

```text
video-runner doctor [--test-tts]
video-runner list-entities
video-runner preview-data --period daily [--synthetic]
video-runner generate --period daily|weekly [--synthetic] [--mock-tts]
video-runner generate-demo [--mock-tts]
video-runner validate-output PATH
video-runner rebuild-index
video-runner cleanup [--no-dry-run]
video-runner print-schedule-example
```

`preview-data` is the disclosure gate: it prints exactly what would be handed to
the storyboard provider under the current `story_detail` setting.

## Privacy and `story_detail`

Everything stays on the user's hardware in both modes; the difference is what the
local CLI is told.

- **`personal` (default)** sends real sensor display names, values, and trend
  descriptions to the Claude Code CLI running inside this add-on, so the video is
  genuinely about the user's life instead of a generic template. It travels only
  as far as that user's own Claude subscription carries it, on the same terms as
  any hand-typed Claude Code session.
- **`aggregate`** sends counts and category bands only: no entity names, no
  values, no identifiers.

Neither mode sends anything to a third-party metered API, and the Supervisor
token is scrubbed before any provider call in both. Choose `aggregate` if you
would not type a sensor name into a Claude Code session by hand.

Libby receives generic narration in both modes, without sensor names, values,
entity identifiers, attributes, raw history, coordinates, or timestamps.

Automatic mode reads all `sensor.*` and `binary_sensor.*` states up to the
configured fail-closed safety cap, then requests period history in bounded
batches with response-size and observation limits. Every usable entity is
considered locally; at most five high-signal readings reach the visual cards.

Copy `config.example.yaml` to private `/data`; it is ignored by Git. **Real
entity IDs, values, credentials, generated videos, narration, screenshots, Nabu
Casa URLs, tokens, cookies, and logs must never enter this public repository.**
`.gitignore`, `.dockerignore`, and `.gitleaks.toml` enforce that, including
Claude credential files (`.credentials.json`, `.claude*/`) and `sk-ant-` tokens.

## Voice and timing

The requested product label `Libby, British Warm` resolves to the provider voice
identifier `en-GB-LibbyNeural`, checked against the provider's live voice list;
production never silently substitutes another voice. Speech uses the natural
provider rate (`+0%`, 1.0x). Scripts are constrained to 145–160 words for
approximately one minute, using `seconds = word count / 150 * 60`; rendered audio
is also checked for an actual 145–160 WPM pace. If narration is long, shorten the
script or extend the scene within 55–65 seconds; never speed up Libby.

Edge TTS is external egress and is disabled by default. Before setting
`tts.allow_external_egress: true`, understand that the full narration is sent to
that provider. CI uses a local test tone. The Edge client is not an authenticated
enterprise speech SLA; users needing contractual processing terms should
implement another provider adapter.

`generate-demo` always uses the offline storyboard and synthetic data, so it
never launches the CLI. By default it renders both a daily and a weekly video
with Libby, which requires the explicit `tts.allow_external_egress: true`
disclosure setting. The `--mock-tts` test-tone option exists only for CI and
local pipeline diagnostics, not final user-facing videos.

## Storyboard provider and fallback

`storyboard_provider: claude` runs one `claude -p` invocation per video, bounded
by `claude_timeout_seconds` (60–900, default 300) and using `claude_model`
(default `claude-opus-5`). Output is validated against a strict Pydantic schema.

Any failure — no login, expired subscription, malformed output, timeout, missing
binary — falls back to the deterministic offline storyboard and still publishes a
complete video with the reason recorded in the private audit log. Setting
`storyboard_provider: offline` skips the CLI entirely and is fully supported.

MoviePy and FFmpeg render the final video locally; no generative video model is
used.

## Storage and atomic publication

```text
/share/personal_video_studio/
├── daily/YYYY/MM/{mp4,webp,vtt,json}
├── weekly/YYYY/{mp4,webp,vtt,json}
├── indexes/{daily,weekly,all}.json
└── temporary/
/data/personal_video_studio/
├── config.yaml
├── claude-home/
├── audit/
└── logs/
```

The runner renders in a same-filesystem temporary directory, validates
video/audio/duration/resolution independently with MoviePy and FFprobe, and
performs a complete FFmpeg decode and long-pause scan. Each render gets immutable
asset filenames; only after the whole bundle validates does the runner atomically
replace the stable `{video-id}.json` sidecar and indexes. A crash can leave an
unreferenced orphan, but it cannot expose a mixed old/new bundle. A lock prevents
duplicate renders and index rebuild races.

Installation creates an empty valid `indexes/all.json`; seeing zero videos after
viewer installation means no completed runner bundle has been published yet. Run
`generate-demo` to publish one daily and one weekly bundle, then refresh Personal
Video Studio.

`/share` is shared across add-ons and is not a security boundary: any add-on
granted share access may read it. The same applies to the configuration mount and
the credential directory. Install only trusted add-ons.

## Container and scheduler

One pinned multi-architecture image (`linux/amd64` and `linux/arm64`) powers the
add-on. On top of the hash-pinned Python base it installs Node.js 22 LTS from a
per-architecture checksum-verified tarball and a version-pinned
`@anthropic-ai/claude-code`. Neither floats on `latest`: a silent CLI upgrade
would change storyboard behaviour between rebuilds. Bump `NODE_VERSION`,
`NODE_SHA256_*`, and `CLAUDE_CODE_VERSION` in the `Dockerfile` deliberately;
Dependabot cannot see them.

A root entrypoint creates `/share/personal_video_studio`, private
`/data/personal_video_studio`, a writable `HOME`, and the `0700` credential
directory owned by UID 10001, then drops permanently to that UID before running
the scheduler. It fails fast with an actionable message if `claude` is not on
`PATH`. The image never receives the Docker socket.

Each generation runs in a child process so the child can scrub `SUPERVISOR_TOKEN`
before importing or calling any provider, while the long-lived scheduler retains
its supervised runtime credential for the next local collection.

## Tests

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/ruff format --check .
.venv/bin/ruff check .
.venv/bin/mypy src
.venv/bin/pytest -m 'not integration'
.venv/bin/pytest -m integration
```

The integration test creates real 55–65 second low-resolution daily and weekly
H.264/AAC MP4s from synthetic data, decodes them fully, samples all seven scenes,
and asserts both structural variation and within-scene motion. Public CI never
calls Home Assistant, the Claude CLI, or live TTS; it runs with an empty
`CLAUDE_CONFIG_DIR` so no developer login can leak into a test run.
`scripts/visual_qa.py` can build a three-second contact sheet and report
frame-change cadence, composition diversity, integrated loudness, silence,
duration, resolution, and file size for release QA.

## Update, rollback, and uninstall

- Update: `scripts/update.sh`
- Validate: `scripts/verify_installation.sh`
- Roll back: check out the previous signed/tagged release and rerun `scripts/install.sh`.
- Uninstall runtime: `scripts/uninstall.sh`. It deliberately preserves `/data`, `/share`, and the credential directory; delete those only after a separate explicit privacy decision.

See [scheduling](docs/SCHEDULING.md), [security policy](SECURITY.md), [contributing](CONTRIBUTING.md), and [release history](CHANGELOG.md).

## Limitations

- The Claude Code login must be performed interactively once; there is no headless first-time login path.
- Subscription rate limits apply. A limited run falls back to the offline storyboard rather than failing.
- Exact Libby availability depends on the live Edge voice catalog and network access.
- Audible autoplay is controlled by the browser; the viewer starts muted and provides a play/unmute fallback.
- Chrome emulation does not prove iOS or Android Companion App WebView behavior.
- Home Assistant Green should start at 720×1280, 24 fps, one render at a time.
- The production runner is intentionally not a privileged Docker-socket add-on.
