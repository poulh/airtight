# The Convergence Pipeline

*Requirements → architecture → deliverables → code, in that order*

A Claude Code skill that replaces "code it, then I'll find the gaps" with a pre-code loop.
Specialist agents — each with one thing it cares about — raise concerns, answer each other,
and build up a requirement set. The human is interrupted only when something is genuinely
theirs to decide. Only once the requirements stop moving does an architect design against
the *whole* picture, and only then is anything cut into deliverables, sliced into milestones,
and built.

The shared state is a SQLite database, not a log file. Every concern, answer, requirement
and decision is a row, stamped with the round it happened in.

---

## The four phases

| | Phase | What happens | Ends when |
|---|---|---|---|
| **1** | **Converge** | The human states the idea. Peter interviews them. Agents join as the project earns them, raise concerns, answer each other, and propose requirements. | Every active agent signs off on the requirement set |
| **2** | **Architect** | Arty designs against the entire settled requirement set and writes `architecture.md` plus the invariants. | The architecture is accepted |
| **3** | **Slice** | Tina proposes deliverables and milestones; Peter rules on them; Arty passes the slicing; Dana checks each milestone file is buildable. | Milestone files written |
| **4** | **Build** | The human picks what to build next. Dana implements one milestone; Quinn and Rita review; either can send it back; merge; the human tests. | The human stops asking for more |

The loop in Phase 1 runs unattended. It has no round budget. It pauses **only** when a
concern is addressed to the human, and it finishes when nothing new is raised and everyone
signs off.

---

## Core ideas

**Concerns, not questions.** A concern is anything an agent raises: a question, a risk, an
objection, a proposal. *"What if they upload a Word document?"* is a concern. Its resolution
is a requirement: *"Files that are not .xlsx are rejected with a message naming the accepted
types."* Concerns are the conversation; requirements are the outcome; every requirement links
back to the concerns that produced it.

**One addressee per concern.** No bitmasks. Asking three agents the same thing is three rows
sharing a `raised_group`. The database enforces that the addressee exists, queues are a plain
`WHERE addressed_to = :me`, and each answer thread belongs to exactly one pair.

**The table is the state; documents are printouts.** `requirements.md` and `milestone-N.md`
are generated from rows. Only `architecture.md` is authored, because prose and diagrams are
not rows.

**One source of truth.** `pipeline.yaml` declares the phases, the thresholds, the duties, the
roster and every vocabulary — and each rule is stored on the thing it describes: "only the
human decides a goal" is an attribute of the `goal` kind, "Tina owns the milestone split" is a
duty on Tina. `cp-init` seeds the database from it and the tools read their rules back out, so
no rule is written in Python or restated in prose. `cp-doctor` reports anything that has
drifted.

**Agents join when the project earns them.** Round 1 is the human, Peter and the scribe.
A security agent looking at "employee hub app" can only produce generic warnings, which cost
tokens every round and teach nobody anything.

**Nobody is staffed without the human's say-so.** A new agent means new constraints and a
slower loop. The human may prefer to drop or defer the requirement that triggered it.

---

## The database

### `phases`, `policy`, `duties` — the rules themselves

```sql
CREATE TABLE phases (number INTEGER PRIMARY KEY, key TEXT, name TEXT, ends_when TEXT);
CREATE TABLE policy (key TEXT PRIMARY KEY, value TEXT, description TEXT);
CREATE TABLE duties (value TEXT PRIMARY KEY, description TEXT, seq INTEGER);
CREATE TABLE duty_relations (value TEXT PRIMARY KEY, ...);  -- owns | rules_on | reviews | checks
CREATE TABLE agent_duties (agent_id INTEGER, duty TEXT, relation TEXT, PRIMARY KEY (...));
CREATE TABLE agent_phases (agent_id INTEGER, phase INTEGER, PRIMARY KEY (...));
```

