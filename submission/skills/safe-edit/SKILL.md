---
name: safe-edit
description: Quote-safe line edits. Use when an edit_file argument would contain triple quotes or end with a quote character, or after any rejected/failed edit_file call.
---
# safe-edit

Edits lines by number plus a 2-char hash. You never put code with triple quotes in a tool argument.

1. `write_file(filepath=".firebreak/cmd.txt", content="view PATH START END")`, then
   `run_skill_script(skill_name="safe-edit", file_path="scripts/edit.py")`.
   Output lines look like `LINE|HH| text`.
2. `write_file(filepath=".firebreak/payload.txt", content=...)` with the NEW lines. Then write
   `.firebreak/cmd.txt` with one of:
   - `apply PATH START:HH END:HH`: replace lines START..END with the payload
   - `insert PATH AFTER:HH`: insert the payload after line AFTER (`0:00` = top of file)
   - `create PATH`: new file from the payload
   Then call `run_skill_script(skill_name="safe-edit", file_path="scripts/edit.py")` again.

Payload rules:
- Write `@@TQ@@` instead of three double quotes and `@@SQ3@@` instead of three single quotes.
- The LAST line of the payload must be exactly `@@END@@`.
- Never pass `args` or `positional_args` to run_skill_script.

Errors: "stale anchor" means re-run `view` and use the fresh hashes. A SyntaxError is reverted
automatically; fix the payload and retry.
