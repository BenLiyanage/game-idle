# Host-Native Codex Issue Worker

`tools/agent/run_issue.sh <issue-number>` runs one explicitly selected GitHub issue through the existing host Codex installation and publishes or updates one pull request. This is the supervised Dev Engine self-hosted runner path for issue #6.

The active path is host-native. It does not invoke Docker, does not use the npm-installed container Codex CLI, does not copy Codex auth into a container, and does not merge pull requests.

## Requirements

- Host: macOS or Linux with `bash`, `python3`, `git`, `gh`, and the existing authenticated host `codex` installation.
- Host `gh` must be authenticated with permission to read issues, push branches, and create or edit pull requests in `BenLiyanage/game-idle`.
- Host `git` must be able to fetch from and push to `origin`.
- The GitHub self-hosted runner must be repository-scoped and labeled `dev-engine`.

## Interface

```bash
tools/agent/run_issue.sh --preflight
tools/agent/run_issue.sh <issue-number>
tools/agent/run_issue.sh <issue-number> --dry-run
```

The issue number is mandatory and must be a positive integer. The worker never scans for another issue and never substitutes a different issue.

Preflight mode resolves the configured host Codex executable to an absolute path and invokes `codex --version`. It does not resolve an issue, create a worktree, invoke Codex against product code, or mutate GitHub.

Dry-run mode runs the executable preflight, resolves issue metadata, and reports the planned branch, worktree, Codex command, validation command, configured repair limit, and existing PR reuse state. It does not run Codex against product code, push, create a PR, edit a PR, or change labels.

## Branch And Worktree Convention

By default, issue `123` uses:

- Branch: `agent/issue-123`
- Worktree: `.worktrees/agent-issue-123`
- Result artifacts: `.codex-agent/issue-123/`

The worker fetches `origin/main` and creates new issue branches from current `origin/main`. If the deterministic branch or worktree already exists, it is reused for explicit retries instead of creating duplicates. Existing failed work is preserved.

## Codex Invocation

The worker invokes host Codex directly in the deterministic worktree:

```text
codex exec --sandbox workspace-write --ask-for-approval never --output-last-message <artifact> -
```

Optional environment variables:

- `CODEX_AGENT_CODEX_BIN`: host Codex binary, default `codex`.
- `CODEX_AGENT_MODEL`: model override.
- `CODEX_AGENT_REASONING`: reasoning effort override.
- `CODEX_AGENT_SANDBOX`: sandbox override, default `workspace-write`.
- `CODEX_AGENT_APPROVAL_POLICY`: approval policy, default `never`.
- `CODEX_AGENT_MAX_REPAIR_ATTEMPTS`: bounded local validation repair attempts, default `1`, maximum `3`.

The worker rejects obvious npm/container Codex binary paths. The future isolated container runtime remains separate work.

The trusted self-hosted workflow injects `CODEX_AGENT_CODEX_BIN` from the repository Actions variable with the same name. Configure that variable to the absolute path returned by `command -v codex` in the authenticated host Codex environment. This explicit job contract avoids depending on an interactive-shell PATH captured when the runner was registered. It identifies the existing executable only; Codex authentication remains in the existing host runtime and is not copied.

Before resolving issue metadata or creating a worktree, the worker verifies that the configured path is executable and that `--version` succeeds. A missing, stale, or unexecutable path returns the bounded `infrastructure_failed` result and exit code `40`.

## Verification And Repair

The default validation command is the same canonical command used by GitHub-hosted CI:

```bash
bash tools/ci/verify.sh
```

The local and remote policy must not drift. Device setup may differ, but the checked behavior should come from `tools/ci/verify.sh`, not a parallel copy of structure, Ruff, Python, or Godot checks.

When canonical verification fails for implementation reasons, the worker returns the failure output to Codex for a bounded repair attempt before publishing. It does not retry indefinitely.

If a host is missing a device prerequisite such as Godot, the worker records that incomplete local evidence distinctly instead of claiming the check passed. The development laptop for this runner path has Godot `4.7.2-stable` installed locally at:

```text
/Users/benliyanage/.local/bin/godot
```

## Pull Request Behavior

