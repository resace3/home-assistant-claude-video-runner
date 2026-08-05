# Security policy

Report vulnerabilities privately through GitHub Security Advisories. Do not open a public issue containing tokens, entity IDs, state values, videos, prompts, private URLs, logs, or screenshots.

Supported versions: the latest release. Security release blockers include token leakage, Claude credential leakage, arbitrary file access, raw-state egress, incomplete artifact publication, and secret-bearing public artifacts.

Before every push and release run full-history and working-tree Gitleaks scans. The `SUPERVISOR_TOKEN` must remain in the collector's in-memory HTTP client. Logs are redacted and public CI uses synthetic inputs only.

## Claude credentials

The Claude Code login lives in `claude_config_dir`, by default
`/homeassistant/.claude-video-runner`, on the Home Assistant configuration
mount. It is a real subscription credential and is treated as a secret:

- The directory is owned by UID 10001 with mode `0700`. The headless CLI needs
  write access there to rotate its own OAuth tokens.
- `.gitignore`, `.dockerignore`, and `.gitleaks.toml` block `.credentials.json`,
  `.claude*/` directories, and `sk-ant-` tokens from entering the repository or
  a build context. Do not weaken those patterns.
- The credential is never passed on a command line, written into image metadata,
  copied into `/share`, or included in any published artifact or log.
- Any add-on with `homeassistant_config` access can read that directory. It is
  not a security boundary between add-ons; install only trusted add-ons.

Revoke a leaked credential from your Claude account settings, delete the
directory contents, and repeat the one-time login.

## Vulnerability scanning and suppressions

Every image build is scanned with Trivy at `HIGH,CRITICAL` and the pipeline
fails on a finding. `.trivyignore` holds the only suppressions, currently eight
CVEs in npm packages vendored inside `@anthropic-ai/claude-code`. They are
suppressed because the image already pins the newest published release and the
vendored tree cannot be patched downstream — not because the findings were
judged unimportant. Each entry records why it is unreachable from this add-on.

Two rules govern that file. Suppressions cover third-party vendored trees only:
anything reachable from Python code under `src/` is fixed, never ignored. And
the list is re-reviewed on every `claude-code` version bump, with a hard review
date recorded in the file itself. Do not add an entry without a written
reachability argument.
