# Convergence Pipeline

A Claude Code skill that turns a project idea into agreed statements (goals, requirements,
invariants), an architecture and milestones before any code gets written, then builds one
milestone at a time. `convergence-pipeline.md` is the spec; `docs/walkthrough.md` shows one
statement's life through the real tools, table by table.

## Where things stand (2026-09-27)

- **Rebuilt to the redesigned model.** `schema.sql`, `pipeline.yaml`, the `convergence`
  package (18 `cp-*` tools), the ten charters, `protocol.md` and the orchestrator skill
  (`.claude/skills/converge/SKILL.md`) all implement the spec. `cp-doctor` reports no drift.
- **Tested through the tools, phases 1–4.** `examples/walkthrough.py` regenerates
  `docs/walkthrough.md` and stops if any step stops behaving as the story says — rerun it
  after changing the tools. Scratch drivers (not in the repo) also exercised the edge cases:
  the goal guard, retraction, stall auto-escalation, early escalation, the human's final
  answer, deliverable splits and homeless statements, report pauses, and a milestone blocked
  by a statement change mid-build.
- **Not yet done:** a dry run with real agents on a real project. No agent has taken a turn
  yet; every test so far drives the tools directly.
- `./setup.sh` creates `.venv` and installs the package editable; re-running it is safe. Tools
  take `--db` or `$CP_DB` (default `.convergence/project.db`).
- `~/git/convergence-pipeline` is a symlink to this directory, not a second copy.

## The rule that governs the rest

**`pipeline.yaml` is the single source of truth**, and every rule is stored on the object it
describes — `statement_kinds.goal.guarded_by`, `statement_kinds.success_criterion.requires_link`,
`verdicts.final.human_only`, `agents.pm.owns`, `policy.stall_replies`. `cp-init` seeds the
database; the tools read the rules back out; the charters point at `cp-policy` instead of
restating them. Nothing is hardcoded in Python and nothing is duplicated in prose. Run
`cp-doctor` after any edit — it cross-checks the YAML, the charters, the skill, the tool names
they mention, and the database, and reports drift.

## The model in brief

The spec has the reasoning; these are the load-bearing decisions.

- **Statements are the only shared object** (`S-n`): goals, non-goals, success criteria and
  invariants project-wide; per deliverable its scope and requirements. One table, one id
  sequence; relationships (`supersedes`, `measures`) are rows in `links`.
- **Unanimous, explicit agreement.** Every approver (active agents with `approves: true`; not
  the human, not Rita) approves each statement or raises a concern on it. Agreed = all
  approved, no open concern, nothing awaiting Peter. Recomputed after every write, so a late
  joiner, a retraction or a new concern turns it back to pending on its own.
- **Nothing agreed is edited.** Changes supersede into new rows via `links`; approvals never
  carry over.
- **Concerns are private mail**, always on one statement or one milestone, one raiser, one
  addressee. Only the raiser closes one (the human may mark their own answer final). One
  answer at a time; reassignments are hand-off notes, never judged, never to the raiser.
- **Peter is the only writer** of statements, deliverables and milestones, and only from
  closed concerns, citing them. He acts on a statement once all its concerns are closed, and
  must consider every one (change or keep) before retiring it.
- **The human's intent cannot drift without them:** goal-level kinds change only on a concern
  the human answered or raised. The human does not approve statements; they get a report every
  `report_every_rounds` rounds, which pauses the loop until they continue.
- **Rounds are sequential**, agents in `seq` order with Peter last; empty queues are recorded
  as skipped turns. Every action happens in a recorded turn (the human's too), every written
  row carries `turn_id` and `created_at`, and each turn ends with the agent's own summary —
  read by the human and by that agent next turn, never used as input. `project_state` is the
  live row a front end would poll.
- **Deliverables start as D-1** (the whole idea) and are split by supersession, never renamed;
  statements in a retired deliverable are homeless until Peter moves them.
- **Phase 4:** the human picks each milestone; a statement change blocks it; Quinn's and
  Rita's findings are concerns on the milestone; Dana merges after both pass; the human
  accepts.

## The tools

Agents never write SQL; the rules live in the tools, not in the prompts.

| Tool | Who | What it does / refuses |
|---|---|---|
| `cp-init` | skill | Builds the database from the YAML; records the brief as S-1 and creates D-1 |
| `cp-turn` | skill | `next` opens the next turn (skipping empty queues), `start --agent human`, `end --summary`. Refuses ending Peter's turn with a report due |
| `cp-queue` | anyone | The turn's work, starting with the agent's last summary and the statements in force |
| `cp-approve` | approvers | Approve, or `--retract` with a concern. Refuses with your own concern open |
| `cp-concern` | anyone | On one statement or milestone, to one agent. Refuses self-addressing, retired statements, staffing (use `cp-staff`) |
| `cp-answer` | addressee | Answer, `--reassign-to`, or `--final` (human only). One answer at a time |
| `cp-review` | raiser | `accepted` or `replied` (with a reply) on the latest answer; stall auto-escalates |
| `cp-escalate` | Peter | Send a stuck concern to the human early |
| `cp-statement` | Peter | `add`, `supersede`, `move`, `cancel`, `keep`, each `--because` closed concerns; enforces the goal guard, required links and live deliverables |
| `cp-deliverable` | Peter | `add`, `split`, `cancel` (refuses a non-empty one) |
| `cp-milestone` | various | `add`/`assign` (Peter), `review` (architect), `check` (developer), `start`/`accept` (human), `submit`/`merge` (developer), `pass` (QA, reviewer) |
| `cp-staff` | anyone / human | Request an agent on a statement; the human approves or declines |
| `cp-report` | Peter / human | `run` pauses the loop; `continue` resumes it |
| `cp-state` | anyone | Round, phase, status, what it is paused on, who is left this round |
| `cp-round` | skill | `--advance` (refuses mid-turn, paused, incomplete round, report due); `--phase N` (1→2 only once converged) |
| `cp-render` | anyone | `requirements`: goals with their criteria, per-deliverable statements, kept as is, history. `milestone --milestone M-3`: what to build, each statement's full trail of concerns back through what it replaced, context, invariants, what is not in it, open findings, done-when |
| `cp-policy` / `cp-doctor` | anyone | The rules in force; drift between config, docs and database |

## Next steps

1. A dry run of phase 1 with real agent turns on a real, low-stakes project.
2. Consider moving the scratch edge-case drivers into the repo as a test suite.
