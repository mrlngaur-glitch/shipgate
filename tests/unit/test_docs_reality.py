"""tests/unit/test_docs_reality.py

Guards against the exact recurring defect this project has now hit three times:
README.md / SECURITY.md stating a test count, or citing a "most recent" CI run, that
has drifted from reality. Mechanical enforcement, not memory -- built and watched RED
against the stale docs it exists to catch (the assertion messages below name the exact
stale value found) before either file was corrected in the same session. A test authored
after the fix proves nothing.

Three independent checks, each derived from a real source at test time, never hardcoded:

1. Test count. "Reality" is TWO independently-derived numbers that must already agree
   with each other before either is trusted against the docs: the actual collected
   count (`pytest --collect-only`, run fresh, this process) and
   `.github/workflows/ci.yml`'s own MIN_TESTS floor (this project's own discipline
   keeps these exactly equal at every commit -- if they disagree, that is itself a real
   defect, asserted first, independent of anything README.md says). Every "this
   commit" test-count claim in README.md is then checked against that number, one
   explicit, literal regex per claim -- not a blanket digit-scan, so a genuinely
   historical, frozen number (what a SPECIFIC named past CI run collected) is never
   forced to track a suite that has since grown past it.
2. Run-link recency. "The most recent run" has no representation anywhere in this repo,
   independent of README.md/SECURITY.md themselves, except one thing this project
   controls directly: ci.yml's own LATEST_CI_RUN_ID comment, updated by hand at the same
   time as MIN_TESTS. This test does NOT call GitHub's API to ask what the actual latest
   run is -- that would be a network call from a test, and this project makes no network
   calls from anywhere, hooks included (see SECURITY.md). What it DOES check, with no
   network access at all: every GitHub Actions run URL cited in README.md or
   SECURITY.md, except the ones both files intentionally keep as permanent historical
   citations (EXEMPT_HISTORICAL_RUN_IDS below -- e.g. "CI's first real run ever"), must
   cite ci.yml's own recorded LATEST_CI_RUN_ID. This catches the defect that is actually
   preventable locally -- README and SECURITY.md citing two different "latest" runs, or
   either one stale relative to a run this repo already knows about -- without claiming
   to verify GitHub's live state, which is not possible here without a network call.
3. Benchmark numbers. README.md's evidence-table row 10 hand-states the committed run's
   worst-path p99 latency (e.g. "measured 173.242 ms"), and `benchmarks/RESULTS.md`'s own
   generated header states the same figure. "Reality" here is
   `benchmarks/results/latest.json` -- the actual data artifact the benchmark script
   writes, read fresh at test time, never hardcoded. Found live: Defect 1
   (`PHASE_PLAN.md` P34) was exactly this drift one file over -- a plan's prose
   undercounting the artifact committed beside it in the same commit. This closes the
   same gap for README.md and RESULTS.md, the two files most likely to read badly if
   `benchmarks/` is ever regenerated without re-pasting both numbers by hand.

The anti-vacuous guard: every extraction below asserts it matched something before any
comparison is allowed to mean anything -- a regex that silently matches zero occurrences
fails loud, not "passes" by finding nothing to disagree with.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
README = REPO_ROOT / "README.md"
SECURITY = REPO_ROOT / "SECURITY.md"
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"
BENCHMARK_RESULTS_JSON = REPO_ROOT / "benchmarks" / "results" / "latest.json"
BENCHMARK_RESULTS_MD = REPO_ROOT / "benchmarks" / "RESULTS.md"

# Run IDs each doc is allowed to cite forever, independent of whatever the actual
# latest run is -- each is a deliberate, permanent citation of a SPECIFIC historical
# event, not a claim about current state. Adding an entry here is a real editorial
# decision, not a way to silence this test -- see the surrounding prose at each
# citation site for why it is exempt. Deliberately PER-FILE, not shared: SECURITY.md
# maintains a running first/second/third/... history where an old run ID is always
# meant to stay frozen, but README.md cites the same old run ID (32288583970) in a
# single "this is the latest" sentence -- there, it is NOT historical, and must keep
# tracking LATEST_CI_RUN_ID like any other non-exempt citation. A shared/global exempt
# set would have silently let README's citation go stale forever; this test was caught
# doing exactly that once, while it was still being written, before this comment or the
# split below existed -- fixed by scoping exemption to the file whose own prose
# actually makes the citation historical.
README_EXEMPT_HISTORICAL_RUN_IDS = {
    "32182623079",  # CI's first real run ever, on this repository -- cited 4x in
                     # README.md, always in "run for the first time" framing, never
                     # as "the latest"
}
SECURITY_EXEMPT_HISTORICAL_RUN_IDS = {
    "32182623079",  # CI's first real run ever, on this repository
    "32221241397",  # CI's second real run (MIN_TESTS 397) -- SECURITY.md's own history
    "32288583970",  # CI's third real run (MIN_TESTS 425) -- SECURITY.md's own history
    "32295996939",  # CI's fourth real run (MIN_TESTS 429) -- SECURITY.md's own history
    "32631982636",  # CI's fifth real run (MIN_TESTS 466) -- SECURITY.md's own history
    "34024918565",  # CI's sixth real run (MIN_TESTS 466, public-repo re-sync) -- SECURITY.md's own history
}


def _read(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert text, f"{path} is empty -- cannot check anything against an empty file"
    return text


def _min_tests() -> int:
    text = _read(CI_YML)
    match = re.search(r"^\s*MIN_TESTS:\s*\"?(\d+)\"?\s*$", text, re.MULTILINE)
    assert match, "ci.yml has no 'MIN_TESTS: N' line -- vacuous, not a real value"
    return int(match.group(1))


def _latest_ci_run_id() -> str:
    text = _read(CI_YML)
    match = re.search(r"LATEST_CI_RUN_ID:\s*\"?(\d+)\"?", text)
    assert match, "ci.yml has no LATEST_CI_RUN_ID comment -- vacuous, not a real value"
    return match.group(1)


@pytest.fixture(scope="module")
def collected_count() -> int:
    """The actual, real, current collected count -- run fresh, this process, never
    hardcoded. A module-scope fixture so the two tests that need it don't each pay for
    a separate subprocess collection pass."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    match = re.search(r"(\d+) tests? collected", result.stdout)
    assert match, (
        f"pytest --collect-only produced no 'N tests collected' summary line -- "
        f"vacuous, not a real count. stdout tail: {result.stdout[-500:]!r}"
    )
    return int(match.group(1))


