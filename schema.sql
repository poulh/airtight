-- Convergence Pipeline — schema
--
-- Tables only; no data. Everything here is seeded from pipeline.yaml by
-- cp-init, including the pipeline's own rules: which kinds only the human can
-- change, which links a kind needs, who owns which duty, and the thresholds.
-- The tools read those rules back out of these tables, so the YAML is the only
-- place a rule is written down.
--
-- Every row a tool writes carries the turn that wrote it (turn_id) and when
-- (created_at), so the project can be replayed turn by turn.

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------- phases and policy

CREATE TABLE phases (
  number    INTEGER PRIMARY KEY,
  key       TEXT NOT NULL UNIQUE,   -- converge | architect | slice | build
  name      TEXT NOT NULL,
  ends_when TEXT NOT NULL
);

CREATE TABLE policy (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL,
  description TEXT NOT NULL
);

-- ------------------------------------------------------------ duties

CREATE TABLE duties (
  value       TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  seq         INTEGER NOT NULL
);

CREATE TABLE duty_relations (
  value       TEXT PRIMARY KEY,   -- owns | rules_on | reviews | checks
  description TEXT NOT NULL,
  seq         INTEGER NOT NULL
);

-- ---------------------------------------------------------------- vocabulary
--
-- Each lookup table carries the rules that belong to its values, so adding a
-- value in pipeline.yaml adds its rule with it.

CREATE TABLE statement_kinds (
  value            TEXT PRIMARY KEY,
  description      TEXT NOT NULL,
  level            TEXT NOT NULL,   -- project | deliverable
  guarded_by       TEXT,            -- role whose answer a change must cite
  requires_link    TEXT,            -- link relation it must have when written
  writable         INTEGER NOT NULL DEFAULT 0,
  document_section TEXT,
  seq              INTEGER NOT NULL
);

CREATE TABLE statement_statuses (
  value       TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  live        INTEGER NOT NULL DEFAULT 0,
  seq         INTEGER NOT NULL
);

CREATE TABLE link_relations (
  value       TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  from_kinds  TEXT,                 -- comma-separated; NULL means any
  to_kinds    TEXT,
  seq         INTEGER NOT NULL
);

CREATE TABLE concern_kinds (
  value        TEXT PRIMARY KEY,
  description  TEXT NOT NULL,
  must_address TEXT,                -- the only role this kind may be addressed to
  via_tool     TEXT,                -- created by this tool, not by cp-concern
  seq          INTEGER NOT NULL
);

CREATE TABLE concern_statuses (
  value TEXT PRIMARY KEY, description TEXT NOT NULL, seq INTEGER NOT NULL
);

CREATE TABLE answer_kinds (
  value       TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  judged      INTEGER NOT NULL DEFAULT 0,   -- the raiser gives a verdict on it
  seq         INTEGER NOT NULL
);

CREATE TABLE verdicts (
  value       TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  closes      INTEGER NOT NULL DEFAULT 0,
  needs_reply INTEGER NOT NULL DEFAULT 0,
  human_only  INTEGER NOT NULL DEFAULT 0,
  seq         INTEGER NOT NULL
);

CREATE TABLE concern_outcomes (
  value TEXT PRIMARY KEY, description TEXT NOT NULL, seq INTEGER NOT NULL
);

CREATE TABLE deliverable_statuses (
  value       TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  live        INTEGER NOT NULL DEFAULT 0,
  seq         INTEGER NOT NULL
);

CREATE TABLE milestone_statuses (
  value TEXT PRIMARY KEY, description TEXT NOT NULL, seq INTEGER NOT NULL
);

CREATE TABLE turn_statuses (
  value TEXT PRIMARY KEY, description TEXT NOT NULL, seq INTEGER NOT NULL
);

CREATE TABLE project_statuses (
  value TEXT PRIMARY KEY, description TEXT NOT NULL, seq INTEGER NOT NULL
);

-- -------------------------------------------------------------------- agents

CREATE TABLE agents (
  id               INTEGER PRIMARY KEY,
  name             TEXT NOT NULL UNIQUE,
  role             TEXT NOT NULL UNIQUE,
  motivation       TEXT NOT NULL,
  charter          TEXT,
  seq              INTEGER NOT NULL,          -- turn order within a round
  approves         INTEGER NOT NULL DEFAULT 0 CHECK (approves IN (0, 1)),
  joins_at_phase   INTEGER NOT NULL REFERENCES phases(number),
  join_trigger     TEXT,
  join_rationale   TEXT,
  auto_staff       INTEGER NOT NULL DEFAULT 0 CHECK (auto_staff IN (0, 1)),
  active           INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0, 1)),
  joined_round     INTEGER,
  joined_turn_id   INTEGER REFERENCES turns(id),
  requested_by     INTEGER REFERENCES agents(id),
  last_seen_change INTEGER NOT NULL DEFAULT 0   -- the latest event this agent has read
);

