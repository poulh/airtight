# The Convergence Pipeline

*Requirements → architecture → milestones → code, in that order*

A Claude Code skill that replaces "code it, then I'll find the gaps" with a pre-code loop.
Specialist agents, each with one thing it cares about, read every requirement and either
approve it or raise a concern on it. Concerns are argued out between agents; the project
manager turns the settled arguments into new requirements; everyone reviews those. The human
is interrupted only when something is genuinely theirs. Only once every requirement is agreed
does an architect design against the *whole* picture, and only then is anything sliced into
milestones and built.

The shared state is a SQLite database. Every requirement, approval, concern and answer is a
row, stamped with the round it happened in.

> **Status (2026-09-27):** this document is the redesigned model, agreed in an interview with
> the human. The code in `convergence/`, `schema.sql` and `pipeline.yaml` still implements the
> previous model and has not been rebuilt yet.

---

## The four phases

| | Phase | What happens | Ends when |
|---|---|---|---|
| **1** | **Converge** | The human's brief becomes requirement R-1. Peter interviews the human through concerns and writes requirements from the answers. Every agent approves each requirement or raises a concern; settled concerns become new requirements. Agents join as the project earns them. | Every live requirement is agreed, no concern is open, and Peter has acted on every closed concern |
| **2** | **Architect** | Arty designs against the entire agreed requirement set and writes `architecture.md`. Invariants he needs become requirements through the normal loop. | The architecture is accepted |
| **3** | **Slice** | Each deliverable's requirements are cut into milestones; Arty passes the slicing; Dana checks each milestone file is buildable. | Milestone files written |
| **4** | **Build** | The human picks a milestone. Dana builds it; Quinn and Rita review; either can send it back; merge; the human tests. | The human stops asking for more |

Phase 1 runs unattended and has no round budget. It pauses when a concern is addressed to
the human, and at each report until the human says continue.

---

## Core ideas

**Terminology.** Everything everyone must agree to is a **statement**: goals, non-goals,
success criteria, invariants (all project-wide) and, per deliverable, its scope and its
**requirements** (functional and constraint). Statements share one table, one id sequence
(`S-12`) and one set of machinery: approvals, concerns, lineage and reasons. Where this
document says "requirement" loosely about approving or superseding, the rule applies to every
statement.

**Requirements are the only shared object.** Every agent reads every requirement it has not
yet approved. Concerns are private mail between two agents; the requirement set is what
everyone sees. The human's brief is itself a requirement, R-1, so everything traces back to
the human's own words.

**Agreement is unanimous and explicit.** A requirement is *agreed* when every approving agent
has approved it, no concern on it is open, and Peter has acted on every closed concern on it.
Silence is not agreement.

**Nothing agreed is ever edited.** A change retires the old requirement (`superseded` or
`cancelled`) and writes new ones, linked through a lineage table. Splits (1 → 2, 3), merges
(2 + 3 → 4) and moves between deliverables are all the same operation. Approvals never carry
over to a new row.

**One writer.** Only Peter writes requirements and deliverables. An agent answering a concern
answers in words; the asker accepting the answer only closes the concern. Peter reads the
closed concerns later, all together, and decides what changes — so two agents' accepted
answers that conflict are caught by one reader before either becomes a requirement.

**Every requirement cites its reasons.** Apart from R-1, a requirement exists only because of
one or more closed concerns, and names them. There is no way to write a requirement out of
thin air: Peter who wants one raises a concern first, usually to the human.

**The human's intent cannot drift without them.** Goals, non-goals, success criteria and the
brief can only be changed by citing a concern the human answered.

**One source of truth.** `pipeline.yaml` declares the phases, the thresholds, the roster and
every vocabulary, and each rule is stored on the thing it describes. `cp-init` seeds the
database; the tools read the rules back; `cp-doctor` reports drift.

**The tables are the state; documents are printouts.** `requirements.md`, `milestone-N.md`
and the human's periodic report are generated. Only `architecture.md` is authored.

**Agents join when the project earns them, and only with the human's approval.**

---

## The objects and their lifecycles

### Deliverables

