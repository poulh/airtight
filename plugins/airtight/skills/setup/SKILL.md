---
name: setup
description: Set up airtight in the current repo — install its tools if needed and copy the default pipeline.yaml into .airtight/ so it can be tuned before the project starts. Use when the user wants to start using airtight in a repo, or asks to set it up.
---

# Set up airtight in this repo

Work from the root of the user's project repo. Do these in order, and stop at the first
failure to show it to the user.

1. **Make sure the tools can run.** Run `at-init --check`. If it prints
   `no Python with PyYAML found`, build the plugin's Python environment once — it lives in the
   plugin's data directory, is shared by every project, and survives plugin updates:

   ```bash
   python3 -m venv "${CLAUDE_PLUGIN_DATA}/airtight-venv"
   "${CLAUDE_PLUGIN_DATA}/airtight-venv/bin/pip" install --quiet pyyaml
   ```

   Then run `at-init --check` again.

2. **Copy the default config into the project.** `at-init --scaffold` creates `.airtight/`,
   copies the default `pipeline.yaml` into it (it never overwrites an existing one), and
   validates it. That file is this project's own: the roster, the thresholds, the kinds of
   statement, and every rule the tools enforce.

3. **Check for drift**: `at-doctor`.

4. **Tell the user**, briefly:
   - `.airtight/pipeline.yaml` is theirs to tune before starting — for example which agents
     can be staffed, or `report_every_rounds`, the number of rounds between reports (each
     report pauses the loop, which is the spend brake). Run `at-init --check` after any edit.
   - `.airtight/` is meant to be committed: the database will record every statement, concern
     and decision, and it should travel with the code.
   - When ready, `/airtight:start`.

Do not create the database here; `/airtight:start` does that with the user's idea.
