# Changelog

## 0.1.1 - 2026-08-05

First published image. The `v0.1.0` tag failed its pre-publish vulnerability
scan and never produced a container, so no `0.1.0` image exists.

- Upgrade `aiohttp` to 3.14.3 in `requirements.lock`, fixing CVE-2026-69244.
  The 0.1.0 lock still pinned 3.14.1.
- Expose `video_id_namespace` in the add-on options and schema. It was already
  a `SchedulerOptions` field, so it silently defaulted and could not be cleared
  from the Home Assistant UI.
- Add `.trivyignore` covering eight CVEs in npm packages vendored inside
  `@anthropic-ai/claude-code`, which pins the newest published release and
  cannot be patched downstream. Each entry carries a reachability argument and
  the file carries a review date. See `SECURITY.md`.
- Replace a real Home Assistant entity identifier in the friendly-name test with
  a synthetic one.

## 0.1.0 - 2026-08-05

First release of the Claude-driven runner. This repository forks
`resace3/home-assistant-codex-video-runner` at its 0.4.0 release and restarts
versioning at 0.1.0; the renderer, collector, validation, and atomic publication
carry over unchanged. Release history before this point lives in that
repository.

- Replace the OpenAI storyboard provider with the **Claude Code CLI** driven
  headlessly by `claude -p`. Authentication is the user's Claude subscription
  login, not an API key, so there is no key to store and no per-video metered
  charge. The `openai` dependency is removed from `pyproject.toml` and
  `requirements.lock`.
- Ship Node.js 22 LTS and a pinned `@anthropic-ai/claude-code` in the runner
  image. Node is checksum-verified per architecture, matching how the base image
  and Python dependencies are pinned.
- Share one credential store between the Advanced SSH & Web Terminal add-on and
  the runner through `CLAUDE_CONFIG_DIR` on the Home Assistant configuration
  mount. This also makes the login survive SSH add-on restarts, which an
  ephemeral `~/.claude` login does not.
- Publish as the `claude_video_runner` add-on, installable beside the existing
  `personal_video_runner`. Both write to `/share/personal_video_studio`, so the
  Personal Video Studio viewer shows videos from either runner unmodified.
- Add `storyboard_provider`, `claude_model`, `claude_config_dir`,
  `claude_timeout_seconds`, and `story_detail` add-on options.
- Add the `story_detail` disclosure control: `personal` sends real sensor names,
  values, and trends to the local CLI; `aggregate` sends counts only.
- Create the credential directory and a writable `HOME` for UID 10001 in the
  entrypoint, so the headless CLI can refresh its own OAuth tokens, and fail
  early with an actionable message when `claude` is missing from `PATH`.
- Extend `.gitignore`, `.dockerignore`, and `.gitleaks.toml` to cover Claude
  credential files so a stray copy cannot be committed.
- Drop the OpenAI model and cost policy from the documentation. Cost is now
  whatever the user's Claude subscription already covers.
