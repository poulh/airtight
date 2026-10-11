"""The airtight floor: a read-only REST service over an airtight project database.

It never writes. The database is opened read-only on every request, and derived
state the tools already compute — the rotation, each agent's queue, who must
approve — comes from the plugin's own airtight.db helpers, so the floor shows
exactly what the tools would act on.

    GET /api/snapshot           everything the floor shows, in one call
    GET /api/statements/{id}    one statement: approvals, links, concerns and their answers
    GET /api/concerns/{id}      one concern's whole thread
    GET /api/log?limit=N        the tail of .airtight/log.jsonl
    GET /api/config             what the page needs to know (refresh interval, paths)
"""

import argparse
import json
import os
import sqlite3
import sys
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
UI_ROOT = HERE.parent
DEFAULTS = {"db": ".airtight/project.db", "log": "", "plugin_root": "", "host": "127.0.0.1",
            "port": 8765, "refresh_seconds": 3}

CONFIG = dict(DEFAULTS)
db = None   # airtight.db, imported once the plugin root is known


# ------------------------------------------------------------------ config

def load_config(argv=None):
    parser = argparse.ArgumentParser(description="Serve the airtight floor")
    parser.add_argument("--config", default=str(UI_ROOT / "airtight-ui.toml"),
                        help="TOML file (default: airtight-ui.toml next to this package)")
    parser.add_argument("--db", help="project database (default: $AT_DB, then the config)")
    parser.add_argument("--log", help="command log (default: log.jsonl next to the database)")
    parser.add_argument("--plugin-root", help="the airtight plugin directory")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--refresh-seconds", type=float)
    args = parser.parse_args(argv)

    config = dict(DEFAULTS)
    if os.path.exists(args.config):
        with open(args.config, "rb") as f:
            config.update(tomllib.load(f))
    if os.environ.get("AT_DB"):
        config["db"] = os.environ["AT_DB"]
    for key in ("db", "log", "plugin_root", "host", "port", "refresh_seconds"):
        value = getattr(args, key)
        if value is not None:
            config[key] = value

    config["db"] = str(Path(config["db"]).expanduser().resolve())
    config["log"] = str(Path(config["log"]).expanduser().resolve()) if config["log"] else \
        str(Path(config["db"]).parent / "log.jsonl")
    config["plugin_root"] = str(Path(config["plugin_root"]).expanduser().resolve()) \
        if config["plugin_root"] else str(UI_ROOT.parent / "plugins" / "airtight")
    return config


def import_plugin(plugin_root):
    global db
    if not (Path(plugin_root) / "airtight" / "db.py").exists():
        sys.exit(f"no airtight plugin at {plugin_root} — set plugin_root in airtight-ui.toml")
    sys.path.insert(0, plugin_root)
    from airtight import db as airtight_db
    db = airtight_db


def connect():
    path = CONFIG["db"]
    if not os.path.exists(path):
        raise HTTPException(404, f"no database at {path} — run /airtight:start in that project, "
                                 "or point airtight-ui at another with --db")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def rows(cursor):
    return [dict(r) for r in cursor]


# ------------------------------------------------------------ the snapshot

def concern_threads(conn):
    """Every concern with its answers, its first addressee and where it stands now."""
    answers = defaultdict(list)
    for a in conn.execute("SELECT * FROM answers ORDER BY id"):
        answers[a["concern_id"]].append(dict(a))
    out = []
    for c in conn.execute("SELECT * FROM concerns ORDER BY id"):
        c = dict(c)
        thread = answers[c["id"]]
        # The first hand-off is made by whoever held it first, so that is who it was raised to.
        handoffs = [a for a in thread if a["kind"] == "reassign"]
        c["raised_to"] = handoffs[0]["answered_by"] if handoffs else c["addressed_to"]
        c["answers"] = thread
        c["answer_count"] = sum(1 for a in thread if a["kind"] != "reassign")
        waiting = next((a for a in thread if a["kind"] == "answer" and a["verdict"] is None), None)
        if c["status"] == "open":
            if waiting:
                c["stage"], c["with"] = "to judge", c["raised_by"]
            else:
                c["stage"], c["with"] = "to answer", c["addressed_to"]
        elif c["statement_id"] and c["acted_on"] is None:
            c["stage"], c["with"] = "awaiting Peter", None
        else:
            c["stage"], c["with"] = c["acted_on"] or "closed", None
        c["on"] = (f"S-{c['statement_id']}" if c["statement_id"] else f"M-{c['milestone_id']}")
        out.append(c)
    return out


def agent_status(a, st, running, rotation_ids, this_round, queue):
    if running and running["agent_id"] == a["id"]:
        return "working"
    if a["id"] == db.HUMAN_ID:
        return "needed" if st["status"] == "paused" else "watching"
    if not a["active"]:
        return "benched"
    if a["id"] not in rotation_ids:
        return "off phase"
    if a["id"] in this_round:
        return this_round[a["id"]]   # done | skipped
    left = [i for i in rotation_ids if i not in this_round]
    if left and left[0] == a["id"] and not running:
        return "up next"
    return "has work" if queue and not db.queue_is_empty(queue) else "waiting"