CREATE TABLE agent_phases (
  agent_id INTEGER NOT NULL REFERENCES agents(id),
  phase    INTEGER NOT NULL REFERENCES phases(number),
  PRIMARY KEY (agent_id, phase)
);

CREATE TABLE agent_duties (
  agent_id INTEGER NOT NULL REFERENCES agents(id),
  duty     TEXT NOT NULL REFERENCES duties(value),
  relation TEXT NOT NULL REFERENCES duty_relations(value),
  PRIMARY KEY (agent_id, duty, relation)
);

-- --------------------------------------------------------------------- turns

-- One per agent per round, skipped ones included. The human's actions during
-- a pause are turns too, so the timeline has no gaps.
CREATE TABLE turns (
  id         INTEGER PRIMARY KEY,           -- shown as T-43
  round      INTEGER NOT NULL,
  seq        INTEGER NOT NULL,              -- position within the round
  phase      INTEGER NOT NULL REFERENCES phases(number),
  agent_id   INTEGER NOT NULL REFERENCES agents(id),
  status     TEXT NOT NULL REFERENCES turn_statuses(value),
  summary    TEXT,                          -- the agent's own words; narration, never input
  tokens     INTEGER,
  started_at TEXT NOT NULL,
  ended_at   TEXT
);

-- ------------------------------------------------------ deliverables and milestones

CREATE TABLE deliverables (
  id            INTEGER PRIMARY KEY,        -- shown as D-1
  name          TEXT NOT NULL,              -- v1, v2
  seq           INTEGER NOT NULL,           -- delivery order
  status        TEXT NOT NULL DEFAULT 'live' REFERENCES deliverable_statuses(value),
  created_round INTEGER NOT NULL,
  retired_round INTEGER,
  turn_id       INTEGER NOT NULL REFERENCES turns(id),
  created_at    TEXT NOT NULL
);

-- old_id was split or reshaped into new_id
CREATE TABLE deliverable_lineage (
  old_id     INTEGER NOT NULL REFERENCES deliverables(id),
  new_id     INTEGER NOT NULL REFERENCES deliverables(id),
  turn_id    INTEGER NOT NULL REFERENCES turns(id),
  created_at TEXT NOT NULL,
  PRIMARY KEY (old_id, new_id)
);

CREATE TABLE milestones (
  id             INTEGER PRIMARY KEY,       -- shown as M-3
  deliverable_id INTEGER NOT NULL REFERENCES deliverables(id),
  name           TEXT NOT NULL,
  seq            INTEGER NOT NULL,
  intent         TEXT,
  status         TEXT NOT NULL DEFAULT 'planned' REFERENCES milestone_statuses(value),
  branch         TEXT,
  sliced_ok_by   INTEGER REFERENCES agents(id),   -- the slicing review passed
  checked_ok_by  INTEGER REFERENCES agents(id),   -- the buildability check passed
  tested_ok_by   INTEGER REFERENCES agents(id),   -- QA passed it (reset on rework)
  reviewed_ok_by INTEGER REFERENCES agents(id),   -- code review passed it (reset on rework)
  turn_id        INTEGER NOT NULL REFERENCES turns(id),
  created_at     TEXT NOT NULL
);

-- ---------------------------------------------------------------- statements

-- Everything everyone must agree to: goals, non-goals, success criteria,
-- invariants (project-wide), and per deliverable its scope and requirements.
CREATE TABLE statements (
  id             INTEGER PRIMARY KEY,       -- shown as S-12
  kind           TEXT NOT NULL REFERENCES statement_kinds(value),
  text           TEXT NOT NULL,
  rationale      TEXT,
  deliverable_id INTEGER REFERENCES deliverables(id),   -- NULL for project-level kinds
  status         TEXT NOT NULL DEFAULT 'pending' REFERENCES statement_statuses(value),
  milestone_id   INTEGER REFERENCES milestones(id),     -- planning, not agreement
  created_round  INTEGER NOT NULL,
  retired_round  INTEGER,
  turn_id        INTEGER NOT NULL REFERENCES turns(id),
  created_at     TEXT NOT NULL
);

CREATE INDEX idx_statements_status ON statements(status, kind);

-- Read as a sentence: <from> <relation> <to>.
--   S-10 supersedes S-2      S-4 measures S-3
CREATE TABLE links (
  from_id    INTEGER NOT NULL REFERENCES statements(id),
  to_id      INTEGER NOT NULL REFERENCES statements(id),
  relation   TEXT NOT NULL REFERENCES link_relations(value),
  turn_id    INTEGER NOT NULL REFERENCES turns(id),
  created_at TEXT NOT NULL,
  PRIMARY KEY (from_id, to_id, relation)
);

-- The closed concerns a statement (or milestone) came from.
CREATE TABLE statement_reasons (
  statement_id INTEGER NOT NULL REFERENCES statements(id),
  concern_id   INTEGER NOT NULL REFERENCES concerns(id),
  turn_id      INTEGER NOT NULL REFERENCES turns(id),
  created_at   TEXT NOT NULL,
  PRIMARY KEY (statement_id, concern_id)
);

