# Convergence Pipeline

A Claude Code skill (planned, not built yet) that turns a project idea into converged
requirements, an architecture, deliverables and milestone files before any code gets written.
It then builds one milestone at a time. `convergence-pipeline.md` holds the full design.

## Redesign in progress (2026-09-27)

`convergence-pipeline.md` now specifies a **redesigned model**, agreed with the human in an
interview: requirements are the only shared object, every agent approves each one or raises a
concern on it, concerns are always on one requirement, only Peter writes requirements (from
closed concerns, citing them), changes supersede into new rows via a lineage table, the brief
is R-1, deliverables start as D-1 and are superseded rather than renamed, goals are
project-wide and only change through a concern the human answered, and there is no Scribe.

**The spec wins where it disagrees with this file.** "Settled decisions" and the tool table
below describe the *previous* model, which is what the code still implements. Next step: the
human reviews the spec, then the schema, `pipeline.yaml`, tools, charters and skill are
rebuilt to match.

## Where things stand (2026-09-26, previous model)

- Working: `schema.sql`, `pipeline.yaml`, the `convergence` package, `pyproject.toml`,
  `setup.sh`, and the eleven agent charters in `.claude/agents/` plus `protocol.md`. All
  sixteen `cp-*` tools are installed in `.venv`. The phase 1–2 tools are tested end to end
  against the employee-hub walkthrough; the phase-3 tools (`cp-deliverable`,
  `cp-milestone`) pass a smoke test (happy path plus wrong-role refusals) but have not been
  run through the walkthrough. The orchestrator skill is `.claude/skills/converge/SKILL.md`.
  There is no milestone template and no `cp-render milestone` yet, so phase 3 can fill the
  tables but cannot produce the `milestone-N.md` files phase 4 builds from.
- Charter file names match `agents.role` in `pipeline.yaml` and the `name:` in each
  charter's frontmatter; `protocol.md` is the shared turn protocol every charter points to.
- `./setup.sh` creates `.venv` and installs the package editable; re-running it is safe, and
  the orchestrator skill should run it when `.venv` is missing. Tools take `--db` or `$CP_DB`.
- `pipeline.yaml` holds both the agent roster and every enumerated vocabulary (concern kinds,
  requirement kinds, answer kinds, statuses, cost flags, link relations), each value with a
  natural-language description: the tools enforce the values, the descriptions help an agent
  pick the right one.
- `cp-init` validates the config and builds the database deterministically
  (`--check` validates only, `--force` replaces). Its PyYAML dependency is installed in
  `.venv` by `setup.sh`.
- The design was substantially revised on 2026-09-19: shared state moved from a Markdown
  concerns log to a SQLite database, the interview merged into the convergence loop, the
  round budget removed, and a deliverables layer added above milestones.
- Design artifact (HTML version of the *pre-revision* design, now out of date):
  https://claude.ai/code/artifact/994f8496-641d-4112-83a1-106665c010fd
- `~/git/convergence-pipeline` is a symlink to this directory, not a second copy.

## The rule that governs the rest

**`pipeline.yaml` is the single source of truth**, and every rule is stored on the object it
describes — `requirement_kinds.goal.decided_by`, `answer_kinds.accepted.requires`,
`agents.ttm.owns`, `policy.stall_replies`. `cp-init` seeds the database; the tools read the
rules back out; the charters point at `cp-policy` instead of restating them. Nothing is
hardcoded in Python and nothing is duplicated in prose. Run `cp-doctor` after any edit — it
cross-checks the YAML, the charters, the skill and the database, and reports drift.

## Settled decisions

- **Four phases:** 1 Converge (concerns → requirements), 2 Architect (whole picture at once),
  3 Slice (deliverables → milestones), 4 Build (one milestone at a time).
- **SQLite, not a log file.** Tables: `agents`, `concerns`, `answers`, `requirements`,
  `requirement_concerns`, `deliverables`, `milestones`, `project_state`. Everything is
  stamped with its round.
- **"Concern", not "question"** — kinds: brief, question, risk, objection, proposal,
  staffing, appeal. Concerns are the conversation; requirements are the outcome; every
  requirement links back to the concerns that produced it.
- **One addressee per concern**, as a foreign key. No bitmask (considered and rejected: the
  database cannot validate a bitmask, and per-recipient state gets awkward).
- **Answers are typed**: accepted (names requirement ids, stored in `answer_requirements`),
  rejected (with reason), or escalated. The raiser sets `satisfied` and may `reply` on the
  answer row; a follow-up is a new answer row with the same `concern_id`.
- **Every concern says what prompted it** — `about_requirement_id` or `about_concern_id`,
  required by `concern_kinds.about`. Only the brief is about nothing; it is the root every
  trail leads back to. An objection is a concern about a requirement.
- **Draft, then agree, then accept.** An accepted answer names a *proposed* requirement so
  the raiser judges exact wording. `cp-decide` will not accept it while a concern from or
  about it is open, or before every active agent has marked it seen. A rewording leaves the
  original in force until the rewording is accepted.
