---
name: continue
description: Continue an airtight project where it left off — answer what is waiting for the user, read a pending report, or resume the loop of agent turns. Use when the user wants to carry on, check on, or answer questions in an existing airtight project.
---

# Continue an airtight project

Work from the root of the user's project repo.

1. If `.airtight/project.db` does not exist, there is nothing to continue: offer
   `/airtight:start`.
2. `at-state`, and tell the user in two lines where the project stands: the phase, the round,
   and whether anything is waiting for them.
3. **Check the project is as current as the plugin**: `at-upgrade`. It changes nothing on its
   own; it lists what this plugin version added since the project was built — new rules in
   `.airtight/pipeline.yaml`, new database columns. If it lists anything, tell the user in a
   line or two what is new, in plain terms (for example "Peter now tracks who still needs
   staffing"), and that their own settings and the project's history are kept, with a backup
   in `.airtight/backup/`. On their yes, run `at-upgrade --apply` once no turn is running,
   then `at-doctor`. If they decline, carry on: the tools still work, but rules added since
   do not apply to this project.
4. Read `${CLAUDE_PLUGIN_ROOT}/reference/orchestrator.md` and follow it from the top of the
   loop. If a turn is still running from an interrupted session, send that agent back to
   finish it before anything else, and apply an upgrade the user agreed to right after.
