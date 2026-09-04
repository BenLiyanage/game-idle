# Development Loop

This repository uses GitHub issues as the work queue and pull requests as Ben's review queue. The initial development work-in-progress limit is one issue.

## Lifecycle Labels

Use GitHub labels for workflow state:

- `needs-grooming`
- `groomed`
- `selected-for-development`
- `in-progress`
- `in-review`
- `blocked`
- `waiting-for-release`

Only one lifecycle label should be present on an issue at a time. If the GitHub integration cannot create these labels automatically, Ben must create them manually in repository settings.

## Supervised Host Runner Loop

1. PM cron reviews issues labeled `needs-grooming`, improves their specification, and moves ready issues to `groomed`.
2. Ben prioritizes work by moving exactly one issue from `groomed` to `selected-for-development`.
3. The trusted default-branch `Selected Issue Dev Engine` workflow re-reads the issue state and enforces WIP=1 under a repository-wide concurrency group.
4. The workflow moves the selected issue to `in-progress` and queues one laptop job for the repository-scoped self-hosted runner labeled `dev-engine`.
5. Ben may start or stop the supervised runner window through `tools/runner/dev_engine_runner.sh start|stop|status` from the existing host Codex installation, including through Codex Remote.
6. The laptop runner invokes `tools/agent/run_issue.sh <issue-number>`, which runs the existing authenticated host Codex installation in a deterministic issue worktree.
7. Codex gets a bounded `bash tools/ci/verify.sh` feedback/fix opportunity before the worker commits, pushes, and creates or reuses one pull request.
8. PR automation moves the issue from `in-progress` to `in-review`.
9. CI runs `bash tools/ci/verify.sh`; automatic Codex review may supplement CI but does not replace it.
10. Ben reviews the PR and decides whether to merge.
11. Merged work moves to `waiting-for-release` until release preparation is approved.

Deduplication markers:

- `<!-- pm-grooming:v1 -->`
- `<!-- dev-engine-claim:v1 -->`

## Main Branch Protection

`main` is protected by the repository ruleset recorded at `.github/rulesets/main.json` and applied in GitHub repository settings as `Protect main`.

The ruleset targets only `refs/heads/main` and enforces:

- pull requests for normal changes to `main`;
- the deterministic GitHub Actions status check `verify`, from the `CI` workflow and the GitHub Actions app (`integration_id` 15368), with strict branch freshness;
- deletion protection for `main`;
- non-fast-forward protection to prevent force pushes to `main`.

For the current single-maintainer milestone, required approving pull request reviews are set to `0`. Ben explicitly performs the merge after reviewing the PR, and that merge action is the current human approval boundary. No separate approving review is required while the repository has no independent disposable, least-privilege reviewer or merger identity.

The required `verify` check is repository-owned deterministic CI that runs `bash tools/ci/verify.sh`. LLM review, Codex review, Copilot review, or other stochastic model output must not be the sole deterministic status gate.

This is a current-stage policy, not a permanent architecture decision. Future reviewer/security work may revisit independent approval only after an appropriately isolated, least-privilege, revocable or disposable identity exists. Future work for #44 may add a narrowly controlled automated merge path only through a separately reviewed policy change that retains required deterministic checks, fails closed on ambiguity, and does not let automation broaden its own authority.

Recovery procedure:

1. Prefer fixing broken code, tests, workflow configuration, or runner availability through a normal pull request.
2. If `main` cannot be repaired through the protected path, Ben may temporarily edit or disable the `Protect main` ruleset in GitHub repository settings.
3. Make the smallest recovery change needed, then immediately restore the ruleset to match `.github/rulesets/main.json`.
4. Record the reason, commands or settings changed, and restoration result in the relevant issue or pull request.

Manual verification in GitHub settings:

- Repository settings -> Rules -> Rulesets contains an active repository ruleset named `Protect main`.
- The ruleset target is Branch and includes `refs/heads/main`.
- Rules include Require a pull request before merging, Require status checks to pass, Block force pushes, and Restrict deletions.
- Required approving reviews is `0`.
- Required status checks contains only the deterministic `verify` check from GitHub Actions unless a later approved policy change adds another deterministic repo-owned check.

## Dev Engine Runner Control

The committed runner control surface is:

```bash
DEV_ENGINE_RUNNER_DIR=/absolute/path/to/actions-runner DEV_ENGINE_RUNNER_NAME=game-idle-dev-engine tools/runner/dev_engine_runner.sh status
DEV_ENGINE_RUNNER_DIR=/absolute/path/to/actions-runner DEV_ENGINE_RUNNER_NAME=game-idle-dev-engine tools/runner/dev_engine_runner.sh start
DEV_ENGINE_RUNNER_DIR=/absolute/path/to/actions-runner DEV_ENGINE_RUNNER_NAME=game-idle-dev-engine tools/runner/dev_engine_runner.sh stop
```

The script controls only the configured GitHub runner service after checking the local runner identity. One-time runner registration still requires a short-lived GitHub registration token from repository settings and is documented in `docs/agent/issue-worker.md`.

## Initial Supervised Run

Manual setup and test procedure:

1. Register the repository-scoped GitHub runner and label it `dev-engine`.
2. Confirm `tools/runner/dev_engine_runner.sh status` recognizes only that runner identity.
3. Start the runner for a supervised development window.
4. Create or choose one groomed test issue with clear acceptance criteria.
5. Add the `selected-for-development` label and ensure no other lifecycle label is present.
6. Confirm the `Selected Issue Dev Engine` workflow moves that issue to `in-progress`.
7. Confirm the laptop runner invokes `tools/agent/run_issue.sh <issue-number>`.
8. Confirm exactly one linked PR is created or updated and the normal GitHub-hosted `verify` check starts.
9. Stop the runner after the supervised window.

## Verification

CI is the authoritative full verification environment. Local agents must run the same canonical `bash tools/ci/verify.sh` before completing work; they must not copy its checks into a parallel local policy. If a device prerequisite is missing, report the environment gap and fix the device setup when the host is expected to run that check.
