---
name: start
description: Start an airtight project — take the user's idea in their own words, record it as the brief, and begin the loop in which specialist agents argue it into airtight requirements. Use when the user wants to begin a new project with airtight.
---

# Start an airtight project

Work from the root of the user's project repo.

1. **Check the repo is set up.** If `.airtight/pipeline.yaml` does not exist, run
   `/airtight:setup` first. If `.airtight/project.db` already exists, this project has
   started: say so and use `/airtight:continue` instead. Never re-initialize an existing
   project; `at-init --force` destroys its history.

2. **Ask for the idea**, in their own words, as much or as little as they like. Do not
   interview them yourself and do not tidy what they say — Peter's interview is the next
   step, and it works from their exact words.

3. **Record it verbatim**: `at-init --brief "<their words>"`. It becomes S-1, and the project
   starts with one deliverable, D-1: the first assumption is that the whole idea ships at
   once.

4. **Tell the user what happens next**, in two lines: Peter will come back with a few
   questions; after that the agents work through rounds on their own, and the loop pauses
   whenever something is theirs to decide, and every few rounds for a report.

5. **Run the loop**: read `${CLAUDE_PLUGIN_ROOT}/reference/orchestrator.md` and follow it.
