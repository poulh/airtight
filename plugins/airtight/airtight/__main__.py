"""python -m airtight <tool> [args] — how bin/airtight runs every at-* tool."""

import sys

from . import cli

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


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in TOOLS:
        print("usage: python -m airtight <tool> [args]\ntools: " + " ".join(sorted(TOOLS)),
              file=sys.stderr)
        return 2
    tool = sys.argv.pop(1)
    sys.argv[0] = tool
    return TOOLS[tool]()


if __name__ == "__main__":
    sys.exit(main())
