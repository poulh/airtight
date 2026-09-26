# The turn protocol

Every agent in this pipeline follows the same shape. Your charter says what you argue for;
this says how a turn runs. The tools enforce the rules, so a refusal is information, not an
obstacle to work around.

Set `CP_DB` to the project database, or pass `--db` to every tool. The tools are on `PATH`
once `.venv` is active; otherwise call them as `.venv/bin/cp-queue`.

## A turn, in order

1. **`cp-queue --agent <your-role>`** — concerns addressed to you, answers to your own
   concerns awaiting your judgement, and every requirement change since you last looked.
2. **Answer your mail.** `cp-answer --concern N --from <role> --kind ...`. Every concern
   addressed to you gets an answer this turn.
3. **Judge the answers to your concerns.** `cp-review --answer N --by <role> --satisfied
   yes|no`. An accepted answer shows the wording of each requirement it names: judge that
   wording, not the promise. Say yes when it is genuinely settled; a "no" needs `--reply`
   saying what is missing. A requirement cannot be accepted while your concern is open.
4. **Read the requirement changes.** Anything in your area that is wrong, missing or risky
   becomes a concern (`cp-concern`) or a proposed requirement (`cp-propose`).
5. **Sign off** with `cp-signoff --agent <role>` when you have nothing further to raise
   against the current state and none of your own concerns is still open. A later change
   puts you back to work; signing off is not leaving.
6. **Close the turn** with `cp-queue --agent <role> --mark-seen`. This records that you have
   read the requirement changes, and a new requirement cannot be accepted until every
   active agent has.

## Answering

An answer is one of three shapes, and the tool refuses anything else:

- `--kind accepted --requirements 12,14` — you agree and acted. Propose the requirement
  first with `cp-propose`, then name its id here.
- `--kind rejected --reason "..."` — you disagree and are changing nothing. Your reason is
  published under *Accepted risks*, so write it for someone reading it in six months.
- `--kind escalated --to pm|human` — you cannot settle it. Escalation moves the concern to
  them; do not use it to avoid thinking.

## Raising a concern

`cp-concern --from <role> --to <role> --kind question|risk|objection|proposal --about R-12|C-3 --body "..."`

- `--about` names what you read that prompted this: the requirement (`R-12`) or the concern
  (`C-3`, often the brief) that made you think of it. It is required, so every concern
  traces back to what the human said. An objection is simply a concern about a requirement.
- One addressee. The same point for three agents is three concerns.
- Address it to the agent who can actually settle it. If you do not know, address the PM.
- Never address a concern to the human directly unless it is genuinely theirs to decide:
  the goal, a cut to something they asked for, an invariant, or a cost only they can weigh.
  The loop stops while they hold it.

## Proposing a requirement

`cp-propose --from <role> --kind functional|constraint|invariant --statement "..." --concern N`

- One testable sentence. "The system is secure" is not a requirement; "Sessions expire after
  30 minutes of inactivity" is.
- `--concern N` links it to what prompted it, which is how anyone later learns why it exists.
- `--supersedes N` rewords requirement N. The original stays in force until the new wording
  is accepted.
- `--rationale` carries the reasoning. Requirements outlive the conversation.
- Only the architect sets `--cost`.

## What you may not do

- Decide requirements unless you are the PM (ordinary ones) or the human (goals, non-goals,
  success criteria, invariants, and anything cutting what the human asked for).
- Bring another agent into the project. Ask: `cp-staff request --agent <role> --by <role>
  --about R-12 --reason "..." --cost "..."`. The human decides.
- Write application code in phases 1–3. No code exists until phase 4.
- Re-raise something already settled. Read the log first; `cp-queue` shows you the changes.

## If you disagree with a decision

Appeal once, and only when it matters: `cp-concern --from <role> --to human --kind appeal
--about R-12 --body "..."`. State the decision, why it is wrong, and what it costs if it stands. The PM
does not hear appeals against his own rulings.
