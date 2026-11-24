# Recovery recipes (load with load_skill_resource when a call fails)

- **edit_file "mandatory input parameters are not present"**: the call was garbled on the wire, usually by
  quotes near triple-quoted strings. Do NOT resend it. Switch to safe_edit (see SKILL.md).
- **edit_file "not found" / FileEditError**: read_file the exact lines, then use a shorter anchor of 1–3 lines
  with no quotes, or switch to safe_edit.
- **The same call failed twice**: stop and change strategy (different anchor, safe_edit, or read the file first).
- **pytest IDs with brackets**: only inside one run_command string, e.g. `run_command(command="python -m pytest 'tests/t.py::test_a[x]' -q")`.
- **Budget under 25%**: stop exploring. Make the smallest fix, run airlock finalize, then call submit_patch.
