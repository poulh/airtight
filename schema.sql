-- Convergence Pipeline — schema
--
-- Tables only; no data. Everything here is seeded from pipeline.yaml by
-- cp-init, including the pipeline's own rules: which role may decide a kind of
-- requirement, what an answer must carry, which agent owns which duty, and the
-- thresholds. The tools read those rules back out of these tables, so the YAML
-- is the only place a rule is written down.

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

CREATE TABLE concern_kinds (
  value         TEXT PRIMARY KEY,
  description   TEXT NOT NULL,
  must_address  TEXT,            -- role this kind may only be addressed to
  via_tool      TEXT,            -- created by this tool, not by cp-concern
  about         TEXT NOT NULL,   -- what it must point at: none | requirement | concern | any
  seq           INTEGER NOT NULL
);

CREATE TABLE concern_statuses (
  value       TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  seq         INTEGER NOT NULL
);

CREATE TABLE answer_kinds (
  value            TEXT PRIMARY KEY,
  description      TEXT NOT NULL,
  requires         TEXT,   -- requirements | reason | recipient
  document_section TEXT,
  seq              INTEGER NOT NULL
);

CREATE TABLE requirement_kinds (
  value            TEXT PRIMARY KEY,
  description      TEXT NOT NULL,
  decided_by       TEXT NOT NULL,   -- role that may rule on it
  document_section TEXT,
  seq              INTEGER NOT NULL
);

CREATE TABLE requirement_statuses (
  value            TEXT PRIMARY KEY,
  description      TEXT NOT NULL,
  in_document      INTEGER NOT NULL DEFAULT 0,
  document_section TEXT,             -- NULL with in_document = 1 means "the kind's section"
  seq              INTEGER NOT NULL
);

CREATE TABLE cost_flags (
  value       TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  set_by      TEXT,   -- the only role that may apply it
  seq         INTEGER NOT NULL
);

CREATE TABLE link_relations (
  value       TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  seq         INTEGER NOT NULL
);

CREATE TABLE deliverable_statuses (
  value       TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  in_document INTEGER NOT NULL DEFAULT 1,
  seq         INTEGER NOT NULL
);

CREATE TABLE milestone_statuses (
  value       TEXT PRIMARY KEY,
  description TEXT NOT NULL,
  in_document INTEGER NOT NULL DEFAULT 1,
  seq         INTEGER NOT NULL
);

-- -------------------------------------------------------------------- agents

CREATE TABLE agents (
  id               INTEGER PRIMARY KEY,
  name             TEXT NOT NULL UNIQUE,
  role             TEXT NOT NULL UNIQUE,
  motivation       TEXT NOT NULL,
  charter          TEXT,
  joins_at_phase   INTEGER NOT NULL REFERENCES phases(number),
  join_trigger     TEXT,
  join_rationale   TEXT,
  auto_staff       INTEGER NOT NULL DEFAULT 0 CHECK (auto_staff IN (0, 1)),
  active           INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0, 1)),
  joined_round     INTEGER,
  requested_by     INTEGER REFERENCES agents(id),
  last_seen_change INTEGER NOT NULL DEFAULT 0
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

-- ------------------------------------------------------------------ the loop

CREATE TABLE concerns (
  id             INTEGER PRIMARY KEY,
  kind           TEXT NOT NULL REFERENCES concern_kinds(value),
  raised_by      INTEGER NOT NULL REFERENCES agents(id),
  addressed_to   INTEGER NOT NULL REFERENCES agents(id),
  body           TEXT NOT NULL,
  -- What the raiser read that prompted this: a requirement or another concern.
  -- Only a kind whose `about` is 'none' (the brief) may leave both empty.
  about_requirement_id INTEGER REFERENCES requirements(id),
  about_concern_id     INTEGER REFERENCES concerns(id),
  raised_group   INTEGER,
  status         TEXT NOT NULL DEFAULT 'open' REFERENCES concern_statuses(value),
  round          INTEGER NOT NULL,
  resolved_round INTEGER,
  CHECK (about_requirement_id IS NULL OR about_concern_id IS NULL)
);

CREATE INDEX idx_concerns_queue ON concerns(addressed_to, status);

CREATE TABLE answers (
  id          INTEGER PRIMARY KEY,
  concern_id  INTEGER NOT NULL REFERENCES concerns(id),
  answered_by INTEGER NOT NULL REFERENCES agents(id),
  kind        TEXT NOT NULL REFERENCES answer_kinds(value),
  body        TEXT NOT NULL,
  satisfied   INTEGER CHECK (satisfied IN (0, 1)),
  reply       TEXT,
  reply_round INTEGER,
  round       INTEGER NOT NULL
);

