# airtight

*You can't code autonomously if you don't have airtight requirements.*

A Claude Code plugin that turns a project idea into agreed statements (goals, requirements,
invariants), an architecture and milestones before any code gets written, then builds one
milestone at a time. This repo is its marketplace. `SPEC.md` is the spec; `docs/walkthrough.md`
shows one statement's life through the real tools, table by table; `README.md` is what a
visitor to the marketplace reads.

## Layout

```
.claude-plugin/marketplace.json   the marketplace (one plugin)
plugins/airtight/                 the plugin
  .claude-plugin/plugin.json
  skills/setup, start, continue   /airtight:setup, /airtight:start, /airtight:continue
  agents/*.md                     the ten charters, namespaced airtight:pm, airtight:architect, …
  reference/protocol.md           the agents' shared turn protocol
  reference/orchestrator.md       the loop, which start and continue follow
  defaults/pipeline.yaml          copied into each project as .airtight/pipeline.yaml
  defaults/schema.sql             code, never copied
  airtight/                       the Python package (cli.py, db.py, config.py, __main__.py)
  bin/airtight, bin/at-*          on PATH while the plugin is enabled; each at-* is a stub
SPEC.md, docs/, examples/, pyproject.toml, setup.sh   development only
```

Inside skill and agent text, `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` are
substituted by Claude Code; they are *not* in the Bash environment. `bin/airtight` finds its
own root, and looks for Python in `$AIRTIGHT_PYTHON`, the `airtight-venv` that
`/airtight:setup` builds in the plugin's data directory, this repo's `.venv`, then `python3`.
Validate with `claude plugin validate plugins/airtight` and `claude plugin validate .`.

## Where things stand (2026-09-27)

- **Packaged as the `airtight` plugin**, renamed from "convergence pipeline": tools `cp-*` →
  `at-*`, `CP_DB` → `AT_DB`, `.convergence/` → `.airtight/`. Both manifests pass
  `claude plugin validate`. The schema, default config, 18 tools, ten charters, protocol and
  three skills implement the spec; `at-doctor` reports no drift. Not yet installed through
  `/plugin` and exercised that way.
- **Tested through the tools, phases 1–4.** `examples/walkthrough.py` regenerates
  `docs/walkthrough.md` and stops if any step stops behaving as the story says — rerun it
  after changing the tools. Scratch drivers (not in the repo) exercised the edge cases: the
  goal guard, retraction, stall auto-escalation, early escalation, the human's final answer,
  deliverable splits and homeless statements, report pauses, and a milestone blocked by a
  statement change mid-build.
- **Not yet done:** a dry run with real agents. Every test so far drives the tools directly.
- Development: `./setup.sh` builds `.venv` with PyYAML, then
  `export PATH="$PWD/plugins/airtight/bin:$PATH"`. Tools take `--db` or `$AT_DB` (default
  `.airtight/project.db`) and `--config` (default `.airtight/pipeline.yaml`, else the plugin
  default).
- GitHub: `poulh/airtight` (renamed from `convergence-pipeline`; the old URL redirects). The
  local directory is still `convergence-pipeline`, and `~/git/convergence-pipeline` is a
  symlink to it.

## The rule that governs the rest

**`pipeline.yaml` is the single source of truth** — the plugin's default, then each project's
copy in `.airtight/` — and every rule is stored on the object it
describes — `statement_kinds.goal.guarded_by`, `statement_kinds.success_criterion.requires_link`,
`verdicts.final.human_only`, `agents.pm.owns`, `policy.stall_replies`. `at-init` seeds the
database; the tools read the rules back out; the charters point at `at-policy` instead of
restating them. Nothing is hardcoded in Python and nothing is duplicated in prose. Run
`at-doctor` after any edit — it cross-checks the YAML, the charters, the skill, the tool names
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
| `at-init` | skill | Builds the database from the YAML; records the brief as S-1 and creates D-1 |
| `at-turn` | skill | `next` opens the next turn (skipping empty queues), `start --agent human`, `end --summary`. Refuses ending Peter's turn with a report due |
| `at-queue` | anyone | The turn's work, starting with the agent's last summary and the statements in force |
| `at-approve` | approvers | Approve, or `--retract` with a concern. Refuses with your own concern open |
| `at-concern` | anyone | On one statement or milestone, to one agent. Refuses self-addressing, retired statements, staffing (use `at-staff`) |
| `at-answer` | addressee | Answer, `--reassign-to`, or `--final` (human only). One answer at a time |
| `at-review` | raiser | `accepted` or `replied` (with a reply) on the latest answer; stall auto-escalates |
| `at-escalate` | Peter | Send a stuck concern to the human early |
| `at-statement` | Peter | `add`, `supersede`, `move`, `cancel`, `keep`, each `--because` closed concerns; enforces the goal guard, required links and live deliverables |
| `at-deliverable` | Peter | `add`, `split`, `cancel` (refuses a non-empty one) |
| `at-milestone` | various | `add`/`assign` (Peter), `review` (architect), `check` (developer), `start`/`accept` (human), `submit`/`merge` (developer), `pass` (QA, reviewer) |
| `at-staff` | anyone / human | Request an agent on a statement; the human approves or declines |
| `at-report` | Peter / human | `run` pauses the loop; `continue` resumes it |
| `at-state` | anyone | Round, phase, status, what it is paused on, who is left this round |
| `at-round` | skill | `--advance` (refuses mid-turn, paused, incomplete round, report due); `--phase N` (1→2 only once converged) |
| `at-render` | anyone | `requirements`: goals with their criteria, per-deliverable statements, kept as is, history. `milestone --milestone M-3`: what to build, each statement's full trail of concerns back through what it replaced, context, invariants, what is not in it, open findings, done-when |
| `at-policy` / `at-doctor` | anyone | The rules in force; drift between config, docs and database |

## Next steps

1. Install through `/plugin marketplace add` and `/plugin install`, then a dry run of phase 1
   with real agent turns on a real, low-stakes project.
2. Consider moving the scratch edge-case drivers into the repo as a test suite.