Each vocabulary table carries the rules belonging to its values: `requirement_kinds` has
`decided_by` and `document_section`, `answer_kinds` has `requires`, `concern_kinds` has
`must_address` and `via_tool`, `cost_flags` has `set_by`. Adding a value adds its rule with
it, and the document sections are generated from these rather than from a list in code.

### `agents` — the roster

Seeded from `pipeline.yaml`, with its joining rules and duties.

```sql
CREATE TABLE agents (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE,   -- 'Peter the Project Manager'
  role            TEXT NOT NULL,          -- 'pm'
  motivation      TEXT NOT NULL,          -- the one sentence this agent argues from
  charter         TEXT,                   -- path to its .claude/agents/ definition
  joins_at_phase  INTEGER NOT NULL REFERENCES phases(number),
  join_trigger    TEXT,                   -- the condition that pulls it in
  join_rationale  TEXT,                   -- why that is the right moment
  auto_staff      INTEGER NOT NULL DEFAULT 0,  -- 1 = join without asking the human
  active          INTEGER NOT NULL DEFAULT 0,
  joined_round    INTEGER,
  requested_by    INTEGER REFERENCES agents(id),
  last_seen_change INTEGER                -- highest requirement change this agent has read
);
```

### `concerns` — the conversation

```sql
CREATE TABLE concerns (
  id            INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL,   -- brief | question | risk | objection | proposal | staffing | appeal
  raised_by     INTEGER NOT NULL REFERENCES agents(id),
  addressed_to  INTEGER NOT NULL REFERENCES agents(id),
  body          TEXT NOT NULL,
  about_requirement_id INTEGER REFERENCES requirements(id),  -- what the raiser read
  about_concern_id     INTEGER REFERENCES concerns(id),      --   that prompted this
  raised_group  INTEGER,         -- same question sent to several agents
  status        TEXT NOT NULL DEFAULT 'open',  -- open | resolved | withdrawn | escalated
  round         INTEGER NOT NULL,
  resolved_round INTEGER
);
```

Every concern points at what prompted it: a requirement (`--about R-12`) or another concern
(`--about C-3`). Only the brief points at nothing, which makes it the root: any concern can be
followed back through the chain to what the human originally said. The rule lives on
`concern_kinds.about`. An objection is simply a concern about a requirement.

### `answers` — the replies, and the argument

```sql
CREATE TABLE answers (
  id          INTEGER PRIMARY KEY,   -- increasing id gives the ordering
  concern_id  INTEGER NOT NULL REFERENCES concerns(id),
  answered_by INTEGER NOT NULL REFERENCES agents(id),
  kind        TEXT NOT NULL,         -- accepted | rejected | escalated
  body        TEXT NOT NULL,
  satisfied   INTEGER,               -- NULL = raiser has not reviewed; 1 = yes; 0 = no
  reply       TEXT,                  -- the raiser's follow-up when satisfied = 0
  round       INTEGER NOT NULL
);

CREATE TABLE answer_requirements (    -- which requirements an accepted answer names
  answer_id      INTEGER NOT NULL REFERENCES answers(id),
  requirement_id INTEGER NOT NULL REFERENCES requirements(id)
);
```

An answer is never just prose. It is one of three shapes:

- **accepted** — names the requirement ids it created or changed. The requirement is drafted
  first (status `proposed`) so the raiser judges the exact wording, and their queue shows it
  under the answer
- **rejected** — states why; that reason becomes an accepted risk in the final document
- **escalated** — hands it to Peter, or to the human

A follow-up is a new `answers` row with the same `concern_id`. The thread for one pair is
`WHERE concern_id = ? AND answered_by = ? ORDER BY id`.

### `requirements` — the outcome

