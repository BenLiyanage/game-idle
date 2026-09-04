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
tools/agent/run_issue.sh <issue-number>
tools/agent/run_issue.sh <issue-number> --dry-run
```

The issue number is mandatory and must be a positive integer. The worker never scans for another issue and never substitutes a different issue.

Dry-run mode resolves issue metadata and reports the planned branch, worktree, Codex command, validation command, configured repair limit, and existing PR reuse state. It does not invoke Codex, push, create a PR, edit a PR, or change labels.

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
- `CODEX_AGENT_VALIDATION_COMMAND`: local validation command override.

The worker rejects obvious npm/container Codex binary paths. The future isolated container runtime remains separate work.

## Local Validation And Repair

The default local validation command is:

```bash
bash tools/agent/local_validate.sh
```

That script runs repository structure checks and Python unit tests. If Ruff is installed locally, it also runs Ruff. If Godot is available locally, it runs full `bash tools/ci/verify.sh`; otherwise it records that pinned Godot verification is left to GitHub-hosted CI.

When local validation fails, the worker returns the failure output to Codex for a bounded repair attempt before publishing. It does not retry indefinitely.

## Pull Request Behavior

On success, the worker commits all worktree changes, pushes only the deterministic issue branch, and checks for an existing open PR from that branch to `main`. If one exists, it updates the body. If none exists, it creates one using the repository PR template and `Closes #<issue>`.

The worker never pushes to `main`, never force-pushes, and never merges.

## Result Contract

The worker prints one JSON object and writes the same result to `CODEX_AGENT_RESULT_PATH`, or to `.codex-agent/issue-<issue-number>/result.json`.

Exit codes:

- `0`: `success`; Codex completed, local validation passed, branch was pushed, and one PR was created or updated.
- `10`: `blocked`; reserved for protected product or architecture blockers reported by Codex.
- `20`: `validation_failed`; local validation failed after the bounded repair opportunity.
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
6. Export or configure `DEV_ENGINE_RUNNER_DIR` and `DEV_ENGINE_RUNNER_NAME` for host Codex/Codex Remote.
7. Verify:

```bash
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
