# Disposable Codex Issue Worker

`tools/agent/run_issue.sh <issue-number>` runs one explicitly selected GitHub issue through a local Codex implementation loop with a disposable worker boundary.

The trusted host launcher owns GitHub issue lookup, worktree creation, Docker invocation, and result collection. Code running inside the worker is untrusted. Model-generated and project-controlled commands run inside the worker container.

The default successful output is a deterministic change artifact. GitHub branch, commit, push, and pull request mutation are handled by the distinct trusted finalizer in `tools/agent/finalize_issue.sh`; see `docs/agent/trusted-finalizer.md`.

The worker intentionally does not choose issues, inspect lifecycle labels, react to `issues:labeled` events, enforce WIP limits, increase WIP above one, or merge pull requests. Those orchestration concerns remain out of scope.

## Requirements

- Host: macOS or Linux with `bash`, `python3`, `git`, `gh`, and Docker.
- For worker runs, host `gh` only needs issue read access.
- For finalizer runs, the trusted finalizer process needs narrow GitHub authority to push the expected issue branch and create or update the expected PR.
- Host `git` must be able to fetch `origin` for worker runs. Push is only required by the trusted finalizer or the supervised compatibility publisher.
- Worker image: `game-idle-codex-worker:local` by default, built from `tools/agent/Dockerfile`.
- Worker image contents: `python3`, `bash`, `codex`, and any runtime dependencies required by `bash tools/ci/verify.sh`, including pinned Godot when full verification is expected to pass.

Build the default worker image with:

```bash
docker build -f tools/agent/Dockerfile -t game-idle-codex-worker:local .
```

The Dockerfile uses the public `ghcr.io/openai/codex-universal:latest` development-environment base and installs the pinned Codex CLI package version matching the locally inspected CLI version for this ticket.

## Interface

```bash
tools/agent/run_issue.sh <issue-number>
tools/agent/run_issue.sh <issue-number> --dry-run
tools/agent/run_issue.sh <issue-number> --publish-local
tools/agent/finalize_issue.sh <artifact> --repo BenLiyanage/game-idle --issue <issue-number> --base-branch main --base-sha <sha>
```

The issue number is mandatory and must be a positive integer. The worker never scans or selects another issue.

Dry-run mode resolves the issue and reports the planned branch, worktree, Codex command, Docker command, verification command, and existing PR reuse state. It does not invoke Codex, push, create a PR, edit a PR, or change labels.

`--publish-local` is a supervised #8 compatibility path. It commits, pushes, and opens or updates a PR from the trusted host after artifact creation. Unattended operation must use `finalize_issue.sh` instead.

## Branch And Worktree Convention

By default, issue `123` uses:

- Branch: `agent/issue-123`
- Worktree: `.worktrees/agent-issue-123`
- Result artifacts: `.codex-agent/issue-123/`

The trusted host fetches `origin/main` and creates new issue worktrees from current `origin/main`. If the deterministic branch or worktree already exists, it is reused for explicit retries instead of creating duplicates. Existing failed work is preserved.

## Worker Boundary

The host launcher creates a fresh Docker container for every implementation run. The container is removed with `--rm`; deliberate artifacts in the result directory survive.

Repository-owned defaults live in `tools/agent/isolation.json`. The launcher validates the loaded configuration before constructing Docker arguments and fails closed when required isolation properties are missing or broadened. The default image is `game-idle-codex-worker:local`.

Default security controls:

```text
docker run --rm
  --network none
  --cpus 2
  --memory 4g
  --pids-limit 256
  --read-only
  --user 1000:1000
  --cap-drop ALL
  --security-opt no-new-privileges:true
  --tmpfs /tmp:rw,nosuid,nodev,size=512m
  --tmpfs /run:rw,nosuid,nodev,size=64m
```

Docker default seccomp protections are retained. The launcher does not pass `--privileged`, does not add capabilities, does not mount the Docker socket, and does not mount Ben's home directory.

Writable paths inside the worker:

- `/workspace`: the intended issue worktree only.
- `/results`: deliberate result and evidence artifacts only.
- `/tmp`: tmpfs.
- `/run`: tmpfs.

Read-only paths inside the worker:

- `/codex-home`: ephemeral minimal Codex auth/config home, only when Codex auth is required.

The root filesystem is read-only.

## Host Paths Mounted

The launcher mounts only these host paths:

- The deterministic issue worktree, writable, at `/workspace`.
- The deterministic result directory, writable, at `/results`.
- A temporary host directory containing copied Codex auth/config files, read-only, at `/codex-home`.

Forbidden mounts are machine-checked before launch. The launcher fails if a mount would expose Ben's home directory, SSH material, local GitHub credential paths, or Docker socket paths.

## Codex Authentication

The narrowest practical local mechanism found for the installed Codex CLI is `CODEX_HOME` containing Codex auth material. The launcher copies only these files from `CODEX_AGENT_CODEX_HOME_SOURCE`, defaulting to `~/.codex`:

