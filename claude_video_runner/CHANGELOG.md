# Changelog

## 0.1.0

- First release of the Claude-driven runner, published as the separate
  `claude_video_runner` add-on so it can run beside the existing
  `personal_video_runner` install.
- Write storyboards with the Claude Code CLI on the user's Claude subscription.
  No API key, no per-video metered charge, and no external model provider.
- Share one credential store with the Advanced SSH & Web Terminal add-on through
  `CLAUDE_CONFIG_DIR` on the Home Assistant configuration mount, so the login
  survives add-on restarts instead of dying with the SSH container.
- Add `storyboard_provider`, `claude_model`, `claude_config_dir`,
  `claude_timeout_seconds`, and `story_detail` options.
- Add `story_detail`, choosing between real sensor names and values
  (`personal`) and counts only (`aggregate`) in the storyboard request.
- Keep publishing to `/share/personal_video_studio`, so the existing Personal
  Video Studio viewer shows videos from both runners unmodified.
- Fall back to the deterministic offline storyboard whenever the CLI is
  unavailable, unauthenticated, or over its time budget.