On success, the worker commits all worktree changes, pushes only the deterministic issue branch, and checks for an existing open PR from that branch to `main`. If one exists, it updates the body. If none exists, it creates one using the repository PR template and `Closes #<issue>`.

The worker never pushes to `main`, never force-pushes, and never merges.

## Result Contract

The worker prints one JSON object and writes the same result to `CODEX_AGENT_RESULT_PATH`, or to `.codex-agent/issue-<issue-number>/result.json`.

Exit codes:

- `0`: `success`; Codex completed, canonical verification passed locally or recorded a distinct missing-device-prerequisite status, branch was pushed, and one PR was created or updated.
- `10`: `blocked`; reserved for protected product or architecture blockers reported by Codex.
- `20`: `validation_failed`; canonical verification failed after the bounded repair opportunity.
- `30`: `implementation_failed`; Codex failed or did not produce committable work.
- `40`: `infrastructure_failed`; local tools, authentication, branch/worktree, push, or PR operations failed.
- `50`: `capacity`; Codex reported capacity exhaustion.
- `64`: `usage_error`; the issue argument or configuration was invalid.

The caller should use this result instead of parsing Codex prose.

## Runner Control

`tools/runner/dev_engine_runner.sh start|stop|status` is the narrow host control surface for the known Dev Engine runner service. It requires:

```bash
DEV_ENGINE_RUNNER_DIR=/absolute/path/to/actions-runner
DEV_ENGINE_RUNNER_NAME=<expected-runner-name>
```

Optional:

```bash
DEV_ENGINE_RUNNER_REPO_URL=https://github.com/BenLiyanage/game-idle
```

The script fails closed unless the configured directory contains GitHub runner identity metadata matching the expected runner name and repository URL. It delegates only to that runner's supported `svc.sh start`, `svc.sh stop`, or `svc.sh status` commands. It is not a generic service-management API and does not use unrestricted passwordless `sudo`.

## One-Time Runner Registration Boundary

Runner registration needs a short-lived GitHub runner registration token and must not be committed. Minimal setup:

1. In GitHub, open `BenLiyanage/game-idle` -> Settings -> Actions -> Runners -> New self-hosted runner.
2. Follow GitHub's repository-scoped runner download and `config.sh` commands on the laptop.
3. Use a dedicated runner name such as `game-idle-dev-engine`.
4. Add the dedicated label `dev-engine`.
5. Install the service with GitHub's supported runner service command from that runner directory.
6. From the existing authenticated host Codex environment, resolve the executable and store its absolute path in the repository Actions variable:

```bash
command -v codex
gh variable set CODEX_AGENT_CODEX_BIN --repo BenLiyanage/game-idle --body "$(command -v codex)"
```

7. Install pinned Godot locally or set `GODOT_BIN` so `bash tools/ci/verify.sh` is runnable on the laptop.
8. Export or configure `DEV_ENGINE_RUNNER_DIR` and `DEV_ENGINE_RUNNER_NAME` for host Codex/Codex Remote.
9. Verify:

```bash
CODEX_AGENT_CODEX_BIN="$(command -v codex)" tools/agent/run_issue.sh --preflight
tools/runner/dev_engine_runner.sh status
tools/runner/dev_engine_runner.sh start
tools/runner/dev_engine_runner.sh stop
```

After registration, normal operation should use the committed control script rather than manual service commands.

## GitHub Actions Orchestration

`.github/workflows/selected-issue-dev-engine.yml` is triggered by `issues:labeled`. Only the trusted default-branch workflow can target the self-hosted `dev-engine` runner label. No PR-controlled workflow targets the laptop runner.

The claim job runs under a repository-wide concurrency group, re-reads the current issue, confirms the `selected-for-development` label is still present, checks for any other open `in-progress` issue, and then moves the selected issue to `in-progress`. The host runner job starts only after that claim succeeds.

Queued jobs remain queued at GitHub while the runner service is stopped. There is no local polling daemon.

## Deferred Work

- Automatic CI-failure repair remains #53.
- Isolated container worker runtime remains #35/#51/#52.
- Additional runner trust hardening remains #36/#37.