CREATE TABLE milestone_reasons (
  milestone_id INTEGER NOT NULL REFERENCES milestones(id),
  concern_id   INTEGER NOT NULL REFERENCES concerns(id),
  turn_id      INTEGER NOT NULL REFERENCES turns(id),
  created_at   TEXT NOT NULL,
  PRIMARY KEY (milestone_id, concern_id)
);

CREATE TABLE approvals (
  statement_id INTEGER NOT NULL REFERENCES statements(id),
  agent_id     INTEGER NOT NULL REFERENCES agents(id),
  round        INTEGER NOT NULL,
  turn_id      INTEGER NOT NULL REFERENCES turns(id),
  created_at   TEXT NOT NULL,
  PRIMARY KEY (statement_id, agent_id)
);

-- ------------------------------------------------------------------ the loop

-- Always on exactly one statement or one milestone, raised by one agent,
-- addressed to one agent. Only the raiser closes it (or the human, on their
-- own answer). A closed concern on a statement waits for Peter to act on it.
CREATE TABLE concerns (
  id              INTEGER PRIMARY KEY,      -- shown as C-4
  statement_id    INTEGER REFERENCES statements(id),
  milestone_id    INTEGER REFERENCES milestones(id),
  kind            TEXT NOT NULL REFERENCES concern_kinds(value),
  raised_by       INTEGER NOT NULL REFERENCES agents(id),
  addressed_to    INTEGER NOT NULL REFERENCES agents(id),   -- current holder
  body            TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'open' REFERENCES concern_statuses(value),
  round           INTEGER NOT NULL,
  closed_round    INTEGER,                  -- the round the answer was accepted
  replies_since_reassign INTEGER NOT NULL DEFAULT 0,
  replies_total   INTEGER NOT NULL DEFAULT 0,
  acted_on        TEXT REFERENCES concern_outcomes(value),  -- NULL: awaiting Peter
  acted_round     INTEGER,
  turn_id         INTEGER NOT NULL REFERENCES turns(id),
  created_at      TEXT NOT NULL,
  CHECK ((statement_id IS NULL) <> (milestone_id IS NULL))
);

CREATE INDEX idx_concerns_queue ON concerns(addressed_to, status);

CREATE TABLE answers (
  id            INTEGER PRIMARY KEY,        -- shown as A-7
  concern_id    INTEGER NOT NULL REFERENCES concerns(id),
  answered_by   INTEGER NOT NULL REFERENCES agents(id),
  kind          TEXT NOT NULL REFERENCES answer_kinds(value),
  body          TEXT NOT NULL,
  reassigned_to INTEGER REFERENCES agents(id),
  verdict       TEXT REFERENCES verdicts(value),
  reply         TEXT,
  round         INTEGER NOT NULL,
  verdict_round INTEGER,
  verdict_turn_id INTEGER REFERENCES turns(id),
  turn_id       INTEGER NOT NULL REFERENCES turns(id),
  created_at    TEXT NOT NULL
);

CREATE INDEX idx_answers_thread ON answers(concern_id, id);

-- Every state change, for the report, the history and a front end. turn_id and
-- actor are empty only for the orchestrator's own moves: advancing the round and
-- changing phase, which happen between turns.
CREATE TABLE events (
  id          INTEGER PRIMARY KEY,          -- the change mark
  turn_id     INTEGER REFERENCES turns(id),
  round       INTEGER NOT NULL,
  actor       INTEGER REFERENCES agents(id),
  object      TEXT NOT NULL,                -- statement | deliverable | concern | milestone
                                            -- | agent | report | project
  object_id   INTEGER NOT NULL,
  from_status TEXT,
  to_status   TEXT,
  detail      TEXT,
  created_at  TEXT NOT NULL
);

CREATE INDEX idx_events_object ON events(object, object_id, id);

CREATE TABLE reports (
  id           INTEGER PRIMARY KEY,         -- shown as P-2
  round        INTEGER NOT NULL,
  turn_id      INTEGER NOT NULL REFERENCES turns(id),
  body         TEXT NOT NULL,               -- as the human saw it
  created_at   TEXT NOT NULL,
  continued_at TEXT,                        -- when the human said continue
  continued_turn_id INTEGER REFERENCES turns(id)
);

-- ------------------------------------------------------------------- state

-- One row: what is happening right now.
CREATE TABLE project_state (
  id              INTEGER PRIMARY KEY CHECK (id = 1),
  round           INTEGER NOT NULL DEFAULT 1,
  phase           INTEGER NOT NULL DEFAULT 1 REFERENCES phases(number),
  status          TEXT NOT NULL DEFAULT 'running' REFERENCES project_statuses(value),
  current_turn_id INTEGER REFERENCES turns(id),
  paused_reason   TEXT,                     -- concern | report
  paused_ref      INTEGER,                  -- the concern or report id
  change_mark     INTEGER NOT NULL DEFAULT 0,
  updated_at      TEXT NOT NULL
);
