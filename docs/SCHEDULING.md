# Scheduling

The supported scheduler runs inside the **Personal Video Runner (Claude)** Home Assistant add-on. Configure `daily_time`, `weekly_day`, and `weekly_time` in the add-on options. Monday is day `0`; Sunday is day `6`. Times use the Home Assistant host timezone and 24-hour `HH:MM` format.

This removes the unsupported cross-add-on `shell_command` bridge and survives host or add-on restarts. Do not place `SUPERVISOR_TOKEN` in automation YAML. Supervisor injects the token into the runner at runtime.

Each scheduled run launches the Claude Code CLI once, with a wall-clock budget of `claude_timeout_seconds`. A run that exceeds it, or that finds no usable login, falls back to the deterministic offline storyboard and still publishes a video; it does not skip the slot.

## Running beside the original runner

The original `personal_video_runner` add-on and this one can be enabled at the same time and publish into the same `/share/personal_video_studio` catalog. A shared lock prevents two renders from racing, but a run that is blocked on the lock does not queue.

Stagger them so scheduled slots do not collide:

- Give the two add-ons `daily_time` and `weekly_time` values at least five minutes apart.
- Set `generate_personal_on_start: false` on one of them, so a Supervisor restart that starts both does not fire two first-run generations at once.

## Advanced SSH & Web Terminal

The Advanced SSH & Web Terminal add-on is used once, interactively, for the Claude Code login described in the add-on documentation. It is not involved in scheduling, and this project never stops, restarts, uninstalls, or reconfigures it. The runner only reads and refreshes the credential directory that login creates on the shared configuration mount.
