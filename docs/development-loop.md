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

## Unattended Loop

1. PM cron reviews issues labeled `needs-grooming`, improves their specification, and moves ready issues to `groomed`.
2. Ben prioritizes work by moving exactly one issue from `groomed` to `selected-for-development`.
3. Dev cron moves the selected issue to `in-progress` and posts one Codex dispatch comment.
4. Codex Cloud implements the linked issue and opens one pull request.
5. PR automation moves the issue from `in-progress` to `in-review`.
6. CI runs `bash tools/ci/verify.sh`; automatic Codex review may supplement CI but does not replace it.
7. Ben reviews the PR and decides whether to merge.
8. Merged work moves to `waiting-for-release` until release preparation is approved.

Deduplication markers:

- `<!-- pm-grooming:v1 -->`
- `<!-- codex-dispatch:v1 -->`

## Main Branch Protection

`main` is protected by the repository ruleset recorded at `.github/rulesets/main.json` and applied in GitHub repository settings as `Protect main`.

The ruleset targets only `refs/heads/main` and enforces:

- pull requests for normal changes to `main`;
- one approving pull request review before merge, preserving Ben as the human merge approver for the initial secure unattended milestone;
- the deterministic GitHub Actions status check `verify`, from the `CI` workflow and the GitHub Actions app (`integration_id` 15368), with strict branch freshness;
- deletion protection for `main`;
- non-fast-forward protection to prevent force pushes to `main`.

The required `verify` check is repository-owned deterministic CI that runs `bash tools/ci/verify.sh`. LLM review, Codex review, Copilot review, or other stochastic model output must not be the sole deterministic status gate.

This is a current-stage policy, not a permanent architecture decision. Future work for #44 may add a narrowly controlled automated merge path only through a separately reviewed policy change that retains required deterministic checks, fails closed on ambiguity, and does not let automation broaden its own authority.

Recovery procedure:

1. Prefer fixing broken code, tests, workflow configuration, or runner availability through a normal pull request.
2. If `main` cannot be repaired through the protected path, Ben may temporarily edit or disable the `Protect main` ruleset in GitHub repository settings.
3. Make the smallest recovery change needed, then immediately restore the ruleset to match `.github/rulesets/main.json`.
4. Record the reason, commands or settings changed, and restoration result in the relevant issue or pull request.

Manual verification in GitHub settings:

- Repository settings -> Rules -> Rulesets contains an active repository ruleset named `Protect main`.
- The ruleset target is Branch and includes `refs/heads/main`.
- Rules include Require a pull request before merging, Require status checks to pass, Block force pushes, and Restrict deletions.
- Required status checks contains only the deterministic `verify` check from GitHub Actions unless a later approved policy change adds another deterministic repo-owned check.

## Codex Cloud Readiness

Connection status for this setup PR: Unable to determine.

Observed constraints:

- Local shell GitHub authentication is invalid.
- Outbound shell networking is unavailable in the Codex sandbox.
- The available GitHub connector can read issues, pull requests, branches, workflow runs, and repository metadata.
- The available GitHub connector exposes branch, file, commit, issue, pull request, and issue-label mutation tools.
- No available diagnostic confirmed that an `@codex` issue comment launches a Codex Cloud implementation task for this repository.

Manual setup and test procedure:

1. Confirm the Codex GitHub app is installed for `BenLiyanage/game-idle`.
2. Confirm Codex Cloud has a configured environment for this repository and can run `bash tools/ci/verify.sh`.
3. Create or choose a non-production test issue with clear acceptance criteria.
4. Add the `selected-for-development` label and ensure no other lifecycle label is present.
5. Post exactly one dispatch comment containing `<!-- codex-dispatch:v1 -->` and the requested `@codex` instruction.
6. Confirm that Codex Cloud creates one implementation branch and one linked pull request.
7. Confirm the PR runs the `verify` GitHub Actions job and that Ben remains the merge approver.

## Verification

CI is the authoritative full verification environment. Local agents must run `bash tools/ci/verify.sh` before completing work. If Godot is missing locally, full verification must fail clearly instead of being reported as a successful skip.
