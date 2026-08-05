# Personal Video Runner (Claude)

Headless Home Assistant add-on that generates the daily and weekly media consumed by Personal Video Studio.

The add-on automatically discovers Home Assistant sensor and binary-sensor
entities, reads current states plus daily and weekly history through the
supervised Core API, and writes the storyboard with the **Claude Code CLI running
on your own Claude subscription**. There is no API key to paste and no per-video
metered charge. The `claude` binary ships inside the image and is invoked
headlessly with `claude -p`; it authenticates from a credential directory you
create once and share with the Advanced SSH & Web Terminal add-on.

Real friendly names and values appear on the video cards. The exact
`en-GB-LibbyNeural` voice narrates at a natural 1.0x using generic text, so raw
Home Assistant readings and the Supervisor credential are never sent to a
text-to-speech service.

This add-on installs alongside the original `personal_video_runner` add-on and
writes to the same `/share/personal_video_studio` tree, so videos from both
appear in the existing Personal Videos viewer with no viewer change.

See [DOCS.md](DOCS.md) for the one-time login and the full option reference.