A deliverable is a release worth having: v1, v2. The project starts with one, **D-1**,
because the first assumption is that the whole idea ships at once.

```
live ──► superseded      (split or reshaped; lineage points to the replacements)
     └─► cancelled       (dropped)
```

A deliverable is never renamed or edited. When scope grows enough that D-1 should be two
releases, an agent raises a concern on D-1's scope requirement. Once it is settled, Peter:

1. **splits** D-1 into D-2 and D-3 (`deliverable_lineage`: D-1→D-2, D-1→D-3). D-1 becomes
   `superseded`.
2. **moves** every live requirement of D-1 into D-2 or D-3. A move is a new requirement row
   with a lineage link, so everyone re-approves it. Until moved, D-1's requirements are listed
   in Peter's queue as homeless and cannot be agreed.
3. writes a **scope** requirement for each new deliverable.

What a deliverable *is for* lives in its `scope` requirement ("v1: requests and approvals;
no accruals"), which goes through the same approve-or-concern loop as anything else.

### Requirements

**Kinds**

| Kind | Where | What it is | Changed only with the human |
|---|---|---|---|
| `brief` | project | The human's own words, verbatim. R-1. | yes |
| `goal` | project | Why the project exists. A direction, not a test. | yes |
| `non_goal` | project | Explicitly out of scope. | yes |
| `success_criterion` | project | How we will know a goal is met. Measurable. **Must name the goal it measures.** | yes |
| `scope` | one deliverable | What this release covers and leaves out. | no |
| `functional` | one deliverable | Something the system does. | no |
| `constraint` | one deliverable | A rule on how it is done. | no |
| `invariant` | project | A rule the code must keep true everywhere, cheap to decide now and a rewrite later (*times are stored in UTC*; *employee rows are dated, never overwritten*). | yes |

Project-level kinds have no deliverable. Every other statement belongs to exactly one live
deliverable. Each kind's rules live on the kind in `pipeline.yaml`: `level` (project or
deliverable), `guarded_by: human`, and `requires_link` (a success criterion must `measure`
a goal). No kind has columns of its own; relationships are rows in `links`. Superseding a goal
flags every success criterion that measures it.

**States**

```
pending ──► agreed          every approver approved, no open concern, nothing awaiting Peter
   ▲          │
   └──────────┘             a retraction, a late-joining agent, or a new concern on it
pending/agreed ──► superseded   replaced; requirement_lineage points to the new rows
pending/agreed ──► cancelled    dropped, nothing replaces it
```

`agreed` is recomputed after every action, so it falls back to `pending` on its own when an
agent retracts, when the human raises a concern on it, or when a new agent joins and has not
approved it yet.

### Approvals

One row per agent per statement. The **approvers** are the active agents marked
`approves: true` in `pipeline.yaml`: everyone except the human and Rita, who reviews code, not
statements.
An agent may not approve a requirement while it has its own concern open on it.

**Retracting** removes the agent's approval of an agreed requirement, and the tool requires a
concern on that requirement in the same call, saying why. This is how an agent who notices
that a new requirement makes an old agreed one wrong gets the old one back on the table.

### Concerns

A concern is always **on one statement or one milestone** (exactly one), raised by one agent,
addressed to one agent. Concerns on a milestone are phase 4's findings: a problem with
delivering that milestone rather than with what was agreed.

```
open ──► closed          the asker accepted an answer, or the human marked an answer final
  │
  └── addressee answers ──► back to the asker ──► accept (closed)
  │                                          └──► reply (open, back to the addressee)
  └── addressee reassigns to another agent (the asker still judges the eventual answer)
```

Only the asker closes a concern, except that the human may mark their own answer **final**,
which closes it as accepted.

Once closed, a concern **waits for Peter** until he acts on it: `changed` (naming the
requirements that resulted) or `kept` (no change needed). The requirement it is on cannot be
agreed while any concern on it is waiting.

**Kinds:** `question`, `risk`, `objection`, `proposal`, `staffing`. (No `brief`: the brief is
R-1. No `appeal`: nobody rules, so there is nothing to appeal.)

