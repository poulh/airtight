"""The turn tools.

Agents never write SQL, and no rule is written here. Every check below reads
its rule from the database, which cp-init seeded from pipeline.yaml: which role
may rule on a kind of requirement, what an answer must carry, who owns a duty,
when a thread has stalled. Changing a rule means editing the YAML and
re-seeding, never editing Python.
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
    parser.add_argument("--db", default=db.default_db(), help="project database (or $CP_DB)")
    return parser


def run(fn):
    """Turn a Refused into a clean exit code instead of a traceback."""
    try:
        return fn()
    except Refused as exc:
        return db.fail(exc)


def ids(text):
    if not text:
        return []
    return [int(part.strip().lstrip("Rr-")) for part in text.split(",") if part.strip()]


def role_of(conn, agent_id):
    return conn.execute("SELECT role FROM agents WHERE id = ?", (agent_id,)).fetchone()["role"]


# --------------------------------------------------------------------- init

def init_main():
    parser = base_parser("Build a project database from schema.sql and pipeline.yaml")
    parser.add_argument("--config", default=str(cfg.DEFAULT_CONFIG))
    parser.add_argument("--schema", default=str(cfg.DEFAULT_SCHEMA))
    parser.add_argument("--force", action="store_true", help="replace an existing database")
    parser.add_argument("--check", action="store_true", help="validate the config and stop")
    args = parser.parse_args()

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

    path = Path(args.db)
    if path.exists():
        if not args.force:
            print(f"{path} already exists (use --force to replace it)", file=sys.stderr)
            return 1
        path.unlink()

    conn = db.connect(str(path), must_exist=False)
    conn.executescript(Path(args.schema).read_text())

    conn.executemany(
        "INSERT INTO phases (number, key, name, ends_when) VALUES (?, ?, ?, ?)",
        [(p["number"], p["key"], p["name"], p["ends_when"].strip())
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
        placeholders = ", ".join("?" * len(columns))
        conn.executemany(
            f"INSERT INTO {name} ({', '.join(columns)}) VALUES ({placeholders})", rows)
        total_values += len(rows)

    for agent in configuration["agents"]:
        conn.execute(
            """INSERT INTO agents (id, name, role, motivation, charter, joins_at_phase,
                                   join_trigger, join_rationale, auto_staff, active, joined_round)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (agent["id"], agent["name"], agent["role"], agent["motivation"].strip(),
             agent.get("charter"), agent["joins_at_phase"],
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

    conn.execute(
        "INSERT INTO project_state (id, round, phase, change_mark, updated_at) VALUES (1,1,1,0,?)",
        (db.now(),),
    )
    conn.commit()

    active = [r["name"] for r in conn.execute("SELECT name FROM agents WHERE active=1 ORDER BY id")]
    print(f"created {path}")
    print(f"  phases:       {len(configuration['phases'])}")
    print(f"  policy:       {len(configuration['policy'])} settings")
    print(f"  vocabularies: {total_values} values across {len(cfg.VOCABULARIES)} tables")
    print(f"  agents:       {len(configuration['agents'])} seeded, {len(active)} active")
    for name in active:
        print(f"                - {name}")
    print("  state:        round 1, phase 1")
    return 0


# -------------------------------------------------------------------- queue

def queue_main():
    parser = base_parser("What this agent has to deal with on its turn")
    parser.add_argument("--agent", required=True, help="role, e.g. pm")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--mark-seen", action="store_true", help="record that changes were read")
    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        me = db.require_active(conn, db.agent(conn, args.agent), "take a turn")
        st = db.state(conn)
        mail = db.outstanding(conn, me)
        reviews = db.to_review(conn, me)
        changes = db.changes_since(conn, me["last_seen_change"])
        duties = db.duties_of(conn, me["id"])

        if args.json:
            print(json.dumps({
                "round": st["round"], "phase": st["phase"], "phase_name": st["phase_name"],
                "agent": {"id": me["id"], "name": me["name"], "role": me["role"],
                          "motivation": me["motivation"],
                          "phases": db.phases_of(conn, me["id"]),
                          "duties": [{"duty": d["duty"], "relation": d["relation"]} for d in duties]},
                "concerns_to_answer": [dict(r) for r in mail],
                "answers_to_review": [dict(r, names=[dict(n) for n in db.named_by(conn, r["id"])])
                                      for r in reviews],
                "requirement_changes": [dict(r) for r in changes],
                "concern_kinds": {r["value"]: r["description"] for r in db.vocab(conn, "concern_kinds")},
                "answer_kinds": {r["value"]: r["description"] for r in db.vocab(conn, "answer_kinds")},
                "requirement_kinds": {r["value"]: r["description"]
                                      for r in db.vocab(conn, "requirement_kinds")},
            }, indent=2))
        else:
            print(f"{me['name']} — round {st['round']}, phase {st['phase']} ({st['phase_name']})")
            print(f"  you argue for: {me['motivation'].strip()}")
            if duties:
                print("  your duties:   " + ", ".join(f"{d['relation']} {d['duty']}" for d in duties))
            print(f"\nCONCERNS ADDRESSED TO YOU ({len(mail)})")
            for row in mail:
                about = (f", about R-{row['about_requirement_id']}" if row["about_requirement_id"]
                         else f", about C-{row['about_concern_id']}" if row["about_concern_id"]
                         else "")
                print(f"  C-{row['id']} [{row['kind']}] from {row['raiser_name']} "
                      f"(round {row['round']}{about})")
                print(f"       {row['body'].strip()}")
            print(f"\nANSWERS AWAITING YOUR REVIEW ({len(reviews)})")
            for row in reviews:
                print(f"  A-{row['id']} on C-{row['concern_id']} from {row['answerer']} [{row['kind']}]")
                print(f"       {row['body'].strip()}")
                for named in db.named_by(conn, row["id"]):
                    print(f"       names R-{named['id']} [{named['kind']}, {named['status']}]: "
                          f"{named['statement'].strip()}")
            print(f"\nREQUIREMENT CHANGES SINCE YOU LAST LOOKED ({len(changes)})")
            for row in changes:
                arrow = f"{row['from_status'] or 'new'} -> {row['to_status']}"
                print(f"  R-{row['requirement_id']} [{row['kind']}] {arrow} by {row['actor_name']}")
                print(f"       {row['statement'].strip()}")

        if args.mark_seen:
            conn.execute("UPDATE agents SET last_seen_change = ? WHERE id = ?",
                         (db.state(conn)["change_mark"], me["id"]))
            conn.commit()
        return 0

    return run(go)


# ------------------------------------------------------------------ concern

def concern_main():
    parser = base_parser("Raise a concern with another agent")
    parser.add_argument("--from", dest="sender", required=True)
    parser.add_argument("--to", dest="recipient", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--group", type=int, help="same concern sent to several agents")
    parser.add_argument("--about", help="what prompted this: R-12 (a requirement) or C-3 (a concern)")
    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        me = db.require_active(conn, db.agent(conn, args.sender), "raise a concern")
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
        about_req, about_concern = db.about(conn, args.about, kind["about"], kind["value"])

        round_no = db.state(conn)["round"]
        cur = conn.execute(
            """INSERT INTO concerns (kind, raised_by, addressed_to, body, about_requirement_id,
                                     about_concern_id, raised_group, round)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (kind["value"], me["id"], them["id"], args.body, about_req, about_concern,
             args.group, round_no),
        )
        conn.commit()
        about_text = (f" about R-{about_req}" if about_req
                      else f" about C-{about_concern}" if about_concern else "")
        print(f"C-{cur.lastrowid} raised by {me['name']} to {them['name']} "
              f"[{kind['value']}]{about_text}")
        if them["id"] == HUMAN_ID:
            print("the loop now pauses: this is addressed to the human")
        return 0

    return run(go)


# ------------------------------------------------------------------- answer

def answer_main():
    parser = base_parser("Answer a concern addressed to you")
    parser.add_argument("--concern", type=int, required=True)
    parser.add_argument("--from", dest="sender", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--body", required=True)
    parser.add_argument("--requirements", help="ids this answer created or changed, e.g. 12,14")
    parser.add_argument("--reason", help="why nothing is changing")
    parser.add_argument("--to", dest="recipient", help="who it goes to when escalating")
    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        me = db.require_active(conn, db.agent(conn, args.sender), "answer")
        row = db.concern(conn, args.concern)
        kind = db.term(conn, "answer_kinds", args.kind, "answer kind")

        if row["addressed_to"] != me["id"]:
            owner = conn.execute("SELECT name FROM agents WHERE id=?", (row["addressed_to"],)).fetchone()
            raise Refused(f"C-{row['id']} is addressed to {owner['name']}, not you")
        if row["status"] not in ("open", "escalated"):
            raise Refused(f"C-{row['id']} is {row['status']}")
        waiting = db.awaiting_verdict(conn, row["id"])
        if waiting:
            raiser = conn.execute("SELECT name FROM agents WHERE id=?", (row["raised_by"],)).fetchone()
            raise Refused(f"A-{waiting['id']} is still with {raiser['name']} for a verdict — "
                          "one answer at a time; wait for their yes or their reply")

        req_ids = ids(args.requirements)
        needs = kind["requires"]
        if needs == "requirements" and not req_ids:
            raise Refused(f"an answer of kind '{kind['value']}' must name the requirements it "
                          "created or changed: --requirements 12,14 (use cp-propose first)")
        if needs == "reason" and not args.reason:
            raise Refused(f"an answer of kind '{kind['value']}' must say why: --reason '...'"
                          + (f" (it is published under {kind['document_section']})"
                             if kind["document_section"] else ""))
        if needs == "recipient" and not args.recipient:
            raise Refused(f"an answer of kind '{kind['value']}' must name who it goes to: --to pm|human")
        for req_id in req_ids:
            db.requirement(conn, req_id)

        body = args.body if not args.reason else f"{args.body}\n\nReason: {args.reason}"
        round_no = db.state(conn)["round"]
        cur = conn.execute(
            "INSERT INTO answers (concern_id, answered_by, kind, body, round) VALUES (?,?,?,?,?)",
            (row["id"], me["id"], kind["value"], body, round_no),
        )
        for req_id in req_ids:
            conn.execute("INSERT INTO answer_requirements (answer_id, requirement_id) VALUES (?,?)",
                         (cur.lastrowid, req_id))
            conn.execute(
                "INSERT OR IGNORE INTO requirement_concerns (requirement_id, concern_id, relation) "
                "VALUES (?,?,?)", (req_id, row["id"], "origin"),
            )

        if needs == "recipient":
            them = db.require_active(conn, db.agent(conn, args.recipient), "be escalated to")
            conn.execute("UPDATE concerns SET addressed_to = ?, status = 'open' WHERE id = ?",
                         (them["id"], row["id"]))
            print(f"A-{cur.lastrowid} escalates C-{row['id']} to {them['name']}")
            if them["id"] == HUMAN_ID:
                print("the loop now pauses: this is addressed to the human")
        else:
            raiser = conn.execute("SELECT name FROM agents WHERE id=?", (row["raised_by"],)).fetchone()
            print(f"A-{cur.lastrowid} answers C-{row['id']} [{kind['value']}] — "
                  f"awaiting {raiser['name']}'s review")
        conn.commit()
        return 0

    return run(go)


# ------------------------------------------------------------------- review

def review_main():
    parser = base_parser("Judge an answer to a concern you raised")
    parser.add_argument("--answer", type=int, required=True)
    parser.add_argument("--by", required=True)
    parser.add_argument("--satisfied", required=True, choices=["yes", "no"])
    parser.add_argument("--reply", help="required when not satisfied")
    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        me = db.require_active(conn, db.agent(conn, args.by), "review an answer")
        row = conn.execute("SELECT * FROM answers WHERE id = ?", (args.answer,)).fetchone()
        if row is None:
            raise Refused(f"no answer A-{args.answer}")
        parent = db.concern(conn, row["concern_id"])
        if parent["raised_by"] != me["id"]:
            raise Refused(f"C-{parent['id']} is not yours to judge")
        if row["satisfied"] is not None:
            raise Refused(f"A-{row['id']} has already been judged")
        waiting = db.awaiting_verdict(conn, parent["id"])
        if waiting is None or waiting["id"] != row["id"]:
            raise Refused(f"A-{row['id']} is a hand-off, not an answer to judge — "
                          f"C-{parent['id']} is waiting on its new addressee"
                          if waiting is None else
                          f"judge the latest answer, A-{waiting['id']}")

        satisfied = args.satisfied == "yes"
        if not satisfied and not args.reply:
            raise Refused("say what is still missing: --reply '...'")

        round_no = db.state(conn)["round"]
        conn.execute("UPDATE answers SET satisfied = ?, reply = ?, reply_round = ? WHERE id = ?",
                     (1 if satisfied else 0, args.reply, None if satisfied else round_no, row["id"]))
        if satisfied:
            conn.execute("UPDATE concerns SET status='resolved', resolved_round=? WHERE id=?",
                         (round_no, parent["id"]))
            print(f"A-{row['id']} accepted; C-{parent['id']} resolved")
        else:
            answerer = conn.execute("SELECT name FROM agents WHERE id=?", (row["answered_by"],)).fetchone()
            print(f"A-{row['id']} sent back to {answerer['name']}; C-{parent['id']} stays open")
        conn.commit()
        return 0

    return run(go)


# ------------------------------------------------------------------ propose

def propose_main():
    parser = base_parser("Propose a requirement")
    parser.add_argument("--from", dest="sender", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--statement", required=True, help="one testable sentence")
    parser.add_argument("--rationale")
    parser.add_argument("--concern", type=int, help="the concern this came out of")
    parser.add_argument("--cost", help="cost flag, if your role may set one")
    parser.add_argument("--supersedes", type=int, help="the requirement this rewords")
    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        me = db.require_active(conn, db.agent(conn, args.sender), "propose")
        kind = db.term(conn, "requirement_kinds", args.kind, "requirement kind")
        if args.cost:
            flag = db.term(conn, "cost_flags", args.cost, "cost flag")
            if flag["set_by"] and me["role"] not in (flag["set_by"], "human"):
                raise Refused(f"only the {flag['set_by']} sets cost flags")
        if args.concern:
            db.concern(conn, args.concern)

        round_no = db.state(conn)["round"]
        old = db.requirement(conn, args.supersedes) if args.supersedes else None
        if old is not None and old["status"] == "superseded":
            raise Refused(f"R-{old['id']} is already superseded; reword the requirement that "
                          "replaced it")
        cur = conn.execute(
            """INSERT INTO requirements (kind, statement, rationale, proposed_by, status,
                                         cost_flag, created_round, supersedes_id)
               VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?)""",
            (kind["value"], args.statement, args.rationale, me["id"], args.cost, round_no,
             args.supersedes),
        )
        new_id = cur.lastrowid
        if args.concern:
            conn.execute(
                "INSERT OR IGNORE INTO requirement_concerns (requirement_id, concern_id, relation) "
                "VALUES (?,?,?)", (new_id, args.concern, "origin"),
            )
        db.record_event(conn, new_id, me["id"], None, "proposed", args.rationale, args.concern)
        conn.commit()

        print(f"R-{new_id} [{kind['value']}] proposed by {me['name']}")
        print(f"the {kind['decided_by']} rules on a {kind['value']} requirement")
        if old is not None:
            print(f"R-{old['id']} stays {old['status']} until R-{new_id} is accepted")
        return 0

    return run(go)


# ------------------------------------------------------------------- decide

def decide_main():
    parser = base_parser("Rule on a requirement")
    parser.add_argument("--requirement", type=int, required=True)
    parser.add_argument("--by", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--reason", help="required for anything that is not accepted")
    parser.add_argument("--deliverable", type=int)
    parser.add_argument("--concern", type=int, help="the concern that prompted this")
    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        me = db.require_active(conn, db.agent(conn, args.by), "decide")
        row = db.requirement(conn, args.requirement)
        status = db.term(conn, "requirement_statuses", args.status, "status")
        kind = db.term(conn, "requirement_kinds", row["kind"], "requirement kind")

        if status["value"] in ("proposed", "superseded"):
            raise Refused("decide on accepted, deferred or rejected "
                          "(superseded happens through cp-propose --supersedes)")
        if status["value"] != "accepted" and not args.reason:
            raise Refused(f"say why it is {status['value']}: --reason '...' — it is shown under "
                          f"{status['document_section'] or 'the document'}")

        human = me["id"] == HUMAN_ID
        if not human:
            if me["role"] != kind["decided_by"]:
                raise Refused(f"only the {kind['decided_by']} or the human rules on a "
                              f"{kind['value']} requirement")
            if db.policy(conn, "human_cuts_own_requests", 1) and status["value"] != "accepted":
                # The interview runs through the PM's questions, so most of what the human
                # wants arrives as their answers, not as concerns they raised.
                asked_by_human = row["proposed_by"] == HUMAN_ID or conn.execute(
                    """SELECT 1 FROM requirement_concerns rc
                         JOIN concerns c ON c.id = rc.concern_id
                        WHERE rc.requirement_id = ? AND rc.relation = 'origin'
                          AND (c.raised_by = ?
                               OR EXISTS (SELECT 1 FROM answers w
                                           WHERE w.concern_id = c.id AND w.answered_by = ?))""",
                    (row["id"], HUMAN_ID, HUMAN_ID),
                ).fetchone() is not None
                if asked_by_human:
                    raise Refused("cutting something the human asked for is the human's call — "
                                  "raise it with them instead (cp-concern --to human)")

        if status["value"] == "accepted":
            if db.policy(conn, "accept_needs_concerns_closed", 1):
                still_open = db.open_concerns_on(conn, row["id"])
                if still_open:
                    raise Refused(
                        f"R-{row['id']} still has open concern(s): "
                        + ", ".join(f"C-{c['id']}" for c in still_open)
                        + " — the raiser has to be satisfied first")
            if db.policy(conn, "accept_needs_all_seen", 1):
                unseen = db.unseen_by(conn, row, me["id"])
                if unseen:
                    raise Refused(
                        f"R-{row['id']} has not been read yet by: "
                        + ", ".join(a["role"] for a in unseen)
                        + " — each gets a turn to raise a concern first")

        round_no = db.state(conn)["round"]
        conn.execute(
            """UPDATE requirements SET status=?, decided_by=?, decided_round=?, updated_round=?,
                      deliverable_id=COALESCE(?, deliverable_id) WHERE id=?""",
            (status["value"], me["id"], round_no, round_no, args.deliverable, row["id"]),
        )
        db.record_event(conn, row["id"], me["id"], row["status"], status["value"],
                        args.reason, args.concern)
        # A rewording replaces the original only once it is itself accepted.
        old = db.requirement(conn, row["supersedes_id"]) if row["supersedes_id"] else None
        if status["value"] == "accepted" and old is not None and old["status"] != "superseded":
            conn.execute("UPDATE requirements SET status='superseded', updated_round=? WHERE id=?",
                         (round_no, old["id"]))
            db.record_event(conn, old["id"], me["id"], old["status"], "superseded",
                            f"replaced by R-{row['id']}", args.concern)
        conn.commit()
        print(f"R-{row['id']} {row['status']} -> {status['value']} by {me['name']}")
        if status["value"] == "accepted" and old is not None and old["status"] != "superseded":
            print(f"R-{old['id']} {old['status']} -> superseded (replaced by R-{row['id']})")
        return 0

    return run(go)


# -------------------------------------------------------------------- staff

def staff_main():
    parser = base_parser("Ask the human to bring another agent into the project")
    sub = parser.add_subparsers(dest="action", required=True)

    request = sub.add_parser("request", help="ask for an agent")
    request.add_argument("--agent", required=True)
    request.add_argument("--by", required=True)
    request.add_argument("--reason", required=True, help="what triggered it")
    request.add_argument("--cost", help="what it will cost in constraints, rounds, money")
    request.add_argument("--about", help="the requirement or concern that triggered it: R-12 or C-3")

    approve = sub.add_parser("approve", help="bring them in")
    approve.add_argument("--agent", required=True)
    approve.add_argument("--concern", type=int)
    approve.add_argument("--by", default="human")

    decline = sub.add_parser("decline", help="do not bring them in")
    decline.add_argument("--agent", required=True)
    decline.add_argument("--concern", type=int, required=True)
    decline.add_argument("--reason", required=True)
    decline.add_argument("--by", default="human")

    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        target = db.agent(conn, args.agent)
        st = db.state(conn)
        round_no = st["round"]

        if args.action == "request":
            me = db.require_active(conn, db.agent(conn, args.by), "request staffing")
            if target["active"]:
                raise Refused(f"{target['name']} is already in the project")
            if target["joins_at_phase"] > st["phase"]:
                raise Refused(f"{target['name']} joins at phase {target['joins_at_phase']}; "
                              f"this project is in phase {st['phase']}")
            kind = db.term(conn, "concern_kinds", "staffing", "concern kind")
            about_req, about_concern = db.about(conn, args.about, kind["about"], kind["value"])
            decider = db.who(conn, "staffing", "rules_on")
            if not decider:
                raise Refused("nobody in this roster rules on staffing")
            body = (
                f"Bring in {target['name']}?\n"
                f"  their brief: {target['motivation'].strip()}\n"
                f"  trigger:     {args.reason}\n"
                f"  joins when:  {target['join_trigger'] or 'n/a'}\n"
                f"  cost:        {args.cost or 'more constraints, more rounds, more spend'}\n"
                f"  alternatives: defer or drop the requirement that triggered this."
            )
            if target["auto_staff"]:
                conn.execute(
                    "UPDATE agents SET active=1, joined_round=?, requested_by=? WHERE id=?",
                    (round_no, me["id"], target["id"]),
                )
                conn.commit()
                print(f"{target['name']} joined in round {round_no} (auto_staff)")
                return 0
            cur = conn.execute(
                """INSERT INTO concerns (kind, raised_by, addressed_to, body, about_requirement_id,
                                         about_concern_id, round) VALUES (?,?,?,?,?,?,?)""",
                (kind["value"], me["id"], decider[0]["id"], body, about_req, about_concern,
                 round_no),
            )
            conn.commit()
            print(f"C-{cur.lastrowid} staffing request for {target['name']} -> {decider[0]['name']}")
            if decider[0]["id"] == HUMAN_ID:
                print("the loop now pauses: this is addressed to the human")
            return 0

        me = db.agent(conn, args.by)
        db.require_duty(conn, me, "staffing", "rules_on", "approve or decline an agent")
        parent = db.concern(conn, args.concern) if args.concern else None

        if args.action == "approve":
            if target["active"]:
                raise Refused(f"{target['name']} is already in the project")
            conn.execute(
                "UPDATE agents SET active=1, joined_round=?, requested_by=? WHERE id=?",
                (round_no, parent["raised_by"] if parent else me["id"], target["id"]),
            )
            if parent:
                conn.execute(
                    "INSERT INTO answers (concern_id, answered_by, kind, body, satisfied, round) "
                    "VALUES (?,?,'accepted',?,1,?)",
                    (parent["id"], me["id"],
                     f"Approved. {target['name']} joins in round {round_no}.", round_no),
                )
                conn.execute("UPDATE concerns SET status='resolved', resolved_round=? WHERE id=?",
                             (round_no, parent["id"]))
            conn.commit()
            print(f"{target['name']} joined in round {round_no}")
            return 0

        conn.execute(
            "INSERT INTO answers (concern_id, answered_by, kind, body, satisfied, round) "
            "VALUES (?,?,'rejected',?,1,?)",
            (parent["id"], me["id"], f"Declined. Reason: {args.reason}", round_no),
        )
        conn.execute("UPDATE concerns SET status='resolved', resolved_round=? WHERE id=?",
                     (round_no, parent["id"]))
        conn.commit()
        print(f"{target['name']} stays out. C-{parent['id']} resolved")
        return 0

    return run(go)


# -------------------------------------------------------- deliverables

def deliverable_main():
    parser = base_parser("Propose or rule on a deliverable (v1, v2, ...)")
    sub = parser.add_subparsers(dest="action", required=True)

    propose = sub.add_parser("propose")
    propose.add_argument("--from", dest="sender", required=True)
    propose.add_argument("--name", required=True)
    propose.add_argument("--intent", required=True, help="who it is useful to, and for what")
    propose.add_argument("--seq", type=int)

    decide = sub.add_parser("decide")
    decide.add_argument("--id", type=int, required=True)
    decide.add_argument("--by", required=True)
    decide.add_argument("--status", required=True)

    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        round_no = db.state(conn)["round"]
        if args.action == "propose":
            me = db.require_active(conn, db.agent(conn, args.sender), "propose a deliverable")
            db.require_duty(conn, me, "deliverables", "owns", "propose a deliverable")
            db.require_phase(conn, me, "propose a deliverable")
            seq = args.seq or (conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM deliverables").fetchone()["n"])
            cur = conn.execute(
                "INSERT INTO deliverables (name, seq, intent, proposed_by) VALUES (?,?,?,?)",
                (args.name, seq, args.intent, me["id"]),
            )
            conn.commit()
            rulers = db.who(conn, "deliverables", "rules_on")
            print(f"D-{cur.lastrowid} '{args.name}' proposed by {me['name']} (seq {seq})")
            print(f"ruled on by: {', '.join(r['role'] for r in rulers) or 'nobody'}")
            return 0

        me = db.require_active(conn, db.agent(conn, args.by), "rule on a deliverable")
        db.require_duty(conn, me, "deliverables", "rules_on", "rule on a deliverable")
        row = db.deliverable(conn, args.id)
        status = db.term(conn, "deliverable_statuses", args.status, "deliverable status")
        conn.execute("UPDATE deliverables SET status=?, decided_by=?, decided_round=? WHERE id=?",
                     (status["value"], me["id"], round_no, row["id"]))
        conn.commit()
        print(f"D-{row['id']} {row['status']} -> {status['value']} by {me['name']}")
        return 0

    return run(go)


# ----------------------------------------------------------- milestones

def milestone_main():
    parser = base_parser("Propose, rule on, review or check a milestone")
    sub = parser.add_subparsers(dest="action", required=True)

    propose = sub.add_parser("propose")
    propose.add_argument("--from", dest="sender", required=True)
    propose.add_argument("--deliverable", type=int, required=True)
    propose.add_argument("--name", required=True)
    propose.add_argument("--intent", required=True)
    propose.add_argument("--seq", type=int)

    decide = sub.add_parser("decide")
    decide.add_argument("--id", type=int, required=True)
    decide.add_argument("--by", required=True)
    decide.add_argument("--status", required=True)
    decide.add_argument("--file", dest="file_path")

    review = sub.add_parser("review", help="pass or fail the slicing")
    review.add_argument("--id", type=int, required=True)
    review.add_argument("--by", required=True)
    review.add_argument("--ok", required=True, choices=["yes", "no"])
    review.add_argument("--note")

    check = sub.add_parser("check", help="confirm the milestone file is buildable")
    check.add_argument("--id", type=int, required=True)
    check.add_argument("--by", required=True)
    check.add_argument("--ok", required=True, choices=["yes", "no"])
    check.add_argument("--note")

    assign = sub.add_parser("assign", help="put a requirement in a milestone")
    assign.add_argument("--requirement", type=int, required=True)
    assign.add_argument("--id", type=int, required=True)
    assign.add_argument("--by", required=True)

    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        round_no = db.state(conn)["round"]

        if args.action == "propose":
            me = db.require_active(conn, db.agent(conn, args.sender), "propose a milestone")
            db.require_duty(conn, me, "milestones", "owns", "propose a milestone")
            db.require_phase(conn, me, "propose a milestone")
            parent = db.deliverable(conn, args.deliverable)
            seq = args.seq or (conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS n FROM milestones WHERE deliverable_id = ?",
                (parent["id"],)).fetchone()["n"])
            cur = conn.execute(
                """INSERT INTO milestones (deliverable_id, name, seq, intent, proposed_by)
                   VALUES (?,?,?,?,?)""",
                (parent["id"], args.name, seq, args.intent, me["id"]),
            )
            conn.commit()
            reviewers = db.who(conn, "slicing", "reviews")
            print(f"M-{cur.lastrowid} '{args.name}' in D-{parent['id']} proposed by {me['name']}")
            print(f"slicing reviewed by: {', '.join(r['role'] for r in reviewers) or 'nobody'}")
            return 0

        if args.action == "assign":
            me = db.require_active(conn, db.agent(conn, args.by), "assign a requirement")
            db.require_duty(conn, me, "milestones", "rules_on", "assign a requirement")
            row = db.milestone(conn, args.id)
            req = db.requirement(conn, args.requirement)
            if req["status"] != "accepted":
                raise Refused(f"R-{req['id']} is {req['status']}; only accepted requirements "
                              "go into a milestone")
            conn.execute("UPDATE requirements SET milestone_id=?, deliverable_id=?, updated_round=? "
                         "WHERE id=?", (row["id"], row["deliverable_id"], round_no, req["id"]))
            conn.commit()
            print(f"R-{req['id']} assigned to M-{row['id']}")
            return 0

        me = db.require_active(conn, db.agent(conn, args.by), "act on a milestone")
        row = db.milestone(conn, args.id)

        if args.action == "review":
            db.require_duty(conn, me, "slicing", "reviews", "pass or fail the slicing")
            if args.ok == "no":
                if not args.note:
                    raise Refused("say what is wrong with the slicing: --note '...'")
                kind = "objection"
                cur = conn.execute(
                    "INSERT INTO concerns (kind, raised_by, addressed_to, body, round) "
                    "VALUES (?,?,?,?,?)",
                    (kind, me["id"], row["proposed_by"],
                     f"M-{row['id']} '{row['name']}' sent back: {args.note}", round_no),
                )
                conn.commit()
                print(f"M-{row['id']} sent back — C-{cur.lastrowid} to the proposer")
                return 0
            conn.execute("UPDATE milestones SET sliced_ok_by=? WHERE id=?", (me["id"], row["id"]))
            conn.commit()
            print(f"M-{row['id']} slicing passed by {me['name']}")
            return 0

        if args.action == "check":
            db.require_duty(conn, me, "buildability", "checks", "check buildability")
            if args.ok == "no":
                if not args.note:
                    raise Refused("say what is missing from the milestone file: --note '...'")
                rulers = db.who(conn, "milestones", "rules_on")
                cur = conn.execute(
                    "INSERT INTO concerns (kind, raised_by, addressed_to, body, round) "
                    "VALUES ('question',?,?,?,?)",
                    (me["id"], rulers[0]["id"] if rulers else HUMAN_ID,
                     f"M-{row['id']} '{row['name']}' is not buildable as written: {args.note}",
                     round_no),
                )
                conn.commit()
                print(f"M-{row['id']} not buildable — C-{cur.lastrowid} raised")
                return 0
            conn.execute("UPDATE milestones SET buildable_ok_by=? WHERE id=?", (me["id"], row["id"]))
            conn.commit()
            print(f"M-{row['id']} confirmed buildable by {me['name']}")
            return 0

        # decide
        db.require_duty(conn, me, "milestones", "rules_on", "rule on a milestone")
        status = db.term(conn, "milestone_statuses", args.status, "milestone status")
        if status["value"] == "planned" and not row["sliced_ok_by"]:
            reviewers = db.who(conn, "slicing", "reviews")
            raise Refused(f"M-{row['id']} has not passed slicing review "
                          f"({', '.join(r['role'] for r in reviewers) or 'nobody'} must pass it first)")
        conn.execute(
            "UPDATE milestones SET status=?, decided_by=?, decided_round=?, "
            "file_path=COALESCE(?, file_path) WHERE id=?",
            (status["value"], me["id"], round_no, args.file_path, row["id"]),
        )
        conn.commit()
        print(f"M-{row['id']} {row['status']} -> {status['value']} by {me['name']}")
        return 0

    return run(go)


# ------------------------------------------------------------------ signoff

def signoff_main():
    parser = base_parser("Declare you have nothing further to raise")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--note")
    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        me = db.require_active(conn, db.agent(conn, args.agent), "sign off")
        mail = db.outstanding(conn, me)
        reviews = db.to_review(conn, me)
        if mail or reviews:
            raise Refused(f"{len(mail)} concern(s) to answer and {len(reviews)} answer(s) to "
                          "review first — run cp-queue")
        waiting = db.waiting_on(conn, me)
        if waiting:
            raise Refused("your own concern(s) are still open: "
                          + ", ".join(f"C-{c['id']}" for c in waiting)
                          + " — sign off once they are settled")
        st = db.state(conn)
        conn.execute(
            "INSERT OR REPLACE INTO signoffs (agent_id, round, change_mark, note) VALUES (?,?,?,?)",
            (me["id"], st["round"], st["change_mark"], args.note),
        )
        conn.execute("UPDATE agents SET last_seen_change = ? WHERE id = ?",
                     (st["change_mark"], me["id"]))
        conn.commit()
        print(f"{me['name']} signed off at change mark {st['change_mark']}")
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
        waiting = db.pause_reason(conn)
        stalls = db.stalled(conn)
        active = conn.execute(
            """SELECT a.* FROM agents a JOIN agent_phases p ON p.agent_id = a.id
                WHERE a.active = 1 AND p.phase = ? ORDER BY a.id""", (st["phase"],)).fetchall()
        signed = {r["agent_id"] for r in conn.execute(
            "SELECT agent_id FROM signoffs WHERE change_mark = ?", (st["change_mark"],))}
        open_count = conn.execute("SELECT COUNT(*) c FROM concerns WHERE status='open'").fetchone()["c"]
        by_status = conn.execute(
            "SELECT status, COUNT(*) c FROM requirements GROUP BY status ORDER BY status").fetchall()
        pending = [a for a in active if a["id"] not in signed and a["id"] != HUMAN_ID]
        abandoned = db.abandoned_drafts(conn)

        if args.json:
            print(json.dumps({
                "round": st["round"], "phase": st["phase"], "phase_name": st["phase_name"],
                "ends_when": st["ends_when"].strip(), "change_mark": st["change_mark"],
                "paused": bool(waiting),
                "waiting_on_human": [dict(r) for r in waiting],
                "stalled": [dict(r) for r in stalls],
                "abandoned_drafts": [dict(r) for r in abandoned],
                "open_concerns": open_count,
                "requirements": {r["status"]: r["c"] for r in by_status},
                "active": [a["role"] for a in active],
                "not_signed_off": [a["role"] for a in pending],
                "converged": not waiting and open_count == 0 and not pending,
            }, indent=2))
            return 0

        print(f"round {st['round']}, phase {st['phase']} ({st['phase_name']}), "
              f"change mark {st['change_mark']}")
        print(f"  ends when:   {st['ends_when'].strip()}")
        print(f"  active:      {', '.join(a['role'] for a in active)}")
        print(f"  concerns:    {open_count} open")
        print("  requirements: " + (", ".join(f"{r['c']} {r['status']}" for r in by_status) or "none"))
        if stalls:
            print(f"\nSTALLED ({len(stalls)}): " + ", ".join(f"C-{r['id']}" for r in stalls))
        if abandoned:
            print(f"\nABANDONED DRAFTS ({len(abandoned)}) — their concerns closed without them; "
                  "reject or revive:")
            for row in abandoned:
                print(f"  R-{row['id']} {row['statement'].strip()}")
        if waiting:
            print(f"\nPAUSED — waiting on the human ({len(waiting)}):")
            for row in waiting:
                print(f"  C-{row['id']} [{row['kind']}] from {row['raiser']}")
        elif open_count == 0 and not pending:
            print("\nCONVERGED — no open concerns, every active agent has signed off")
        else:
            print(f"\nrunning — not signed off: {', '.join(a['role'] for a in pending) or 'none'}")
        return 0

    return run(go)


# ---------------------------------------------------------- round and phase

def round_main():
    parser = base_parser("Advance the round counter, or move to the next phase")
    parser.add_argument("--advance", action="store_true")
    parser.add_argument("--phase", type=int, help="move the project to this phase")
    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        st = db.state(conn)
        if args.phase:
            row = conn.execute("SELECT * FROM phases WHERE number = ?", (args.phase,)).fetchone()
            if row is None:
                raise Refused(f"no phase {args.phase}")
            conn.execute("UPDATE project_state SET phase=?, updated_at=? WHERE id=1",
                         (args.phase, db.now()))
            conn.commit()
            print(f"phase {st['phase']} -> {args.phase} ({row['name']})")
            print(f"ends when: {row['ends_when'].strip()}")
            return 0
        if not args.advance:
            raise Refused("nothing to do: pass --advance or --phase N")
        waiting = db.pause_reason(conn)
        if waiting:
            raise Refused(f"{len(waiting)} concern(s) are with the human; the loop is paused")
        conn.execute("UPDATE project_state SET round = ?, updated_at = ? WHERE id = 1",
                     (st["round"] + 1, db.now()))
        conn.commit()
        print(f"round {st['round']} -> {st['round'] + 1}")
        return 0

    return run(go)


# ------------------------------------------------------------------- policy

def policy_main():
    parser = base_parser("The rules in force: thresholds, duties, and who decides what")
    parser.add_argument("--agent", help="show one agent's motivation, phases and duties")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        settings = [dict(r) for r in conn.execute("SELECT * FROM policy ORDER BY key")]
        kinds = [dict(r) for r in db.vocab(conn, "requirement_kinds")]
        answers = [dict(r) for r in db.vocab(conn, "answer_kinds")]
        concerns = [dict(r) for r in db.vocab(conn, "concern_kinds")]

        if args.agent:
            me = db.agent(conn, args.agent)
            duties = [dict(d) for d in db.duties_of(conn, me["id"])]
            phases = db.phases_of(conn, me["id"])
            if args.json:
                print(json.dumps({"agent": dict(me), "duties": duties, "phases": phases,
                                  "policy": settings}, indent=2))
                return 0
            print(f"{me['name']} ({me['role']})")
            print(f"  argues for: {me['motivation'].strip()}")
            print(f"  phases:     {', '.join(str(p) for p in phases)}")
            print(f"  joins at:   phase {me['joins_at_phase']}"
                  + (f" — {me['join_trigger']}" if me["join_trigger"] else ""))
            for duty in duties:
                print(f"  {duty['relation']:9} {duty['duty']} — {duty['description'].strip()}")
            print("\nthresholds")
            for setting in settings:
                print(f"  {setting['key']:24} {setting['value']}")
            return 0

        if args.json:
            print(json.dumps({"policy": settings, "requirement_kinds": kinds,
                              "answer_kinds": answers, "concern_kinds": concerns}, indent=2))
            return 0

        print("thresholds")
        for setting in settings:
            print(f"  {setting['key']:24} {setting['value']}  — {setting['description'].strip()}")
        print("\nwho rules on a requirement")
        for kind in kinds:
            print(f"  {kind['value']:18} {kind['decided_by']}")
        print("\nwhat an answer must carry")
        for kind in answers:
            print(f"  {kind['value']:18} {kind['requires'] or '—'}")
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


def doctor_main():
    parser = base_parser("Check the config, the charters and the database for drift")
    parser.add_argument("--config", default=str(cfg.DEFAULT_CONFIG))
    parser.add_argument("--agents-dir", default=str(cfg.ROOT / ".claude" / "agents"))
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
        if f"cp-policy --agent {agent['role']}" not in text:
            findings.append(("charter", f"{charter}: does not tell the agent to read its "
                                        f"motivation and duties from cp-policy --agent "
                                        f"{agent['role']}"))

    roster_paths = {p.name for p in charters.values()}
    for path in sorted(Path(args.agents_dir).glob("*.md")):
        if path.name != "protocol.md" and path.name not in roster_paths:
            findings.append(("charter", f"{path}: no agent in pipeline.yaml points to it"))

    # Numbers that belong to policy should not be restated in prose.
    docs = list(Path(args.agents_dir).glob("*.md"))
    skills = cfg.ROOT / ".claude" / "skills"
    if skills.exists():
        docs += list(skills.rglob("*.md"))
    for path in docs:
        for line in path.read_text().splitlines():
            if POLICY_IN_PROSE.search(EMPHASIS.sub("", line)) and "cp-policy" not in line:
                findings.append(("policy", f"{path.relative_to(cfg.ROOT)}: a threshold in prose "
                                           f"— read it from cp-policy instead: {line.strip()[:70]}"))

    # Duties nobody holds
    duties = {d["value"] for d in configuration.get("duties") or []}
    held = {duty for agent in agents for relation in cfg.DUTY_RELATIONS
            for duty in agent.get(relation) or []}
    for duty in sorted(duties - held):
        findings.append(("duties", f"'{duty}' is declared but no agent holds it"))

    # The database, if there is one
    path = Path(args.db)
    if path.exists():
        conn = db.connect(str(path))
        for name in cfg.VOCABULARIES:
            in_db = {r["value"] for r in db.vocab(conn, name)}
            in_yaml = {e["value"] for e in configuration["vocabularies"][name]}
            for value in sorted(in_yaml - in_db):
                findings.append(("database", f"{name}: '{value}' is in pipeline.yaml but not in "
                                             f"{path} — re-run cp-init"))
            for value in sorted(in_db - in_yaml):
                findings.append(("database", f"{name}: '{value}' is in {path} but not in "
                                             "pipeline.yaml"))

    if not findings:
        print("no drift found")
        return 0
    print(f"{len(findings)} finding(s):")
    for area, text in findings:
        print(f"  [{area}] {text}")
    return 1


# ------------------------------------------------------------------- render

def render_main():
    parser = base_parser("Generate a document from the tables")
    parser.add_argument("what", choices=["requirements"])
    parser.add_argument("--out", help="write to this file instead of stdout")
    args = parser.parse_args()

    def go():
        conn = db.connect(args.db)
        st = db.state(conn)
        lines = ["# Requirements", "",
                 f"*Generated from the project database — round {st['round']}, "
                 f"change mark {st['change_mark']}.*", ""]

        # Section order comes from the vocabularies, so a new kind or status
        # appears in the document without touching this code.
        sections = []
        for kind in db.vocab(conn, "requirement_kinds"):
            if kind["document_section"] and kind["document_section"] not in sections:
                sections.append(kind["document_section"])
        for status in db.vocab(conn, "requirement_statuses"):
            if status["in_document"] and status["document_section"] \
                    and status["document_section"] not in sections:
                sections.append(status["document_section"])
        for answer in db.vocab(conn, "answer_kinds"):
            if answer["document_section"] and answer["document_section"] not in sections:
                sections.append(answer["document_section"])

        rows = conn.execute(
            """SELECT r.*, g.name AS proposer, k.document_section AS kind_section,
                      s.in_document, s.document_section AS status_section,
                      e.reason AS last_reason
                 FROM requirements r
                 JOIN agents g ON g.id = r.proposed_by
                 JOIN requirement_kinds k ON k.value = r.kind
                 JOIN requirement_statuses s ON s.value = r.status
                 LEFT JOIN requirement_events e ON e.id = (
                      SELECT MAX(id) FROM requirement_events WHERE requirement_id = r.id)
                ORDER BY r.id""").fetchall()

        risks = conn.execute(
            """SELECT c.id, c.body, w.body AS answer, g.name AS answerer,
                      k.document_section AS section
                 FROM answers w
                 JOIN concerns c ON c.id = w.concern_id
                 JOIN agents g ON g.id = w.answered_by
                 JOIN answer_kinds k ON k.value = w.kind
                WHERE k.document_section IS NOT NULL AND w.satisfied = 1
                ORDER BY c.id""").fetchall()

        for section in sections:
            lines += [f"## {section}", ""]
            written = 0
            for row in rows:
                if not row["in_document"]:
                    continue
                where = row["status_section"] or row["kind_section"]
                if where != section:
                    continue
                written += 1
                lines.append(f"- **R-{row['id']}** ({row['kind']}) {row['statement']}")
                detail = [f"raised by {row['proposer']}"]
                if row["status"] != "accepted":
                    detail.append(f"{row['status']}: {row['last_reason'] or 'no reason recorded'}")
                if row["cost_flag"]:
                    detail.append(f"cost: {row['cost_flag']}")
                if row["rationale"]:
                    detail.append(row["rationale"].strip())
                lines.append(f"  - {' · '.join(detail)}")
            for risk in risks:
                if risk["section"] != section:
                    continue
                written += 1
                lines.append(f"- **C-{risk['id']}** {risk['body'].strip().splitlines()[0]}")
                lines.append(f"  - {risk['answerer']}: {risk['answer'].strip().splitlines()[-1]}")
            if not written:
                lines.append("*Nothing yet.*")
            lines.append("")

        text = "\n".join(lines)
        if args.out:
            Path(args.out).write_text(text)
            print(f"wrote {args.out}")
        else:
            print(text)
        return 0

    return run(go)
