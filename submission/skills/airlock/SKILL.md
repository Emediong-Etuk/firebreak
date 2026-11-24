---
name: airlock
description: Clean the working tree right before submit_patch. Removes scratch files and test edits, and reverts any changed Python file that does not compile.
---
# airlock

Run `run_skill_script(skill_name="airlock", file_path="scripts/finalize.py")` once, right before
`submit_patch`. It takes no other arguments. It prints the final `git diff --stat` and every file it removed or
reverted, with the reason. Use `scripts/check.py` instead to see the report without reverting broken code.