- **Status changes update the requirement row in place**; only a change to the `statement`
  text creates a new row with `supersedes_id`. `requirement_events` keeps the history, so a
  requirement deferred, revived and deferred again still reads back.
- **The table is called `concerns`** — considered renaming it after the scope grew to include
  brief, staffing and appeal kinds, and kept it: "raise a concern" prompts a better-formed
  row from an agent than a neutral name like `threads` would.
- **The tables are the state; documents are printouts.** `requirements.md` and
  `milestone-N.md` are generated. Only `architecture.md` is authored, by Arty.
- **The goal lives in `requirements`** as rows of kind goal / non_goal / success_criterion,
  revised through `supersedes_id`. There is no separate brief table; the human's original
  words are concern #1, kind `brief`.
- **No round budget.** The loop pauses only when a concern is addressed to the human, and
  finishes when every active agent signs off on the requirement set.
- **Agents join on triggers**, recorded in the `agents` table (`join_trigger`,
  `join_rationale`) — that table is the project's config file. Round 1 is the human, Peter
  and the Scribe only.
- **Staffing needs the human's approval.** A staffing concern goes to the human with its
  trigger and its likely cost; the human approves the agent, defers the triggering
  requirement, or drops it. `auto_staff` defaults to 0.
- **Peter decides ordinary requirements and disputes between others**; the human decides the
  goal, invariants, feature cuts, staffing, disputes Peter is party to, and appeals. Any agent
  may appeal one Peter decision once, straight to the human.
- **The Scribe has no opinions** — records, routes, reports, escalates. Split out of Peter so
  that nobody reports on their own decisions.
- **Arty joins early** (as soon as a feature list exists) because invariants decided late mean
  rework. He writes concerns, invariants and cost flags each round; the architecture document
  comes once, in Phase 2.
- **Dana is not in the requirements loop.** He checks milestone files for buildability at the
  end of Phase 3, then builds in Phase 4.
- **The human picks each milestone to build** and tests after each merge. Phase 4 never runs
  "everything".

## Open threads

1. The spend cap, and what the Scribe reports when it trips (nothing measures money yet).
2. Whether dormant agents wake automatically when their area is touched again.
3. Where the database lives relative to the project repo, and whether it is committed.

Settled and now in `policy` (see `cp-policy`): stall threshold (`stall_replies`,
`stall_rounds_open`) and the Scribe's check-in cadence (`report_every_rounds`).

## The tools

Agents never write SQL; the rules live in the tools, not in the prompts.

| Tool | What it does / refuses |
|---|---|
| `cp-init` | Validates `pipeline.yaml`, builds the database. `--check`, `--force` |
| `cp-queue` | An agent's turn: concerns to answer, answers to review, requirement changes since `last_seen_change`. `--json`, `--mark-seen` |
| `cp-concern` | Raise one. `--about R-n\|C-n` is required except for the brief. Refuses `staffing` (use `cp-staff`), an appeal aimed anywhere but the human, self-addressing, inactive agents |
| `cp-answer` | Refuses anything that is not accepted-with-requirement-ids, rejected-with-a-reason, or escalated-to-someone, and refuses a second answer while the first awaits the raiser's verdict. Escalation reassigns the concern and is never judged |
| `cp-review` | The raiser judges the latest answer (not an escalation). A "no" needs a reply; a "yes" resolves the concern |
| `cp-propose` | New requirement. Cost flags are the architect's only. `--supersedes` rewords; the original stays in force until the rewording is accepted |
| `cp-decide` | Refuses: a goal/non_goal/success_criterion/invariant decided by anyone but the human; Peter cutting something the human asked for (including via their answers); a defer/reject with no reason; accepting while a concern from or about it is open, or before every active agent has seen it |
| `cp-staff` | `request` (states trigger and cost, goes to the human), `approve`, `decline` — human only |
| `cp-signoff` | Refuses while the agent still has mail, or has a concern of its own still open |
| `cp-state` | Round, phase, what it is paused on, who has not signed off, abandoned drafts, whether it has converged |
| `cp-round` | `--advance` refuses while a concern sits with the human; `--phase N` moves phase |
| `cp-deliverable` | `propose` (owns deliverables), `decide` (rules_on) |
| `cp-milestone` | `propose`, `decide`, `review --ok yes/no` (reviews slicing), `check --ok yes/no` (checks buildability), `assign`. Deliverable statuses: proposed, planned, building, delivered |
| `cp-render` | Generates `requirements.md`; sections come from the vocabularies |
| `cp-policy` | The rules in force: thresholds, who decides what, duties. `--agent <role>` for one agent |
| `cp-doctor` | Cross-checks config, charters, skill and database for drift |

## Next steps

1. The human reviews the redesign in `convergence-pipeline.md` and settles its *Open* list.
2. Rebuild `schema.sql`, `pipeline.yaml`, the tools, the charters and the skill to the spec;
   rerun the one-requirement walkthrough against the real tools.
3. Then `cp-render milestone` and a dry run of phase 1 on a real, low-stakes project.
