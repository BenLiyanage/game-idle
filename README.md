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

- Runs `bash tools/ci/verify.sh` as the canonical verification command.
- Downloads the pinned Linux Godot 4.7.2-stable binary in CI before verification.

See: `.github/workflows/ci.yml`
