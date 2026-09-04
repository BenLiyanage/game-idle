The local validation command for issue #{{ issue_number }} failed.

Command:
{{ validation_command }}

Exit code:
{{ validation_exit_code }}

Output:
```text
{{ validation_output }}
```

Fix only the implementation for issue #{{ issue_number }}. Re-run the relevant local validation after the fix. Do not implement another issue, do not merge, and stop if a protected product or architecture decision is required.

Return JSON matching the provided schema. Use status "blocked" only for a genuine protected or blocking decision. Use status "failure" if implementation could not be completed for other reasons. Use status "success" only when the locally observable validation failure has been fixed.