def test_min_tests_matches_actual_collected_count(collected_count):
    """The two independent 'reality' sources must already agree before either is used
    to check the docs below -- if this fails, ci.yml's own MIN_TESTS is the defect,
    not README.md/SECURITY.md."""
    assert _min_tests() == collected_count, (
        f"ci.yml's MIN_TESTS ({_min_tests()}) has drifted from the actual collected "
        f"test count ({collected_count}) -- raise (never lower) MIN_TESTS with a new "
        f"supersession comment, per its own ledger's own stated discipline."
    )


# One literal, explicit anchor per "this commit" test-count claim in README.md -- not a
# blanket digit-scan, so a genuinely historical/frozen number (what a SPECIFIC past CI
# run collected) is never forced to track a suite that has since grown past it.
README_CURRENT_COUNT_PATTERNS = {
    "intro blockquote, Windows/local count (README.md ~line 7)":
        r"tested \((\d+) tests, Windows, local, this commit",
    "evidence table row 3, Windows column (README.md ~line 191)":
        r"\*\*(\d+), Windows, local, this commit\*\*",
    # pytest appends " (H:MM:SS)" to its own summary line once a run crosses 60s wall
    # time (a real formatting behavior, not a wording choice this project made) --
    # found live, this session, when a genuinely clean re-run happened to land at
    # 61.01s: the un-widened version of this pattern matched nothing at all, which
    # is exactly this test's own vacuous-pass failure mode turned on itself.
    "pasted pytest output block, the real `pytest -q` summary line (README.md ~line 87)":
        r"\n(\d+) passed in [\d.]+s(?: \(\d+:\d{2}:\d{2}\))?\n",
    "repository map, tests/ row (README.md ~line 207)":
        r"`tests/` \| (\d+) tests \(unit \+ integration\)",
}


def test_readme_current_test_count_claims_match_reality(collected_count):
    readme = _read(README)
    for label, pattern in README_CURRENT_COUNT_PATTERNS.items():
        match = re.search(pattern, readme)
        assert match, (
            f"{label}: pattern {pattern!r} matched nothing in README.md -- either the "
            f"prose was reworded (update this test's pattern to match the new wording) "
            f"or the claim was silently dropped (put it back). A check that observed "
            f"nothing never renders green."
        )
        assert int(match.group(1)) == collected_count, (
            f"{label}: README.md says {match.group(1)}, actual collected count is "
            f"{collected_count}. Stale prose number -- this is the exact defect this "
            f"test exists to catch."
        )


def _run_url_ids(text: str) -> list[str]:
    return re.findall(
        r"github\.com/mrlngaur-glitch/shipgate/actions/runs/(\d+)", text
    )