def queue_counts(conn, a):
    q = db.queue(conn, a)
    counts = {k: len(v) if isinstance(v, list) else int(bool(v)) for k, v in q.items()
              if k not in ("roster", "last_summary")}
    return q, {k: v for k, v in counts.items() if v}


def snapshot():
    conn = connect()
    st = db.state(conn)
    running = db.running_turn(conn)
    rotation = db.rotation(conn)
    rotation_ids = [r["id"] for r in rotation]
    approver_ids = [r["id"] for r in db.approvers(conn)]
    this_round = {r["agent_id"]: r["status"] for r in conn.execute(
        "SELECT agent_id, status FROM turns WHERE round = ? AND status <> 'running'",
        (st["round"],))}

    agents = rows(conn.execute("SELECT * FROM agents ORDER BY seq"))
    by_id = {a["id"]: a for a in agents}
    concerns = concern_threads(conn)

    # ---- per-agent stats
    raised = Counter(c["raised_by"] for c in concerns)
    raised_open = Counter(c["raised_by"] for c in concerns if c["status"] == "open")
    holding = Counter(c["with"] for c in concerns if c["with"])
    received = Counter(c["raised_to"] for c in concerns)
    by_kind = defaultdict(Counter)
    for c in concerns:
        by_kind[c["raised_by"]][c["kind"]] += 1
    matrix = Counter((c["raised_by"], c["raised_to"]) for c in concerns)
    answered = Counter()
    handed_off = Counter()
    verdicts = defaultdict(Counter)
    for c in concerns:
        for a in c["answers"]:
            (handed_off if a["kind"] == "reassign" else answered)[a["answered_by"]] += 1
            if a["verdict"]:
                verdicts[c["raised_by"]][a["verdict"]] += 1
    approvals = Counter(r["agent_id"] for r in conn.execute("SELECT agent_id FROM approvals"))
    turn_stats = defaultdict(Counter)
    tokens = Counter()
    for t in conn.execute("SELECT agent_id, status, tokens FROM turns"):
        turn_stats[t["agent_id"]][t["status"]] += 1
        tokens[t["agent_id"]] += t["tokens"] or 0
    written = Counter(r["agent_id"] for r in conn.execute(
        "SELECT t.agent_id FROM statements s JOIN turns t ON t.id = s.turn_id"))
    last = {r["agent_id"]: dict(r) for r in conn.execute(
        """SELECT t.* FROM turns t JOIN (SELECT agent_id, MAX(id) AS id FROM turns
             WHERE summary IS NOT NULL GROUP BY agent_id) m ON m.id = t.id""")}

    for a in agents:
        q, counts = queue_counts(conn, a) if a["active"] else (None, {})
        a["status"] = agent_status(a, st, running, rotation_ids, this_round, q)
        a["queue"] = counts
        a["approver"] = a["id"] in approver_ids
        a["in_rotation"] = a["id"] in rotation_ids
        a["duties"] = rows(conn.execute(
            "SELECT duty, relation FROM agent_duties WHERE agent_id = ?", (a["id"],)))
        a["last_turn"] = last.get(a["id"])
        a["stats"] = {
            "turns_done": turn_stats[a["id"]]["done"],
            "turns_skipped": turn_stats[a["id"]]["skipped"],
            "tokens": tokens[a["id"]],
            "concerns_raised": raised[a["id"]],
            "concerns_raised_open": raised_open[a["id"]],
            "concerns_received": received[a["id"]],
            "concerns_holding": holding[a["id"]],
            "answers": answered[a["id"]],
            "handoffs": handed_off[a["id"]],
            "approvals": approvals[a["id"]],
            "statements_written": written[a["id"]],
            "raised_by_kind": dict(by_kind[a["id"]]),
            "verdicts_given": dict(verdicts[a["id"]]),
            "raised_to": {by_id[t]["role"]: n for (f, t), n in matrix.items() if f == a["id"]},
        }

    # ---- statements
    approved_by = defaultdict(list)
    for r in conn.execute("SELECT statement_id, agent_id FROM approvals ORDER BY created_at"):
        approved_by[r["statement_id"]].append(r["agent_id"])
    links_from, links_to = defaultdict(list), defaultdict(list)
    for l in conn.execute("SELECT from_id, to_id, relation FROM links"):
        links_from[l["from_id"]].append({"relation": l["relation"], "id": l["to_id"]})
        links_to[l["to_id"]].append({"relation": l["relation"], "id": l["from_id"]})
    on_statement = defaultdict(list)
    for c in concerns:
        if c["statement_id"]:
            on_statement[c["statement_id"]].append(c)
    kinds = {k["value"]: dict(k) for k in conn.execute("SELECT * FROM statement_kinds")}
    statements = []
    for s in conn.execute("SELECT * FROM statements ORDER BY id"):
        s = dict(s)
        cs = on_statement[s["id"]]
        s["label"] = db.label(conn, s)
        s["kind_label"] = (kinds.get(s["kind"]) or {}).get("label") or s["kind"].replace("_", " ")
        s["level"] = (kinds.get(s["kind"]) or {}).get("level")
        s["approved_by"] = approved_by[s["id"]]
        s["approvals_missing"] = [i for i in approver_ids if i not in approved_by[s["id"]]] \
            if s["status"] == "pending" else []
        s["links_out"], s["links_in"] = links_from[s["id"]], links_to[s["id"]]
        s["concerns"] = {"total": len(cs),
                         "open": sum(1 for c in cs if c["status"] == "open"),
                         "awaiting_peter": sum(1 for c in cs if c["stage"] == "awaiting Peter"),
                         "ids": [c["id"] for c in cs]}
        statements.append(s)

    # ---- growth per round
    growth = defaultdict(lambda: {"statements": 0, "concerns": 0, "answers": 0, "agreed": 0})
    for s in statements:
        growth[s["created_round"]]["statements"] += 1
    for c in concerns:
        growth[c["round"]]["concerns"] += 1
        for a in c["answers"]:
            growth[a["round"]]["answers"] += 1
    for e in conn.execute("SELECT round FROM events WHERE object = 'statement' "
                          "AND to_status = 'agreed'"):
        growth[e["round"]]["agreed"] += 1
    per_round = [{"round": r, **growth[r]} for r in range(1, st["round"] + 1)]

    # ---- the rest
    turns = rows(conn.execute("SELECT * FROM turns ORDER BY id DESC LIMIT 200"))
    events = rows(conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 200"))
    phases = rows(conn.execute("SELECT * FROM phases ORDER BY number"))
    deliverables = rows(conn.execute("SELECT * FROM deliverables ORDER BY seq, id"))
    milestones = rows(conn.execute("SELECT * FROM milestones ORDER BY seq, id"))
    reports = rows(conn.execute("SELECT * FROM reports ORDER BY id DESC"))
    by_status = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM statements GROUP BY status")}

    state = dict(st)
    state.update({
        "current_turn": dict(running) if running else None,
        "rotation": rotation_ids,
        "left_this_round": [i for i in rotation_ids if i not in this_round
                            and not (running and running["agent_id"] == i)],
        "report_due": db.report_due(conn),
        "statements_by_status": by_status,
        "open_concerns": sum(1 for c in concerns if c["status"] == "open"),
        "awaiting_peter": sum(1 for c in concerns if c["stage"] == "awaiting Peter"),
        "stuck": [c["id"] for c in db.stuck(conn)],
        "turns_total": conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0],
        "tokens_total": sum(tokens.values()),
        "db": CONFIG["db"],
    })
    conn.close()
    return {"state": state, "phases": phases, "agents": agents, "statements": statements,
            "concerns": concerns, "deliverables": deliverables, "milestones": milestones,
            "turns": turns, "events": events, "reports": reports, "per_round": per_round,
            "matrix": [{"from": f, "to": t, "n": n} for (f, t), n in matrix.items()]}


