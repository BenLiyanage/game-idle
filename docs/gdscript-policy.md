# Typed GDScript and warning policy

All repository-owned `.gd` files must pass Godot 4.7.2's parser with the warning levels configured in
`project.godot`. `bash tools/ci/verify.sh` is the only local and cloud verification entrypoint. It invokes
`tools/ci/check_gdscript.py`, which discovers every tracked or untracked, non-ignored `.gd` file outside Godot's
generated `.godot` directory and checks each script directly with pinned Godot's `--check-only` mode. Direct
checks ensure that scripts not yet referenced by a scene are still covered while ignored worktrees and generated
state remain outside the project policy.

Godot 4.7.2 can report a `SCRIPT ERROR` from `--check-only` while still returning process status zero. The
repository checker therefore treats Godot's script-load error diagnostics as failures in addition to honoring the
process status. The isolated negative fixture locks down this behavior.

The policy requires explicit static types for declarations, parameters, and return values. Typed inference with
`:=` is allowed because Godot resolves a static type at parse time. Untyped declarations and unsafe operations on
`Variant` values are errors. Godot's built-in correctness warnings are also errors, including unused or shadowed
declarations, unreachable code, lossy conversions, missing `await`, and ambiguous or invalid API use.
No directory-wide warning exclusion is configured; any future third-party add-on exception requires an explicit,
documented policy change.

Two built-in warnings remain disabled intentionally:

- `inferred_declaration`: `:=` is an accepted typed declaration.
- `return_value_discarded`: deliberately ignoring an API return value is common and is not part of the typing
  contract.

## Suppressions

Avoid warnings instead of suppressing them. If an engine callback or other unavoidable construct needs a
suppression, place one exact `@warning_ignore("warning_name")` annotation immediately before the affected
declaration or statement and add a nearby comment explaining why the warning cannot reasonably be avoided.

Region-wide `@warning_ignore_start` and `@warning_ignore_restore` annotations are prohibited and mechanically
rejected by `tools/ci/check_gdscript.py`. Do not weaken a warning globally in `project.godot` to accommodate a
single site.

## Policy fixtures

Canonical verification runs isolated fixtures from `tests/fixtures/gdscript`: a fully typed script must pass, an
untyped declaration and return value must fail with an untyped diagnostic, and a typed script with a disallowed
unused-parameter warning must fail with its expected diagnostic. Invalid fixtures use the `.gd.fixture` suffix so
intentionally invalid code never becomes a production Godot script.