- `auth.json`, required.
- `config.toml`, optional.

The copy is made into a temporary host directory, mounted read-only at `/codex-home`, and removed after the worker exits. Ben's general home directory and general-purpose credential stores are not mounted.

Residual risk: Codex auth/config are readable by the Codex process and technically by project-controlled subprocesses running as the same non-root worker user. The launcher minimizes and makes this exposure explicit. Repository-write credentials are not mounted into the worker and are reserved for the trusted finalizer.

## Network Policy

Default network policy is `--network none`. This is the policy used by the security probe and it prevents silent network broadening by default.

Codex implementation requires outbound access to the Codex/OpenAI service. Docker's built-in `bridge` network does not provide destination-level allow-listing by itself. To avoid pretending that Docker can enforce a narrow host allow-list without additional proxy or firewall infrastructure, bridge networking is permitted only when explicitly requested:

```bash
CODEX_AGENT_WORKER_NETWORK=bridge CODEX_AGENT_ALLOW_WORKER_NETWORK=1 tools/agent/run_issue.sh 35
```

When enabled, bridge networking is active for model/project-controlled execution inside the worker. The residual risk is ordinary container outbound network access for that run. Destination-level egress restriction is deferred until a real proxy/firewall boundary is introduced.

## Verification

The worker invokes the canonical repository verification path:

```bash
bash tools/ci/verify.sh
```

This command runs inside the same isolated worker after Codex implementation. The script is not weakened to compensate for local Godot availability. If pinned Godot is unavailable, verification fails honestly and the worker result preserves that failure.

Security tests do not depend on Godot.

## Security Probe

Run the repeatable negative test with:

```bash
python3 tests/agent/security_probe.py
```

The probe seeds representative protected host resources, including a host secret, fake SSH key, fake GitHub CLI credentials, fake Git credential store, and another repository. It then executes adversarial shell commands inside the exact Docker hardening path used by the launcher.

The probe asserts that the worker cannot:

- Read the host secret outside `/workspace`.
- Read local GitHub credentials.
- Read SSH material.
- Mutate another repository/worktree.
- Access the Docker socket or daemon.
- Write to read-only root filesystem paths such as `/etc`.
- Run without `NoNewPrivs`.
- Retain effective Linux capabilities.
- Silently acquire a default network route under the default policy.

The probe also asserts that the worker can:

- Read/write `/workspace`.
- Write `/results`.
- Preserve a deliberate result artifact after the container is destroyed.

If Docker is unavailable, the probe exits `77` and reports that the local Docker daemon is missing.

## Result Contract

The worker prints one JSON object and writes the same result to `CODEX_AGENT_RESULT_PATH`, or to `.codex-agent/issue-<issue-number>/result.json` under the invoking repository root.

Exit codes:

- `0`: `success`; Codex completed, verification passed in the disposable worker, and `change-artifact.json` was written. `pr` is `null` unless `--publish-local` was used.
- `10`: `blocked`; Codex reported a protected product or architecture blocker.
- `20`: `verification_failed`; canonical verification failed inside the isolated worker.
- `30`: `implementation_failed`; Codex failed or did not produce committable work.
- `40`: `infrastructure_failed`; local tools, authentication, Docker, network, or branch/worktree operations failed. Push or PR failures only apply to `--publish-local`.
- `64`: `usage_error`; the issue argument was missing or invalid.

The caller should use this result instead of parsing Codex prose.

The worker also writes `.codex-agent/issue-<issue-number>/change-artifact.json`, a schema-versioned JSON artifact containing repository, issue, base branch/SHA, branch identity, run identity, changed paths, a `git diff --binary` patch, and worker-side verification evidence. The trusted finalizer treats this artifact strictly as untrusted data.

## Trusted Finalizer

`tools/agent/finalize_issue.sh` consumes `change-artifact.json`, validates it against caller-supplied expectations, creates a commit with Git plumbing from a temporary index, pushes the expected issue branch, and creates or updates exactly one PR.

The finalizer does not invoke Codex, run Godot/tests, check out worker output, install dependencies, source shell code, or execute scripts from the proposed change. It preserves provenance in the resulting commit and PR body.

See `docs/agent/trusted-finalizer.md` for the artifact schema, validation rules, idempotency behavior, credential design, and residual-risk documentation.

## Supervised Compatibility Publisher

When `--publish-local` is supplied, the trusted host commits all worktree changes, pushes only the deterministic issue branch, and checks for an existing open PR from that branch to `main`. If one exists, it updates the body. If none exists, it creates one using the repository PR template and `Closes #<issue>`.

The worker never pushes to `main`, never force-pushes, and never merges.

## Deferred Protections

- GitHub Actions runner trust redesign remains #37.
- Godot provisioning remains #33.
- Multi-agent execution and WIP greater than one remain disabled.
