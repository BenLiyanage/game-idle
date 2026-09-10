# game-idle

Phase 1 bootstrap for a minimal Godot 4.7.2-stable project.

## What this is

- A barebones Godot 4.7.2-stable project that boots to a single responsive UI screen showing **“Hello Idle”**.
- No gameplay, economy, prestige, ads, analytics, or abstraction layers.
- The project uses typed GDScript and the Compatibility renderer.

## Run locally

1. Install **Godot 4.7.2-stable**.
2. Open the project by selecting this folder in Godot (it contains `project.godot`).
3. Press **Play**.

You should see a centered label: **Hello Idle**.

### Screenshot / snapshot

While running, press **F12** to write a PNG screenshot to `user://snapshots/`.

## Export

This repo includes a placeholder `export_presets.cfg` (no signing, no platform-specific setup yet).

In Godot:
- Project → Export…
- Add a preset for your target platform
- Export

## CI

GitHub Actions runs on pushes and pull requests to `main`:

- Runs `bash tools/ci/bootstrap.sh` to prepare the versions pinned by `.godot-version` and
  `requirements-ruff.txt`.
- Runs `bash tools/ci/verify.sh` as the canonical verification command.
- Keeps bootstrap and verification separate so environment failures are distinguishable from code failures.

Bootstrap supports GitHub-hosted Linux x86_64 and macOS arm64/x86_64. It installs tools under
`$(git rev-parse --git-common-dir)/game-idle-tools`, so one checkout and its `agent/issue-N` worktrees share the
same idempotent toolchain without adding generated files to Git status. The bootstrap command prints a JSON
environment contract; direct tool users can prepend its `bin_dir` to `PATH`, while `verify.sh` resolves the shared
location itself.

See: `.github/workflows/ci.yml`