**Stuck threads.** Each concern counts replies since its last reassignment and replies over
its lifetime. Peter watches threads and may reassign a stuck one to the human early, with a
summary of both positions. At `policy.stall_replies` it goes to the human automatically.
Reassignment resets the first count; the lifetime count never resets, and the report flags a
concern that keeps coming back. Every loop passes through the human, so it cannot cycle
unattended.

### Answers

One row per answer, stamped with its round. Kinds:

- `answer` — words. The asker judges it: `accepted` (closes the concern, stamping the round
  it was accepted) or `replied` (with the reply text; the concern goes back to the addressee).
- `reassign` — a hand-off note: who passed it on, to whom, and why. Never judged.
- An answer from the human may be marked `final`.

One answer at a time: an addressee cannot answer again while their previous answer awaits the
asker's verdict.

---

## Who does what

| | Does |
|---|---|
| **The human** | Gives the brief. Answers concerns addressed to them, and may mark an answer final. Reads the report every `report_every_rounds` rounds: newly agreed requirements and everything Peter changed. Raises concerns on any requirement, agreed or not. Approves staffing. Picks each milestone to build. Does **not** approve requirements. |
| **Peter** (PM) | Interviews the human by raising concerns. **The only writer** of requirements and deliverables. Acts on closed concerns: checks them against every live goal, non-goal and criterion, then writes, supersedes, moves or cancels requirements, marks concerns kept, or raises new concerns (to the human when the goal is touched). Watches for stuck threads. Runs the report. Approves requirements like any agent. |
| **Every other active agent** | Approves or raises concerns on each requirement it has not approved. Answers or reassigns concerns addressed to it. Accepts or replies to answers on its own concerns. May retract an approval, with a concern. |

There is no Scribe. The report to the human is a printout of the tables, so Peter triggers it
but cannot slant it, and "nobody reports on their own decisions" still holds.

### An agent's turn

1. **Answers to my concerns** — accept, or reply.
2. **Concerns addressed to me** — answer, or reassign.
3. **Requirements I have not approved** (pending, not retired) — approve, or raise a concern.
   The queue shows each one with every live goal and its deliverable's scope, for context.
4. **Optionally, retract** an approval of an agreed requirement, with a concern.
5. **Close the turn** — mark the queue seen.

### Peter's turn

Steps 1–5 above, plus, before closing:

- **Act on settled requirements.** The queue lists every requirement whose concerns are all
  closed and at least one awaits action, with those concerns, their accepted answers, and
  every live goal-level requirement. For each, Peter either writes the change (citing the
  concerns), marks the concerns kept, or raises a new concern if two answers conflict or a
  goal is touched.
- **Rehome** requirements of a superseded deliverable.
- **Watch** stuck threads and send any to the human.
- **Report** to the human every `report_every_rounds` rounds.

---

## One requirement's life

```
R1   Human's brief is recorded as R-1 (brief, project).  D-1 exists.
     Peter raises questions on R-1 to the human. The loop pauses.

R2   Human answers. Peter accepts, then acts: supersedes R-1 into goals, non-goals,
     criteria, D-1's scope, and R-6 "a manager approves or declines each leave request" (D-1).
     Every requirement cites the human-answered concerns it came from.

R3   Ian approves R-6. Otto approves R-6.
     Arty raises C-4 on R-6 → Peter: "what if the manager does nothing for 5 days?"
     Quinn raises C-5 on R-6 → Uma: "can a manager approve from a phone?"

R4   Peter answers C-4: "it escalates to the manager's manager after 5 working days."
     Uma reassigns C-5 to Arty. Arty answers it: "yes, the screen is responsive."
     Arty accepts Peter's answer → C-4 closed, awaiting Peter.

R5   Quinn accepts Arty's answer → C-5 closed, awaiting Peter.
     Peter's turn: every concern on R-6 is closed. He reads C-4 and C-5 against the goals:
       C-4 needs a change → supersede R-6 into R-7 (R-6's wording) and R-8 (the escalation),
                            lineage R-6→R-7, R-6→R-8, both citing C-4.
       C-5 needs none     → mark kept.
     Ian's and Otto's approvals were on R-6; they do not carry to R-7 and R-8.

R6+  Everyone reviews R-7 and R-8.

R10  Peter runs the report: newly agreed R-7, R-8; R-6 superseded because of C-4.
     The human raises a concern on R-8. R-8 falls back to pending until it is settled.
```

