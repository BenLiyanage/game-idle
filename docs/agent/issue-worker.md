# Local Codex Issue Worker

`tools/agent/run_issue.sh <issue-number>` runs one explicitly selected GitHub issue through a deterministic local Codex implementation loop.

The worker intentionally does not choose issues, inspect lifecycle labels, react to `issues:labeled` events, enforce WIP limits, or merge pull requests. Those orchestration concerns belong to issue #6. Credential separation and stronger execution hardening belong to issue #7.

## Requirements

- macOS or Linux with `bash`, `python3`, `git`, `gh`, and `codex` on `PATH`.
- `gh` must be authenticated with permission to read issues, push branches, and create or edit pull requests in `BenLiyanage/game-idle`.
- `git` must be able to fetch from and push to `origin`.
- `codex exec` must support the installed noninteractive flags used by the worker: `--cd`, `--sandbox`, `--ask-for-approval`, `--output-schema`, `--output-last-message`, optional `--model`, and `--config`.
- Repository verification still requires Godot 4.7.2-stable via `godot` on `PATH` or `GODOT_BIN`.

## Interface

```bash
tools/agent/run_issue.sh <issue-number>
tools/agent/run_issue.sh <issue-number> --dry-run
```

The issue number is mandatory and must be a positive integer. The worker never scans or selects another issue.

Dry-run mode resolves the issue and reports the planned branch, worktree, verification command, Codex command, and existing PR reuse state. It does not invoke Codex, push, create a PR, edit a PR, or change labels.

## Branch And Worktree Convention

By default, issue `123` uses:

- Branch: `agent/issue-123`
- Worktree: `.worktrees/agent-issue-123`

The worker fetches `origin/main` and creates new issue branches from the current `origin/main`. If the deterministic branch or worktree already exists, it is reused for explicit retries instead of creating duplicates. Existing failed work is preserved.

Set `CODEX_AGENT_WORKTREE_ROOT` to place worktrees outside the repository, for example on a self-hosted runner workspace.

## Codex Configuration

The default Codex invocation is noninteractive:

```bash
codex exec --cd <worktree> --sandbox workspace-write --ask-for-approval never --output-schema <schema> --output-last-message <json> -
```

Optional environment variables:

- `CODEX_AGENT_MODEL`: passed as `--model`.
- `CODEX_AGENT_REASONING`: passed through `--config`.
- `CODEX_AGENT_REASONING_CONFIG_KEY`: config key for reasoning, default `model_reasoning_effort`.
- `CODEX_AGENT_SANDBOX`: sandbox mode, default `workspace-write`.
- `CODEX_AGENT_APPROVAL_POLICY`: approval policy, default `never`.
- `CODEX_AGENT_CODEX_BIN`, `CODEX_AGENT_GH_BIN`, `CODEX_AGENT_GIT_BIN`: command overrides.
- `CODEX_AGENT_BRANCH_PREFIX`: branch prefix, default `agent/issue-`.
- `CODEX_AGENT_BASE_BRANCH`: PR base and branch base, default `main`.
- `CODEX_AGENT_REPO`: GitHub repository override, default parsed from `origin`.
- `CODEX_AGENT_RESULT_DIR` or `CODEX_AGENT_RESULT_PATH`: result artifact location.

Because the installed CLI exposes reasoning configuration through generic `--config` rather than a dedicated reasoning flag, the config key is explicit and independently configurable.

## Result Contract

The worker prints one JSON object and writes the same result to `CODEX_AGENT_RESULT_PATH`, or to `.codex-agent/issue-<issue-number>/result.json` under the invoking repository root.

Exit codes:

- `0`: `success`; Codex completed, verification passed, branch was pushed, and one PR was created or updated.
- `10`: `blocked`; Codex reported a protected product or architecture blocker.
- `20`: `verification_failed`; `bash tools/ci/verify.sh` failed.
- `30`: `implementation_failed`; Codex failed or did not produce committable work.
- `40`: `infrastructure_failed`; local tools, authentication, network, branch/worktree, push, or PR operations failed.
- `64`: `usage_error`; the issue argument was missing or invalid.

The caller should use this result instead of parsing Codex prose.

## Pull Request Behavior

On success, the worker commits all worktree changes, pushes only the deterministic issue branch, and checks for an existing open PR from that branch to `main`. If one exists, it updates the body. If none exists, it creates one using the repository PR template and `Closes #<issue>`.

The worker never pushes to `main`, never force-pushes, and never merges.

## Expected #6 Invocation

A future selected-issue workflow should pass the selected issue number directly:

```bash
CODEX_AGENT_WORKTREE_ROOT="$RUNNER_TEMP/game-idle-worktrees" tools/agent/run_issue.sh "$ISSUE_NUMBER"
```

The workflow should branch on the documented exit code or result JSON and should not require the worker to know about label events or WIP selection.
