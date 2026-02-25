# game-idle

Phase 1 bootstrap for a minimal Godot 4.x project.

## What this is

- A barebones Godot 4.x project that boots to a single responsive UI screen showing **“Hello Idle”**.
- No gameplay, economy, prestige, ads, analytics, or abstraction layers.

## Run locally

1. Install **Godot 4.x**.
2. Open the project by selecting this folder in Godot (it contains `project.godot`).
3. Press **Play**.

You should see a centered label: **Hello Idle**.

## Export

This repo includes a placeholder `export_presets.cfg` (no signing, no platform-specific setup yet).

In Godot:
- Project → Export…
- Add a preset for your target platform
- Export

## CI

GitHub Actions runs on pushes and pull requests to `main`:

- Validates the required file/folder structure exists.
- Attempts a lightweight headless Godot sanity check (downloads a Linux headless Godot binary on CI and runs it against the project).

See: `.github/workflows/ci.yml`