```sql
CREATE TABLE requirements (
  id             INTEGER PRIMARY KEY,
  kind           TEXT NOT NULL,   -- goal | non_goal | success_criterion
                                  -- | functional | constraint | invariant
  statement      TEXT NOT NULL,   -- one testable sentence
  rationale      TEXT,
  proposed_by    INTEGER REFERENCES agents(id),
  status         TEXT NOT NULL,   -- proposed | accepted | deferred | rejected | superseded
  cost_flag      TEXT,            -- cheap | moderate | expensive   (Arty sets this)
  decided_by     INTEGER REFERENCES agents(id),
  decided_round  INTEGER,
  deliverable_id INTEGER REFERENCES deliverables(id),
  milestone_id   INTEGER REFERENCES milestones(id),
  created_round  INTEGER NOT NULL,
  updated_round  INTEGER,
  supersedes_id  INTEGER REFERENCES requirements(id)
);

CREATE TABLE requirement_concerns (
  requirement_id INTEGER NOT NULL REFERENCES requirements(id),
  concern_id     INTEGER NOT NULL REFERENCES concerns(id),
  relation       TEXT NOT NULL,   -- origin
  PRIMARY KEY (requirement_id, concern_id, relation)
);
```

A requirement becomes real in this order: drafted (`proposed`), the raiser is satisfied with
the answer that names it, every active agent has read it in their queue, and only then can it
be accepted. `cp-decide` enforces the last two through `policy.accept_needs_concerns_closed`
and `policy.accept_needs_all_seen`. A rewording (`supersedes_id`) leaves the original in force
until the rewording is itself accepted.

The goal lives here too, as rows of kind `goal`, `non_goal` and `success_criterion`. Revising
the goal in round 12 works like any other requirement change, through `supersedes_id`.

### `deliverables` and `milestones`

```sql
CREATE TABLE deliverables (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, seq INTEGER NOT NULL,
  intent TEXT, status TEXT NOT NULL DEFAULT 'planned'
);

CREATE TABLE milestones (
  id INTEGER PRIMARY KEY, deliverable_id INTEGER NOT NULL REFERENCES deliverables(id),
  name TEXT NOT NULL, seq INTEGER NOT NULL, file_path TEXT,
  status TEXT NOT NULL DEFAULT 'planned'  -- planned | building | in_review | merged | accepted
);
```

### `project_state`

```sql
CREATE TABLE project_state (
  round INTEGER NOT NULL, phase INTEGER NOT NULL, updated_at TEXT
);
```

---

## The roster

Seeded into `agents` at project start. Only the first three are active in round 1.

Duties in the table below are `owns` / `rules_on` / `reviews` / `checks` rows the tools
enforce; `cp-policy` prints the live set.

| id | Agent | Motivation | Joins when | Why then |
|---|---|---|---|---|
| 1 | **The Human** | Owns the goal. Settles what no one else can. | always | — |
| 2 | **Peter** the Project Manager | Does this still serve the stated goal? | always | Interviews the human; decides ordinary requirements |
| 3 | **The Scribe** | No opinions. Records, routes, reports, escalates. | always | Mechanical from round 1 |
| 4 | **Tina** the Time-to-Market | Ship something useful sooner. | a first feature list exists | Her "what would you ship first?" shapes the interview rather than following it |
| 5 | **Arty** the Architect | Will this hold together, and what must v1 not preclude? | a first feature list exists | Deliberately early: invariants decided after the features are settled mean rework |
| 6 | **Quinn** the QA | What breaks it? | features are concrete | Corner cases surface missing features the human must rule on |
| 7 | **Ian** the Infosec | Who can see or do what they should not? | personal data, login or permissions appear | Needs something concrete to attack |
| 8 | **Carla** the Compliance Officer | What do law, regulation and policy demand? | regulated data appears (personal, time off, pay, medical, EU staff) | Same |
| 9 | **Otto** the Ops/SRE | Will it survive real load, and can it be run and recovered? | scale, refresh frequency or uptime expectations appear | Needs numbers |
| 10 | **Uma** the UX | Can a real person accomplish the task? | the UI is more than a list or a form | Needs screens to critique |
| 11 | **Dana** the Developer | Build exactly this milestone. | end of Phase 3 | Buildability check, then Phase 4 |
| 12 | **Rita** the Reviewer | Is the implementation sound? | Phase 4 | Reviews code, not plans |

