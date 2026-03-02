# Architecture (Phase 1)

Phase 1 intentionally contains only:

- A single UI scene (`scenes/main.tscn`) with a centered label.
- A single script (`scripts/main.gd`) that logs a startup message.

## Snapshot mechanic (debug)

For convenience (and to support PR screenshots), `main.gd` includes a tiny snapshot helper:

- Press **F12** to save a PNG screenshot to `user://snapshots/`.

No gameplay systems exist yet.