---

## The database

```sql
CREATE TABLE deliverables (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,              -- v1, v2
  seq          INTEGER NOT NULL,           -- delivery order
  status       TEXT NOT NULL,              -- live | superseded | cancelled
  created_round INTEGER NOT NULL,
  retired_round INTEGER
);

CREATE TABLE deliverable_lineage (
  old_id INTEGER NOT NULL REFERENCES deliverables(id),
  new_id INTEGER NOT NULL REFERENCES deliverables(id),
  PRIMARY KEY (old_id, new_id)
);

CREATE TABLE statements (
  id             INTEGER PRIMARY KEY,      -- shown as S-12
  kind           TEXT NOT NULL,            -- brief | goal | non_goal | success_criterion
                                           -- | invariant | scope | functional | constraint
  text           TEXT NOT NULL,
  rationale      TEXT,
  deliverable_id INTEGER REFERENCES deliverables(id),   -- NULL for project-level kinds
  status         TEXT NOT NULL,            -- pending | agreed | superseded | cancelled
  milestone_id   INTEGER REFERENCES milestones(id),     -- phase 3; not part of agreement
  created_round  INTEGER NOT NULL,
  retired_round  INTEGER
);

-- Read as a sentence: <from> <relation> <to>.
--   S-10 supersedes S-2   (splits, merges, rewordings, moves)
--   S-4  measures   S-3   (a success criterion and its goal)
-- Each relation's rules (which kinds it joins, whether it is required) are in pipeline.yaml.
CREATE TABLE links (
  from_id  INTEGER NOT NULL REFERENCES statements(id),
  to_id    INTEGER NOT NULL REFERENCES statements(id),
  relation TEXT NOT NULL,                  -- supersedes | measures
  PRIMARY KEY (from_id, to_id, relation)
);

CREATE TABLE statement_reasons (           -- the closed concerns a statement came from
  statement_id   INTEGER NOT NULL REFERENCES statements(id),
  concern_id     INTEGER NOT NULL REFERENCES concerns(id),
  PRIMARY KEY (statement_id, concern_id)
);

CREATE TABLE approvals (
  statement_id   INTEGER NOT NULL REFERENCES statements(id),
  agent_id       INTEGER NOT NULL REFERENCES agents(id),
  round          INTEGER NOT NULL,
  PRIMARY KEY (statement_id, agent_id)
);

CREATE TABLE concerns (
  id              INTEGER PRIMARY KEY,
  statement_id    INTEGER REFERENCES statements(id),             -- on exactly one of
  milestone_id    INTEGER REFERENCES milestones(id),             --   these two
  kind            TEXT NOT NULL,           -- question | risk | objection | proposal | staffing
  raised_by       INTEGER NOT NULL REFERENCES agents(id),
  addressed_to    INTEGER NOT NULL REFERENCES agents(id),        -- current holder
  body            TEXT NOT NULL,
  status          TEXT NOT NULL,           -- open | closed
  round           INTEGER NOT NULL,
  closed_round    INTEGER,                 -- the round the answer was accepted
  replies_since_reassign INTEGER NOT NULL DEFAULT 0,
  replies_total   INTEGER NOT NULL DEFAULT 0,
  acted_on        TEXT,                    -- NULL (awaiting Peter) | changed | kept
  acted_round     INTEGER,                 --   (statement concerns only)
  CHECK ((statement_id IS NULL) <> (milestone_id IS NULL))
);

CREATE TABLE answers (
  id          INTEGER PRIMARY KEY,
  concern_id  INTEGER NOT NULL REFERENCES concerns(id),
  answered_by INTEGER NOT NULL REFERENCES agents(id),
  kind        TEXT NOT NULL,               -- answer | reassign
  body        TEXT NOT NULL,
  reassigned_to INTEGER REFERENCES agents(id),                   -- for reassign
  verdict     TEXT,                        -- NULL | accepted | replied | final
  reply       TEXT,
  round       INTEGER NOT NULL,
  verdict_round INTEGER
);

CREATE TABLE events (                      -- every state change, for the report and history
  id INTEGER PRIMARY KEY, round INTEGER NOT NULL, actor INTEGER NOT NULL,
  object TEXT NOT NULL, object_id INTEGER NOT NULL,     -- statement | deliverable | concern
  from_status TEXT, to_status TEXT, detail TEXT
);
```

