# The turn protocol

Every agent in this pipeline follows the same shape. Your charter says what you argue for;
this says how a turn runs. The tools enforce the rules, so a refusal is information, not an
obstacle to work around.

Set `AT_DB` to the project database, or pass `--db` to every tool. The tools are on `PATH`
once `.venv` is active; otherwise call them as `.venv/bin/at-queue`.

## The objects

- **Statements** (`S-12`) are the only thing everyone reads: goals, non-goals, success
  criteria and invariants for the whole project, and per deliverable its scope and its
  requirements. A statement is *agreed* once every approver has approved it, no concern on it
  is open, and Peter has acted on every closed concern on it. Agreed statements are never
  edited: a change retires the old one and writes new ones that link back.
- **Concerns** (`C-4`) are private mail between two agents, always on one statement or one
  milestone (`M-3`). Only the raiser closes a concern.
- **Only Peter writes statements**, deliverables and milestones, and only from closed
  concerns. If you want a statement added, reworded or cut, raise a `proposal` concern to
  him saying exactly what, and why.

## A turn, in order

The orchestrator opens your turn. You work inside it; every tool refuses to act outside it.

1. **`at-queue --agent <your-role>`** — your last turn's summary, who is on the project, the
   statements in force, and everything waiting on you. Only address concerns to agents who
   are on the project.
2. **Judge the answers to your concerns.** `at-review --answer A-7 --by <role> --verdict
   accepted` when it is genuinely settled, or `--verdict replied --reply "..."` saying what is
   still missing. Accepting only closes the concern; Peter decides what, if anything, changes.
3. **Answer the concerns addressed to you.** `at-answer --concern C-4 --from <role> --body
   "..."`, or hand it to someone better placed with `--reassign-to <role> --body "why"`. You
   cannot answer again until the raiser has judged your last answer.
4. **Review every statement you have not approved.** For each: `at-approve --agent <role>
   --statement S-12`, or raise a concern on it. You cannot approve a statement while your own
   concern on it is open.
5. **Retract, if you must.** Reading something new may show that a statement you approved is
   wrong. `at-approve --agent <role> --statement S-3 --retract --to <role> --body "why"`
   withdraws your approval and raises the concern in one step.
6. **Close the turn** with `at-turn end --agent <role> --summary "..."`: one or two sentences,
   in your own words, on what you did and why. The human reads these, and so will you at the
   start of your next turn — you carry no memory between turns.

## Raising a concern

`at-concern --from <role> --to <role> --kind question|risk|objection|proposal --on S-12 --body "..."`

- One addressee. The same point for three agents is three concerns.
- Address it to the agent who can actually settle it. If you do not know, address Peter.
- Put it on the statement it is about. A concern about delivering a milestone goes on the
  milestone (`--on M-3`).
- A `proposal` says exactly what should change: the wording of the new statement, or which
  statement should go and why. Peter writes it only if the argument holds.
- Never address a concern to the human unless it is genuinely theirs to decide: a goal, a
  non-goal, a success criterion, an invariant, or a cost only they can weigh. The loop stops
  while they hold it.
- **Ids always carry a summary.** Whenever you write an id — in a concern, an answer, a
  reply or your turn summary — follow it with a few words in parentheses saying what it is:
  `S-7 (escalate after 5 working days)`, `C-4 (who approves leave?)`, `A-11 (yes, the screen
  is responsive)`. Nobody remembers what S-7 was.
- **Writing to the human:** one question per concern, so they can answer each on its own.
  Refer to other agents by name and role: Arty (Architect), Uma (UI/UX).
  Never say "statement" — it is the name of a table. Say what it is: the goal, the
  requirement, the success criterion, the invariant, the scope of v1, their brief — and quote
  what it says rather than only its id.

## What you may not do

- Write statements, deliverables or milestones, unless you are Peter.
- Close someone else's concern, or answer one that is not addressed to you.
- Bring another agent in. Ask: `at-staff request --agent <role> --by <role> --on S-12
  --reason "..." --cost "..."`. The human decides.
- Write application code before phase 4.
- Treat another agent's turn summary as information. Agents talk to each other only through
  concerns.

## If a thread goes round in circles

Say so in your reply, plainly. Peter watches for stuck threads and can send one to the human
early; past the limit in `at-policy` it goes to the human on its own, with both positions.
The human's answer comes back to the raiser like any other, unless they mark it final.
