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
ui/                               the airtight floor: read-only dashboard (FastAPI + plain JS),
                                  its own pyproject.toml, airtight-ui.toml and ui/.venv
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
  `claude plugin validate`. The schema, default config, 19 tools, ten charters, protocol and
  three skills implement the spec; `at-doctor` reports no drift. Not yet installed through
  `/plugin` and exercised that way.
- **Tested through the tools, phases 1–4.** `examples/walkthrough.py` regenerates
  `docs/walkthrough.md` and stops if any step stops behaving as the story says — rerun it
  after changing the tools. Scratch drivers (not in the repo) exercised the edge cases: the
  goal guard, retraction, stall auto-escalation, early escalation, the human's final answer,
  deliverable splits and homeless statements, report pauses, and a milestone blocked by a
  statement change mid-build.
- **Command log.** Every `at-*` call appends a line to `.airtight/log.jsonl` (next to the
  database): time, round, turn, agent, tool, args, outcome (`ok`/`refused`/`usage`/`error`),
  exit code and output, capped at 4000 characters. It is written by `airtight/__main__.py`
  and is the place to look for refusals and crashes, which leave nothing in the database.
  Claude Code's own transcripts (`~/.claude/projects/<repo path>/`) hold the orchestrator's
  and agents' reasoning.
- **The floor** (`ui/`, 2026-10-11): a read-only web dashboard over a project database:
  phase, round, the running turn, agent desks with queues and stats, statements, concern
  threads, a who-raises-to-whom matrix, the timeline and the command log, polled every few
  seconds. It imports `airtight.db` from the plugin for derived state rather than
  reimplementing it. `ui/setup.sh`, then `ui/.venv/bin/airtight-ui` in the project. Not part
  of the plugin yet.
- **Versions and upgrades** (2026-10-11, 0.2.0): see *Shipping a change*. The database
  records the version that built it in `meta`; databases from before 0.2.0 have no `meta`
  and show as "before 0.2.0". `at-state` names the agent holding the running turn.
- **After 0.2.0** (2026-10-11, from the Retirement-Calculator run): agents who join in a later
  phase are listed in Peter's queue but no longer count as work, so he is skipped instead of
  taking empty turns; `db.phase_end()` checks each phase's end condition, shown by `at-state`
  and enforced by `at-round --advance`; turns record tokens via `at-turn tokens`, which the
  orchestrator runs after each subagent returns.
  An agent the next phase needs (its `needs`) and nobody active can stand in for is real work
  in Peter's queue (*staff now for phase N*), whatever its join trigger says: the reviewer's
  trigger can only fire in phase 4, which deadlocked 3→4. Defects found outside a milestone
  go to Peter as a `proposal` (protocol, QA, reviewer, developer and PM charters), not into a
  summary or to the human.
- **Not yet done:** a dry run with real agents. Every test so far drives the tools directly.
- Development: `./setup.sh` builds `.venv` with PyYAML, then
  `export PATH="$PWD/plugins/airtight/bin:$PATH"`. Tools take `--db` or `$AT_DB` (default
  `.airtight/project.db`) and `--config` (default `.airtight/pipeline.yaml`, else the plugin
  default).
- GitHub: `poulh/airtight` (renamed from `convergence-pipeline`; the old URL redirects). The
  local directory is still `convergence-pipeline`, and `~/git/convergence-pipeline` is a
  symlink to it.

## Shipping a change

Claude Code caches an installed plugin **by version**: a change shipped under the same
`version` in `plugins/airtight/.claude-plugin/plugin.json` never reaches anyone who has it
installed (0.1.0 stayed installed for weeks this way). `./setup.sh` sets
`core.hooksPath .githooks`, whose pre-commit hook (`scripts/plugin_version.py`) bumps the patch
version whenever a commit changes the plugin; bump minor or major by hand. The
`plugin version` GitHub workflow fails a push or PR that changes the plugin without moving it.

A project then catches up with `at-upgrade`: each project has its own copy of the rules (its
`pipeline.yaml` and the database seeded from it), so new rules reach it only that way.
`/airtight:continue` checks on every resume, and `at-doctor` reports a project that lags. A
schema change must be additive (new tables, nullable or defaulted columns) or come with a
written migration in `at-upgrade`.

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
| `at-turn` | skill | `next` opens the next turn (skipping empty queues), `start --agent human`, `end --summary`, `tokens --turn T-n` (the orchestrator records a finished turn's usage). Refuses ending Peter's turn with a report due |
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
| `at-state` | anyone | Round, phase, status, what it is paused on, who holds the running turn, who is left this round, and whether the phase's end condition holds (and what is unmet) |
| `at-round` | skill | `--advance` (refuses mid-turn, paused, incomplete round, report due, a phase whose end condition holds, and a round where every agent was skipped — naming what is unmet); `--phase N` (1→2 only once converged; refuses a phase whose `needs` nobody on the project holds) |
| `at-render` | anyone | `requirements`: goals with their criteria, per-deliverable statements, kept as is, history. `milestone --milestone M-3`: what to build, each statement's full trail of concerns back through what it replaced, context, invariants, what is not in it, open findings, done-when |
| `at-policy` / `at-doctor` | anyone | The rules in force; drift between config, docs and database |
| `at-upgrade` | skill | What the installed plugin added since the project was built; `--apply` merges new config entries into `.airtight/pipeline.yaml` (never changing the project's values), adds new tables and columns, re-seeds the rules, keeps staffing and history, backs both up to `.airtight/backup/` |

## Next steps

1. Install through `/plugin marketplace add` and `/plugin install`, then a dry run of phase 1
   with real agent turns on a real, low-stakes project.
2. Consider moving the scratch edge-case drivers into the repo as a test suite.