Dana stays out of Phase 1 entirely: Arty covers feasibility, and a developer this early
produces implementation detail instead of requirements. His one job before Phase 4 is to
confirm each milestone file is self-contained and unambiguous.

---

## Rules

### What an agent does on its turn

1. Answers concerns addressed to it.
2. Reviews answers to its own concerns: satisfied, or a reply.
3. Raises new concerns.
4. Proposes or challenges requirements — including other agents' requirements.

Every agent reads the requirement set, not just its own mail. To keep that affordable,
`agents.last_seen_change` lets an agent read only what changed since its last turn.

### Who decides what

| Decision | Decided by |
|---|---|
| Goal, non-goals, success criteria | **The human.** Peter proposes; the human accepts or corrects |
| Ordinary requirements: accept, defer, reject | **Peter** |
| A dispute between two other agents | **Peter** |
| A dispute where Peter is one of the sides | **The human** |
| Anything that changes the goal or cuts a feature the human asked for | **The human** |
| An invariant (expensive to reverse) | **The human** |
| An appealed decision | **The human** |
| Which agent joins the project | **The human** |
| Which deliverable or milestone is built next | **The human** |

Peter settles the small stuff so the human is not doing the work themselves, and every
decision he makes appears in the status report, where the human can overrule it. Nothing is
hidden; the human simply does not have to be present for it.

**The appeal.** Any agent may appeal one of Peter's decisions once. The appeal does not go
back to Peter — it goes to the human, with the scribe presenting both sides. Peter's authority
never silently buries a serious objection.

**Why Peter and not a neutral party.** Settling Tina against Ian means answering "which better
serves the goal?", so whoever decides is arguing from the goal whether or not they are called
a participant. A decider with no stake has no criterion and defers to whoever spoke last. The
conflict is real, and what bounds it is the party rule and the appeal above.

### Staffing

A `staffing` concern is addressed to the human, never actioned automatically (unless
`agents.auto_staff = 1`, which is off by default). It states:

- what triggered it — the requirement or concern id
- what the new agent will do
- the likely effect: more constraints, more rounds, more cost

The human picks one of three: **approve** the agent, **defer** the triggering requirement to a
later deliverable, or **drop** it. A requirement the human never really wanted should not
quietly import a compliance review.

### Pausing, finishing, and safety

- **Pause:** any open concern addressed to the human. Nothing else stops the loop.
- **Finish Phase 1:** no open concerns, and every active agent has signed off on the current
  requirement set — each confirming its concerns are either included, or recorded in
  *Tradeoffs decided*, *Deferred and cut*, or *Accepted risks* with a reason.
- **Stall:** a concern thread past K rounds of replies goes to the human with both positions.
- **Circuit breaker:** a spend cap. On reaching it the scribe reports and waits, rather than
  quietly continuing.

---

## Phase 1 — Converge

```
R1  Human: "I want to make an employee hub app."        [concern #1, kind=brief → Peter]
    Peter → Human: who uses it? what problem today? what is in it?     [loop pauses]

R2  Human: HR and employees; directory, org tree, PTO requests, onboarding.
    Peter proposes goal + non-goals → Human accepts
    Peter → Human: this touches personal data and time off. Bring in Ian and Carla?
                   (more constraints, slower loop)                     [staffing]
    Tina and Arty join on the feature list

R3  Tina → Human: if you could ship one feature first, which?
    Arty → Human: will "the org as it was last quarter" ever be needed?
                  Dated rows cost little now and a rewrite later.      [invariant]
    Quinn joins

R4+ Ian and Carla raise concerns against specific features, not generic warnings.
    Agents answer each other. The human is untouched unless something is theirs.
```

The human appears in rounds 1–3, then only when a decision is genuinely theirs.

### The generated document

`requirements.md`, rendered from the tables at the end of Phase 1:

