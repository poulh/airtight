"""Database access, and the rules the tools read back out of it.

No rule lives in this file. Anything a tool enforces — which kinds only the
human can change, what a verdict does, who owns a duty, when a thread has
stalled — is read from tables that cp-init seeded from pipeline.yaml.

Derived state (whether a statement is agreed, whether a milestone is blocked,
whether the project is paused) is never set by hand: every writing tool calls
refresh() before committing, and refresh() recomputes it and records an event
for anything that changed.
"""

import os
import sqlite3
import sys
from datetime import datetime, timezone

HUMAN_ID = 1

PREFIXES = {"S": "statement", "C": "concern", "D": "deliverable", "M": "milestone",
            "A": "answer", "T": "turn", "P": "report"}


class Refused(Exception):
    """A tool refused the call because it breaks a pipeline rule."""


def default_db():
    return os.environ.get("CP_DB", ".convergence/project.db")


def connect(path, must_exist=True):
    if must_exist and not os.path.exists(path):
        raise Refused(f"no database at {path} — run cp-init first")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fail(exc):
    print(f"refused: {exc}", file=sys.stderr)
    return 2


# ------------------------------------------------------------------ lookups

def agent(conn, ident):
    """Find an agent by role, id or name. Roles are what tools should use."""
    if ident is None:
        raise Refused("no agent given")
    ident = str(ident)
    row = conn.execute("SELECT * FROM agents WHERE role = ?", (ident.lower(),)).fetchone()
    if row is None and ident.isdigit():
        row = conn.execute("SELECT * FROM agents WHERE id = ?", (int(ident),)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM agents WHERE name = ?", (ident,)).fetchone()
    if row is None:
        known = ", ".join(r["role"] for r in conn.execute("SELECT role FROM agents ORDER BY id"))
        raise Refused(f"no agent '{ident}'. Known roles: {known}")
    return row


def agent_by_id(conn, agent_id):
    return conn.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()


def require_active(conn, row, what="act"):
    if not row["active"]:
        raise Refused(
            f"{row['name']} is not in this project yet, so cannot {what}. "
            f"Use cp-staff request --agent {row['role']} to ask the human."
        )
    return row


def state(conn):
    return conn.execute(
        """SELECT s.*, p.key AS phase_key, p.name AS phase_name, p.ends_when
             FROM project_state s JOIN phases p ON p.number = s.phase WHERE s.id = 1"""
    ).fetchone()


def vocab(conn, table):
    return conn.execute(f"SELECT * FROM {table} ORDER BY seq").fetchall()


def term(conn, table, value, label):
    """One vocabulary value, with the rules it carries."""
    row = conn.execute(f"SELECT * FROM {table} WHERE value = ?", (value,)).fetchone()
    if row is None:
        values = [r["value"] for r in vocab(conn, table)]
        raise Refused(f"{label} '{value}' is not valid. Choose one of: {', '.join(values)}")
    return row


def live_values(conn, table):
    """The values of a status vocabulary marked live."""
    return [r["value"] for r in conn.execute(f"SELECT value FROM {table} WHERE live = 1")]


def policy(conn, key, default=None):
    row = conn.execute("SELECT value FROM policy WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    value = row["value"]
    return int(value) if str(value).lstrip("-").isdigit() else value


def duties_of(conn, agent_id):
    return conn.execute(
        """SELECT d.duty, d.relation, t.description
             FROM agent_duties d JOIN duties t ON t.value = d.duty
            WHERE d.agent_id = ? ORDER BY d.relation, d.duty""",
        (agent_id,),
    ).fetchall()


def has_duty(conn, agent_id, duty, relation):
    return conn.execute(
        "SELECT 1 FROM agent_duties WHERE agent_id = ? AND duty = ? AND relation = ?",
        (agent_id, duty, relation),
    ).fetchone() is not None


def phases_of(conn, agent_id):
    return [r["phase"] for r in conn.execute(
        "SELECT phase FROM agent_phases WHERE agent_id = ? ORDER BY phase", (agent_id,))]


def who(conn, duty, relation):
    """The agents holding a duty, e.g. who owns 'statements'."""
    return conn.execute(
        """SELECT a.* FROM agent_duties d JOIN agents a ON a.id = d.agent_id
            WHERE d.duty = ? AND d.relation = ? ORDER BY a.id""",
        (duty, relation),
    ).fetchall()


def require_duty(conn, agent_row, duty, relation, what):
    if has_duty(conn, agent_row["id"], duty, relation):
        return
    holders = who(conn, duty, relation)
    names = ", ".join(h["role"] for h in holders) or "nobody in this roster"
    verb = {"owns": "own", "rules_on": "rule on", "reviews": "review",
            "checks": "check"}.get(relation, relation)
    raise Refused(f"{agent_row['name']} does not {verb} '{duty}', so cannot {what}. "
                  f"That is: {names}")


# --------------------------------------------------------------- references

def parse_ref(text, allowed):
    """'S-12' -> ('S', 12). `allowed` is a string of accepted prefixes."""
    raw = str(text).strip().upper()
    prefix, _, number = raw.partition("-")
    if not number and prefix.isdigit() and len(allowed) == 1:
        prefix, number = allowed, prefix
    if prefix not in allowed or not number.isdigit():
        wanted = " or ".join(f"{p}-<id>" for p in allowed)
        raise Refused(f"expected {wanted}, not '{text}'")
    return prefix, int(number)


def ref_ids(text, prefix):
    """'C-4,C-5' -> [4, 5]."""
    if not text:
        return []
    return [parse_ref(part, prefix)[1] for part in str(text).split(",") if part.strip()]


def _one(conn, table, prefix, row_id):
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise Refused(f"no {PREFIXES[prefix]} {prefix}-{row_id}")
    return row


def statement(conn, sid):
    return _one(conn, "statements", "S", sid)


def concern(conn, cid):
    return _one(conn, "concerns", "C", cid)


def deliverable(conn, did):
    return _one(conn, "deliverables", "D", did)


def milestone(conn, mid):
    return _one(conn, "milestones", "M", mid)


def answer(conn, aid):
    return _one(conn, "answers", "A", aid)


def is_live_statement(conn, row):
    return row["status"] in live_values(conn, "statement_statuses")


def is_live_deliverable(conn, row):
    return row["status"] in live_values(conn, "deliverable_statuses")


def about(row):
    """How a concern names what it is on: 'S-12' or 'M-3'."""
    return f"S-{row['statement_id']}" if row["statement_id"] else f"M-{row['milestone_id']}"


# -------------------------------------------------------------------- turns

def running_turn(conn):
    return conn.execute("SELECT * FROM turns WHERE status = 'running'").fetchone()


def require_turn(conn, agent_row):
    """The caller's running turn. Every writing tool acts inside one."""
    turn = running_turn(conn)
    if turn is None:
        raise Refused(f"{agent_row['name']} has no running turn — "
                      + ("cp-turn start --agent human" if agent_row["id"] == HUMAN_ID
                         else "the orchestrator opens turns with cp-turn next"))
    if turn["agent_id"] != agent_row["id"]:
        holder = agent_by_id(conn, turn["agent_id"])
        raise Refused(f"it is {holder['name']}'s turn (T-{turn['id']}), not {agent_row['name']}'s")
    return turn


def rotation(conn):
    """The agents that take turns this phase, in order. The human is never in it."""
    return conn.execute(
        """SELECT a.* FROM agents a JOIN agent_phases p ON p.agent_id = a.id
            WHERE a.active = 1 AND a.id <> ? AND p.phase = ?
            ORDER BY a.seq""",
        (HUMAN_ID, state(conn)["phase"]),
    ).fetchall()


def approvers(conn):
    """Active agents that must approve every live statement."""
    return conn.execute(
        """SELECT a.* FROM agents a JOIN agent_phases p ON p.agent_id = a.id
            WHERE a.active = 1 AND a.approves = 1 AND p.phase = ?
            ORDER BY a.seq""",
        (state(conn)["phase"],),
    ).fetchall()


# ------------------------------------------------------------------ writing

def record_event(conn, turn, obj, obj_id, from_status=None, to_status=None, detail=None):
    """Append to events and move the change mark. Every change is one of these.

    `turn` is None only for the orchestrator's round and phase moves.
    """
    stamp = now()
    cur = conn.execute(
        """INSERT INTO events (turn_id, round, actor, object, object_id, from_status,
                               to_status, detail, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (turn["id"] if turn else None, turn["round"] if turn else state(conn)["round"],
         turn["agent_id"] if turn else None, obj, obj_id, from_status, to_status, detail, stamp),
    )
    conn.execute("UPDATE project_state SET change_mark = ?, updated_at = ? WHERE id = 1",
                 (cur.lastrowid, stamp))
    return cur.lastrowid


def open_concerns_on(conn, statement_id):
    return conn.execute(
        "SELECT * FROM concerns WHERE statement_id = ? AND status = 'open' ORDER BY id",
        (statement_id,),
    ).fetchall()


def awaiting_peter(conn, statement_id=None):
    """Closed concerns on statements that Peter has not acted on yet."""
    sql = """SELECT * FROM concerns WHERE statement_id IS NOT NULL AND status = 'closed'
               AND acted_on IS NULL"""
    if statement_id is None:
        return conn.execute(sql + " ORDER BY id").fetchall()
    return conn.execute(sql + " AND statement_id = ? ORDER BY id", (statement_id,)).fetchall()


def awaiting_verdict(conn, concern_id):
    """The answer on this concern the raiser has yet to judge, if any (at most one)."""
    return conn.execute(
        """SELECT w.* FROM answers w JOIN answer_kinds k ON k.value = w.kind
            WHERE w.concern_id = ? AND w.verdict IS NULL AND k.judged = 1
            ORDER BY w.id DESC LIMIT 1""",
        (concern_id,),
    ).fetchone()


def human_involved(conn, concern_id):
    """The human answered this concern (their answer was accepted or final) or raised it."""
    row = concern(conn, concern_id)
    if row["raised_by"] == HUMAN_ID:
        return True
    return conn.execute(
        """SELECT 1 FROM answers w JOIN verdicts v ON v.value = w.verdict
            WHERE w.concern_id = ? AND w.answered_by = ? AND v.closes = 1""",
        (concern_id, HUMAN_ID),
    ).fetchone() is not None


def reassign(conn, turn, concern_row, to_agent, by_agent_id, body):
    """Hand a concern to another agent, leaving a note. Resets the stall count."""
    if to_agent["id"] == concern_row["raised_by"]:
        raise Refused(f"C-{concern_row['id']} was raised by {to_agent['name']}; a concern is "
                      "never addressed to its own raiser")
    stamp = now()
    cur = conn.execute(
        """INSERT INTO answers (concern_id, answered_by, kind, body, reassigned_to, round,
                                turn_id, created_at)
           VALUES (?, ?, 'reassign', ?, ?, ?, ?, ?)""",
        (concern_row["id"], by_agent_id, body, to_agent["id"], turn["round"], turn["id"], stamp),
    )
    conn.execute("UPDATE concerns SET addressed_to = ?, replies_since_reassign = 0 WHERE id = ?",
                 (to_agent["id"], concern_row["id"]))
    record_event(conn, turn, "concern", concern_row["id"], None, None,
                 f"reassigned to {to_agent['role']} (A-{cur.lastrowid})")
    return cur.lastrowid


# ---------------------------------------------------------------- deriving

def statement_agreed(conn, row, approver_ids):
    if row["deliverable_id"] is not None:
        if not is_live_deliverable(conn, deliverable(conn, row["deliverable_id"])):
            return False
    if open_concerns_on(conn, row["id"]) or awaiting_peter(conn, row["id"]):
        return False
    approved = {r["agent_id"] for r in conn.execute(
        "SELECT agent_id FROM approvals WHERE statement_id = ?", (row["id"],))}
    return set(approver_ids) <= approved


def refresh(conn, turn):
    """Recompute everything derived, recording an event for each change."""
    ids = [a["id"] for a in approvers(conn)]
    live = live_values(conn, "statement_statuses")
    marks = ",".join("?" * len(live))
    for row in conn.execute(f"SELECT * FROM statements WHERE status IN ({marks})", live).fetchall():
        target = "agreed" if statement_agreed(conn, row, ids) else "pending"
        if target != row["status"]:
            conn.execute("UPDATE statements SET status = ? WHERE id = ?", (target, row["id"]))
            record_event(conn, turn, "statement", row["id"], row["status"], target)

    for row in conn.execute("SELECT * FROM milestones").fetchall():
        target = row["status"]
        not_agreed = conn.execute(
            "SELECT 1 FROM statements WHERE milestone_id = ? AND status = 'pending'",
            (row["id"],)).fetchone()
        open_on_it = conn.execute(
            "SELECT 1 FROM concerns WHERE milestone_id = ? AND status = 'open'",
            (row["id"],)).fetchone()
        if row["status"] == "building" and not_agreed:
            target = "blocked"
        elif row["status"] == "blocked" and not not_agreed:
            target = "building"
        elif row["status"] == "merged" and open_on_it:
            target = "building"   # the human's test found a bug
        if target != row["status"]:
            conn.execute("UPDATE milestones SET status = ? WHERE id = ?", (target, row["id"]))
            if row["status"] == "merged":
                # Rework needs a fresh pass from both reviewers.
                conn.execute("UPDATE milestones SET tested_ok_by = NULL, reviewed_ok_by = NULL "
                             "WHERE id = ?", (row["id"],))
            record_event(conn, turn, "milestone", row["id"], row["status"], target)

    st = state(conn)
    status, reason, ref = "running", None, None
    report = conn.execute(
        "SELECT id FROM reports WHERE continued_at IS NULL ORDER BY id LIMIT 1").fetchone()
    # Paused while the human owes an answer: a concern with them whose latest
    # answer is not already waiting for the raiser's verdict.
    with_human = next((c for c in conn.execute(
        "SELECT id FROM concerns WHERE status = 'open' AND addressed_to = ? ORDER BY id",
        (HUMAN_ID,)) if awaiting_verdict(conn, c["id"]) is None), None)
    if report:
        status, reason, ref = "paused", "report", report["id"]
    elif with_human:
        status, reason, ref = "paused", "concern", with_human["id"]
    elif st["status"] == "done":
        status = "done"
    elif st["phase"] == 1 and converged(conn):
        status = "converged"
    if (status, reason, ref) != (st["status"], st["paused_reason"], st["paused_ref"]):
        conn.execute("""UPDATE project_state SET status = ?, paused_reason = ?, paused_ref = ?,
                        updated_at = ? WHERE id = 1""", (status, reason, ref, now()))
        detail = f"{reason} {'P' if reason == 'report' else 'C'}-{ref}" if reason else None
        record_event(conn, turn, "project", 1, st["status"], status, detail)


def converged(conn):
    live = live_values(conn, "statement_statuses")
    marks = ",".join("?" * len(live))
    pending = conn.execute(
        f"SELECT 1 FROM statements WHERE status IN ({marks}) AND status <> 'agreed'", live
    ).fetchone()
    anything = conn.execute(f"SELECT 1 FROM statements WHERE status IN ({marks})", live).fetchone()
    open_any = conn.execute("SELECT 1 FROM concerns WHERE status = 'open'").fetchone()
    return bool(anything) and not pending and not open_any and not awaiting_peter(conn)


# -------------------------------------------------------------------- queues

def context(conn):
    """The project-level statements in force, shown with everything an agent reviews."""
    live = live_values(conn, "statement_statuses")
    marks = ",".join("?" * len(live))
    return conn.execute(
        f"""SELECT s.* FROM statements s JOIN statement_kinds k ON k.value = s.kind
             WHERE k.level = 'project' AND s.status IN ({marks})
             ORDER BY k.seq, s.id""", live).fetchall()


def scope_of(conn, deliverable_id):
    return conn.execute(
        """SELECT * FROM statements WHERE kind = 'scope' AND deliverable_id = ?
             AND status IN ('pending', 'agreed') ORDER BY id""", (deliverable_id,)).fetchall()


def queue(conn, me):
    """Everything this agent has to deal with on its turn."""
    st = state(conn)
    q = {}
    q["verdicts"] = conn.execute(
        """SELECT w.*, c.body AS concern_body, g.name AS answerer, c.statement_id,
                  c.milestone_id
             FROM answers w JOIN concerns c ON c.id = w.concern_id
             JOIN agents g ON g.id = w.answered_by
             JOIN answer_kinds k ON k.value = w.kind
            WHERE c.raised_by = ? AND c.status = 'open' AND w.verdict IS NULL AND k.judged = 1
            ORDER BY w.id""", (me["id"],)).fetchall()
    q["to_answer"] = [r for r in conn.execute(
        """SELECT c.*, a.name AS raiser_name FROM concerns c JOIN agents a ON a.id = c.raised_by
            WHERE c.addressed_to = ? AND c.status = 'open' ORDER BY c.id""", (me["id"],))
        if awaiting_verdict(conn, r["id"]) is None]
    q["to_review"] = []
    if me["approves"]:
        q["to_review"] = [r for r in conn.execute(
            """SELECT s.* FROM statements s
                WHERE s.status = 'pending'
                  AND NOT EXISTS (SELECT 1 FROM approvals p
                                   WHERE p.statement_id = s.id AND p.agent_id = ?)
                  AND NOT EXISTS (SELECT 1 FROM concerns c WHERE c.statement_id = s.id
                                   AND c.raised_by = ? AND c.status = 'open')
                ORDER BY s.id""", (me["id"], me["id"]))
            if r["deliverable_id"] is None
            or is_live_deliverable(conn, deliverable(conn, r["deliverable_id"]))]

    q["ready"], q["homeless"], q["orphans"], q["stuck"], q["report_due"] = [], [], [], [], False
    if has_duty(conn, me["id"], "statements", "owns"):
        waiting = {c["statement_id"] for c in awaiting_peter(conn)}
        q["ready"] = [statement(conn, sid) for sid in sorted(waiting)
                      if not open_concerns_on(conn, sid)]
        q["homeless"] = conn.execute(
            """SELECT s.* FROM statements s JOIN deliverables d ON d.id = s.deliverable_id
                WHERE s.status IN ('pending', 'agreed') AND d.status <> 'live'
                ORDER BY s.id""").fetchall()
        q["orphans"] = conn.execute(
            """SELECT s.* FROM statements s JOIN statement_kinds k ON k.value = s.kind
                WHERE s.status IN ('pending', 'agreed') AND k.requires_link IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM links l JOIN statements t ON t.id = l.to_id
                                   WHERE l.from_id = s.id AND l.relation = k.requires_link
                                     AND t.status IN ('pending', 'agreed'))
                ORDER BY s.id""").fetchall()
    if has_duty(conn, me["id"], "escalation", "owns"):
        q["stuck"] = stuck(conn)
    if has_duty(conn, me["id"], "reporting", "owns"):
        q["report_due"] = report_due(conn)

    q["milestones"] = milestone_duties(conn, me, st["phase"])
    if me["id"] == HUMAN_ID:
        q["reports"] = conn.execute(
            "SELECT * FROM reports WHERE continued_at IS NULL ORDER BY id").fetchall()
    else:
        q["reports"] = []
    q["last_summary"] = conn.execute(
        """SELECT * FROM turns WHERE agent_id = ? AND status = 'done' AND summary IS NOT NULL
            ORDER BY id DESC LIMIT 1""", (me["id"],)).fetchone()
    return q


def queue_is_empty(q):
    return not any(q[k] for k in ("verdicts", "to_answer", "to_review", "ready", "homeless",
                                  "orphans", "stuck", "report_due", "milestones", "reports"))


def milestone_duties(conn, me, phase):
    """Milestones waiting on this agent's duties, with what it should do."""
    items = []
    duty_checks = [
        ("slicing", "reviews", "status = 'planned' AND sliced_ok_by IS NULL",
         "review the slicing: cp-milestone review"),
        ("buildability", "checks", "status = 'planned' AND checked_ok_by IS NULL",
         "check it is buildable: cp-milestone check"),
        ("code", "owns", "status = 'building'",
         "build it on its branch, then cp-milestone submit"),
        ("code", "owns", """status = 'in_review' AND tested_ok_by IS NOT NULL
                            AND reviewed_ok_by IS NOT NULL""",
         "merge it: cp-milestone merge"),
        ("testing", "owns", "status = 'in_review' AND tested_ok_by IS NULL",
         "write and run its acceptance tests, then cp-milestone pass"),
        ("code_review", "owns", "status = 'in_review' AND reviewed_ok_by IS NULL",
         "review the code, then cp-milestone pass"),
        ("acceptance", "rules_on", "status = 'merged'",
         "try it, then cp-milestone accept, or raise a concern on it"),
    ]
    if phase >= 3 and has_duty(conn, me["id"], "milestone_proposals", "owns"):
        # A deliverable with agreed requirements that no milestone holds yet, and
        # no proposal of this agent's still open on its scope.
        for d in conn.execute(
                """SELECT d.* FROM deliverables d WHERE d.status = 'live' AND EXISTS (
                     SELECT 1 FROM statements s JOIN statement_kinds k ON k.value = s.kind
                      WHERE s.deliverable_id = d.id AND s.status = 'agreed'
                        AND s.milestone_id IS NULL AND s.kind <> 'scope')
                   AND NOT EXISTS (
                     SELECT 1 FROM concerns c JOIN statements s ON s.id = c.statement_id
                      WHERE s.deliverable_id = d.id AND s.kind = 'scope'
                        AND c.raised_by = ? AND c.status = 'open')
                   ORDER BY d.seq""", (me["id"],)):
            scope = scope_of(conn, d["id"])
            on = f"S-{scope[0]['id']}" if scope else f"D-{d['id']}'s scope"
            items.append({"deliverable": dict(d),
                          "todo": f"propose its milestones: a proposal concern on {on} "
                                  "to the writer of milestones"})
    for duty, relation, where, todo in duty_checks:
        if not has_duty(conn, me["id"], duty, relation):
            continue
        for row in conn.execute(f"SELECT * FROM milestones WHERE {where} ORDER BY id"):
            items.append({"milestone": dict(row), "todo": todo})
    return items


def stuck(conn):
    """Open concerns one reply from going to the human, not already with them."""
    limit = policy(conn, "stall_replies", 4)
    return conn.execute(
        """SELECT * FROM concerns WHERE status = 'open' AND addressed_to <> ?
             AND replies_since_reassign >= ? ORDER BY id""",
        (HUMAN_ID, max(limit - 1, 1))).fetchall()


def bouncing(conn):
    return conn.execute(
        "SELECT * FROM concerns WHERE replies_total >= ? ORDER BY id",
        (policy(conn, "bounce_total_replies", 8),)).fetchall()


def report_due(conn):
    every = policy(conn, "report_every_rounds", 10)
    rnd = state(conn)["round"]
    if not every or rnd % every:
        return False
    return conn.execute("SELECT 1 FROM reports WHERE round = ?", (rnd,)).fetchone() is None
