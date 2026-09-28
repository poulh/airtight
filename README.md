# airtight

**You can't code autonomously if you don't have airtight requirements.**

airtight is a Claude Code plugin that turns your idea into requirements with no gaps before
any code is written — then builds it one milestone at a time.

A panel of specialist agents reads every requirement and either approves it or raises a
concern on it: an architect, a QA engineer, a security reviewer, a compliance officer, an ops
engineer, a UX designer, and someone whose only job is to ship sooner. They argue it out
between themselves. A project manager turns each settled argument into the next version of
the requirements, and every agent reviews that again. Nothing counts as agreed until every
one of them has approved it.

You are asked only what is genuinely yours to decide: the goal, what's in and out, a
trade-off only you can weigh. Everything else they settle without you, and every few rounds
you get a report and the chance to object to anything.

What you end up with:

- **Requirements with nothing left to guess.** Every statement has been challenged by every
  specialist, and each one traces back to the concerns that shaped it and, eventually, to
  your own words.
- **A record of every decision.** Nothing agreed is ever edited; a change replaces it and
  links back, so "why is it like this?" always has an answer.
- **Milestones a coding agent can build without stopping to ask.** Each comes with its
  requirements, their full history, the rules the code must keep, what is deliberately out,
  and automated acceptance tests.

## Install

```
/plugin marketplace add poulh/airtight
/plugin install airtight@airtight
```

## Use

In the repo for your project:

| Command | What it does |
|---|---|
| `/airtight:setup` | Installs the plugin's tools if needed and copies the default config into `.airtight/pipeline.yaml`, which you can tune before starting: who can be staffed, how often you get a report. |
| `/airtight:start` | Asks for your idea in your own words, and starts the loop. The project manager's first questions come straight back to you. |
| `/airtight:continue` | Picks up where it left off: shows where things stand, puts anything waiting on you in front of you, and carries on. |

Everything lives in `.airtight/` in your repo — commit it, so the requirements and their
history travel with the code.

## How it works

Four phases:

1. **Converge** — the loop above, until every requirement is agreed.
2. **Architect** — the architect designs against the whole agreed set at once.
3. **Slice** — the work is cut into milestones; the developer joins by approving every
   requirement, which is where anything ambiguous surfaces, before any code exists.
4. **Build** — you pick each milestone. The developer builds it, QA tests it, a reviewer
   reads the code, and you try it before it counts as done.

The design and its reasoning are in [`SPEC.md`](SPEC.md). [`docs/walkthrough.md`](docs/walkthrough.md)
follows one requirement from your first words to agreed, table by table, through the real
tools.