Kept from the previous design: `agents`, `agent_phases`, `agent_duties`, `policy`, `phases`,
the vocabulary tables, `milestones`, `project_state` (round, phase, change mark).
`agents.last_seen_change` still records how far each agent has read.

---

## The tools

Agents never write SQL. The rules live in the tools and are read from `pipeline.yaml`.

| Tool | Who | What it does / refuses |
|---|---|---|
| `cp-init` | skill | Builds the database, records the brief as R-1 and creates D-1 |
| `cp-queue` | anyone | The turn: answers to judge, concerns to answer, requirements to review (with goals and scope), and for Peter, requirements ready to act on, homeless requirements and stuck threads. `--mark-seen` |
| `cp-approve` | approvers | Approve a requirement. `--retract` removes an approval and requires a concern body. Refuses while the agent's own concern on it is open |
| `cp-concern` | anyone | Raise a concern on one requirement, to one agent. Refuses self-addressing, inactive agents, retired requirements |
| `cp-answer` | the addressee | `answer`, or `reassign --to`. Refuses a second answer while one awaits a verdict. `--final` is the human's only |
| `cp-review` | the asker | `accept`, or `reply` with text. Judges only the latest answer, never a reassignment |
| `cp-escalate` | Peter | Reassign any stuck concern to the human with a summary |
| `cp-statement` | Peter | `add`, `supersede` (one or many old → one or many new), `move` (to another deliverable), `cancel`, `keep`. Every write cites closed concerns (`--because C-4,C-5`) and marks them acted on. Refuses: citing an open concern; changing a goal-level requirement without a concern the human answered; a success criterion with no goal; a deliverable-level requirement with no live deliverable |
| `cp-deliverable` | Peter | `add`, `split` (one → many, via lineage), `cancel`. Cites closed concerns |
| `cp-staff` | anyone / human | Request an agent (a concern to the human on the triggering requirement); approve or decline |
| `cp-state` | anyone | Round, phase, what it is paused on, requirements by status, whether phase 1 has converged |
| `cp-round` | skill | `--advance` refuses while a concern is with the human; `--phase N` |
| `cp-report` | Peter | The human's update: newly agreed, everything Peter changed and why, stuck and bouncing threads, staffing. **Pauses the loop** until the human continues it |
| `cp-render` | anyone | `requirements.md`; later `milestone-N.md` |
| `cp-policy` / `cp-doctor` | anyone | The rules in force; drift between config, charters, skill and database |

Removed: `cp-propose`, `cp-decide`, `cp-signoff` (approvals replace sign-off).
Milestones follow the one-writer rule too: Tina raises concerns proposing them, Peter writes
them (`cp-milestone`), Arty passes the slicing and Dana checks buildability.

---

## The roster

| id | Agent | Motivation | Joins when |
|---|---|---|---|
| 1 | **The Human** | Owns the goal. Settles what no one else can. | always |
| 2 | **Peter** the Project Manager | Does this still serve the goals? | always |
| 3 | **Tina** the Time-to-Market | Ship something useful sooner; argues for deliverable splits. | a first feature list exists |
| 4 | **Arty** the Architect | Will this hold together, and what must v1 not preclude? | a first feature list exists |
| 5 | **Quinn** the QA | What breaks it? | features are concrete |
| 6 | **Ian** the Infosec | Who can see or do what they should not? | personal data, login or permissions appear |
| 7 | **Carla** the Compliance Officer | What do law, regulation and policy demand? | regulated data appears |
| 8 | **Otto** the Ops/SRE | Will it survive real load, and can it be run and recovered? | scale, refresh or uptime expectations appear |
| 9 | **Uma** the UX | Can a real person accomplish the task? | the UI is more than a list or a form |
| 10 | **Dana** the Developer | Build exactly this milestone. | end of phase 3. Like any late joiner she must approve every live statement, which *is* her buildability check |
| 11 | **Rita** the Reviewer | Is the code sound, safe and maintainable? | phase 4. `approves: false`: she raises concerns on milestones, and on statements if she spots a spec problem, but is not an approver |

