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
3. Read `${CLAUDE_PLUGIN_ROOT}/reference/orchestrator.md` and follow it from the top of the
   loop. If a turn is still running from an interrupted session, send that agent back to
   finish it before anything else.
