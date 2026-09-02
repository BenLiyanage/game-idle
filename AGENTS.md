# Agent Contract

The goal of this repository is to ship the smallest viable mobile idle game quickly.

GitHub issues are the canonical implementation specifications. Implement only an issue that is explicitly selected for development. Preserve the issue's scope and acceptance criteria.

Work rules:

- One issue, one branch, and one coherent pull request.
- Keep changes inside the linked issue's stated scope.
- Stop when blocking product or architectural questions remain.
- Never silently change architecture, dependencies, save formats, renderer, platform SDKs, signing, monetization, analytics, or performance budgets.
- Use typed GDScript for all new or changed scripts.
- Use the Compatibility renderer unless Ben explicitly approves a renderer change.
- Run `bash tools/ci/verify.sh` before completing work.
- Report commands, results, unavailable checks, and human verification steps.
- Never merge a pull request.

Human approval is required before changing architecture, dependencies, save-data formats or migrations, renderer policy, platform SDKs, signing, releases, monetization, analytics, or significant performance trade-offs.

## Code Review Rules

Review pull requests for:

- Whether the PR stays within the linked issue.
- Whether acceptance criteria are satisfied.
- Missing or misleading tests.
- Unsupported claims of successful verification.
- Unapproved architectural or dependency changes.
- Save compatibility and performance risks.