def test_readme_and_security_run_citations_match_the_recorded_latest():
    latest = _latest_ci_run_id()
    readme_runs = _run_url_ids(_read(README))
    security_runs = _run_url_ids(_read(SECURITY))
    assert readme_runs, "README.md cites no CI run URL at all -- vacuous, not a real check"
    assert security_runs, "SECURITY.md cites no CI run URL at all -- vacuous, not a real check"
    stale = [
        (fname, run_id)
        for fname, runs, exempt in (
            ("README.md", readme_runs, README_EXEMPT_HISTORICAL_RUN_IDS),
            ("SECURITY.md", security_runs, SECURITY_EXEMPT_HISTORICAL_RUN_IDS),
        )
        for run_id in runs
        if run_id not in exempt and run_id != latest
    ]
    assert not stale, (
        f"Run URL(s) citing neither the recorded latest run ({latest}) nor an "
        f"explicitly exempt historical one (per-file): {stale}. Either ci.yml's "
        f"LATEST_CI_RUN_ID is stale (a new run happened -- update it), or the doc "
        f"cites an old run that needs updating to the latest, or a genuinely "
        f"historical citation is missing from that file's own exempt set above, with "
        f"a stated reason."
    )


def _committed_worst_p99_ms() -> float:
    """The worst (max) p99 across every measured hook path in the actually-committed
    benchmark data artifact -- the same "worst measured hook path" both RESULTS.md's
    generated header and README.md's evidence-table row 10 claim to report. Read from
    the JSON latency_bench.py itself writes, never hardcoded, so this test checks
    whatever the most recently regenerated-and-committed run actually produced."""
    text = _read(BENCHMARK_RESULTS_JSON)
    data = json.loads(text)
    hooks = data.get("hooks")
    assert hooks, (
        f"{BENCHMARK_RESULTS_JSON} has no 'hooks' list, or it's empty -- vacuous, not "
        f"a real benchmark artifact."
    )
    return max(h["p99_ms"] for h in hooks)


def test_readme_committed_p99_matches_latest_results_json():
    """Defect 1's mechanism (PHASE_PLAN.md P34), one file over: README row 10 states a
    specific committed p99 by hand ("...measured 173.242 ms"). If benchmarks/ is
    regenerated and results/latest.json is re-committed without also re-pasting this
    sentence, the hand-typed number goes stale silently -- exactly the gap that produced
    Defect 1. This anchors it so a future regeneration fails loud instead of drifting."""
    readme = _read(README)
    pattern = r"most recently committed run \(`benchmarks/RESULTS\.md`\) measured (\d+\.\d+) ms"
    match = re.search(pattern, readme)
    assert match, (
        f"pattern {pattern!r} matched nothing in README.md -- either the prose was "
        f"reworded (update this test's pattern to match the new wording) or the claim "
        f"was silently dropped (put it back). A check that observed nothing never "
        f"renders green."
    )
    committed = _committed_worst_p99_ms()
    claimed = float(match.group(1))
    assert claimed == committed, (
        f"README.md row 10 claims the committed run measured {claimed} ms; "
        f"benchmarks/results/latest.json's actual worst hook p99 is {committed} ms. "
        f"Stale prose number after a benchmark regeneration -- the exact defect this "
        f"test exists to catch. Regenerate via `python -m benchmarks.latency_bench` and "
        f"re-paste README row 10, don't hand-edit the digit."
    )


def test_results_md_worst_p99_matches_latest_results_json():
    """RESULTS.md is generated by the same script, in the same run, as latest.json, and
    should never disagree with it -- but RESULTS.md is committed prose, hand-editable,
    and Defect 1 already proved a generated file's own header text can undercount its
    sibling data artifact. Checked directly here, not assumed from "they're generated
    together"."""
    results_md = _read(BENCHMARK_RESULTS_MD)
    pattern = r"Worst measured hook path: `[\w.]+` at \*\*(\d+\.\d+) ms\*\* p99"
    match = re.search(pattern, results_md)
    assert match, (
        f"pattern {pattern!r} matched nothing in benchmarks/RESULTS.md -- either the "
        f"generator's wording changed (update this test's pattern to match, and check "
        f"why _render_markdown in latency_bench.py changed without this test noticing) "
        f"or the claim was silently dropped. A check that observed nothing never "
        f"renders green."
    )
    committed = _committed_worst_p99_ms()
    claimed = float(match.group(1))
    assert claimed == committed, (
        f"benchmarks/RESULTS.md's own header claims worst p99 {claimed} ms; "
        f"benchmarks/results/latest.json's actual worst hook p99 is {committed} ms. "
        f"These two files are meant to be generated together in the same run -- if "
        f"they disagree, regenerate both via `python -m benchmarks.latency_bench`, "
        f"never hand-edit either one."
    )
