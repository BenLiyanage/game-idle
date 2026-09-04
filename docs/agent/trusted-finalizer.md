# Trusted Issue Finalizer

Issue #36 establishes this trust boundary:

```text
trusted launcher
  -> disposable untrusted worker
       Codex and repository verification, no GitHub write authority
  -> JSON change artifact as data
  -> trusted finalizer
       validates expected repo/issue/base/result, commits, pushes, opens PR
```

`tools/agent/finalize_issue.sh` is the trusted finalizer entrypoint. It must run outside the disposable worker, on a host where GitHub write authority is intentionally available.

## Worker Artifact Contract

`tools/agent/run_issue.sh <issue-number>` writes `.codex-agent/issue-<issue-number>/change-artifact.json` after Codex implementation and worker-side verification succeed.

The artifact is untrusted data. Schema version `1` contains:

- `repository`: expected `owner/name`.
- `issue.number`, `issue.title`, `issue.url`: traceability to the selected issue.
- `base.branch`, `base.sha`: exact intended base.
- `branch`: deterministic issue branch, normally `agent/issue-<number>`.
- `run.id`: stable issue/base run identity unless `CODEX_AGENT_RUN_ID` is supplied.
- `status`: must be `success`.
- `change.format`: `git-diff-binary`.
- `change.changed_paths`: relative repository paths expected after applying the patch.
- `change.patch`: the literal `git diff --binary --no-ext-diff` output.
- `verification`: worker-produced evidence, including `command: bash tools/ci/verify.sh` and `trusted_finalizer_reran: false`.
- `provenance`: worker contract and artifact location.
- `publisher`: whether any supervised compatibility publisher ran.

The artifact hash is `sha256` of canonical JSON with sorted keys and compact separators. The trusted finalizer records this hash in the commit message and PR body.

## Finalizer Usage

```bash
tools/agent/finalize_issue.sh \
  .codex-agent/issue-36/change-artifact.json \
  --repo BenLiyanage/game-idle \
  --issue 36 \
  --base-branch main \
  --base-sha <expected-origin-main-sha>
```

The caller supplies the expected repository, issue, base branch, and base SHA. Suspicious or stale artifacts fail closed instead of being repaired.

## Validation Before Mutation

Before pushing or creating/editing a PR, the finalizer validates:

- local `origin` resolves to the expected repository;
- artifact `repository` matches the expected repository;
- artifact issue number matches the expected issue;
- artifact base branch and SHA match the expected base;
- current `origin/<base-branch>` still resolves to the expected base SHA;
- artifact branch matches the deterministic issue branch or explicit `--branch`;
- schema version, required objects, status, run id, verification command, and change format are valid;
- changed paths are relative repository paths, not absolute paths, path traversal, NUL-containing paths, or `.git` metadata;
- applying the patch to a temporary Git index produces exactly the declared changed paths;
- there is no more than one open PR for the expected head/base branch pair.

Malformed, incomplete, mismatched, stale, or duplicated PR identity state fails closed.

## Finalizer Safety

The finalizer never invokes Codex, runs Godot/tests, installs dependencies, checks out the worker tree, sources shell code, or executes scripts from the proposed change.

Patch finalization uses Git plumbing only:

- `git read-tree <base-sha>` seeds a temporary index.
- `git apply --cached --binary` applies the patch as data.
- `git diff --cached --name-only <base-sha>` validates resulting paths.
- `git write-tree` writes the tree.
- `git commit-tree -p <base-sha>` creates the commit without hooks.
- `git update-ref` updates the local issue branch.
- `git push origin refs/heads/<branch>:refs/heads/<branch>` publishes that branch.

The trusted path avoids checkout-triggered execution, repository hooks, dependency installation, worker command strings, and worker-defined scripts. The Git environment sets `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_SYSTEM=/dev/null`, `GIT_ATTR_NOSYSTEM=1`, and `GIT_TERMINAL_PROMPT=0` for Git operations.

## Idempotency

The artifact hash is the stable idempotency key. A retry:

- does not rerun Codex;
- does not create another worktree;
- reuses an existing local or remote issue branch commit when its commit message already contains the same `Artifact-Hash`;
- does not create a duplicate commit for that finalized artifact;
- checks for an existing open PR for the exact expected head/base pair;
- edits the existing PR body instead of creating another PR;
- fails if more than one matching open PR exists.

## Credential Design

The disposable worker must not receive GitHub write authority:

- no write-capable `GITHUB_TOKEN`;
- no `GH_TOKEN`;
- no mounted local `gh` auth directory;
- no SSH key capable of GitHub push;
- no git credential store;
- no PAT or long-lived personal GitHub credential;
- no signing, release, or repository settings credential.

The trusted finalizer may use a narrow GitHub credential available only to the finalizer process. The intended credential is a GitHub App installation token scoped only to `BenLiyanage/game-idle`, with:

- contents: read/write, only for branch creation/update;
- pull requests: read/write, only for create/update PR;
- issues: read, only for linked issue context if needed by the caller;
- metadata: read.

It must not have permission to modify repository settings/rulesets, bypass branch protection, push to `main`, create releases, access signing keys, or access repositories other than `BenLiyanage/game-idle`.

Credential location: outside the worker filesystem, in the trusted launcher/finalizer environment only. Do not mount it into Docker. If local `gh` auth is used temporarily for supervised manual runs, that host auth is considered trusted-finalizer credential material and must not be exposed to `tools/agent/run_issue.sh` containers.

Rotation/revocation: revoke the GitHub App installation or rotate its private key/token source. Remove any temporary local `gh` auth used for supervised manual testing with `gh auth logout` or by deleting the host credential entry.

Residual risk: if the trusted finalizer host or credential is compromised, an attacker can update the expected issue branch and PR within that credential's permissions. Branch protection must still prevent direct `main` modification, and the credential must not be able to alter rulesets or repository settings.

If the available authentication cannot be narrowed to these permissions, unattended operation must stop short of finalization instead of introducing a broad PAT.

## PR Evidence Checklist

Every finalizer-created PR body must state:

- trusted and untrusted processes;
- credential location and permissions;
- proof the worker lacked repository-write credentials;
- artifact/change format;
- validation rules;
- idempotency mechanism;
- duplicate branch/commit/PR prevention;
- how malicious worker content is kept inert;
- negative-test results;
- provenance fields;
- #8 compatibility changes;
- remaining blockers before unattended use.
