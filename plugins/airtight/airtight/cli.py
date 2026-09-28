"""The pipeline tools.

Agents never write SQL, and no rule is written here. Every check below reads
its rule from the database, which at-init seeded from pipeline.yaml: which
kinds only the human can change, what a verdict does, who owns a duty, when a
thread has stalled. Changing a rule means editing the YAML and re-seeding,
never editing Python.

Every writing tool acts inside the caller's running turn, stamps what it writes
with that turn, and calls db.refresh() before committing so derived state
(agreed, blocked, paused, converged) is never stale.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from . import config as cfg
from . import db
from .db import HUMAN_ID, Refused


def base_parser(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--db", default=db.default_db(), help="project database (or $AT_DB)")
    return parser


def run(fn):
    """Turn a Refused into a clean exit code instead of a traceback."""
    try:
        return fn()
    except Refused as exc:
        return db.fail(exc)


def begin(path, role, what):
    """Open the database for an agent acting inside its own running turn."""
    conn = db.connect(path)
    me = db.require_active(conn, db.agent(conn, role), what)
    return conn, me, db.require_turn(conn, me)


def finish(conn, turn):
    db.refresh(conn, turn)
    conn.commit()


def show(conn, row):
    """One statement on one line: requirement S-12 (D-1, pending): text."""
    where = f"D-{row['deliverable_id']}, " if row["deliverable_id"] else ""
    return f"{db.label(conn, row)} ({where}{row['status']}): {row['text'].strip()}"


def raise_concern(conn, turn, me, them, kind, body, statement_id=None, milestone_id=None):
    cur = conn.execute(
        """INSERT INTO concerns (statement_id, milestone_id, kind, raised_by, addressed_to, body,
                                 round, turn_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (statement_id, milestone_id, kind, me["id"], them["id"], body, turn["round"],
         turn["id"], db.now()),
    )
    on = f"S-{statement_id}" if statement_id else f"M-{milestone_id}"
    db.record_event(conn, turn, "concern", cur.lastrowid, None, "open",
                    f"{kind} on {on}, to {them['role']}")
    return cur.lastrowid


# --------------------------------------------------------------------- init