**Staffing.** A staffing concern is raised on the requirement that triggered it and addressed
to the human, stating what the agent would do and what it will cost. The human approves the
agent, or answers that the requirement should be deferred or dropped — which Peter then acts
on like any other closed concern. A new agent starts with no approvals, so every live
requirement falls back to pending until they have reviewed it.

---

## Phase 1 — Converge

Ends when every live requirement is agreed, no concern is open, and no closed concern is
waiting for Peter. The generated `requirements.md`:

```
1. Goals              goals, each with its success criteria; non-goals
2. Per deliverable    its scope, then its requirements
3. Kept as is         concerns Peter settled with no change (risks knowingly accepted)
4. History            what was superseded or cancelled, into what, and which concerns caused it
```

## Phase 2 — Architect

Arty designs once, against the entire agreed set, and writes `architecture.md`. An
**invariant** is a decision later requirements depend on and that is expensive to reverse
(*employee rows are dated*; *authorization happens at the query layer*). The test: if we skip
this now and want it later, do we rewrite or just add? Invariants are project-wide and
guarded like goals: Arty raises each as a concern to the human ("two days now, or weeks
later"), Peter writes it from the human's answer, everyone approves it.

## Phase 3 — Slice

Deliverables already exist from phase 1. Tina raises concerns proposing how each
deliverable's requirements are cut into milestones; Peter writes the milestones; Arty passes
the slicing. Dana joins, reads every live statement and approves each or raises a concern on
it — the ambiguities are meant to surface here, before any code exists — and checks each
generated `milestone-N.md`. Milestone assignment is planning, not agreement: moving a
statement between milestones does not reset approvals.

## Phase 4 — Build

Strictly one milestone at a time, and the human picks each one. Every active agent stays
active, so a statement added mid-build is approved by everyone, exactly as in phase 1.

```
planned ──► building ──► in_review ──► merged ──► accepted
               │  ▲          │            │
               ▼  │          │            └──► building   the human's test finds a bug
            blocked          └──► building     a concern on the milestone needs a fix
```

1. **The human picks** a milestone. `milestone-N.md` is regenerated from the current agreed
   statements: its requirements, the invariants and success criteria it must honour.
2. **Dana re-reads** it and builds on a branch for that milestone.
3. **A spec problem blocks the milestone.** If a statement is ambiguous or wrong, before or
   during coding, Dana raises a concern on the statement, addressed to Peter. The milestone is
   `blocked` until the statement is settled and re-agreed; then its file is regenerated. Dana
   never builds on a guess.
4. **Review.** Quinn tests the behaviour against the milestone file; Rita reads the code
   (invariants honoured, failure paths, security in the implementation, fit for the next
   milestone). Each problem is a **concern on the milestone**, addressed to Dana — or, if it
   is really a spec gap, a concern on the statement, addressed to Peter. Only the raiser
   closes it.
5. **Dana merges** once no concern on the milestone is open.
6. **The loop pauses for the human to test.** A bug in what M-N was supposed to do is a
   concern on the milestone, and it goes back to `building`. Something new ("I expected an
   email") is a concern on the deliverable's scope, addressed to Peter, and becomes a new
   statement through the normal loop, to be built in a later milestone. The milestone is
   `accepted` only when the human says so.

---

## Open

- **Phase 4 code home:** one branch per milestone in the project's own repo — confirm, and
  whether Quinn writes automated acceptance tests or tests by hand.
- **Prose pass:** sections above still say "requirement" loosely; tighten to "statement"
  during the rebuild.
- **Spend cap**, **dormant agents** waking when their area is touched, and **where the
  database lives** and whether it is committed — unchanged from before.