```
1. Goal and scope           rows of kind goal / non_goal / success_criterion
2. Requirements by feature  organized by what gets built, not by who asked
3. Cross-cutting rules      constraints and invariants that apply everywhere
4. Tradeoffs decided        the conflict, both sides, the decision, who decided, why
5. Deferred and cut         what was cut, why, and which deliverable could bring it back
6. Accepted risks           raised, knowingly not addressed, with the reason
```

Sections 4–6 are what the old `concerns-log.md` was for, and they are what every agent checks
before signing off.

---

## Phase 2 — Architect

Arty designs once, against the entire settled requirement set, and writes `architecture.md`:
components, data model, reasoning, diagrams. Alongside it he records **invariants** as
requirement rows.

An invariant is a decision that later requirements depend on and that is expensive to change
once code exists. Not a feature — a rule the code must keep true:

- *Employee rows are dated.* v1 shows only today, but history later is a feature, not a rewrite.
- *Import is an interface, not Workday-specific.* A second HR source is one implementation.
- *Authorization happens at the query layer.* A future API cannot bypass Ian's visibility rules.

The test: **if we skip this now and want it later, do we rewrite, or just add?** Rewrite means
it is an invariant. Every milestone file lists the invariants it must honor, so nothing gets
quietly bolted on.

---

## Phase 3 — Slice

```
accepted requirements
      ↓
Tina PROPOSES deliverables                owns 'deliverables'   cp-deliverable propose
Peter RULES on them                       rules_on              cp-deliverable decide
      ↓
Tina PROPOSES milestones                  owns 'milestones'     cp-milestone propose
Peter RULES on them                       rules_on              cp-milestone decide
      ↓
Arty PASSES the slicing                   reviews 'slicing'     cp-milestone review --ok
      ↓                                   nothing is planned until he does
Dana CHECKS buildability                  checks 'buildability' cp-milestone check --ok
      ↓                                   gaps become concerns
milestone-N.md written
```

Those four rows are the `agent_duties` table, not a description of it: the tools refuse an
agent that does not hold the duty, and `cp-policy` prints who currently does.

Deliverables come before milestones because "what is the first version worth having?" is a
different question from "what order do we build it in", and the human answers the first one.

Each `milestone-N.md` is generated from the rows and is self-contained: its requirements, its
corner cases, the invariants it must honor, and its acceptance criteria.

---

## Phase 4 — Build

```
Scribe → Human: deliverable v1 has 4 milestones. Which do you want built next?
                                                     [the human chooses; never "everything"]
      ↓
Dana implements that milestone against milestone-N.md
      ↓
Quinn tests behavior vs. spec + corner cases     Rita reviews code quality, security, design
      ↓                     either can send it back to Dana as a concern
      ↓
merge
      ↓
Human tests it.  Feedback becomes new concerns, and may become new requirements
                 for a later milestone or deliverable.
      ↓
Scribe → Human: what next?
```

Strictly one milestone at a time, and the human chooses each one. The stakeholder tries the
app as it is built, which is the point of shipping in slices at all. Phase 4 uses the same
database: Quinn's and Rita's findings are concerns addressed to Dana, so the full history from
idea to merge lives in one place, and Dana can read *why* a requirement exists instead of
guessing.

---

## Still open

- The spend cap itself — `report_every_rounds` exists, but nothing measures money yet.
- Whether a dormant agent (its area settled) wakes automatically when a requirement touches
  its area again.
- Where the database lives relative to the project repo, and whether it is committed.
- `cp-render milestone` and the `milestone-N.md` template.

Settled since the first draft, and now in `policy` rather than in prose: `stall_replies` (4),
`stall_rounds_open` (3), `report_every_rounds` (10), `appeals_per_decision` (1),
`human_cuts_own_requests` (1).

## Next steps

Done: `schema.sql`, the agent definitions, the orchestrator skill, the `requirements.md`
generator, and the phase-3 tools (`cp-deliverable`, `cp-milestone`).

1. Write the `milestone-N.md` generator (`cp-render milestone`) and its template.
2. Dry-run on one real, low-stakes project.