def init_main():
    parser = base_parser("Set up a project, or build its database and record the brief as S-1")
    parser.add_argument("--config", default=str(cfg.config_path()))
    parser.add_argument("--schema", default=str(cfg.DEFAULT_SCHEMA))
    parser.add_argument("--scaffold", action="store_true",
                        help=f"copy the default pipeline.yaml into {cfg.PROJECT_DIR}/ and validate it")
    parser.add_argument("--auto-staff", choices=["yes", "no"],
                        help="with --scaffold: approve staffing requests automatically, or ask")
    parser.add_argument("--brief", help="the human's idea, verbatim")
    parser.add_argument("--deliverable", default="v1", help="name of the first deliverable")
    parser.add_argument("--force", action="store_true", help="replace an existing database")
    parser.add_argument("--check", action="store_true", help="validate the config and stop")
    args = parser.parse_args()

    if args.scaffold:
        cfg.PROJECT_DIR.mkdir(exist_ok=True)
        if cfg.PROJECT_CONFIG.exists():
            print(f"{cfg.PROJECT_CONFIG} already exists; leaving it as it is")
        else:
            cfg.PROJECT_CONFIG.write_text(cfg.DEFAULT_CONFIG.read_text())
            print(f"copied the default config to {cfg.PROJECT_CONFIG}")
        if args.auto_staff:
            cfg.set_policy(cfg.PROJECT_CONFIG, "auto_approve_staffing",
                           1 if args.auto_staff == "yes" else 0)
            print("staffing requests: " + ("approved automatically" if args.auto_staff == "yes"
                                           else "each one asks the human"))
        args.config, args.check = str(cfg.PROJECT_CONFIG), True

    configuration = cfg.load(args.config)
    problems = cfg.validate(configuration)
    if problems:
        print(f"{args.config} has {len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    if args.check:
        print(f"{args.config}: valid")
        return 0
    if not args.brief:
        print("at-init needs the human's idea in their own words: --brief '...'", file=sys.stderr)
        return 1

    path = Path(args.db)
    if path.exists():
        if not args.force:
            print(f"{path} already exists (use --force to replace it)", file=sys.stderr)
            return 1
        path.unlink()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = db.connect(str(path), must_exist=False)
    conn.executescript(Path(args.schema).read_text())

    conn.executemany(
        "INSERT INTO phases (number, key, name, ends_when, needs) VALUES (?, ?, ?, ?, ?)",
        [(p["number"], p["key"], p["name"], p["ends_when"].strip(),
          ",".join(p.get("needs") or []) or None)
         for p in configuration["phases"]],
    )
    conn.executemany(
        "INSERT INTO policy (key, value, description) VALUES (?, ?, ?)",
        [(p["key"], str(p["value"]), p["description"].strip())
         for p in configuration["policy"]],
    )
    for name, table in cfg.SIMPLE_LISTS.items():
        conn.executemany(
            f"INSERT INTO {table} (value, description, seq) VALUES (?, ?, ?)",
            [(e["value"], e["description"].strip(), seq)
             for seq, e in enumerate(configuration[name], start=1)],
        )
    total_values = 0
    for name, attrs in cfg.VOCABULARIES.items():
        columns = ["value", "description", *attrs, "seq"]
        rows = []
        for seq, entry in enumerate(configuration["vocabularies"][name], start=1):
            values = [entry["value"], entry["description"].strip()]
            for attr in attrs:
                raw = entry.get(attr)
                if attr in cfg.BOOL_ATTRS:
                    values.append(1 if raw else 0)
                else:
                    values.append(raw.strip() if isinstance(raw, str) else raw)
            values.append(seq)
            rows.append(tuple(values))
        conn.executemany(
            f"INSERT INTO {name} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
            rows)
        total_values += len(rows)

    for agent in configuration["agents"]:
        conn.execute(
            """INSERT INTO agents (id, name, role, motivation, charter, seq, approves,
                                   joins_at_phase, join_trigger, join_rationale, auto_staff,
                                   active, joined_round)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (agent["id"], agent["name"], agent["role"], agent["motivation"].strip(),
             agent.get("charter"), agent["seq"], 1 if agent.get("approves") else 0,
             agent["joins_at_phase"],
             (agent.get("join_trigger") or "").strip() or None,
             (agent.get("join_rationale") or "").strip() or None,
             1 if agent.get("auto_staff") else 0,
             1 if agent.get("active") else 0,
             agent.get("joined_round")),
        )
        conn.executemany(
            "INSERT INTO agent_phases (agent_id, phase) VALUES (?, ?)",
            [(agent["id"], phase) for phase in agent["phases"]],
        )
        for relation in cfg.DUTY_RELATIONS:
            conn.executemany(
                "INSERT INTO agent_duties (agent_id, duty, relation) VALUES (?, ?, ?)",
                [(agent["id"], duty, relation) for duty in agent.get(relation) or []],
            )

    stamp = db.now()
    conn.execute("INSERT INTO project_state (id, updated_at) VALUES (1, ?)", (stamp,))
    # The brief is the human's first turn.
    cur = conn.execute(
        """INSERT INTO turns (round, seq, phase, agent_id, status, started_at)
           VALUES (1, 1, 1, ?, 'running', ?)""", (HUMAN_ID, stamp))
    turn = conn.execute("SELECT * FROM turns WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.execute("UPDATE project_state SET current_turn_id = ? WHERE id = 1", (turn["id"],))
    conn.execute("UPDATE agents SET joined_turn_id = ? WHERE active = 1", (turn["id"],))

    cur = conn.execute(
        """INSERT INTO deliverables (name, seq, created_round, turn_id, created_at)
           VALUES (?, 1, 1, ?, ?)""", (args.deliverable, turn["id"], stamp))
    db.record_event(conn, turn, "deliverable", cur.lastrowid, None, "live",
                    "the first assumption: the whole idea ships at once")
    cur = conn.execute(
        """INSERT INTO statements (kind, text, created_round, turn_id, created_at)
           VALUES ('brief', ?, 1, ?, ?)""", (args.brief.strip(), turn["id"], stamp))
    db.record_event(conn, turn, "statement", cur.lastrowid, None, "pending", "the human's brief")
    db.refresh(conn, turn)
    conn.execute("UPDATE turns SET status = 'done', summary = 'Gave the brief.', ended_at = ? "
                 "WHERE id = ?", (db.now(), turn["id"]))
    conn.execute("UPDATE project_state SET current_turn_id = NULL WHERE id = 1")
    conn.commit()

    active = [r["name"] for r in conn.execute("SELECT name FROM agents WHERE active=1 ORDER BY id")]
    print(f"created {path}")
    print(f"  phases:       {len(configuration['phases'])}")
    print(f"  policy:       {len(configuration['policy'])} settings")
    print(f"  vocabularies: {total_values} values across {len(cfg.VOCABULARIES)} tables")
    print(f"  agents:       {len(configuration['agents'])} seeded, {len(active)} active: "
          + ", ".join(active))
    print(f"  D-1 ({args.deliverable}) and S-1 (the brief), written in T-{turn['id']}")
    print("  state:        round 1, phase 1")
    return 0


# -------------------------------------------------------------------- turns

def turn_main():
    parser = base_parser("Open and close turns. Every action happens inside one")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("next", help="open the next agent's turn this round, skipping empty queues")
    start = sub.add_parser("start", help="open the human's turn")
    start.add_argument("--agent", required=True)
    end = sub.add_parser("end", help="close your turn")
    end.add_argument("--agent", required=True)
    end.add_argument("--summary", help="one or two sentences: what you did and why")
    end.add_argument("--tokens", type=int)
    for p in (sub.choices["next"], start, end):
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    args = parser.parse_args()

    def emit(payload, text):
        print(json.dumps(payload, indent=2) if args.json else text)

    def open_turn(conn, agent_row, status):
        st = db.state(conn)
        seq = conn.execute("SELECT COUNT(*) AS n FROM turns WHERE round = ?",
                           (st["round"],)).fetchone()["n"] + 1
        stamp = db.now()
        cur = conn.execute(
            """INSERT INTO turns (round, seq, phase, agent_id, status, started_at, ended_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (st["round"], seq, st["phase"], agent_row["id"], status, stamp,
             stamp if status == "skipped" else None))
        if status == "running":
            conn.execute("UPDATE project_state SET current_turn_id = ?, updated_at = ? WHERE id = 1",
                         (cur.lastrowid, stamp))
        return cur.lastrowid

    def go():
        conn = db.connect(args.db)
        running = db.running_turn(conn)

        if args.action == "end":
            me = db.agent(conn, args.agent)
            turn = db.require_turn(conn, me)
            if db.has_duty(conn, me["id"], "reporting", "owns") and db.report_due(conn):
                raise Refused("a report is due this round — at-report run before ending your turn")
            did = conn.execute("SELECT 1 FROM events WHERE turn_id = ?", (turn["id"],)).fetchone()
            if did and not args.summary:
                raise Refused("say what you did and why, in a sentence or two: --summary '...'")
            db.refresh(conn, turn)
            st = db.state(conn)
            conn.execute("UPDATE turns SET status = 'done', summary = ?, tokens = ?, ended_at = ? "
                         "WHERE id = ?", (args.summary, args.tokens, db.now(), turn["id"]))
            conn.execute("UPDATE agents SET last_seen_change = ? WHERE id = ?",
                         (st["change_mark"], me["id"]))
            conn.execute("UPDATE project_state SET current_turn_id = NULL WHERE id = 1")
            conn.commit()
            emit({"turn": turn["id"], "agent": me["role"], "status": "done"},
                 f"T-{turn['id']} ({me['role']}) done")
            return 0

        if running:
            holder = db.agent_by_id(conn, running["agent_id"])
            raise Refused(f"T-{running['id']} ({holder['role']}) is still running — "
                          f"at-turn end --agent {holder['role']} first")

        if args.action == "start":
            me = db.require_active(conn, db.agent(conn, args.agent), "take a turn")
            if me["id"] != HUMAN_ID:
                raise Refused("agents take turns in order through at-turn next; "
                              "start is for the human's turn")
            turn_id = open_turn(conn, me, "running")
            conn.commit()
            emit({"turn": turn_id, "agent": "human"}, f"T-{turn_id} human")
            return 0

        st = db.state(conn)
        if st["status"] == "paused":
            what = "P" if st["paused_reason"] == "report" else "C"
            raise Refused(f"paused on {what}-{st['paused_ref']}: it is the human's turn — "
                          "at-turn start --agent human")
        taken = {r["agent_id"] for r in conn.execute(
            "SELECT agent_id FROM turns WHERE round = ?", (st["round"],))}
        skipped = []
        for candidate in db.rotation(conn):
            if candidate["id"] in taken:
                continue
            if db.queue_is_empty(db.queue(conn, candidate)):
                open_turn(conn, candidate, "skipped")
                skipped.append(candidate["role"])
                continue
            turn_id = open_turn(conn, candidate, "running")
            conn.commit()
            emit({"turn": turn_id, "agent": candidate["role"], "round": st["round"],
                  "skipped": skipped},
                 (f"skipped (empty queue): {', '.join(skipped)}\n" if skipped else "")
                 + f"T-{turn_id} {candidate['role']} (round {st['round']})")
            return 0
        conn.commit()
        emit({"turn": None, "round": st["round"], "round_complete": True, "skipped": skipped},
             (f"skipped (empty queue): {', '.join(skipped)}\n" if skipped else "")
             + f"round {st['round']} complete — at-round --advance")
        return 0

    return run(go)


# -------------------------------------------------------------------- queue

def queue_main():
    parser = base_parser("What this agent has to deal with on its turn")
    parser.add_argument("--agent", required=True, help="role, e.g. pm")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    def thread(conn, concern_id):
        return conn.execute(
            """SELECT w.*, g.role AS by_role FROM answers w JOIN agents g ON g.id = w.answered_by
                WHERE w.concern_id = ? ORDER BY w.id""", (concern_id,)).fetchall()

    def go():
        conn = db.connect(args.db)
        me = db.require_active(conn, db.agent(conn, args.agent), "take a turn")
        st = db.state(conn)
        q = db.queue(conn, me)
        in_force = db.context(conn)

        if args.json:
            print(json.dumps({
                "round": st["round"], "phase": st["phase"], "phase_name": st["phase_name"],
                "agent": {"role": me["role"], "name": me["name"], "motivation": me["motivation"],
                          "approves": bool(me["approves"]),
                          "duties": [dict(d) for d in db.duties_of(conn, me["id"])]},
                "last_summary": q["last_summary"]["summary"] if q["last_summary"] else None,
                "in_force": [dict(r) for r in in_force],
                "answers_to_judge": [dict(r) for r in q["verdicts"]],
                "concerns_to_answer": [dict(r, thread=[dict(t) for t in thread(conn, r["id"])])
                                       for r in q["to_answer"]],
                "statements_to_review": [
                    dict(r, scope=[dict(s) for s in db.scope_of(conn, r["deliverable_id"])]
                         if r["deliverable_id"] else [])
                    for r in q["to_review"]],
                "ready_to_act_on": [
                    dict(r, concerns=[dict(c, thread=[dict(t) for t in thread(conn, c["id"])])
                                      for c in db.awaiting_peter(conn, r["id"])])
                    for r in q["ready"]],
                "homeless": [dict(r) for r in q["homeless"]],
                "orphaned": [dict(r) for r in q["orphans"]],
                "stuck": [dict(r) for r in q["stuck"]],
                "on_the_project": [{"role": a["role"], "name": a["name"]} for a in q["roster"]],
                "not_yet_on_the_project": [
                    {"role": a["role"], "name": a["name"], "joins_when": a["join_trigger"],
                     "joins_at_phase": a["joins_at_phase"]} for a in q["unstaffed"]],
                "report_due": q["report_due"],
                "milestones": q["milestones"],
                "reports_to_continue": [dict(r) for r in q["reports"]],
            }, indent=2))
            return 0

        print(f"{me['name']} — round {st['round']}, phase {st['phase']} ({st['phase_name']})")
        print(f"  you argue for: {me['motivation'].strip()}")
        duties = db.duties_of(conn, me["id"])
        if duties:
            print("  your duties:   " + ", ".join(f"{d['relation']} {d['duty']}" for d in duties))
        if q["last_summary"]:
            print(f"  your last turn (T-{q['last_summary']['id']}): "
                  f"{q['last_summary']['summary'].strip()}")

        print("\nON THE PROJECT: " + ", ".join(
            f"{a['name']} [{a['role']}]" for a in q["roster"]))

        print("\nIN FORCE (project-wide)")
        for row in in_force:
            print(f"  {show(conn, row)}")

        print(f"\nANSWERS TO JUDGE ({len(q['verdicts'])}) — at-review")
        for row in q["verdicts"]:
            on = f"S-{row['statement_id']}" if row["statement_id"] else f"M-{row['milestone_id']}"
            print(f"  A-{row['id']} on C-{row['concern_id']} ({on}) from {row['answerer']}")
            print(f"       you asked: {row['concern_body'].strip()}")
            print(f"       answer:    {row['body'].strip()}")

        print(f"\nCONCERNS TO ANSWER ({len(q['to_answer'])}) — at-answer")
        for row in q["to_answer"]:
            print(f"  C-{row['id']} [{row['kind']}] on {db.about(conn, row)} from {row['raiser_name']}")
            print("       " + row["body"].strip().replace("\n", "\n       "))
            for t in thread(conn, row["id"]):
                if t["kind"] == "reassign":
                    print(f"       ↳ {t['by_role']} reassigned it: {t['body'].strip()}")
                else:
                    print(f"       ↳ {t['by_role']} answered: {t['body'].strip()}")
                    if t["reply"]:
                        print(f"         {row['raiser_name']} replied: {t['reply'].strip()}")

        if me["approves"]:
            print(f"\nSTATEMENTS TO REVIEW ({len(q['to_review'])}) — at-approve, or at-concern")
            for row in q["to_review"]:
                print(f"  {show(conn, row)}")
                if row["deliverable_id"]:
                    for scope in db.scope_of(conn, row["deliverable_id"]):
                        if scope["id"] != row["id"]:
                            print(f"       scope of D-{row['deliverable_id']}: "
                                  f"{scope['text'].strip()}")

        if db.has_duty(conn, me["id"], "statements", "owns"):
            print(f"\nREADY TO ACT ON ({len(q['ready'])}) — at-statement, at-deliverable, "
                  "at-milestone, or raise a new concern")
            for row in q["ready"]:
                print(f"  {show(conn, row)}")
                for c in db.awaiting_peter(conn, row["id"]):
                    raiser = db.agent_by_id(conn, c["raised_by"])
                    body = c["body"].strip().replace("\n", "\n         ")
                    print(f"       C-{c['id']} [{c['kind']}] from {raiser['role']}: {body}")
                    for t in thread(conn, c["id"]):
                        if t["kind"] == "answer":
                            print(f"         ↳ {t['by_role']} ({t['verdict'] or 'not judged'}): "
                                  f"{t['body'].strip()}")
            if q["homeless"]:
                print(f"\nHOMELESS ({len(q['homeless'])}) — their deliverable was split or "
                      "cancelled: at-statement move")
                for row in q["homeless"]:
                    print(f"  {show(conn, row)}")
            if q["orphans"]:
                print(f"\nMISSING A LINK ({len(q['orphans'])}) — what they point at was retired")
                for row in q["orphans"]:
                    print(f"  {show(conn, row)}")
        if q["unstaffed"]:
            print(f"\nNOT YET ON THE PROJECT ({len(q['unstaffed'])}) — when a trigger has "
                  "appeared, at-staff request; if the project will never need them, at-staff pass "
                  "with the reason")
            for a in q["unstaffed"]:
                soon = (f" (joins at phase {a['joins_at_phase']})"
                        if a["joins_at_phase"] > st["phase"] else "")
                print(f"  {a['name']} [{a['role']}]{soon} — joins when: "
                      f"{a['join_trigger'] or 'needed'}")
        if q["stuck"]:
            print(f"\nSTUCK ({len(q['stuck'])}) — at-escalate sends one to the human early")
            for row in q["stuck"]:
                print(f"  C-{row['id']} on {db.about(conn, row)}: {row['replies_since_reassign']} "
                      f"replies since last reassigned")
        if q["report_due"]:
            print("\nREPORT DUE — at-report run")
        if q["milestones"]:
            print(f"\nMILESTONE WORK ({len(q['milestones'])})")
            for item in q["milestones"]:
                if "milestone" in item:
                    m = item["milestone"]
                    print(f"  M-{m['id']} '{m['name']}' [{m['status']}] — {item['todo']}")
                else:
                    d = item["deliverable"]
                    print(f"  D-{d['id']} '{d['name']}' — {item['todo']}")
        if q["reports"]:
            print(f"\nREPORTS WAITING FOR YOU ({len(q['reports'])}) — at-report continue")
            for row in q["reports"]:
                print(f"  P-{row['id']} (round {row['round']})")
        return 0

    return run(go)


# ------------------------------------------------------------------ approve

def approve_main():
    parser = base_parser("Approve a statement, or retract an approval with a concern")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--statement", required=True,
                        help="S-12, or several to approve in one call: S-2,S-3,S-9")
    parser.add_argument("--retract", action="store_true",
                        help="withdraw your approval of one statement; needs --to and --body")
    parser.add_argument("--to", help="who the retraction's concern is addressed to")
    parser.add_argument("--kind", default="objection", help="the retraction concern's kind")
    parser.add_argument("--body", help="why you are retracting")
    args = parser.parse_args()

    def go():
        conn, me, turn = begin(args.db, args.agent, "approve")
        if not me["approves"]:
            raise Refused(f"{me['name']} does not approve statements")
        rows = [db.statement(conn, sid) for sid in db.ref_ids(args.statement, "S")]
        if not rows:
            raise Refused("name a statement: --statement S-12")
        for row in rows:
            if not db.is_live_statement(conn, row):
                raise Refused(f"S-{row['id']} is {row['status']}")

        if not args.retract:
            # All or nothing: one refusal approves none, and names the statement to leave out.
            for row in rows:
                if row["deliverable_id"] and not db.is_live_deliverable(
                        conn, db.deliverable(conn, row["deliverable_id"])):
                    raise Refused(f"S-{row['id']} is in a retired deliverable; Peter moves it first")
                if conn.execute("SELECT 1 FROM approvals WHERE statement_id = ? AND agent_id = ?",
                                (row["id"], me["id"])).fetchone():
                    raise Refused(f"you already approved S-{row['id']}")
                own = conn.execute("SELECT id FROM concerns WHERE statement_id = ? AND raised_by = ? "
                                   "AND status = 'open'", (row["id"], me["id"])).fetchone()
                if own:
                    raise Refused(f"your concern C-{own['id']} on S-{row['id']} is still open")
                conn.execute("INSERT INTO approvals (statement_id, agent_id, round, turn_id, "
                             "created_at) VALUES (?, ?, ?, ?, ?)",
                             (row["id"], me["id"], turn["round"], turn["id"], db.now()))
                db.record_event(conn, turn, "statement", row["id"], None, None,
                                f"approved by {me['role']}")
            finish(conn, turn)
            print(", ".join(f"S-{r['id']}" for r in rows) + f" approved by {me['name']}")
            return 0

        if len(rows) > 1:
            raise Refused("retract one statement at a time, each with its own concern")
        row = rows[0]
        mine = conn.execute("SELECT 1 FROM approvals WHERE statement_id = ? AND agent_id = ?",
                            (row["id"], me["id"])).fetchone()
        if not mine:
            raise Refused(f"you have not approved S-{row['id']}, so there is nothing to retract "
                          "— raise a concern instead")
        if not args.to or not args.body:
            raise Refused("a retraction comes with a concern saying why: --to <role> --body '...'")
        them = db.require_active(conn, db.agent(conn, args.to), "be addressed")
        if them["id"] == me["id"]:
            raise Refused("an agent cannot address a concern to itself")
        kind = db.term(conn, "concern_kinds", args.kind, "concern kind")
        if kind["via_tool"]:
            raise Refused(f"a '{kind['value']}' concern is raised with {kind['via_tool']}")
        conn.execute("DELETE FROM approvals WHERE statement_id = ? AND agent_id = ?",
                     (row["id"], me["id"]))
        db.record_event(conn, turn, "statement", row["id"], None, None,
                        f"approval retracted by {me['role']}")
        cid = raise_concern(conn, turn, me, them, kind["value"], args.body, statement_id=row["id"])
        finish(conn, turn)
        print(f"S-{row['id']}: {me['name']} retracted, with C-{cid} to {them['name']}")
        return 0

    return run(go)


# ------------------------------------------------------------------ concern

def concern_main():
    parser = base_parser("Raise a concern on one statement or milestone, to one agent")
    parser.add_argument("--from", dest="sender", required=True)
    parser.add_argument("--to", dest="recipient", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--on", required=True, help="S-12 (a statement) or M-3 (a milestone)")
    parser.add_argument("--body", required=True)
    args = parser.parse_args()

    def go():
        conn, me, turn = begin(args.db, args.sender, "raise a concern")
        them = db.require_active(conn, db.agent(conn, args.recipient), "be addressed")
        kind = db.term(conn, "concern_kinds", args.kind, "concern kind")
        if kind["via_tool"]:
            raise Refused(f"a '{kind['value']}' concern is raised with {kind['via_tool']}, "
                          "which states what the human needs to decide it")
        if kind["must_address"] and them["role"] != kind["must_address"]:
            raise Refused(f"a '{kind['value']}' concern is addressed to the "
                          f"{kind['must_address']}, not to {them['name']}")
        if me["id"] == them["id"]:
            raise Refused("an agent cannot address a concern to itself")
        prefix, target = db.parse_ref(args.on, "SM")
        if prefix == "S":
            row = db.statement(conn, target)
            if not db.is_live_statement(conn, row):
                raise Refused(f"S-{target} is {row['status']}; raise it on what replaced it")
            cid = raise_concern(conn, turn, me, them, kind["value"], args.body, statement_id=target)
        else:
            db.milestone(conn, target)
            cid = raise_concern(conn, turn, me, them, kind["value"], args.body, milestone_id=target)
        finish(conn, turn)
        print(f"C-{cid} raised by {me['name']} to {them['name']} [{kind['value']}] on {args.on.upper()}")
        if them["id"] == HUMAN_ID:
            print("the loop pauses until the human answers")
        return 0

    return run(go)


# ------------------------------------------------------------------- answer

def answer_main():
    parser = base_parser("Answer a concern addressed to you, or reassign it")
    parser.add_argument("--concern", required=True, help="C-4")
    parser.add_argument("--from", dest="sender", required=True)
    parser.add_argument("--body", required=True, help="the answer, or why you are reassigning")
    parser.add_argument("--reassign-to", help="hand it to this agent instead of answering")
    parser.add_argument("--final", action="store_true",
                        help="the human only: this answer closes the concern")
    args = parser.parse_args()

    def go():
        conn, me, turn = begin(args.db, args.sender, "answer")
        row = db.concern(conn, db.parse_ref(args.concern, "C")[1])
        if row["addressed_to"] != me["id"]:
            owner = db.agent_by_id(conn, row["addressed_to"])
            raise Refused(f"C-{row['id']} is with {owner['name']}, not you")
        if row["status"] != "open":
            raise Refused(f"C-{row['id']} is {row['status']}")
        raiser = db.agent_by_id(conn, row["raised_by"])
        waiting = db.awaiting_verdict(conn, row["id"])
        if waiting:
            raise Refused(f"A-{waiting['id']} is still with {raiser['name']} for a verdict — "
                          "one answer at a time")

        if args.reassign_to:
            if args.final:
                raise Refused("a reassignment cannot be final")
            them = db.require_active(conn, db.agent(conn, args.reassign_to), "be reassigned to")
            if them["id"] == me["id"]:
                raise Refused("it is already with you")
            aid = db.reassign(conn, turn, row, them, me["id"], args.body)
            finish(conn, turn)
            print(f"A-{aid} reassigns C-{row['id']} to {them['name']}")
            if them["id"] == HUMAN_ID:
                print("the loop pauses until the human answers")
            return 0

        final = db.term(conn, "verdicts", "final", "verdict") if args.final else None
        if final is not None and final["human_only"] and me["id"] != HUMAN_ID:
            raise Refused("only the human can mark an answer final")
        stamp = db.now()
        cur = conn.execute(
            """INSERT INTO answers (concern_id, answered_by, kind, body, verdict, round,
                                    verdict_round, verdict_turn_id, turn_id, created_at)
               VALUES (?, ?, 'answer', ?, ?, ?, ?, ?, ?, ?)""",
            (row["id"], me["id"], args.body, final["value"] if final else None, turn["round"],
             turn["round"] if final else None, turn["id"] if final else None, turn["id"], stamp))
        if final is not None and final["closes"]:
            conn.execute("UPDATE concerns SET status = 'closed', closed_round = ? WHERE id = ?",
                         (turn["round"], row["id"]))
            db.record_event(conn, turn, "concern", row["id"], "open", "closed",
                            f"final answer A-{cur.lastrowid}")
            finish(conn, turn)
            print(f"A-{cur.lastrowid} answers C-{row['id']} and closes it (final)")
            return 0
        db.record_event(conn, turn, "concern", row["id"], None, None, f"answered A-{cur.lastrowid}")
        finish(conn, turn)
        print(f"A-{cur.lastrowid} answers C-{row['id']} — with {raiser['name']} for a verdict")
        return 0

    return run(go)


# ------------------------------------------------------------------- review

def review_main():
    parser = base_parser("Give a verdict on the latest answer to a concern you raised")
    parser.add_argument("--answer", required=True, help="A-7")
    parser.add_argument("--by", required=True)
    parser.add_argument("--verdict", required=True, help="accepted, or replied")
    parser.add_argument("--reply", help="what is still missing (required with replied)")
    args = parser.parse_args()

    def go():
        conn, me, turn = begin(args.db, args.by, "judge an answer")
        ans = db.answer(conn, db.parse_ref(args.answer, "A")[1])
        parent = db.concern(conn, ans["concern_id"])
        if parent["raised_by"] != me["id"]:
            raise Refused(f"C-{parent['id']} is not yours to judge")
        if parent["status"] != "open":
            raise Refused(f"C-{parent['id']} is {parent['status']}")
        verdict = db.term(conn, "verdicts", args.verdict, "verdict")
        if verdict["human_only"]:
            raise Refused("only the human marks their own answer final, with at-answer --final")
        latest = db.awaiting_verdict(conn, parent["id"])
        if latest is None or latest["id"] != ans["id"]:
            kind = db.term(conn, "answer_kinds", ans["kind"], "answer kind")
            if not kind["judged"]:
                raise Refused(f"A-{ans['id']} is a hand-off, not an answer to judge")
            if ans["verdict"]:
                raise Refused(f"A-{ans['id']} has already been judged")
            raise Refused(f"judge the latest answer, A-{latest['id']}")
        if verdict["needs_reply"] and not args.reply:
            raise Refused("say what is still missing: --reply '...'")

        conn.execute("UPDATE answers SET verdict = ?, reply = ?, verdict_round = ?, "
                     "verdict_turn_id = ? WHERE id = ?",
                     (verdict["value"], args.reply, turn["round"], turn["id"], ans["id"]))
        if verdict["closes"]:
            conn.execute("UPDATE concerns SET status = 'closed', closed_round = ? WHERE id = ?",
                         (turn["round"], parent["id"]))
            db.record_event(conn, turn, "concern", parent["id"], "open", "closed",
                            f"A-{ans['id']} accepted")
            finish(conn, turn)
            print(f"A-{ans['id']} accepted; C-{parent['id']} closed"
                  + (" — waiting for Peter to act on it" if parent["statement_id"] else ""))
            return 0

        conn.execute("UPDATE concerns SET replies_since_reassign = replies_since_reassign + 1, "
                     "replies_total = replies_total + 1 WHERE id = ?", (parent["id"],))
        db.record_event(conn, turn, "concern", parent["id"], None, None, f"replied on A-{ans['id']}")
        parent = db.concern(conn, parent["id"])
        answerer = db.agent_by_id(conn, ans["answered_by"])
        limit = db.policy(conn, "stall_replies", 4)
        if (parent["replies_since_reassign"] >= limit and parent["addressed_to"] != HUMAN_ID
                and parent["raised_by"] != HUMAN_ID):
            human = db.agent_by_id(conn, HUMAN_ID)
            db.reassign(conn, turn, parent, human, me["id"],
                        f"Automatic: {parent['replies_since_reassign']} replies since the last "
                        "reassignment (policy stall_replies). Both positions are in the thread.")
            finish(conn, turn)
            print(f"A-{ans['id']} sent back; C-{parent['id']} has stalled and goes to the human")
            return 0
        finish(conn, turn)
        print(f"A-{ans['id']} sent back to {answerer['name']}; C-{parent['id']} stays open")
        return 0

    return run(go)


# ----------------------------------------------------------------- escalate

def escalate_main():
    parser = base_parser("Send a stuck concern to the human early, with both positions")
    parser.add_argument("--concern", required=True)
    parser.add_argument("--by", required=True)
    parser.add_argument("--summary", required=True, help="both positions, evenly")
    args = parser.parse_args()

    def go():
        conn, me, turn = begin(args.db, args.by, "escalate")
        db.require_duty(conn, me, "escalation", "owns", "escalate a concern")
        row = db.concern(conn, db.parse_ref(args.concern, "C")[1])
        if row["status"] != "open":
            raise Refused(f"C-{row['id']} is {row['status']}")
        if row["addressed_to"] == HUMAN_ID:
            raise Refused(f"C-{row['id']} is already with the human")
        aid = db.reassign(conn, turn, row, db.agent_by_id(conn, HUMAN_ID), me["id"],
                          f"Escalated by {me['name']}: {args.summary}")
        finish(conn, turn)
        print(f"A-{aid} sends C-{row['id']} to the human; the loop pauses")
        return 0

    return run(go)


# --------------------------------------------------------------- statements

def cited(conn, because):
    """The closed concerns on statements a write is based on."""
    ids = db.ref_ids(because, "C")
    if not ids:
        raise Refused("cite the closed concerns this comes from: --because C-4,C-5")
    rows = []
    for cid in ids:
        row = db.concern(conn, cid)
        if row["status"] != "closed":
            raise Refused(f"C-{cid} is still open — act once its raiser is satisfied")
        rows.append(row)
    return rows


def check_guard(conn, kinds, concerns):
    """A kind guarded by a role can only change on a concern that role answered or raised."""
    for kind in kinds:
        role = kind["guarded_by"]
        if not role:
            continue
        guard = db.agent(conn, role)
        ok = any(
            c["raised_by"] == guard["id"] or conn.execute(
                """SELECT 1 FROM answers w JOIN verdicts v ON v.value = w.verdict
                    WHERE w.concern_id = ? AND w.answered_by = ? AND v.closes = 1""",
                (c["id"], guard["id"])).fetchone()
            for c in concerns)
        if not ok:
            names = ", ".join("C-%d" % c["id"] for c in concerns)
            raise Refused(f"a {kind['value']} changes only on a concern the {role} answered or "
                          f"raised; none of {names} is one — raise it with them first")


def mark_acted(conn, turn, concerns, outcome):
    for c in concerns:
        if c["acted_on"] == outcome:
            continue
        conn.execute("UPDATE concerns SET acted_on = ?, acted_round = ? WHERE id = ?",
                     (outcome, turn["round"], c["id"]))
        db.record_event(conn, turn, "concern", c["id"], None, None, f"acted on: {outcome}")


def retire(conn, turn, row, to_status, concerns, detail):
    """Take a statement out of play, once every concern on it is settled and considered."""
    if not db.is_live_statement(conn, row):
        raise Refused(f"S-{row['id']} is already {row['status']}")
    still_open = db.open_concerns_on(conn, row["id"])
    if still_open:
        raise Refused(f"S-{row['id']} still has open concern(s): "
                      + ", ".join(f"C-{c['id']}" for c in still_open))
    cited_ids = {c["id"] for c in concerns}
    unread = [c for c in db.awaiting_peter(conn, row["id"]) if c["id"] not in cited_ids]
    if unread:
        raise Refused(f"S-{row['id']} has closed concerns you have not considered: "
                      + ", ".join(f"C-{c['id']}" for c in unread)
                      + " — cite them, or at-statement keep them first")
    conn.execute("UPDATE statements SET status = ?, retired_round = ? WHERE id = ?",
                 (to_status, turn["round"], row["id"]))
    db.record_event(conn, turn, "statement", row["id"], row["status"], to_status, detail)


def write_statement(conn, turn, spec, concerns, milestone_id=None):
    """Create one statement from {kind, text, rationale, deliverable, measures}."""
    kind = db.term(conn, "statement_kinds", spec.get("kind"), "statement kind")
    if not kind["writable"]:
        raise Refused(f"a {kind['value']} is written only by at-init")
    text = (spec.get("text") or "").strip()
    if not text:
        raise Refused("a statement needs its text")
    deliverable_id = None
    if kind["level"] == "project":
        if spec.get("deliverable"):
            raise Refused(f"a {kind['value']} is project-wide; it has no deliverable")
    else:
        if not spec.get("deliverable"):
            raise Refused(f"a {kind['value']} belongs to one deliverable: name it (D-1)")
        deliverable_id = db.parse_ref(spec["deliverable"], "D")[1]
        if not db.is_live_deliverable(conn, db.deliverable(conn, deliverable_id)):
            raise Refused(f"D-{deliverable_id} is not live")

    link_to = None
    if kind["requires_link"]:
        relation = db.term(conn, "link_relations", kind["requires_link"], "link relation")
        if not spec.get(relation["value"]):
            raise Refused(f"a {kind['value']} must name what it {relation['value']}: "
                          f"--{relation['value']} S-3 (or \"{relation['value']}\" in --new)")
        target = db.statement(conn, db.parse_ref(spec[relation["value"]], "S")[1])
        wanted = cfg.kinds_list(relation["to_kinds"])
        if wanted and target["kind"] not in wanted:
            raise Refused(f"a {kind['value']} {relation['value']} a {' or '.join(wanted)}, "
                          f"and S-{target['id']} is a {target['kind']}")
        if not db.is_live_statement(conn, target):
            raise Refused(f"S-{target['id']} is {target['status']}")
        link_to = (target["id"], relation["value"])

    stamp = db.now()
    cur = conn.execute(
        """INSERT INTO statements (kind, text, rationale, deliverable_id, milestone_id,
                                   created_round, turn_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (kind["value"], text, spec.get("rationale"), deliverable_id, milestone_id,
         turn["round"], turn["id"], stamp))
    new_id = cur.lastrowid
    for c in concerns:
        conn.execute("INSERT INTO statement_reasons (statement_id, concern_id, turn_id, created_at) "
                     "VALUES (?, ?, ?, ?)", (new_id, c["id"], turn["id"], stamp))
    if link_to:
        add_link(conn, turn, new_id, link_to[0], link_to[1])
    db.record_event(conn, turn, "statement", new_id, None, "pending",
                    "because " + ", ".join(f"C-{c['id']}" for c in concerns))
    return new_id


def add_link(conn, turn, from_id, to_id, relation):
    conn.execute("INSERT INTO links (from_id, to_id, relation, turn_id, created_at) "
                 "VALUES (?, ?, ?, ?, ?)", (from_id, to_id, relation, turn["id"], db.now()))


def statement_main():
    parser = base_parser("Peter's tool: write, supersede, move, cancel, or keep")
    sub = parser.add_subparsers(dest="action", required=True)

    add = sub.add_parser("add", help="write a new statement")
    add.add_argument("--kind", required=True)
    add.add_argument("--text", required=True)
    add.add_argument("--rationale")
    add.add_argument("--deliverable", help="D-1, for deliverable-level kinds")
    add.add_argument("--measures", help="S-3, for a success criterion: the goal it measures")

    sup = sub.add_parser("supersede", help="replace one or more statements with one or more")
    sup.add_argument("--old", required=True, help="S-2,S-9")
    sup.add_argument("--new", action="append", required=True,
                     help='JSON per new statement: {"kind", "text", "rationale", "deliverable", '
                          '"measures"}; kind and deliverable default to the old one\'s')

    move = sub.add_parser("move", help="move a statement to another deliverable")
    move.add_argument("--statement", required=True)
    move.add_argument("--to", required=True, help="D-2")

    cancel = sub.add_parser("cancel", help="drop a statement, replacing it with nothing")
    cancel.add_argument("--statement", required=True)
    cancel.add_argument("--reason", required=True)

    keep = sub.add_parser("keep", help="considered: no change needed")
    keep.add_argument("--note")

    for p in (add, sup, move, cancel, keep):
        p.add_argument("--by", required=True)
        p.add_argument("--because", required=True, help="the closed concerns: C-4,C-5")
    args = parser.parse_args()

    def go():
        conn, me, turn = begin(args.db, args.by, "write statements")
        db.require_duty(conn, me, "statements", "owns", "write statements")
        concerns = cited(conn, args.because)
        for c in concerns:
            if c["statement_id"] is None:
                raise Refused(f"C-{c['id']} is on a milestone; statements come from concerns "
                              "on statements")

        if args.action == "keep":
            for c in concerns:
                if c["acted_on"]:
                    raise Refused(f"C-{c['id']} was already acted on ({c['acted_on']})")
            mark_acted(conn, turn, concerns, "kept")
            finish(conn, turn)
            print("kept as is: " + ", ".join(f"C-{c['id']}" for c in concerns))
            return 0

        if args.action == "add":
            kind = db.term(conn, "statement_kinds", args.kind, "statement kind")
            check_guard(conn, [kind], concerns)
            new_id = write_statement(conn, turn, {
                "kind": args.kind, "text": args.text, "rationale": args.rationale,
                "deliverable": args.deliverable, "measures": args.measures}, concerns)
            mark_acted(conn, turn, concerns, "changed")
            finish(conn, turn)
            print(f"S-{new_id} [{args.kind}] written — every approver reviews it")
            return 0

        if args.action == "cancel":
            row = db.statement(conn, db.parse_ref(args.statement, "S")[1])
            check_guard(conn, [db.term(conn, "statement_kinds", row["kind"], "kind")], concerns)
            retire(conn, turn, row, "cancelled", concerns, args.reason)
            mark_acted(conn, turn, concerns, "changed")
            finish(conn, turn)
            print(f"S-{row['id']} cancelled")
            return 0

        if args.action == "move":
            row = db.statement(conn, db.parse_ref(args.statement, "S")[1])
            kind = db.term(conn, "statement_kinds", row["kind"], "kind")
            if kind["level"] != "deliverable":
                raise Refused(f"a {kind['value']} is project-wide; it has no deliverable to move")
            target = db.parse_ref(args.to, "D")[1]
            if target == row["deliverable_id"]:
                raise Refused(f"S-{row['id']} is already in D-{target}")
            retire(conn, turn, row, "superseded", concerns, f"moved to D-{target}")
            new_id = write_statement(conn, turn, {
                "kind": row["kind"], "text": row["text"], "rationale": row["rationale"],
                "deliverable": f"D-{target}"}, concerns)
            add_link(conn, turn, new_id, row["id"], "supersedes")
            mark_acted(conn, turn, concerns, "changed")
            finish(conn, turn)
            print(f"S-{row['id']} moved to D-{target} as S-{new_id} — every approver reviews it")
            return 0

        # supersede
        olds = [db.statement(conn, sid) for sid in db.ref_ids(args.old, "S")]
        try:
            specs = [json.loads(text) for text in args.new]
        except json.JSONDecodeError as exc:
            raise Refused(f"--new must be JSON: {exc}")
        if len(olds) == 1:
            for spec in specs:
                spec.setdefault("kind", olds[0]["kind"])
                if olds[0]["deliverable_id"]:
                    spec.setdefault("deliverable", f"D-{olds[0]['deliverable_id']}")
        kinds = [db.term(conn, "statement_kinds", r["kind"], "kind") for r in olds]
        kinds += [db.term(conn, "statement_kinds", s.get("kind"), "statement kind") for s in specs]
        check_guard(conn, kinds, concerns)
        milestone_id = next((r["milestone_id"] for r in olds if r["milestone_id"]), None)
        new_ids = [write_statement(conn, turn, spec, concerns, milestone_id) for spec in specs]
        for row in olds:
            retire(conn, turn, row, "superseded",
                   concerns, "replaced by " + ", ".join(f"S-{n}" for n in new_ids))
            for new_id in new_ids:
                add_link(conn, turn, new_id, row["id"], "supersedes")
        mark_acted(conn, turn, concerns, "changed")
        finish(conn, turn)
        print(", ".join(f"S-{r['id']}" for r in olds) + " superseded by "
              + ", ".join(f"S-{n}" for n in new_ids) + " — every approver reviews them")
        return 0

    return run(go)


# ------------------------------------------------------------- deliverables

def deliverable_main():
    parser = base_parser("Peter's tool: add, split, or cancel a deliverable")
    sub = parser.add_subparsers(dest="action", required=True)
    add = sub.add_parser("add", help="a new deliverable")
    add.add_argument("--name", required=True)
    split = sub.add_parser("split", help="replace one deliverable with several")
    split.add_argument("--old", required=True, help="D-1")
    split.add_argument("--into", required=True, help="names, comma-separated: v1,v2")
    cancel = sub.add_parser("cancel", help="drop an empty deliverable")
    cancel.add_argument("--old", required=True)
    for p in (add, split, cancel):
        p.add_argument("--by", required=True)
        p.add_argument("--because", required=True, help="the closed concerns: C-4")
    args = parser.parse_args()

    def create(conn, turn, name):
        seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM deliverables").fetchone()["n"]
        cur = conn.execute(
            """INSERT INTO deliverables (name, seq, created_round, turn_id, created_at)
               VALUES (?, ?, ?, ?, ?)""", (name, seq, turn["round"], turn["id"], db.now()))
        db.record_event(conn, turn, "deliverable", cur.lastrowid, None, "live", name)
        return cur.lastrowid

    def go():
        conn, me, turn = begin(args.db, args.by, "change deliverables")
        db.require_duty(conn, me, "deliverables", "owns", "change deliverables")
        concerns = cited(conn, args.because)

        if args.action == "add":
            did = create(conn, turn, args.name)
            mark_acted(conn, turn, concerns, "changed")
            finish(conn, turn)
            print(f"D-{did} '{args.name}' added — it needs a scope statement")
            return 0

        old = db.deliverable(conn, db.parse_ref(args.old, "D")[1])
        if not db.is_live_deliverable(conn, old):
            raise Refused(f"D-{old['id']} is {old['status']}")

        if args.action == "cancel":
            inside = conn.execute("SELECT id FROM statements WHERE deliverable_id = ? AND status IN "
                                  "('pending', 'agreed')", (old["id"],)).fetchall()
            if inside:
                raise Refused(f"D-{old['id']} still holds "
                              + ", ".join(f"S-{r['id']}" for r in inside)
                              + " — move or cancel them first")
            conn.execute("UPDATE deliverables SET status = 'cancelled', retired_round = ? "
                         "WHERE id = ?", (turn["round"], old["id"]))
            db.record_event(conn, turn, "deliverable", old["id"], "live", "cancelled")
            mark_acted(conn, turn, concerns, "changed")
            finish(conn, turn)
            print(f"D-{old['id']} cancelled")
            return 0

        names = [n.strip() for n in args.into.split(",") if n.strip()]
        if len(names) < 2:
            raise Refused("a split makes at least two deliverables: --into v1,v2")
        new_ids = [create(conn, turn, name) for name in names]
        for new_id in new_ids:
            conn.execute("INSERT INTO deliverable_lineage (old_id, new_id, turn_id, created_at) "
                         "VALUES (?, ?, ?, ?)", (old["id"], new_id, turn["id"], db.now()))
        conn.execute("UPDATE deliverables SET status = 'superseded', retired_round = ? WHERE id = ?",
                     (turn["round"], old["id"]))
        db.record_event(conn, turn, "deliverable", old["id"], "live", "superseded",
                        "split into " + ", ".join(f"D-{n}" for n in new_ids))
        mark_acted(conn, turn, concerns, "changed")
        finish(conn, turn)
        print(f"D-{old['id']} split into " + ", ".join(f"D-{n}" for n in new_ids)
              + " — move each of its statements (at-statement move) and write each a scope")
        return 0

    return run(go)


# --------------------------------------------------------------- milestones

def milestone_main():
    parser = base_parser("Milestones: written by Peter, reviewed, checked, built, accepted")
    sub = parser.add_subparsers(dest="action", required=True)

    add = sub.add_parser("add", help="Peter: write a milestone")
    add.add_argument("--deliverable", required=True)
    add.add_argument("--name", required=True)
    add.add_argument("--intent")
    add.add_argument("--because", required=True, help="the closed concerns proposing it")

    assign = sub.add_parser("assign", help="Peter: put agreed statements in a planned milestone")
    assign.add_argument("--milestone", required=True)
    assign.add_argument("--statements", required=True, help="S-9,S-12")

    for name, helptext in (("review", "the architect: pass or fail the slicing"),
                           ("check", "the developer: is it buildable from its file?")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--milestone", required=True)
        p.add_argument("--ok", required=True, choices=["yes", "no"])
        p.add_argument("--note", help="what is wrong (required with --ok no)")

    start = sub.add_parser("start", help="the human: build this milestone next")
    start.add_argument("--milestone", required=True)
    start.add_argument("--branch", help="defaults to m-<id>-<name>")

    for name, helptext in (("submit", "the developer: built, ready for review"),
                           ("pass", "QA or code review: nothing more from me"),
                           ("merge", "the developer: merge it"),
                           ("accept", "the human: tried it, satisfied")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--milestone", required=True)

    for p in sub.choices.values():
        p.add_argument("--by", required=True)
    args = parser.parse_args()

    def move(conn, turn, row, to_status, detail=None):
        conn.execute("UPDATE milestones SET status = ? WHERE id = ?", (to_status, row["id"]))
        db.record_event(conn, turn, "milestone", row["id"], row["status"], to_status, detail)

    def open_on(conn, row):
        return conn.execute("SELECT id FROM concerns WHERE milestone_id = ? AND status = 'open'",
                            (row["id"],)).fetchall()

    def go():
        conn, me, turn = begin(args.db, args.by, "act on a milestone")

        if args.action == "add":
            db.require_duty(conn, me, "milestones", "owns", "write a milestone")
            parent = db.deliverable(conn, db.parse_ref(args.deliverable, "D")[1])
            if not db.is_live_deliverable(conn, parent):
                raise Refused(f"D-{parent['id']} is {parent['status']}")
            concerns = cited(conn, args.because)
            seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM milestones "
                               "WHERE deliverable_id = ?", (parent["id"],)).fetchone()["n"]
            stamp = db.now()
            cur = conn.execute(
                """INSERT INTO milestones (deliverable_id, name, seq, intent, turn_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (parent["id"], args.name, seq, args.intent, turn["id"], stamp))
            for c in concerns:
                conn.execute("INSERT INTO milestone_reasons (milestone_id, concern_id, turn_id, "
                             "created_at) VALUES (?, ?, ?, ?)", (cur.lastrowid, c["id"], turn["id"],
                                                                 stamp))
            db.record_event(conn, turn, "milestone", cur.lastrowid, None, "planned", args.name)
            mark_acted(conn, turn, [c for c in concerns if c["statement_id"]], "changed")
            finish(conn, turn)
            print(f"M-{cur.lastrowid} '{args.name}' in D-{parent['id']} — assign its statements")
            return 0

        row = db.milestone(conn, db.parse_ref(args.milestone, "M")[1])

        if args.action == "assign":
            db.require_duty(conn, me, "milestones", "owns", "assign statements")
            if row["status"] != "planned":
                raise Refused(f"M-{row['id']} is {row['status']}; only a planned milestone "
                              "takes new statements")
            for sid in db.ref_ids(args.statements, "S"):
                s = db.statement(conn, sid)
                if s["deliverable_id"] != row["deliverable_id"]:
                    raise Refused(f"S-{sid} is not in D-{row['deliverable_id']}")
                if s["status"] != "agreed":
                    raise Refused(f"S-{sid} is {s['status']}; only agreed statements are built")
                conn.execute("UPDATE statements SET milestone_id = ? WHERE id = ?", (row["id"], sid))
                db.record_event(conn, turn, "statement", sid, None, None, f"assigned to M-{row['id']}")
            finish(conn, turn)
            print(f"assigned to M-{row['id']}")
            return 0

        if args.action in ("review", "check"):
            duty, relation, column = (("slicing", "reviews", "sliced_ok_by")
                                      if args.action == "review"
                                      else ("buildability", "checks", "checked_ok_by"))
            db.require_duty(conn, me, duty, relation, f"{args.action} a milestone")
            if row["status"] != "planned":
                raise Refused(f"M-{row['id']} is {row['status']}")
            if args.ok == "no":
                if not args.note:
                    raise Refused("say what is wrong: --note '...'")
                owner = db.who(conn, "milestones", "owns")[0]
                cid = raise_concern(conn, turn, me, owner, "objection", args.note,
                                    milestone_id=row["id"])
                finish(conn, turn)
                print(f"M-{row['id']} not passed — C-{cid} to {owner['name']}")
                return 0
            conn.execute(f"UPDATE milestones SET {column} = ? WHERE id = ?", (me["id"], row["id"]))
            db.record_event(conn, turn, "milestone", row["id"], None, None,
                            f"{duty} passed by {me['role']}")
            finish(conn, turn)
            print(f"M-{row['id']} {duty} passed by {me['name']}")
            return 0

        if args.action == "start":
            db.require_duty(conn, me, "acceptance", "rules_on", "start a milestone")
            if row["status"] != "planned":
                raise Refused(f"M-{row['id']} is {row['status']}")
            if not row["sliced_ok_by"] or not row["checked_ok_by"]:
                raise Refused(f"M-{row['id']} needs the slicing review and the buildability check")
            inside = conn.execute("SELECT id, status FROM statements WHERE milestone_id = ?",
                                  (row["id"],)).fetchall()
            if not inside:
                raise Refused(f"M-{row['id']} has no statements assigned")
            if any(s["status"] != "agreed" for s in inside):
                raise Refused(f"M-{row['id']} holds statements that are not agreed")
            if open_on(conn, row):
                raise Refused(f"M-{row['id']} has open concerns")
            branch = args.branch or f"m-{row['id']}-" + re.sub(r"[^a-z0-9]+", "-",
                                                               row["name"].lower()).strip("-")
            conn.execute("UPDATE milestones SET branch = ? WHERE id = ?", (branch, row["id"]))
            move(conn, turn, row, "building", f"branch {branch}")
            finish(conn, turn)
            print(f"M-{row['id']} building on {branch}")
            return 0

        if args.action == "submit":
            db.require_duty(conn, me, "code", "owns", "submit a milestone")
            if row["status"] != "building":
                raise Refused(f"M-{row['id']} is {row['status']}")
            move(conn, turn, row, "in_review")
            finish(conn, turn)
            print(f"M-{row['id']} in review")
            return 0

        if args.action == "pass":
            column = None
            if db.has_duty(conn, me["id"], "testing", "owns"):
                column = "tested_ok_by"
            elif db.has_duty(conn, me["id"], "code_review", "owns"):
                column = "reviewed_ok_by"
            else:
                raise Refused(f"{me['name']} neither tests nor reviews code")
            if row["status"] != "in_review":
                raise Refused(f"M-{row['id']} is {row['status']}")
            mine = conn.execute("SELECT id FROM concerns WHERE milestone_id = ? AND raised_by = ? "
                                "AND status = 'open'", (row["id"], me["id"])).fetchone()
            if mine:
                raise Refused(f"your concern C-{mine['id']} on M-{row['id']} is still open")
            conn.execute(f"UPDATE milestones SET {column} = ? WHERE id = ?", (me["id"], row["id"]))
            db.record_event(conn, turn, "milestone", row["id"], None, None, f"passed by {me['role']}")
            finish(conn, turn)
            print(f"M-{row['id']} passed by {me['name']}")
            return 0

        if args.action == "merge":
            db.require_duty(conn, me, "code", "owns", "merge a milestone")
            if row["status"] != "in_review":
                raise Refused(f"M-{row['id']} is {row['status']}")
            if not row["tested_ok_by"] or not row["reviewed_ok_by"]:
                raise Refused(f"M-{row['id']} needs a pass from QA and from code review")
            still = open_on(conn, row)
            if still:
                raise Refused(f"M-{row['id']} has open concerns: "
                              + ", ".join(f"C-{c['id']}" for c in still))
            move(conn, turn, row, "merged", f"branch {row['branch']}")
            finish(conn, turn)
            print(f"M-{row['id']} merged — the human tries it next")
            return 0

        # accept
        db.require_duty(conn, me, "acceptance", "rules_on", "accept a milestone")
        if row["status"] != "merged":
            raise Refused(f"M-{row['id']} is {row['status']}")
        if open_on(conn, row):
            raise Refused(f"M-{row['id']} has open concerns")
        move(conn, turn, row, "accepted")
        finish(conn, turn)
        print(f"M-{row['id']} accepted")
        return 0

    return run(go)


# -------------------------------------------------------------------- staff

def staff_main():
    parser = base_parser("Staffing: who joins the project, and who is not needed")
    sub = parser.add_subparsers(dest="action", required=True)
    request = sub.add_parser("request", help="ask the human to bring an agent in")
    request.add_argument("--agent", required=True)
    request.add_argument("--by", required=True)
    request.add_argument("--on", required=True, help="the statement that triggered it: S-12")
    request.add_argument("--reason", required=True, help="what triggered it")
    request.add_argument("--cost", help="what it will cost in constraints, rounds, spend")
    passing = sub.add_parser("pass", help="record that an agent is not needed, and why")
    passing.add_argument("--agent", required=True)
    passing.add_argument("--by", required=True)
    passing.add_argument("--reason", required=True, help="why this project does not need them")
    approve = sub.add_parser("approve", help="the human: bring them in (with or without a request)")
    approve.add_argument("--agent", required=True)
    approve.add_argument("--concern", help="the staffing request, if there is one")
    approve.add_argument("--by", default="human")
    decline = sub.add_parser("decline", help="the human: do not")
    decline.add_argument("--agent", required=True)
    decline.add_argument("--concern", required=True)
    decline.add_argument("--reason", required=True, help="defer or drop the trigger, and why")
    decline.add_argument("--by", default="human")
    args = parser.parse_args()

    def joinable(target, st):
        """Can be staffed now: its phase has come, or comes next (so a phase never waits)."""
        if target["active"]:
            raise Refused(f"{target['name']} is already in the project")
        if target["id"] == HUMAN_ID:
            raise Refused("the human is always in the project")
        if target["joins_at_phase"] > st["phase"] + 1:
            raise Refused(f"{target['name']} joins at phase {target['joins_at_phase']}; "
                          f"this project is in phase {st['phase']}")

    def activate(conn, turn, target, requested_by, detail):
        conn.execute("UPDATE agents SET active = 1, joined_round = ?, joined_turn_id = ?, "
                     "requested_by = ? WHERE id = ?",
                     (turn["round"], turn["id"], requested_by, target["id"]))
        db.record_event(conn, turn, "agent", target["id"], None, "active", detail)

    def go():
        conn, me, turn = begin(args.db, args.by, "staff")
        target = db.agent(conn, args.agent)
        st = db.state(conn)

        if args.action == "request":
            joinable(target, st)
            trigger = db.statement(conn, db.parse_ref(args.on, "S")[1])
            if target["auto_staff"] or db.policy(conn, "auto_approve_staffing", 0):
                activate(conn, turn, target, me["id"],
                         f"auto-approved on S-{trigger['id']}: {args.reason}")
                finish(conn, turn)
                print(f"{target['name']} joined (auto-approved; the human sees it in the report)")
                return 0
            deciders = db.who(conn, "staffing", "rules_on")
            if not deciders:
                raise Refused("nobody in this roster rules on staffing")
            kind = db.term(conn, "concern_kinds", "staffing", "concern kind")
            body = (f"Bring in {target['name']}?\n"
                    f"  they argue for: {target['motivation'].strip()}\n"
                    f"  trigger:        S-{trigger['id']}: {args.reason}\n"
                    f"  joins when:     {target['join_trigger'] or 'n/a'}\n"
                    f"  cost:           {args.cost or 'more concerns, more rounds, more spend'}\n"
                    f"  or instead:     defer or drop S-{trigger['id']}.")
            cid = raise_concern(conn, turn, me, deciders[0], kind["value"], body,
                                statement_id=trigger["id"])
            db.record_event(conn, turn, "agent", target["id"], None, "requested", f"C-{cid}")
            finish(conn, turn)
            print(f"C-{cid} staffing request for {target['name']} -> {deciders[0]['name']}")
            return 0

        if args.action == "pass":
            db.require_duty(conn, me, "recruiting", "owns", "decide an agent is not needed")
            joinable(target, st)
            db.record_event(conn, turn, "agent", target["id"], None, "passed", args.reason)
            finish(conn, turn)
            print(f"{target['name']}: not needed — {args.reason} (shown to the human in the report)")
            return 0

        db.require_duty(conn, me, "staffing", "rules_on", "decide staffing")
        row = None
        if args.concern:
            row = db.concern(conn, db.parse_ref(args.concern, "C")[1])
            if row["kind"] != "staffing" or row["status"] != "open":
                raise Refused(f"C-{row['id']} is not an open staffing request")
        if args.action == "approve":
            joinable(target, st)
            activate(conn, turn, target, row["raised_by"] if row else me["id"],
                     f"approved on C-{row['id']}" if row else "staffed directly by the human")
            body = (f"Approved. {target['name']} joins in round {turn['round']} and reviews "
                    "every live statement.")
        else:
            db.record_event(conn, turn, "agent", target["id"], None, "declined", args.reason)
            body = f"Declined. {args.reason}"
        if row is None:
            finish(conn, turn)
            print(f"{target['name']} joined in round {turn['round']}")
            return 0
        stamp = db.now()
        cur = conn.execute(
            """INSERT INTO answers (concern_id, answered_by, kind, body, verdict, round,
                                    verdict_round, verdict_turn_id, turn_id, created_at)
               VALUES (?, ?, 'answer', ?, 'final', ?, ?, ?, ?, ?)""",
            (row["id"], me["id"], body, turn["round"], turn["round"], turn["id"], turn["id"],
             stamp))
        conn.execute("UPDATE concerns SET status = 'closed', closed_round = ? WHERE id = ?",
                     (turn["round"], row["id"]))
        db.record_event(conn, turn, "concern", row["id"], "open", "closed",
                        f"staffing {args.action}d (A-{cur.lastrowid})")
        finish(conn, turn)
        print(f"{target['name']}: {args.action}d. C-{row['id']} closed — Peter acts on it")
        return 0

    return run(go)


# ------------------------------------------------------------------- report

def build_report(conn, report_no, since_turn):
    st = db.state(conn)
    lines = [f"# Report P-{report_no} — round {st['round']}, phase {st['phase']} "
             f"({st['phase_name']})", ""]
    turns = conn.execute(
        """SELECT t.*, a.role FROM turns t JOIN agents a ON a.id = t.agent_id
            WHERE t.id > ? ORDER BY t.id""", (since_turn,)).fetchall()
    taken = [t for t in turns if t["status"] == "done"]
    tokens = sum(t["tokens"] or 0 for t in turns)
    lines.append(f"Turns since the last report: {len(taken)} taken, "
                 f"{len(turns) - len(taken)} skipped"
                 + (f", {tokens} tokens" if tokens else "") + ".")

    def since(obj, where):
        return conn.execute(
            f"""SELECT e.*, a.role FROM events e LEFT JOIN agents a ON a.id = e.actor
                 WHERE e.object = ? AND (e.turn_id > ? OR e.turn_id IS NULL AND e.id > ?)
                   AND {where} ORDER BY e.id""",
            (obj, since_turn, 0 if not since_turn else conn.execute(
                "SELECT COALESCE(MAX(id), 0) AS n FROM events WHERE turn_id <= ?",
                (since_turn,)).fetchone()["n"])).fetchall()

    agreed = {e["object_id"] for e in since("statement", "e.to_status = 'agreed'")}
    agreed = [db.statement(conn, sid) for sid in sorted(agreed)]
    agreed = [s for s in agreed if s["status"] == "agreed"]
    lines += ["", f"## Newly agreed ({len(agreed)})"]
    lines += [f"- {show(conn, s)}" for s in agreed] or ["- none"]

    writers = [a["id"] for a in db.who(conn, "statements", "owns")]
    changes = [e for e in since("statement", "(e.from_status IS NULL AND e.to_status = 'pending') "
                                             "OR e.to_status IN ('superseded', 'cancelled')")
               if e["actor"] in writers]
    lines += ["", f"## What Peter changed ({len(changes)})"]
    for e in changes:
        s = db.statement(conn, e["object_id"])
        verb = "wrote" if e["to_status"] == "pending" else e["to_status"]
        lines.append(f"- {verb} S-{s['id']} [{s['kind']}] {s['text'].strip()} — {e['detail'] or ''}")
    if not changes:
        lines.append("- nothing")

    kept = since("concern", "e.detail = 'acted on: kept'")
    if kept:
        lines += ["", f"## Considered and kept as is ({len(kept)})"]
        for e in kept:
            c = db.concern(conn, e["object_id"])
            lines.append(f"- C-{c['id']} on {db.about(conn, c)}: {c['body'].strip()}")

    staffing = since("agent", "e.to_status IN ('active', 'requested', 'declined', 'passed')")
    if staffing:
        lines += ["", f"## Staffing ({len(staffing)})"]
        verbs = {"active": "joined", "requested": "requested", "declined": "declined",
                 "passed": "not needed"}
        for e in staffing:
            a = db.agent_by_id(conn, e["object_id"])
            lines.append(f"- {a['name']}: {verbs[e['to_status']]}"
                         + (f" — {e['detail']}" if e["detail"] else ""))
    left = db.unstaffed(conn)
    if left:
        lines += ["", "## Not yet on the project, and not decided",
                  "- " + ", ".join(a["name"] for a in left)]

    waiting = conn.execute("SELECT * FROM concerns WHERE status = 'open' AND addressed_to = ?",
                           (HUMAN_ID,)).fetchall()
    if waiting:
        lines += ["", f"## Waiting for you ({len(waiting)})"]
        lines += [f"- C-{c['id']} [{c['kind']}] on {db.about(conn, c)}: {c['body'].strip()}"
                  for c in waiting]
    stuck, bouncing = db.stuck(conn), db.bouncing(conn)
    if stuck or bouncing:
        lines += ["", "## Threads to watch"]
        lines += [f"- C-{c['id']} stuck: {c['replies_since_reassign']} replies since reassigned"
                  for c in stuck]
        lines += [f"- C-{c['id']} bouncing: {c['replies_total']} replies in total"
                  for c in bouncing]

    lines += ["", "## What each agent said"]
    for t in taken:
        if t["summary"]:
            lines.append(f"- T-{t['id']} {t['role']} (round {t['round']}): {t['summary'].strip()}")
    return "\n".join(lines) + "\n"


def report_main():
    parser = base_parser("The periodic report to the human, which pauses the loop")
    sub = parser.add_subparsers(dest="action", required=True)
    runp = sub.add_parser("run", help="Peter: write the report; the loop pauses")
    runp.add_argument("--by", required=True)
    cont = sub.add_parser("continue", help="the human: read it, carry on")
    cont.add_argument("--by", default="human")
    cont.add_argument("--report", help="P-2; defaults to the one waiting")
    args = parser.parse_args()

    def go():
        conn, me, turn = begin(args.db, args.by, "report")
        if args.action == "run":
            db.require_duty(conn, me, "reporting", "owns", "run the report")
            if conn.execute("SELECT 1 FROM reports WHERE continued_at IS NULL").fetchone():
                raise Refused("a report is already waiting for the human")
            last = conn.execute("SELECT turn_id FROM reports ORDER BY id DESC LIMIT 1").fetchone()
            report_no = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 AS n FROM reports"
                                     ).fetchone()["n"]
            body = build_report(conn, report_no, last["turn_id"] if last else 0)
            cur = conn.execute("INSERT INTO reports (round, turn_id, body, created_at) "
                               "VALUES (?, ?, ?, ?)", (turn["round"], turn["id"], body, db.now()))
            db.record_event(conn, turn, "report", cur.lastrowid, None, "waiting")
            finish(conn, turn)
            print(body)
            print(f"P-{cur.lastrowid} written; the loop pauses until the human continues")
            return 0

        if me["id"] != HUMAN_ID:
            raise Refused("only the human continues after a report")
        row = conn.execute(
            "SELECT * FROM reports WHERE continued_at IS NULL ORDER BY id LIMIT 1").fetchone()
        if args.report:
            row = conn.execute("SELECT * FROM reports WHERE id = ?",
                               (db.parse_ref(args.report, "P")[1],)).fetchone()
        if row is None or row["continued_at"]:
            raise Refused("no report is waiting")
        conn.execute("UPDATE reports SET continued_at = ?, continued_turn_id = ? WHERE id = ?",
                     (db.now(), turn["id"], row["id"]))
        db.record_event(conn, turn, "report", row["id"], "waiting", "continued")
        finish(conn, turn)
        print(f"P-{row['id']} read; the loop continues")
        return 0

    return run(go)


# -------------------------------------------------------------------- state

def state_main():
    parser = base_parser("Where the project stands, and what it is waiting on")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        st = db.state(conn)
        running = db.running_turn(conn)
        taken = {r["agent_id"] for r in conn.execute(
            "SELECT agent_id FROM turns WHERE round = ?", (st["round"],))}
        left = [a["role"] for a in db.rotation(conn) if a["id"] not in taken]
        by_status = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM statements GROUP BY status")}
        open_count = conn.execute("SELECT COUNT(*) AS n FROM concerns WHERE status = 'open'"
                                  ).fetchone()["n"]
        waiting_peter = len(db.awaiting_peter(conn))
        active = [a["role"] for a in conn.execute("SELECT role FROM agents WHERE active = 1 "
                                                  "ORDER BY seq")]
        payload = {
            "round": st["round"], "phase": st["phase"], "phase_name": st["phase_name"],
            "status": st["status"], "paused": st["status"] == "paused",
            "paused_reason": st["paused_reason"], "paused_ref": st["paused_ref"],
            "current_turn": running["id"] if running else None,
            "left_this_round": left, "report_due": db.report_due(conn),
            "statements": by_status, "open_concerns": open_count,
            "awaiting_peter": waiting_peter, "active": active,
            "converged": st["status"] == "converged", "ends_when": st["ends_when"].strip(),
            "change_mark": st["change_mark"],
        }
        if args.json:
            print(json.dumps(payload, indent=2))
            return 0
        print(f"round {st['round']}, phase {st['phase']} ({st['phase_name']}) — {st['status']}")
        if st["status"] == "paused":
            what = "P" if st["paused_reason"] == "report" else "C"
            print(f"  paused on:    {what}-{st['paused_ref']} (the human's turn)")
        print(f"  ends when:    {st['ends_when'].strip()}")
        print(f"  active:       {', '.join(active)}")
        print(f"  turn:         " + (f"T-{running['id']} running" if running else "none running"))
        print(f"  left:         {', '.join(left) or 'nobody — at-round --advance'}")
        print("  statements:   " + (", ".join(f"{n} {s}" for s, n in by_status.items()) or "none"))
        print(f"  concerns:     {open_count} open, {waiting_peter} closed and waiting for Peter")
        if payload["report_due"]:
            print("  report due:   Peter runs at-report run this round")
        return 0

    return run(go)


# ---------------------------------------------------------- round and phase

def round_main():
    parser = base_parser("Advance the round, or move the project to another phase")
    parser.add_argument("--advance", action="store_true")
    parser.add_argument("--phase", type=int, help="move the project to this phase")
    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        st = db.state(conn)
        if db.running_turn(conn):
            raise Refused("a turn is still running")
        if args.phase:
            if not conn.execute("SELECT 1 FROM phases WHERE number = ?", (args.phase,)).fetchone():
                raise Refused(f"there is no phase {args.phase}")
            if st["phase"] == 1 and args.phase > 1 and st["status"] != "converged":
                raise Refused("phase 1 has not converged: " + st["ends_when"].strip())
            missing = db.missing_for_phase(conn, args.phase)
            if missing:
                raise Refused(f"phase {args.phase} needs someone on the project for: " + "; ".join(
                    f"{duty} ({' or '.join(h['name'] for h in holders) or 'nobody in the roster'})"
                    for duty, holders in missing)
                    + " — staff them first (at-staff approve in the human's turn)")
            conn.execute("UPDATE project_state SET phase = ?, updated_at = ? WHERE id = 1",
                         (args.phase, db.now()))
            db.record_event(conn, None, "project", 1, None, None,
                            f"phase {st['phase']} -> {args.phase}")
            db.refresh(conn, None)
            conn.commit()
            print(f"phase {st['phase']} -> {args.phase}")
            return 0
        if not args.advance:
            parser.error("give --advance or --phase N")
        if st["status"] == "paused":
            raise Refused("the loop is paused for the human")
        if st["status"] == "converged":
            raise Refused("phase 1 has converged — at-round --phase 2")
        taken = {r["agent_id"] for r in conn.execute(
            "SELECT agent_id FROM turns WHERE round = ?", (st["round"],))}
        left = [a["role"] for a in db.rotation(conn) if a["id"] not in taken]
        if left:
            raise Refused(f"not everyone has had a turn this round: {', '.join(left)} "
                          "— at-turn next")
        if db.report_due(conn):
            raise Refused("a report is due this round — Peter runs at-report run")
        conn.execute("UPDATE project_state SET round = round + 1, updated_at = ? WHERE id = 1",
                     (db.now(),))
        db.record_event(conn, None, "project", 1, None, None, f"round {st['round'] + 1}")
        conn.commit()
        print(f"round {st['round']} -> {st['round'] + 1}")
        return 0

    return run(go)


# ------------------------------------------------------------------- policy

def policy_main():
    parser = base_parser("The rules in force: thresholds, kinds, verdicts, duties, turn order")
    parser.add_argument("--agent", help="one agent's motivation, phases and duties")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--set", metavar="KEY=VALUE",
                        help="the human, in their turn: change a policy value for this project")
    args = parser.parse_args()

    def go():
        if args.set:
            key, _, value = args.set.partition("=")
            conn, me, turn = begin(args.db, "human", "change policy")
            old = conn.execute("SELECT value FROM policy WHERE key = ?", (key,)).fetchone()
            if old is None:
                # A project started before the plugin added this policy: take it from the defaults.
                known = {p["key"]: p for p in cfg.load(cfg.DEFAULT_CONFIG)["policy"]}
                if key not in known:
                    keys = ", ".join(sorted(set(known) | {r["key"] for r in conn.execute(
                        "SELECT key FROM policy")}))
                    raise Refused(f"no policy '{key}'. Known: {keys}")
                conn.execute("INSERT INTO policy (key, value, description) VALUES (?, ?, ?)",
                             (key, str(known[key]["value"]), known[key]["description"].strip()))
                old = {"value": str(known[key]["value"])}
            conn.execute("UPDATE policy SET value = ? WHERE key = ?", (value, key))
            db.record_event(conn, turn, "project", 1, None, None,
                            f"policy {key}: {old['value']} -> {value}")
            if cfg.PROJECT_CONFIG.exists():
                cfg.set_policy(cfg.PROJECT_CONFIG, key, value)
            finish(conn, turn)
            print(f"{key}: {old['value']} -> {value}")
            return 0
        conn = db.connect(args.db)
        settings = [dict(r) for r in conn.execute("SELECT * FROM policy ORDER BY key")]
        kinds = [dict(r) for r in db.vocab(conn, "statement_kinds")]
        verdicts = [dict(r) for r in db.vocab(conn, "verdicts")]
        concern_kinds = [dict(r) for r in db.vocab(conn, "concern_kinds")]

        if args.agent:
            me = db.agent(conn, args.agent)
            duties = [dict(d) for d in db.duties_of(conn, me["id"])]
            phases = db.phases_of(conn, me["id"])
            if args.json:
                print(json.dumps({"agent": dict(me), "duties": duties, "phases": phases,
                                  "policy": settings, "statement_kinds": kinds,
                                  "concern_kinds": concern_kinds}, indent=2))
                return 0
            print(f"{me['name']} ({me['role']})")
            print(f"  argues for: {me['motivation'].strip()}")
            print(f"  phases:     {', '.join(str(p) for p in phases)}")
            print(f"  approves:   {'every live statement' if me['approves'] else 'nothing'}")
            print(f"  joins at:   phase {me['joins_at_phase']}"
                  + (f" — {me['join_trigger']}" if me["join_trigger"] else ""))
            for duty in duties:
                print(f"  {duty['relation']:9} {duty['duty']} — {duty['description'].strip()}")
            print("\nthresholds")
            for setting in settings:
                print(f"  {setting['key']:22} {setting['value']}")
            print("\nconcern kinds")
            for kind in concern_kinds:
                print(f"  {kind['value']:10} {kind['description'].strip()}")
            return 0

        if args.json:
            print(json.dumps({"policy": settings, "statement_kinds": kinds, "verdicts": verdicts,
                              "concern_kinds": concern_kinds}, indent=2))
            return 0
        print("thresholds")
        for setting in settings:
            print(f"  {setting['key']:22} {setting['value']}  — {setting['description'].strip()}")
        print("\nstatement kinds            level        changed only with   must link")
        for kind in kinds:
            print(f"  {kind['value']:24} {kind['level']:12} {kind['guarded_by'] or '—':19} "
                  f"{kind['requires_link'] or '—'}")
        print("\nverdicts")
        for v in verdicts:
            print(f"  {v['value']:10} {v['description'].strip()}")
        print("\nturn order")
        for a in conn.execute("SELECT role, active, approves FROM agents WHERE id <> ? ORDER BY seq",
                              (HUMAN_ID,)):
            print(f"  {a['role']:12} {'active' if a['active'] else 'not staffed':12} "
                  f"{'approves' if a['approves'] else ''}")
        print("\nduties")
        for row in conn.execute(
                """SELECT d.relation, d.duty, a.role FROM agent_duties d
                     JOIN agents a ON a.id = d.agent_id ORDER BY d.duty, d.relation"""):
            print(f"  {row['duty']:14} {row['relation']:9} {row['role']}")
        return 0

    return run(go)


# ------------------------------------------------------------------- doctor

# A threshold written into prose is a second copy of a policy value.
POLICY_IN_PROSE = re.compile(
    r"\b(two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(rounds|replies)\b", re.I)
EMPHASIS = re.compile(r"[*_`]")
TOOL_NAME = re.compile(r"\bat-[a-z]+\b")




def doctor_main():
    parser = base_parser("Check the config, the charters, the skill and the database for drift")
    parser.add_argument("--config", default=str(cfg.config_path()))
    parser.add_argument("--agents-dir", default=str(cfg.ROOT / "agents"))
    args = parser.parse_args()

    findings = []
    configuration = cfg.load(args.config)
    for problem in cfg.validate(configuration):
        findings.append(("config", problem))

    agents = configuration.get("agents") or []
    charters = {}
    for agent in agents:
        charter = agent.get("charter")
        if not charter:
            if agent.get("id") != cfg.HUMAN_ID:
                findings.append(("charter", f"{agent.get('name')} has no charter file"))
            continue
        path = cfg.ROOT / charter
        if not path.exists():
            findings.append(("charter", f"{agent.get('name')}: {charter} does not exist"))
            continue
        charters[agent["role"]] = path
        text = path.read_text()
        match = re.search(r"^name:\s*(\S+)\s*$", text, re.M)
        if not match:
            findings.append(("charter", f"{charter}: no 'name:' in the frontmatter"))
        elif match.group(1) != agent["role"]:
            findings.append(("charter", f"{charter}: frontmatter name '{match.group(1)}' "
                                        f"is not the role '{agent['role']}'"))
        if f"at-policy --agent {agent['role']}" not in text:
            findings.append(("charter", f"{charter}: does not tell the agent to read its "
                                        f"motivation and duties from at-policy --agent "
                                        f"{agent['role']}"))

    roster_paths = {p.name for p in charters.values()}
    for path in sorted(Path(args.agents_dir).glob("*.md")):
        if path.name not in roster_paths:
            findings.append(("charter", f"{path}: no agent in pipeline.yaml points to it"))

    docs = list(Path(args.agents_dir).glob("*.md"))
    for folder in ("skills", "reference"):
        if (cfg.ROOT / folder).exists():
            docs += list((cfg.ROOT / folder).rglob("*.md"))
    tools = cfg.tools()
    for path in docs:
        for line in path.read_text().splitlines():
            if POLICY_IN_PROSE.search(EMPHASIS.sub("", line)) and "at-policy" not in line:
                findings.append(("policy", f"{path.relative_to(cfg.ROOT)}: a threshold in prose "
                                           f"— read it from at-policy instead: {line.strip()[:70]}"))
            for name in sorted(set(TOOL_NAME.findall(line)) - tools):
                findings.append(("tools", f"{path.relative_to(cfg.ROOT)}: mentions {name}, "
                                          "which is not a registered tool"))

    duties = {d["value"] for d in configuration.get("duties") or []}
    held = {duty for agent in agents for relation in cfg.DUTY_RELATIONS
            for duty in agent.get(relation) or []}
    for duty in sorted(duties - held):
        findings.append(("duties", f"'{duty}' is declared but no agent holds it"))

    path = Path(args.db)
    if path.exists():
        conn = db.connect(str(path))
        for name in cfg.VOCABULARIES:
            try:
                in_db = {r["value"] for r in db.vocab(conn, name)}
            except Exception:
                findings.append(("database", f"{path} predates this schema — rebuild it"))
                break
            in_yaml = {e["value"] for e in configuration["vocabularies"][name]}
            for value in sorted(in_yaml - in_db):
                findings.append(("database", f"{name}: '{value}' is in pipeline.yaml but not in "
                                             f"{path} — rebuild it"))
            for value in sorted(in_db - in_yaml):
                findings.append(("database", f"{name}: '{value}' is in {path} but not in "
                                             "pipeline.yaml"))
        in_db = {(r["role"], r["duty"], r["relation"]) for r in conn.execute(
            "SELECT a.role, d.duty, d.relation FROM agent_duties d JOIN agents a ON a.id = d.agent_id")}
        in_yaml = {(a["role"], duty, relation) for a in agents for relation in cfg.DUTY_RELATIONS
                   for duty in a.get(relation) or []}
        for role, duty, relation in sorted(in_yaml ^ in_db):
            where = "pipeline.yaml" if (role, duty, relation) in in_yaml else str(path)
            findings.append(("database", f"{role} {relation} {duty} is only in {where} "
                                         "— the database predates the config"))

    if not findings:
        print("no drift found")
        return 0
    print(f"{len(findings)} finding(s):")
    for area, text in findings:
        print(f"  [{area}] {text}")
    return 1


# ------------------------------------------------------------------- render

def answer_that_closed(conn, concern_id):
    return conn.execute(
        """SELECT w.body, g.name FROM answers w JOIN agents g ON g.id = w.answered_by
            JOIN verdicts v ON v.value = w.verdict
            WHERE w.concern_id = ? AND v.closes = 1 ORDER BY w.id DESC LIMIT 1""",
        (concern_id,)).fetchone()


def render_milestone(conn, mid):
    """milestone-N.md: everything the developer builds from and QA tests against."""
    st = db.state(conn)
    m = db.milestone(conn, mid)
    d = db.deliverable(conn, m["deliverable_id"])
    inside = conn.execute("SELECT * FROM statements WHERE milestone_id = ? AND status IN "
                          "('pending', 'agreed') ORDER BY id", (m["id"],)).fetchall()
    in_force = db.context(conn)
    lines = [f"# Milestone M-{m['id']}: {m['name']}", "",
             f"*Generated from the project database — round {st['round']}, change mark "
             f"{st['change_mark']}. Regenerate it rather than editing it.*", "",
             f"- **Deliverable:** {d['name']} (D-{d['id']})",
             f"- **Status:** {m['status']}" + (f", branch `{m['branch']}`" if m["branch"] else ""),
             ""]
    if m["intent"]:
        lines += [m["intent"].strip(), ""]
    blocked = [s for s in inside if s["status"] != "agreed"]
    if blocked:
        lines += ["> **Blocked.** " + ", ".join(f"S-{s['id']}" for s in blocked)
                  + " is not agreed. Build nothing that depends on it until it is.", ""]

    lines += ["## What to build", "",
              "Each statement is one behaviour to implement and one acceptance check to pass.", ""]
    for s in inside:
        flag = "" if s["status"] == "agreed" else f" *({s['status']} — do not build yet)*"
        lines.append(f"### S-{s['id']} ({s['kind']}){flag}")
        lines += ["", s["text"].strip(), ""]
        if s["rationale"]:
            lines += [f"*Why:* {s['rationale'].strip()}", ""]
        # Its own reasons, then those of every statement it replaced, oldest last.
        chain, frontier = [s["id"]], [s["id"]]
        while frontier:
            frontier = [r["to_id"] for sid in frontier for r in conn.execute(
                "SELECT to_id FROM links WHERE from_id = ? AND relation = 'supersedes'", (sid,))]
            chain += [f for f in frontier if f not in chain]
        seen = set()
        for sid in chain:
            for c in conn.execute(
                    """SELECT c.* FROM statement_reasons r JOIN concerns c ON c.id = r.concern_id
                        WHERE r.statement_id = ? ORDER BY c.id""", (sid,)):
                if c["id"] in seen:
                    continue
                seen.add(c["id"])
                via = "" if sid == s["id"] else f" (when S-{sid} was written)"
                closed = answer_that_closed(conn, c["id"])
                lines.append(f"- From C-{c['id']} ({c['kind']}){via}: {c['body'].strip()}")
                if closed:
                    lines.append(f"  - {closed['name']}: {closed['body'].strip()}")
        if seen:
            lines.append("")
    if not inside:
        lines += ["*No statements assigned yet.*", ""]

    scope = db.scope_of(conn, d["id"])
    lines += ["## Context", ""]
    for s in scope:
        lines.append(f"- **Scope of {d['name']}** (S-{s['id']}): {s['text'].strip()}")
    for kind, label in (("goal", "Goal"), ("non_goal", "Not a goal"),
                        ("success_criterion", "Success criterion")):
        for s in in_force:
            if s["kind"] == kind:
                lines.append(f"- **{label}** (S-{s['id']}): {s['text'].strip()}")
    lines.append("")

    invariants = [s for s in in_force if s["kind"] == "invariant"]
    lines += ["## Invariants to honour", "",
              "Rules the code must keep true everywhere. Breaking one to make this milestone "
              "simpler is never acceptable; raise a concern instead.", ""]
    lines += [f"- **S-{s['id']}** {s['text'].strip()}" for s in invariants] or ["*None yet.*"]
    lines.append("")

    others = conn.execute(
        """SELECT * FROM statements WHERE deliverable_id = ? AND status IN ('pending', 'agreed')
             AND kind <> 'scope' AND (milestone_id IS NULL OR milestone_id <> ?) ORDER BY id""",
        (d["id"], m["id"])).fetchall()
    lines += ["## Not in this milestone", "",
              "Belongs to the same deliverable but is built elsewhere. If this milestone needs "
              "any of it, that is a concern, not a licence.", ""]
    for s in others:
        where = f"M-{s['milestone_id']}" if s["milestone_id"] else "not yet in a milestone"
        lines.append(f"- **S-{s['id']}** ({where}) {s['text'].strip()}")
    if not others:
        lines.append("*Nothing.*")
    lines.append("")

    findings = conn.execute("SELECT * FROM concerns WHERE milestone_id = ? AND status = 'open' "
                            "ORDER BY id", (m["id"],)).fetchall()
    if findings:
        lines += ["## Open concerns on this milestone", ""]
        for c in findings:
            raiser = db.agent_by_id(conn, c["raised_by"])
            lines.append(f"- **C-{c['id']}** ({c['kind']}, from {raiser['role']}) "
                         f"{c['body'].strip()}")
        lines.append("")

    lines += ["## Done when", "",
              f"- every statement above passes an automated acceptance test written by QA",
              f"- the invariants above still hold",
              f"- QA and code review have passed it and no concern on it is open",
              f"- the human has tried it and accepted it", ""]
    return "\n".join(lines)


def render_main():
    parser = base_parser("Generate a document from the tables")
    parser.add_argument("document", choices=["requirements", "milestone"])
    parser.add_argument("--milestone", help="M-3, for the milestone document")
    parser.add_argument("--out", help="write to this file instead of stdout")
    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        if args.document == "milestone":
            if not args.milestone:
                raise Refused("which milestone: --milestone M-3")
            text = db.annotate(conn, render_milestone(conn, db.parse_ref(args.milestone, "M")[1]))
            if args.out:
                Path(args.out).write_text(text)
                print(f"wrote {args.out}")
            else:
                print(text, end="")
            return 0
        st = db.state(conn)
        live = ("pending", "agreed")
        mark = lambda s: "" if s["status"] == "agreed" else f" *({s['status']})*"
        lines = ["# Requirements", "",
                 f"*Generated from the project database — round {st['round']}, "
                 f"change mark {st['change_mark']}. Unmarked statements are agreed.*", ""]

        kinds = db.vocab(conn, "statement_kinds")
        sections = []
        for kind in kinds:
            if kind["level"] == "project" and kind["document_section"] not in sections:
                sections.append(kind["document_section"])
        measured = {}
        for link in conn.execute("SELECT * FROM links WHERE relation = 'measures'"):
            measured.setdefault(link["to_id"], []).append(link["from_id"])
        nested = {i for ids in measured.values() for i in ids}

        for section in sections:
            rows = conn.execute(
                """SELECT s.* FROM statements s JOIN statement_kinds k ON k.value = s.kind
                    WHERE k.level = 'project' AND k.document_section = ? AND s.status IN (?, ?)
                    ORDER BY k.seq, s.id""", (section, *live)).fetchall()
            rows = [r for r in rows if r["id"] not in nested]
            if not rows:
                continue
            lines += [f"## {section}", ""]
            for r in rows:
                label = "" if r["kind"] in ("goal", "invariant", "brief") else f"({r['kind']}) "
                lines.append(f"- **S-{r['id']}** {label}{r['text'].strip()}{mark(r)}")
                for cid in measured.get(r["id"], []):
                    c = db.statement(conn, cid)
                    if c["status"] in live:
                        lines.append(f"  - measured by **S-{c['id']}**: {c['text'].strip()}{mark(c)}")
            lines.append("")

        for d in conn.execute("SELECT * FROM deliverables WHERE status = 'live' ORDER BY seq"):
            lines += [f"## Deliverable {d['name']} (D-{d['id']})", ""]
            rows = conn.execute(
                """SELECT s.* FROM statements s JOIN statement_kinds k ON k.value = s.kind
                    WHERE s.deliverable_id = ? AND s.status IN (?, ?) ORDER BY k.seq, s.id""",
                (d["id"], *live)).fetchall()
            for r in rows:
                label = "Scope: " if r["kind"] == "scope" else f"({r['kind']}) "
                lines.append(f"- **S-{r['id']}** {label}{r['text'].strip()}{mark(r)}")
            if not rows:
                lines.append("*No statements yet.*")
            lines.append("")

        kept = conn.execute("SELECT * FROM concerns WHERE acted_on = 'kept' ORDER BY id").fetchall()
        lines += ["## Kept as is", ""]
        for c in kept:
            answer = conn.execute(
                """SELECT w.body, g.name FROM answers w JOIN agents g ON g.id = w.answered_by
                    WHERE w.concern_id = ? AND w.verdict IN ('accepted', 'final')
                    ORDER BY w.id DESC LIMIT 1""", (c["id"],)).fetchone()
            lines.append(f"- **C-{c['id']}** ({c['kind']}, on {db.about(conn, c)}) {c['body'].strip()}")
            if answer:
                lines.append(f"  - {answer['name']}: {answer['body'].strip()}")
        if not kept:
            lines.append("*Nothing yet.*")
        lines.append("")

        retired = conn.execute("SELECT * FROM statements WHERE status NOT IN (?, ?) ORDER BY id",
                               live).fetchall()
        lines += ["## History", ""]
        for r in retired:
            by = [row["from_id"] for row in conn.execute(
                "SELECT from_id FROM links WHERE to_id = ? AND relation = 'supersedes'", (r["id"],))]
            reasons = [row["concern_id"] for row in conn.execute(
                "SELECT concern_id FROM statement_reasons WHERE statement_id = ?", (r["id"],))]
            fate = ("superseded by " + ", ".join(f"S-{b}" for b in by)) if by else r["status"]
            why = sorted({row["concern_id"] for b in by for row in conn.execute(
                "SELECT concern_id FROM statement_reasons WHERE statement_id = ?", (b,))})
            if not by:
                why = sorted({e["detail"] for e in conn.execute(
                    "SELECT detail FROM events WHERE object = 'statement' AND object_id = ? "
                    "AND to_status = ?", (r["id"], r["status"])) if e["detail"]})
            because = (" because " + ", ".join(f"C-{c}" if isinstance(c, int) else c for c in why)
                       if why else "")
            lines.append(f"- **S-{r['id']}** ({r['kind']}) {r['text'].strip()} — {fate}{because}")
            if reasons:
                lines.append("  - it came from " + ", ".join(f"C-{c}" for c in reasons))
        if not retired:
            lines.append("*Nothing yet.*")

        text = db.annotate(conn, "\n".join(lines) + "\n")
        if args.out:
            Path(args.out).write_text(text)
            print(f"wrote {args.out}")
        else:
            print(text, end="")
        return 0

    return run(go)
