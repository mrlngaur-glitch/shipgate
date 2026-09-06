# ShipGate

**The independent completion gate for AI coding agents — the agent doesn't get to grade its own homework.**

AI coding agents constantly fall into a classic trap: declaring victory the moment code is written to disk, regardless of whether declared done-conditions actually ran or produced evidence. I built ShipGate to close that loop by establishing an independent validation layer—because an AI should never grade its own homework.

> **Status: pre-launch, Phase 3 substantially built.** The gate, the append-only ledger, the
> hooks that write to it, and the CLI below (`init` / `status` / `report` / `doctor` /
> `declare-task-class` / `analyze`) are real, tested (466 tests, Windows, local, this commit,
> shown running below; CI's own most recently recorded run — Linux, [run
> 34025172117](https://github.com/mrlngaur-glitch/shipgate/actions/runs/34025172117) —
> collected 466, 465 passed and 1 skipped, see the table below for exactly what that run
> covered; this exact commit has not yet been through CI — a push will be the next step). `shipfile.yaml` at the repo
> root defines this repository's own real done-conditions, and **as of a prior session,
> `.claude/settings.json` wires all three hooks in for real — ShipGate now gates itself,
> record-only** (`gate_policy.max_retries: 0`, the same rollout already standing for the three
> pilots — a real `gate_evaluation` is recorded every `Stop`, but the retry cap exhausts on the
> first non-green result, so it can never block this repo's own working sessions). Confirmed by
> directly invoking all three real hook entrypoints against this repo, not by inspection alone:
> real `PreToolUse`/`PostToolUse` events and a real `gate_evaluation` event now exist in
> `.shipgate/ledger.db`. **`hook-installed`'s self-poisoning bug (PHASE_PLAN.md P38 item 3) is
> now fixed, this commit** — `check_runtime_evidence` used to search every recorded event
> indiscriminately, including the gate's own `gate_evaluation` events, whose reason text can
> quote the exact marker being searched for; a real second-evaluation reproduction (a genuine
> failure followed by the gate_evaluation event recording it) was confirmed to render a false
> `verified` before the fix and confirmed `contradicted` after, via an explicit allowlist of
> genuinely-observed record types. One limitation remains, unrelated, not touched this round —
> see the evidence table below: `lint-clean` currently cannot confirm a real green (the venv
> holding `ruff` isn't on `PATH` inside a hook launched via its own absolute-interpreter
> command, so it honestly renders `unverified`, never a false `contradicted`, but also never a
> true `verified`). **This repository is now public, and CI has run for the
> first time and passed** — see the evidence table below for exactly what that run did and
> didn't prove. What is **not** true yet, stated plainly rather than implied: there is no PyPI
> package, no tagged release, no signed artifact, no dollar-cost figure anywhere in this
> project (no price tables exist — `shipgate analyze` is a real, built command, but a
> read-only cross-project ledger aggregator, not a cost calculator). A latency benchmark now
> exists and is published (`benchmarks/`) — measured honestly, it does **not** meet the <10ms
> p99 target on this Windows dev machine; see the evidence table below and
> `benchmarks/RESULTS.md` for the real number, the breakdown, and why. Install from source, as
> shown below — that is the only way to run this today.

ShipGate converts rough plain-English requests into machine-checkable contracts, and refuses to
accept an agent's work until its claims are verified against recorded evidence **by a party that
is not the agent**.

## Quickstart

Requires Python 3.12. There is no package on PyPI yet — install from a clone.

**Windows (PowerShell) — runtime-verified this session, output below:**

```powershell
git clone https://github.com/mrlngaur-glitch/shipgate.git
cd shipgate
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install --require-hashes -r requirements.lock
.venv\Scripts\python.exe -m pip install -e . --no-deps
```

**macOS / Linux (bash) — the standard equivalent of the same steps. Linux: independently run for
the first time by CI's first real run ([run 32182623079](https://github.com/mrlngaur-glitch/shipgate/actions/runs/32182623079))
— see the evidence table below. macOS: still not independently run by this project — no macOS
runner in CI and no macOS dev machine behind this repository today. If the macOS form breaks,
that is new information — say so, don't assume it works because the commands look right:**

```bash
git clone https://github.com/mrlngaur-glitch/shipgate.git
cd shipgate
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install --require-hashes -r requirements.lock
pip install -e . --no-deps
```

Both forms are the same three steps CI runs (`.github/workflows/ci.yml`, "Install
(pinned, hash-verified)") — matched at the level of *what happens* (upgrade pip, install the
hash-verified pinned set, install this package editable with no dependency resolution), not at
the level of literal shell text: CI installs into a bare runner Python with no venv step at all,
and the Windows/macOS forms above differ from each other in the interpreter invocation and the
venv's internal path (`Scripts\` vs. `bin/`). `--require-hashes` refuses to install anything
whose downloaded file doesn't match a hash already recorded in `requirements.lock` — see that
file's own header for exactly which platforms are hash-covered versus which have actually been
run, and `SECURITY.md` for the same distinction stated in full. Do **not** use
`pip install -e ".[dev]"` as a single command — it resolves dependency versions against live PyPI
ranges instead of the pinned, hashed lock file, and has been observed to drift (`Pygments`
2.21.0 resolved against this lock file's pinned 2.20.0, 2026-08-17).

Then run the suite, the same way CI does:

```
pytest
lint-imports        # core purity: no harness-specific imports in the gate/ledger core
ruff check .
```

**Real output, this session, fresh venv, a clean `git clone` of this repository's own
committed source (not the working tree) into a new temp directory — re-run, not hand-edited,
when the suite grew since the last paste (Windows; command as shown above). A `git clone` here,
not a `git archive` extraction: `test_archive_boundary.py` invokes `git` itself to inspect this
repository, and a bare `git archive` extraction has no `.git` directory for it to find — found
live, this session, by running exactly this reproduction against an archive extraction first
and watching that file fail (2 failed, 2 errored — "not a git repository", exit 128, not a real
result about anything this README claims; `test_docs_reality.py`'s own 5 tests do not touch
`git` and passed against the archive extraction unaffected), then switching to a clone, which
carries real history and passes clean:**

```
$ .venv\Scripts\python.exe -m pytest -q
........................................................................ [ 15%]
........................................................................ [ 30%]
........................................................................ [ 46%]
........................................................................ [ 61%]
........................................................................ [ 77%]
........................................................................ [ 92%]
..................................                                       [100%]
466 passed in 46.51s

$ .venv\Scripts\lint-imports.exe
=============
Import Linter
=============


---------
Contracts
---------

Analyzed 43 files, 65 dependencies.
-----------------------------------

Core is harness-agnostic (no Claude Code imports in
ledger/gate/verdicts/shipfile) KEPT

Contracts: 1 kept, 0 broken.
```

(`lint-imports` prints an ASCII-art banner some runs and not others — the same pinned 2.13
build, observed both ways across sessions; this paste shows the plain banner-less form this
run actually produced, not trimmed for aesthetics either way.)

## See it work

`shipgate init` is the first command a real user runs — a minimal interview that writes
`shipfile.yaml` (your project's own machine-checkable done-conditions), a `CLAUDE.md` your coding
agent reads, and `.claude/settings.json` wiring the hooks below into Claude Code. Never
overwrites an existing file — merges additively, or refuses and says exactly what it found.

Real output below, this session, against a fresh temp project directory — with that directory's
own absolute path replaced by `<project>` throughout, consistently standing in for the FULL path
each time it appears (a prior version of this block silently dropped part of the real path in
one line while keeping it in another — found and fixed the same way every other stale claim in
this file was: by re-running the real command and diffing against what was pasted):

```
$ shipgate init --project-dir . --intent-summary "..." --test-command "pytest -q"
[+] shipfile.yaml: written — wrote a new shipfile to <project>/shipfile.yaml
[+] CLAUDE.md: written — wrote a new CLAUDE.md
[+] .claude/settings.json: written — wrote a new <project>/.claude/settings.json

Done. Review the generated files, then start your agent session normally.
```

From there, `shipgate` is not something you run by hand — the hooks `init` just wired fire on
their own, on every tool call and at the end of every Claude Code session, and write to a local,
append-only ledger (`.shipgate/ledger.db`, git-ignored) — every one of its five tables refuses
`UPDATE`/`DELETE` outright, and its evidence-bearing tables (`events`, `claims`, `verdicts`) are
additionally hash-chained so a tampered row can be named exactly (see `SECURITY.md` and
`docs/ledger_schema_design.md` for the full scope). What follows is that real
mechanism, driven directly rather than through a live Claude Code session (which a README can't
reproduce) — the exact three hook entrypoints Claude Code invokes
(`python -m shipgate.hooks.pretooluse` / `.posttooluse` / `.stop`), fed the same JSON shape on
stdin Claude Code feeds them, run as real subprocesses against a fresh demo project with one
`tests_pass` done-condition (`pytest -q` against one real passing test). Anyone can reproduce
this exactly — the commands are the ones above, run in order.

`shipgate status` — quick, plain, closer to `git status`:

```
$ shipgate status --project-dir .
[verified] tests-pass: 1 passed, 1 collected (command: 'pytest -q')

GATE: GREEN
```

`shipgate report` — the full, screenshot-able Ship Report:

```
$ shipgate report --project-dir .
┌─────────────────────────────────────────────────────────────────────────────┐
│                                 GATE: GREEN                                 │
└─────────────────────────────────────────────────────────────────────────────┘

                                    Claims
┌───────────────────────┬──────────┬──────────────────┬───────────────────────┐
│ Claim                 │ Verdict  │ Tier             │ Reason                │
├───────────────────────┼──────────┼──────────────────┼───────────────────────┤
│ The test suite given  │ verified │ runtime-verified │ 1 passed, 1 collected │
│ to `shipgate init`    │          │                  │ (command: 'pytest     │
│ actually runs and     │          │                  │ -q')                  │
│ passes.               │          │                  │                       │
└───────────────────────┴──────────┴──────────────────┴───────────────────────┘

Blast radius: 0 high-risk change(s) self-declared this session (self-declared
only — ShipGate cannot detect an undeclared one).
Tokens this session: 0 in / 0 out / 0 cache-read (no price table exists
anywhere in this project — no command computes a dollar cost).

Ledger receipt (verdicts): #1 (8ff41cddfa0f…) through #1 (8ff41cddfa0f…) — this
project's entire claim history.
--verify: VERIFIED — entire ledger hash chain (events, claims, verdicts)
intact; the embedded verdicts range matches the ledger exactly, unchanged
```

`--verify` (on by default) independently recomputes and checks every hash-chained table
(`events`, `claims`, `verdicts`) before rendering the banner above it — a tampered row renders `GATE: INCONSISTENT — do not trust
this report` and names the disagreeing claim, not a silently vouched-for green (the taxonomy
behind `verified`/`unverified`/`unverified-vacuous`/the other four verdict classes is explained
in `docs/verdicts_explainer.md`).

The token line above reads zero because this demo drove the hooks directly rather than through a
real, billed Claude Code session — a real session's real token counts populate that line the same
way. The "no price table" clause next to it is real, not a placeholder: no price table exists
anywhere in this codebase and no command computes a dollar cost — the report states that plainly
next to the one real number it does have (tokens), rather than inventing a figure or dropping the
line silently.

### See it work without Claude Code

The walkthrough above deliberately drives the three hook entrypoints directly with `stdin` JSON,
described narratively rather than pasted verbatim — a real Claude Code session builds and sends
that JSON automatically once `shipgate init` has wired the hooks in, but a README can't reproduce
a live multi-turn agent session, so the literal payload shape was left implicit above. Two scripts
in this repo's own root close that gap explicitly, one per platform, doing exactly what's narrated
above end to end — including the literal JSON on `stdin` for the `Stop` hook call — against a
disposable demo project:

```powershell
# Windows (PowerShell)
.\demo_scenario.ps1
```

```bash
# macOS / Linux
./demo_scenario.sh
```

Both scripts: create a fresh temp project, write one passing test, run `shipgate init`, print the
generated `shipfile.yaml`/`.claude/settings.json`, run the test suite, fire a real `Stop` hook with
its literal JSON `stdin` payload shown in the script source, then run `shipgate status` and
`shipgate report` — the same sequence as above, runnable end to end without Claude Code, an agent,
or any out-of-band knowledge of the hook payload shape. `docs/QUICKSTART.md` walks through the
same sequence as a step-by-step guide rather than a single script, for a reader who wants to run
each command by hand and see what it does.

## What's built, what isn't — every claim above, one evidence class each

| # | Claim | Evidence class | Basis |
|---|---|---|---|
| 1 | The Windows install (three `pip` commands above) works end-to-end | `runtime-verified` | Run in a genuinely fresh venv this session; `pytest`/`lint-imports` output pasted above, unedited |
| 2 | The macOS/Linux install works the same way — narrowly true for the three `pip` lines only, not for `git clone` / `python3.12 -m venv .venv` / `source .venv/bin/activate` | `runtime-verified` (Linux, the three `pip` lines) / not run anywhere (Linux, the other two lines) / `disk-verified` (macOS, the whole block) | CI's first real run ([run 32182623079](https://github.com/mrlngaur-glitch/shipgate/actions/runs/32182623079)) runs `actions/checkout` (not `git clone`) and `actions/setup-python` (not `python3.12 -m venv .venv`, and no `source activate`), then this block's three `pip` commands — so only those three are CI-verified on Linux. Fair to this project's own other work: `ci.yml:158`'s audit step does run `python -m venv "$RUNNER_TEMP/auditenv"` on this same Ubuntu runner, so the `venv` *module* is demonstrably not broken on CI's Python; what's untested is the `python3.12` binary name on a stock Ubuntu (`ensurepip` ships separately as the `python3.12-venv` package there — a real, plausible failure, not a pedantic one) and the activation line. macOS: nothing in this block has run anywhere |
| 3 | Tests pass — **466, Windows, local, this commit**, and separately, **465 passed / 1 skipped of 466 collected, Linux, CI, [run 34025172117](https://github.com/mrlngaur-glitch/shipgate/actions/runs/34025172117) — not this commit**, since a push hasn't happened since this commit was made and CI only runs on push; the gap is this commit's own 37 tests added since that run (29 already counted the last time this row was updated — see git history for that breakdown — plus 8 new this round: `tests/unit/test_checkers.py`'s fix for `hook-installed`'s self-poisoning bug, PHASE_PLAN.md P38 item 3 — 3 negative-control tests, one per allowlisted genuine record type; 4 parametrized tests enumerating the full closed set of excluded self-generated record types; 1 reproducing the exact self-poisoning sequence end-to-end, confirmed RED against the pre-fix code first), not a platform difference | `runtime-verified` (both, against their own stated scope) | Windows: pasted above, this session, this commit. Linux/CI: run 34025172117 — 466 collected (minimum 466 at that commit), 465 passed, 1 skipped; the skip is `tests/integration/test_hooks_e2e.py`'s Windows-only `icacls` ACL test (`skipif(os.name != "nt")`) — the same honest platform skip CI has always shown, not a vacuous pass |
| 4 | `shipgate init` writes `shipfile.yaml` / `CLAUDE.md` / `.claude/settings.json`, never overwrites | `runtime-verified` | Real run, this session, pasted above; the never-overwrite behavior is separately tested (`tests/`) |
| 5 | The hooks write real ledger rows via the same entrypoints Claude Code invokes | `runtime-verified` | Real subprocess run of all three hook modules this session, JSON on stdin, feeding the `status`/`report` output above |
| 6 | `shipgate report` renders a verdict per claim, a blast-radius line, a token line, and a self-verifying ledger receipt | `runtime-verified` | Pasted above, unedited, this session |
| 7 | No hook makes a network call | `runtime-verified`, Windows local **and** Linux CI | `tests/integration/test_hooks_e2e.py` passes locally on Windows; CI's first real run ([run 32182623079](https://github.com/mrlngaur-glitch/shipgate/actions/runs/32182623079), Linux) executed the same test and passed. State plainly what that proves and no more: CI confirmed this one claim, end-to-end, on Linux — it did not additionally exercise the Windows-only `icacls` test, which CI itself honestly skips (see row 3) |
| 8 | Dependencies are pinned and hash-verified for the dev/CI install | `runtime-verified` (Windows, local; Linux, CI) / `disk-verified` (other hash-covered platforms) | `requirements.lock`'s own header states exactly which platforms are hash-covered vs. tested; CI's first real run ([run 32182623079](https://github.com/mrlngaur-glitch/shipgate/actions/runs/32182623079)) installed with `--require-hashes` on Linux and it succeeded, before the suite ran; `SECURITY.md` states the same split for the published-package install path |
| 9 | This project gates its own repository under the rules it ships | **True as of a prior session, record-only, with one named limitation remaining** | `.claude/settings.json` wires all three real hook entrypoints; `shipfile.yaml`'s `gate_policy.max_retries` is `0` (record-only — the same rollout already standing for the three pilots). `runtime-verified` by directly invoking `PreToolUse`/`PostToolUse`/`Stop` against this repo: real events and a real `gate_evaluation` now exist in `.shipgate/ledger.db`; `Stop` released without blocking, as designed. **`hook-installed`'s self-poisoning bug (PHASE_PLAN.md P38 item 3) is fixed, this commit** — see row 3's own breakdown; `check_runtime_evidence` now searches only an explicit allowlist of genuinely-observed record types, never the gate's own `gate_evaluation`/`gate_shipfile_invalid`/`high_risk_change`/`high_risk_change_refused` commentary. **One real limitation remains, found live in a prior session, not touched this round:** `lint-clean` (`command_succeeds`, `ruff check .`) cannot currently confirm a true `verified` — the venv holding `ruff` isn't on `PATH` inside a hook launched via its own absolute-interpreter `settings.json` command, so it honestly renders `unverified` (the Sixth-founder-finding fix working as intended — not a false `contradicted`, but also not a green). |
| 10 | A published, CI-measured <10ms p99 hook-latency benchmark exists | **Built and published this session (`benchmarks/`); does NOT meet the target** | `runtime-verified`, Windows local — `benchmarks/latency_bench.py` measures the real `python -m shipgate.hooks.<name>` subprocess end-to-end (not an in-process call), with a vacuous-pass guard proven to fire against a real no-op process (`tests/unit/test_latency_bench.py`). Five real local runs across this session and the one before it: worst-path p99 ranged 115–221 ms, roughly 12–22x the target, high run-to-run variance on this machine — the most recently committed run (`benchmarks/RESULTS.md`) measured 173.242 ms. Wired into `ci.yml` as an advisory (not hard-blocking) step — full breakdown and an honest recommendation in `benchmarks/RESULTS.md`. Not yet CI-measured: no push has happened since this landed, so CI's own (likely lower, Linux, dedicated-runner) number does not exist yet |
| 11 | A dollar-cost figure appears on the Ship Report | **Does not exist** | No price tables exist anywhere in this codebase — `shipgate analyze` is now built (a read-only cross-project ledger aggregator over this project's own ledger files) but does not compute cost and isn't planned to; the report states the gap inline (see above) rather than inventing a number |
| 12 | This project has run in CI, has a tagged release, or is on PyPI | **Does not exist yet** | Pre-launch; install from source only, as shown above |

## Why this matters

I built this because I kept watching agents commit three distinct classes of quiet failures:

The Ghost Success: An agent creates or updates a file on disk, assumes the job is done, and marks the task complete without ever attempting to execute it.

The Partial Fix: An agent modifies a script to solve an edge case, but breaks existing core functionality because it never re-ran the verification suite.

The Hallucinated Verification: An agent outputting "all tests passed" in its response prose while the actual CLI logs underneath show unhandled exceptions or failed assertions.

Without an independent gate recording hash-chained proof, these failures drift into production undetected, draining human developer hours to debug phantom completions.

## Repository map

| Path | What it is |
|---|---|
| `shipgate/` | The core package — ledger, gate, verdict taxonomy, checkers, hooks, CLI |
| `tests/` | 466 tests (unit + integration) — the real evidence behind every `runtime-verified` row above. 466 pass / 0 skipped locally on Windows, this commit; CI's own most recently recorded run (run 34025172117, not this commit — see the evidence table above) saw 465 pass / 1 skipped of 466 collected on Linux (the Windows-only `icacls` test) |
| `reporters/` | Per-test-runner reporters (`pytest` today; the vacuous-pass detection `tests_pass` relies on) |
| `docs/verdicts_explainer.md` | The 7-class verdict taxonomy, plain-language, frozen since Gate A |
| `docs/shipfile_worked_example.yaml` (+ `.md`) | A fuller worked `shipfile.yaml` than `shipgate init` generates |
| `docs/ledger_schema_design.md` | How the append-only ledger (and its hash-chained tables) is structured |
| `docs/jsonl_format_notes.md` | Notes on the Claude Code transcript format the hooks read |
| `docs/QUICKSTART.md` | Step-by-step install → init → first session → status/report walkthrough, expanding on this README's own Quickstart |
| `demo_scenario.ps1` / `demo_scenario.sh` | Scripted, reproducible run of the same sequence — see "See it work without Claude Code" above; includes the literal hook `stdin` JSON this README describes narratively |
| `shipfile.yaml` | This repo's own shipfile, defining real done-conditions — **wired live as of this session, record-only** (see evidence table row 9): `.claude/settings.json` now exists, `gate_policy.max_retries` is `0` |
| `SECURITY.md` | Threat model, what's enforced today and how to verify it yourself, known limitations stated rather than hidden |

## Security

See `SECURITY.md` — how to report a vulnerability, what's actually enforced today versus
disk-verified-but-CI-unconfirmed, and the known limitations this project states about itself
rather than leaves for someone else to find.

## What's next

Ships Now: Core CLI engine, shipfile.yaml parsing, independent verification runner, hash-chained append-only execution ledger, local verification reports, and cross-project ledger aggregation through shipgate analyze.

Ships Later: Signed release artifacts with GitHub attestation, dollar-cost attribution with price tables for verification runs, and latency optimizations toward the <10 ms p99 hook-latency target.

## Licence

Apache-2.0. See `LICENSE`.

Built by Laxmi Narayan, FRM — after 100 days auditing an AI coding agent's output on a
long project, and finding it graded its own homework.

## Contributing

Contributions are welcome—if you find a bug or want to support additional done_conditions, open an issue or submit a pull request.