# --------------------------------------------------------------------- app

app = FastAPI(title="airtight floor", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.get("/api/config")
def api_config():
    return {"refresh_seconds": CONFIG["refresh_seconds"], "db": CONFIG["db"],
            "log": CONFIG["log"]}


@app.get("/api/snapshot")
def api_snapshot():
    return snapshot()


@app.get("/api/concerns/{concern_id}")
def api_concern(concern_id: int):
    conn = connect()
    found = next((c for c in concern_threads(conn) if c["id"] == concern_id), None)
    if found is None:
        raise HTTPException(404, f"no concern C-{concern_id}")
    if found["statement_id"]:
        found["statement"] = dict(db.statement(conn, found["statement_id"]))
    found["acted_by"] = rows(conn.execute(
        "SELECT statement_id FROM statement_reasons WHERE concern_id = ?", (concern_id,)))
    conn.close()
    return found


@app.get("/api/statements/{statement_id}")
def api_statement(statement_id: int):
    for s in api_snapshot()["statements"]:
        if s["id"] == statement_id:
            conn = connect()
            s["reasons"] = [r["concern_id"] for r in conn.execute(
                "SELECT concern_id FROM statement_reasons WHERE statement_id = ?", (statement_id,))]
            s["history"] = rows(conn.execute(
                "SELECT * FROM events WHERE object = 'statement' AND object_id = ? ORDER BY id",
                (statement_id,)))
            conn.close()
            return s
    raise HTTPException(404, f"no statement S-{statement_id}")


@app.get("/api/log")
def api_log(limit: int = 100):
    path = Path(CONFIG["log"])
    if not path.exists():
        return []
    with path.open("rb") as f:
        # Read backwards just far enough: lines are capped at a few KB each.
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - limit * 6000))
        lines = f.read().decode("utf-8", "replace").splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out[::-1]


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


def main(argv=None):
    import uvicorn

    CONFIG.update(load_config(argv))
    import_plugin(CONFIG["plugin_root"])
    print(f"airtight floor — watching {CONFIG['db']}")
    print(f"  open http://{CONFIG['host']}:{CONFIG['port']}/")
    uvicorn.run(app, host=CONFIG["host"], port=int(CONFIG["port"]), log_level="warning")


if __name__ == "__main__":
    main()
