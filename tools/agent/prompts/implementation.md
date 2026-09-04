Work issue #{{ issue_number }} in this repository worktree:
{{ worktree }}

Issue URL:
{{ issue_url }}

Issue title:
{{ issue_title }}

Issue body:
{{ issue_body }}

Repository contract from AGENTS.md:
{{ agents_text }}

Task contract:
- Read and obey AGENTS.md before changing files.
- Treat the selected GitHub issue above as the canonical implementation specification.
- Preserve the issue scope and acceptance criteria.
- Stop on protected or blocking product/architecture decisions under AGENTS.md.
- Do not select, groom, prioritize, or implement any other issue.
- Run locally available validation before claiming success.
- Use failures from local validation as feedback and fix your own work.
- Produce one coherent pull-request worth of work.
- Never merge a pull request.

Final response contract:
Return JSON matching the provided schema. Use status "blocked" only for a genuine protected or blocking decision. Use status "failure" if implementation could not be completed for other reasons. Use status "success" only when implementation and locally required validation are complete.
