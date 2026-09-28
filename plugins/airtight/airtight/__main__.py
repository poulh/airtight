"""python -m airtight <tool> [args] — how bin/airtight runs every at-* tool.

Every call is also appended to .airtight/log.jsonl, next to the database: when,
the round and turn it ran in, the full command, how it ended (ok, refused,
usage, error) and what it printed. Refusals and crashes leave nothing in the
database by design, so this log is where they show up. Logging never changes
what the tool does or returns.
"""

import io
import json
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from . import cli, db

TOOLS = {
    "at-init": cli.init_main,
    "at-turn": cli.turn_main,
    "at-queue": cli.queue_main,
    "at-approve": cli.approve_main,
    "at-concern": cli.concern_main,
    "at-answer": cli.answer_main,
    "at-review": cli.review_main,
    "at-escalate": cli.escalate_main,
    "at-statement": cli.statement_main,
    "at-deliverable": cli.deliverable_main,
    "at-milestone": cli.milestone_main,
    "at-staff": cli.staff_main,
    "at-report": cli.report_main,
    "at-state": cli.state_main,
    "at-round": cli.round_main,
    "at-render": cli.render_main,
    "at-policy": cli.policy_main,
    "at-doctor": cli.doctor_main,
}

OUTPUT_LIMIT = 4000   # characters of output kept per call


class Tee(io.TextIOBase):
    """Writes through to the real stream and keeps a copy for the log."""

    def __init__(self, stream):
        self.stream, self.copy = stream, io.StringIO()

    def write(self, text):
        self.copy.write(text)
        return self.stream.write(text)

    def flush(self):
        self.stream.flush()


def database_path(args):
    for i, arg in enumerate(args):
        if arg == "--db" and i + 1 < len(args):
            return Path(args[i + 1])
        if arg.startswith("--db="):
            return Path(arg.split("=", 1)[1])
    return Path(db.default_db())


def where(path):
    """The round and the running turn at the moment the command starts."""
    if not path.exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        row = conn.execute(
            """SELECT s.round, s.phase, s.status, t.id, a.role
                 FROM project_state s LEFT JOIN turns t ON t.id = s.current_turn_id
                 LEFT JOIN agents a ON a.id = t.agent_id WHERE s.id = 1""").fetchone()
        conn.close()
    except sqlite3.Error:
        return {}
    if row is None:
        return {}
    return {"round": row[0], "phase": row[1], "project": row[2], "turn": row[3], "agent": row[4]}


def record(path, entry):
    log = path.parent / "log.jsonl"
    if not log.parent.is_dir():
        return   # no project here yet (e.g. at-init --check before setup)
    try:
        with log.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in TOOLS:
        print("usage: python -m airtight <tool> [args]\ntools: " + " ".join(sorted(TOOLS)),
              file=sys.stderr)
        return 2
    tool = sys.argv.pop(1)
    sys.argv[0] = tool
    args = sys.argv[1:]
    path = database_path(args)
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             **where(path), "tool": tool, "args": args}

    out, err = Tee(sys.stdout), Tee(sys.stderr)
    sys.stdout, sys.stderr = out, err
    code, crash = 0, None
    try:
        code = TOOLS[tool]() or 0
    except SystemExit as exc:   # argparse errors and explicit exits
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    except Exception:           # a bug: keep the traceback, then fail as Python would
        crash = traceback.format_exc()
        err.write(crash)
        code = 1
    finally:
        sys.stdout, sys.stderr = out.stream, err.stream

    printed = err.copy.getvalue()
    outcome = ("error" if crash else "ok" if code == 0
               else "refused" if "refused:" in printed else "usage")
    entry.update(outcome=outcome, exit=code,
                 stdout=out.copy.getvalue()[:OUTPUT_LIMIT],
                 stderr=printed[:OUTPUT_LIMIT])
    record(path, entry)
    return code


if __name__ == "__main__":
    sys.exit(main())