CREATE INDEX idx_answers_thread ON answers(concern_id, answered_by, id);

-- The requirements an accepted answer names, so the raiser judges the answer
-- and the exact wording together.
CREATE TABLE answer_requirements (
  answer_id      INTEGER NOT NULL REFERENCES answers(id),
  requirement_id INTEGER NOT NULL REFERENCES requirements(id),
  PRIMARY KEY (answer_id, requirement_id)
);

-- -------------------------------------------------------------- the outcome

CREATE TABLE deliverables (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  seq           INTEGER NOT NULL,
  intent        TEXT,
  status        TEXT NOT NULL DEFAULT 'proposed' REFERENCES deliverable_statuses(value),
  proposed_by   INTEGER REFERENCES agents(id),
  decided_by    INTEGER REFERENCES agents(id),
  decided_round INTEGER
);

CREATE TABLE milestones (
  id             INTEGER PRIMARY KEY,
  deliverable_id INTEGER NOT NULL REFERENCES deliverables(id),
  name           TEXT NOT NULL,
  seq            INTEGER NOT NULL,
  intent         TEXT,
  file_path      TEXT,
  status         TEXT NOT NULL DEFAULT 'proposed' REFERENCES milestone_statuses(value),
  proposed_by    INTEGER REFERENCES agents(id),
  decided_by     INTEGER REFERENCES agents(id),
  decided_round  INTEGER,
  sliced_ok_by   INTEGER REFERENCES agents(id),   -- the reviewer of the slicing
  buildable_ok_by INTEGER REFERENCES agents(id)   -- whoever checks buildability
);

CREATE TABLE requirements (
  id             INTEGER PRIMARY KEY,
  kind           TEXT NOT NULL REFERENCES requirement_kinds(value),
  statement      TEXT NOT NULL,
  rationale      TEXT,
  proposed_by    INTEGER NOT NULL REFERENCES agents(id),
  status         TEXT NOT NULL DEFAULT 'proposed' REFERENCES requirement_statuses(value),
  cost_flag      TEXT REFERENCES cost_flags(value),
  decided_by     INTEGER REFERENCES agents(id),
  decided_round  INTEGER,
  deliverable_id INTEGER REFERENCES deliverables(id),
  milestone_id   INTEGER REFERENCES milestones(id),
  created_round  INTEGER NOT NULL,
  updated_round  INTEGER,
  supersedes_id  INTEGER REFERENCES requirements(id)
);

CREATE INDEX idx_requirements_status ON requirements(status, kind);

-- Why a requirement exists. What argues against it is concerns.about_requirement_id.
CREATE TABLE requirement_concerns (
  requirement_id INTEGER NOT NULL REFERENCES requirements(id),
  concern_id     INTEGER NOT NULL REFERENCES concerns(id),
  relation       TEXT NOT NULL REFERENCES link_relations(value),
  PRIMARY KEY (requirement_id, concern_id, relation)
);

-- Status changes update the requirement row in place; this keeps the history,
-- so a requirement deferred, revived and deferred again still reads back.
CREATE TABLE requirement_events (
  id             INTEGER PRIMARY KEY,
  requirement_id INTEGER NOT NULL REFERENCES requirements(id),
  round          INTEGER NOT NULL,
  actor          INTEGER NOT NULL REFERENCES agents(id),
  from_status    TEXT REFERENCES requirement_statuses(value),
  to_status      TEXT NOT NULL REFERENCES requirement_statuses(value),
  reason         TEXT,
  concern_id     INTEGER REFERENCES concerns(id)
);

CREATE INDEX idx_requirement_events ON requirement_events(requirement_id, id);

-- Phase 1 ends when every active agent has signed off on the current state.
CREATE TABLE signoffs (
  agent_id    INTEGER NOT NULL REFERENCES agents(id),
  round       INTEGER NOT NULL,
  change_mark INTEGER NOT NULL,
  note        TEXT,
  PRIMARY KEY (agent_id, round)
);

-- ------------------------------------------------------------------- state

CREATE TABLE project_state (
  id          INTEGER PRIMARY KEY CHECK (id = 1),
  round       INTEGER NOT NULL DEFAULT 1,
  phase       INTEGER NOT NULL DEFAULT 1 REFERENCES phases(number),
  change_mark INTEGER NOT NULL DEFAULT 0,
  updated_at  TEXT
);
