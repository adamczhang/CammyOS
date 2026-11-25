# CammyOS project working notes

## Start here

Read in this order: `README.md` (what Cammy is), `docs/STATUS.md` (where things stand),
`docs/PLAN.md` ("Now": what is next, in order). Then only the part you are working on:
`docs/C3.md` (PC voice), `architecture.md` (hardware), `docs/README.md` (index of evidence
reports). Hindsight holds the decisions behind them.

## Keep the architecture current

Adam requires `architecture.md` to be updated periodically as camera functionality
is learned. Update the relevant sections at meaningful investigation checkpoints
and before handing back substantial findings. Include dates and evidence links;
distinguish live observations, static-code findings, hypotheses and untested paths.
Correct superseded statements instead of accumulating contradictory appendices.
Keep raw logs, credentials and sensor evidence out of the architecture and git;
link to concise investigation reports and retain exact checkpoints privately.

## One plan, one status

`docs/PLAN.md` is the only forward plan and `docs/STATUS.md` is the only current-state
handoff. Part documents such as `docs/C3.md` describe how a part works; their to-do items
go in PLAN.md. Change those files instead of adding new plan, roadmap or milestone documents.
Put dated evidence reports in `docs/`, list them in `docs/README.md`, and move
superseded documents to `docs/archive/`. Never edit or move the docs hashed by
`tools/rc06_build.py` or `tools/trial_package/package.py` (listed in `docs/README.md`).
