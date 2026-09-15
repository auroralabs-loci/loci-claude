"""The eval harness's own accounting — verified without ever calling `claude`.

Two things are pinned here.

**The model is pinned, and provably.** Only the grader used to carry `--model`;
the main `claude -p` call inherited whatever default the operator's machine had,
so a recorded eval result could not be attributed to a model after the fact. The
flag now exists on both, and the tests below prove it reaches the real argv by
driving `run_evals.sh --dry-run` with a `claude` on PATH that does nothing but
record that it was called. That tripwire is the point: every test in this file
asserts the file it writes stays empty. Evals are metered on the fattest prose in
the repo, and no test may spend a token of it.

**A manifest is attributed to an eval by identity, not by mtime.** The harness
used to score every manifest written since an eval started, which credits the
wrong eval as soon as two of them run in parallel against the one shared
BLE_ROOT. The turn id comes from `.data.turn` in `loci analyse prepare`'s own
envelope — output the eval produced, so nothing rests on injected context
reaching the transcript. `prepare` stamps the same `turn` into the manifest and
`measure` copies `turn` + `manifest_id` onto the run record, so attribution keys
on that pair; an eval whose turn id cannot be learned is named in the report
rather than back-filled by time.

**The match rate is reported with its caveat attached.** The fixtures compile at
-O0, where only the structural heuristics act, so any match rate measured on them
is a lower bound — and the helper that formats the number carries that sentence,
rather than leaving it to whoever quotes it.

**An eval leaves the fixture as it found it, and says which plugin answered.**
Three T14 passes of `pf-critical-1` edited the BLE checkout — under plan mode —
and the pass in between then measured a tree that already had the change in it.
Two answers: the edit tools are denied at the tool layer for plan-mode evals, and
whatever a run does change is restored and named in `fixture_dirty_after_run`.
The plugin under test moved the same way — the working tree by default, so a
branch's edit-flow evals actually run, with the installed copy behind
`--installed-plugin` and the skew guard scoped to it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
LIB = PLUGIN_ROOT / "lib" / "eval-metrics.sh"
RUNNER = PLUGIN_ROOT / "run_evals.sh"


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (r"C:\Program Files\Git\usr\bin\bash.exe",
                     r"C:\Program Files (x86)\Git\usr\bin\bash.exe"):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


def _to_bash_path(p: Path) -> str:
    import re
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s


BASH = _find_bash()
pytestmark = pytest.mark.skipif(
    BASH is None or shutil.which("jq") is None,
    reason="bash and jq required")


def sh(script: str, **env) -> str:
    """Run a snippet with `lib/eval-metrics.sh` sourced."""
    full = f'set -euo pipefail\nsource "{_to_bash_path(LIB)}"\n{script}'
    # `encoding="utf-8"` on both calls: the runner and the metrics lib print UTF-8
    # (box glyphs, arrows, an em-dash in the caveat) and `text=True` alone decodes
    # with the locale codec -- cp1252 on Windows, where the reader thread died on
    # byte 0x81 and `proc.stdout` came back None. Green on LF hosts, red on every
    # Windows clone, which is how PR #259 shipped it.
    proc = subprocess.run([BASH, "-c", full], capture_output=True, text=True,
                          encoding="utf-8", env={**os.environ, **env})
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


# ── eval_metrics_json: the result event, kept ────────────────────────────────

STREAM = [
    {"type": "system", "subtype": "init", "session_id": "s-1"},
    {"type": "assistant", "message": {"model": "claude-sonnet-4-5",
                                      "content": [{"type": "text", "text": "hi"}]}},
    {"type": "result", "num_turns": 7, "total_cost_usd": 0.4213,
     "duration_ms": 91000, "stop_reason": "end_turn", "is_error": False,
     "session_id": "s-1",
     "usage": {"input_tokens": 120, "output_tokens": 44,
               "cache_read_input_tokens": 30000}},
]


def _stream(tmp_path: Path, events=STREAM) -> Path:
    path = tmp_path / "full.json"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n",
                    encoding="utf-8")
    return path


def test_metrics_carry_usage_turns_cost_and_wall_clock(tmp_path):
    out = json.loads(sh(
        f'eval_metrics_json "{_to_bash_path(_stream(tmp_path))}" sonnet 137 single'))
    assert out["num_turns"] == 7
    assert out["total_cost_usd"] == pytest.approx(0.4213)
    assert out["usage"]["input_tokens"] == 120
    assert out["usage"]["cache_read_input_tokens"] == 30000
    assert out["wall_clock_s"] == 137
    assert out["model_requested"] == "sonnet"
    assert out["flow"] == "single"
    assert out["duration_ms"] == 91000
    assert out["stop_reason"] == "end_turn"


def test_metrics_record_the_model_that_actually_answered(tmp_path):
    # `--model sonnet` is what was asked for; the assistant events say what
    # served it. Both are kept, because an alias can resolve anywhere.
    out = json.loads(sh(
        f'eval_metrics_json "{_to_bash_path(_stream(tmp_path))}" sonnet 1 single'))
    assert out["models"] == ["claude-sonnet-4-5"]
    assert out["model_requested"] == "sonnet"


def test_metrics_survive_a_transcript_with_no_result_event(tmp_path):
    # A timeout leaves a partial transcript. Accounting must degrade, not die.
    partial = _stream(tmp_path, STREAM[:2])
    out = json.loads(sh(
        f'eval_metrics_json "{_to_bash_path(partial)}" sonnet 600 single'))
    assert out["num_turns"] is None and out["total_cost_usd"] is None
    assert out["wall_clock_s"] == 600


def test_metrics_survive_a_missing_file(tmp_path):
    out = json.loads(sh(
        f'eval_metrics_json "{_to_bash_path(tmp_path / "nope.json")}" sonnet 0 edit'))
    assert out["model_requested"] == "sonnet" and out["flow"] == "edit"


def test_two_turn_metrics_total_both_billed_calls(tmp_path):
    t1, t2 = tmp_path / "t1.json", tmp_path / "t2.json"
    t1.write_text(json.dumps({"num_turns": 3, "total_cost_usd": 0.1,
                              "wall_clock_s": 40}), encoding="utf-8")
    t2.write_text(json.dumps({"num_turns": 5, "total_cost_usd": 0.25,
                              "wall_clock_s": 60}), encoding="utf-8")
    out = tmp_path / "merged.json"
    sh(f'eval_metrics_merge "{_to_bash_path(out)}" '
       f'"turn1={_to_bash_path(t1)}" "turn2={_to_bash_path(t2)}"')
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert merged["turn1"]["num_turns"] == 3
    assert merged["totals"]["num_turns"] == 8
    assert merged["totals"]["total_cost_usd"] == pytest.approx(0.35)
    assert merged["totals"]["wall_clock_s"] == 100


# ── the optimization level the match rate was measured at ────────────────────

@pytest.mark.parametrize("flags,expected", [
    (["-g", "-O0", "-mthumb"], "-O0"),
    (["-Os"], "-Os"),
    (["-O2", "-g"], "-O2"),
    # No -O flag is -O0 — what the compiler does. Calling it "unknown" would
    # hide exactly the case the lower-bound caveat exists for.
    (["-g", "-mthumb"], "-O0"),
    # Last wins, as on a real command line.
    (["-O2", "-O0"], "-O0"),
])
def test_the_opt_level_comes_from_the_build_record(tmp_path, flags, expected):
    meta = tmp_path / "x.o.meta.json"
    meta.write_text(json.dumps({"flags": flags}), encoding="utf-8")
    assert sh(f'opt_level_from_meta "{_to_bash_path(meta)}"').strip() == expected


def test_no_build_record_is_unknown_not_a_guess(tmp_path):
    assert sh(f'opt_level_from_meta "{_to_bash_path(tmp_path / "gone")}"').strip() \
        == "unknown"


# ── match rows: (proposed, selection) per measured manifest ──────────────────

def _tree(tmp_path: Path, *, proposed, selection, state="measured",
          flags=("-O0",)) -> tuple[Path, Path]:
    root = tmp_path / "proj"
    obj_dir = root / ".loci" / "build" / "objects" / "armv6-m" / "src"
    obj_dir.mkdir(parents=True)
    (obj_dir / "comms.o.meta.json").write_text(
        json.dumps({"flags": list(flags)}), encoding="utf-8")
    manifests = root / ".loci" / "build" / "turns" / "9f3a" / "manifests"
    manifests.mkdir(parents=True)
    path = manifests / "m-abcdef123456.json"
    path.write_text(json.dumps({
        "id": "m-abcdef123456", "state": state, "turn": "t-1",
        "artifacts": {"before": {}, "after": {".loci/build/objects/armv6-m/src/comms.o": "sha256:x"}},
        "proposed": proposed, "selection": selection,
    }), encoding="utf-8")
    return root, path


def test_a_confirmed_top_candidate_scores_as_a_match(tmp_path):
    root, manifest = _tree(tmp_path, proposed=["p1", "p3"], selection=["p1", "p3"])
    row = json.loads(sh(
        f'match_row "{_to_bash_path(manifest)}" "{_to_bash_path(root)}" pf-1'))
    assert row["proposed_n"] == 2 and row["confirmed"] == 2
    assert row["overridden"] == []
    assert row["opt_level"] == "-O0" and row["lower_bound"] is True
    assert row["eval"] == "pf-1" and row["manifest"] == "m-abcdef123456"


def test_an_overridden_candidate_is_named_not_just_counted(tmp_path):
    root, manifest = _tree(tmp_path, proposed=["p1", "p3"], selection=["p1", "p4"])
    row = json.loads(sh(
        f'match_row "{_to_bash_path(manifest)}" "{_to_bash_path(root)}" pf-1'))
    assert row["confirmed"] == 1 and row["overridden"] == ["p4"]


def test_an_unmeasured_manifest_is_not_scored(tmp_path):
    # `prepare` alone carries no agent decision — scoring it would count a
    # selection that was never made.
    root, manifest = _tree(tmp_path, proposed=["p1"], selection=[],
                           state="prepared")
    assert sh(f'match_row "{_to_bash_path(manifest)}" "{_to_bash_path(root)}" x') == ""


def test_a_production_optimization_level_is_not_a_lower_bound(tmp_path):
    root, manifest = _tree(tmp_path, proposed=["p1"], selection=["p1"],
                           flags=("-Os",))
    row = json.loads(sh(
        f'match_row "{_to_bash_path(manifest)}" "{_to_bash_path(root)}" x'))
    assert row["opt_level"] == "-Os" and row["lower_bound"] is False


def _envelope(turn: str, *, state="prepared", around=False) -> dict:
    body = json.dumps({"ok": True, "data": {
        "id": "m-abcdef123456", "state": state, "turn": turn}})
    text = f"prepared\n{body}\ndone" if around else body
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "content": [{"type": "text", "text": text}]}]}}


def _transcript(tmp_path: Path, *events, name="full.json") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n",
                    encoding="utf-8")
    return path


def test_the_turn_id_comes_from_prepares_own_envelope(tmp_path):
    # `.data.turn` — the same spelling as the manifest field and the run record.
    # Read from output the eval produced, not from the context line the
    # UserPromptSubmit hook injects, so nothing depends on injected context
    # reaching the transcript.
    path = _transcript(tmp_path, _envelope("prm_01k3aaa"),
                       _envelope("prm_01k3bbb", around=True),
                       _envelope("prm_01k3aaa"))
    out = sh(f'turn_ids_from_prepare "{_to_bash_path(path)}"').split()
    assert out == ["prm_01k3aaa", "prm_01k3bbb"]


def test_an_eval_that_never_prepared_has_no_turn_id(tmp_path):
    # No `prepare` call means no manifest to attribute either, so the
    # `no-turn-id` path is the right answer rather than a miss. `jq` finds
    # nothing here, and an unguarded non-zero would abort the eval mid-flow
    # under the harness's `set -eo pipefail` — the verdict never gets written.
    path = _transcript(tmp_path,
                       {"type": "assistant", "message": {"content": []}},
                       name="bare.json")
    assert sh(f'turn_ids_from_prepare "{_to_bash_path(path)}"; echo done').split() \
        == ["done"]


def test_a_truncated_transcript_yields_no_turn_id_rather_than_an_error(tmp_path):
    path = tmp_path / "partial.json"
    path.write_text('{"type":"user","message":{"content":[{"type":"tool_res',
                    encoding="utf-8")
    assert sh(f'turn_ids_from_prepare "{_to_bash_path(path)}"; echo done').split() \
        == ["done"]


def test_the_scan_keys_on_the_turn_not_on_when_the_file_was_written(tmp_path):
    root, manifest = _tree(tmp_path, proposed=["p1"], selection=["p1"])
    rows = tmp_path / "rows.jsonl"
    # A manifest written long before this eval still counts when the turn matches:
    # nothing in attribution reads a timestamp any more.
    os.utime(manifest, (1_000_000, 1_000_000))
    sh(f'match_scan "{_to_bash_path(root / ".loci" / "build")}" "{_to_bash_path(root)}" '
       f'tag "{_to_bash_path(rows)}" "t-1"')
    row = json.loads(rows.read_text(encoding="utf-8").strip())
    assert row["manifest"] == "m-abcdef123456" and row["turn"] == "t-1"
    assert row["attributed_by"] == "manifest"


def test_the_scan_ignores_a_manifest_from_another_evals_turn(tmp_path):
    # The mtime fence's failure mode: two single-turn evals share BLE_ROOT, so
    # "written since I started" credits one eval with the other's manifest.
    root, manifest = _tree(tmp_path, proposed=["p1"], selection=["p1"])
    rows = tmp_path / "rows.jsonl"
    sh(f'match_scan "{_to_bash_path(root / ".loci" / "build")}" "{_to_bash_path(root)}" '
       f'tag "{_to_bash_path(rows)}" "t-someone-else"')
    assert not rows.exists()


def test_a_turn_whose_manifest_was_never_measured_scores_nothing_and_returns_ok(tmp_path):
    # `prepare` without `measure` is the common outcome of a failed eval. The
    # scan must come back clean, not non-zero: the caller runs under `set -e`.
    root, manifest = _tree(tmp_path, proposed=["p1"], selection=[],
                           state="prepared")
    rows = tmp_path / "rows.jsonl"
    sh(f'match_scan "{_to_bash_path(root / ".loci" / "build")}" "{_to_bash_path(root)}" '
       f'tag "{_to_bash_path(rows)}" "t-1"; echo done')
    assert not rows.exists()


def test_the_run_record_is_the_attribution_key_when_one_exists(tmp_path):
    root, manifest = _tree(tmp_path, proposed=["p1", "p2"], selection=["p1"])
    runs = tmp_path / "loci-skill-runs-x.jsonl"
    runs.write_text(json.dumps({"skill": "measure", "turn": "t-1",
                                "manifest_id": "m-abcdef123456"}) + "\n",
                    encoding="utf-8")
    rows = tmp_path / "rows.jsonl"
    sh(f'match_scan "{_to_bash_path(root / ".loci" / "build")}" "{_to_bash_path(root)}" '
       f'tag "{_to_bash_path(rows)}" "t-1" "{_to_bash_path(runs)}"')
    row = json.loads(rows.read_text(encoding="utf-8").strip())
    assert row["attributed_by"] == "run-record"
    assert row["manifest"] == "m-abcdef123456"


def test_the_basis_is_decided_per_turn_and_never_leaks_between_them(tmp_path):
    """One eval, two turns: the first answered by a run record, the second only by the
    manifest on disk. `attributed_by` was computed once for the scan and kept, so the
    second row claimed a run record that was never found — a diagnostic field asserting
    the stronger of the two attributions."""
    root, manifest = _tree(tmp_path, proposed=["p1"], selection=["p1"])
    second = manifest.parent / "m-fedcba654321.json"
    second.write_text(json.dumps({
        "id": "m-fedcba654321", "state": "measured", "turn": "t-2",
        "artifacts": {"before": {},
                      "after": {".loci/build/objects/armv6-m/src/comms.o": "sha256:x"}},
        "proposed": ["p1"], "selection": ["p1"],
    }), encoding="utf-8")
    runs = tmp_path / "loci-skill-runs-x.jsonl"     # names t-1 only
    runs.write_text(json.dumps({"skill": "measure", "turn": "t-1",
                                "manifest_id": "m-abcdef123456"}) + "\n",
                    encoding="utf-8")
    rows = tmp_path / "rows.jsonl"
    sh(f'match_scan "{_to_bash_path(root / ".loci" / "build")}" "{_to_bash_path(root)}" '
       f'tag "{_to_bash_path(rows)}" "t-1 t-2" "{_to_bash_path(runs)}"')
    by_id = {json.loads(l)["manifest"]: json.loads(l)
             for l in rows.read_text(encoding="utf-8").splitlines() if l.strip()}
    assert by_id["m-abcdef123456"]["attributed_by"] == "run-record"
    assert by_id["m-fedcba654321"]["attributed_by"] == "manifest"


def test_an_unknown_turn_is_reported_not_silently_backfilled_by_mtime(tmp_path):
    root, manifest = _tree(tmp_path, proposed=["p1"], selection=["p1"])
    rows = tmp_path / "rows.jsonl"
    sh(f'match_scan "{_to_bash_path(root / ".loci" / "build")}" "{_to_bash_path(root)}" '
       f'pf-7 "{_to_bash_path(rows)}" ""')
    row = json.loads(rows.read_text(encoding="utf-8").strip())
    assert row == {"eval": "pf-7", "attribution": "no-turn-id",
                   "note": row["note"]}
    assert "mtime is never used as a fallback" in row["note"]

    summary = json.loads(sh(f'match_rate_summary "{_to_bash_path(rows)}"'))
    assert summary["unattributed"] == ["pf-7"]
    assert summary["runs"] == 0 and summary["match_rate"] is None
    out = tmp_path / "s.json"
    line = sh(f'match_rate_summary "{_to_bash_path(rows)}" > "{_to_bash_path(out)}"; '
              f'match_rate_line "{_to_bash_path(out)}"')
    assert "could not be attributed to a turn (pf-7)" in line


def test_the_turn_stamp_on_disk_is_the_serial_run_fallback(tmp_path):
    root, _ = _tree(tmp_path, proposed=["p1"], selection=["p1"])
    stamp = root / ".loci" / "build" / "turn" / "current"
    stamp.parent.mkdir(parents=True)
    stamp.write_text("prm_01k3ccc\n", encoding="utf-8")
    assert sh(f'turn_id_from_stamp "{_to_bash_path(root)}"').strip() == "prm_01k3ccc"


def test_a_stamp_under_the_legacy_root_is_not_read(tmp_path):
    """A project last measured before the layout moved has a stamp under
    `.loci-build/`; since T14 the CLI neither writes nor reads there, so that
    stamp names a turn no current measurement was scoped to. Reading it would
    attribute a run to the wrong turn — the one silent error the metrics cannot
    detect after the fact."""
    root = tmp_path / "proj"
    old = root / ".loci-build" / "turn" / "current"
    old.parent.mkdir(parents=True)
    old.write_text("prm_old\n", encoding="utf-8")
    assert sh(f'turn_id_from_stamp "{_to_bash_path(root)}" || true').strip() == ""
    new = root / ".loci" / "build" / "turn" / "current"
    new.parent.mkdir(parents=True)
    new.write_text("prm_new\n", encoding="utf-8")
    assert sh(f'turn_id_from_stamp "{_to_bash_path(root)}"').strip() == "prm_new"


def test_build_dir_of_is_the_one_root(tmp_path):
    """Whatever is on disk — a legacy directory alone, both, neither — the
    answer is where the CLI writes, because that is the only place `analyse
    prepare` puts a manifest since T14."""
    root = tmp_path / "proj"
    (root / ".loci-build").mkdir(parents=True)
    assert sh(f'build_dir_of "{_to_bash_path(root)}"').strip().endswith("/.loci/build")
    (root / ".loci" / "build").mkdir(parents=True)
    assert sh(f'build_dir_of "{_to_bash_path(root)}"').strip().endswith("/.loci/build")
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert sh(f'build_dir_of "{_to_bash_path(fresh)}"').strip().endswith("/.loci/build")


def test_match_scan_reads_manifests_from_the_turn_trees_and_the_old_flat_directory(tmp_path):
    root, in_turn = _tree(tmp_path, proposed=["p1"], selection=["p1"])
    flat = root / ".loci" / "build" / "manifests"
    flat.mkdir(parents=True)
    (flat / "m-000000000000.json").write_text(json.dumps({
        "id": "m-000000000000", "state": "measured", "turn": "t-1",
        "artifacts": {"before": {}, "after": {".loci/build/objects/armv6-m/src/comms.o": "sha256:x"}},
        "proposed": ["p1"], "selection": ["p1"]}), encoding="utf-8")
    out = tmp_path / "out.jsonl"
    sh(f'match_scan "{_to_bash_path(root / ".loci" / "build")}" "{_to_bash_path(root)}" '
       f'tag "{_to_bash_path(out)}" "t-1"')
    ids = {json.loads(l)["manifest"] for l in out.read_text(encoding="utf-8").splitlines() if l.strip()}
    assert ids == {"m-abcdef123456", "m-000000000000"}


# ── the aggregate, and the caveat that must travel with it ───────────────────

def _rows(tmp_path: Path, *rows) -> Path:
    path = tmp_path / "rows.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _summary(path: Path) -> dict:
    return json.loads(sh(f'match_rate_summary "{_to_bash_path(path)}"'))


def test_the_match_rate_is_confirmed_over_proposed(tmp_path):
    rows = _rows(tmp_path,
                 {"proposed_n": 2, "confirmed": 2, "overridden": [], "opt_level": "-O0"},
                 {"proposed_n": 2, "confirmed": 1, "overridden": ["p9"], "opt_level": "-O0"})
    summary = _summary(rows)
    assert summary["runs"] == 2
    assert (summary["proposed"], summary["confirmed"]) == (4, 3)
    assert summary["match_rate"] == pytest.approx(0.75)
    assert summary["overridden"] == 1
    assert summary["unattributed"] == []


def test_an_all_o0_run_is_flagged_as_a_lower_bound_with_the_reason(tmp_path):
    rows = _rows(tmp_path,
                 {"proposed_n": 1, "confirmed": 1, "overridden": [], "opt_level": "-O0"})
    summary = _summary(rows)
    assert summary["lower_bound"] is True
    assert "-O0" in summary["caveat"] and "LOWER BOUND" in summary["caveat"]
    assert "structural" in summary["caveat"]


def test_a_run_at_a_production_level_is_not_flagged(tmp_path):
    rows = _rows(tmp_path,
                 {"proposed_n": 1, "confirmed": 1, "overridden": [], "opt_level": "-Os"})
    assert _summary(rows)["lower_bound"] is False


def test_no_measured_manifests_reports_nothing_rather_than_zero(tmp_path):
    # A 0% match rate and "nobody measured anything" are opposite findings.
    empty = tmp_path / "rows.jsonl"
    empty.write_text("", encoding="utf-8")
    summary = _summary(empty)
    assert summary["match_rate"] is None and summary["proposed"] == 0
    line = sh(f'match_rate_summary "{_to_bash_path(empty)}" > "{_to_bash_path(tmp_path / "s.json")}"; '
              f'match_rate_line "{_to_bash_path(tmp_path / "s.json")}"')
    assert "nothing to report" in line


def test_the_reported_line_carries_the_caveat_not_just_the_number(tmp_path):
    rows = _rows(tmp_path,
                 {"proposed_n": 4, "confirmed": 3, "overridden": ["p9"], "opt_level": "-O0"})
    out = tmp_path / "s.json"
    line = sh(f'match_rate_summary "{_to_bash_path(rows)}" > "{_to_bash_path(out)}"; '
              f'match_rate_line "{_to_bash_path(out)}"')
    assert "3/4" in line and "75% of top-ranked" in line
    assert "could not be attributed" not in line
    assert "-O0" in line and "LOWER BOUND" in line


# ── the flags reach the real argv, and no model is ever called ───────────────

@pytest.fixture
def tripwire(tmp_path):
    """A `claude` on PATH that records any invocation and fails.

    Every test using this asserts the log stays empty: that is the proof the
    harness was verified without spending metered usage.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "claude-was-called.log"
    fake = bindir / "claude"
    fake.write_text('#!/usr/bin/env bash\n'
                    f'echo "called: $*" >> "{_to_bash_path(log)}"\nexit 1\n',
                    encoding="utf-8")
    fake.chmod(0o755)
    ble = tmp_path / "ble"
    # Two directories, on purpose: `app_data.c` lives under `basic_ble/app/` and
    # `app_connection.c` under `basic_ble_profiles/app/`, because that is which
    # of the two the BLE fixture's compile database has an entry for. The files
    # are byte-identical in the real checkout; the compdb is the only thing that
    # tells them apart, and `loci analyse prepare` refuses the other one.
    for rel in ("examples/rtos/LP_EM_CC2340R5/ble5stack/basic_ble/app/app_data.c",
                "examples/rtos/LP_EM_CC2340R5/ble5stack/basic_ble_profiles/app/app_connection.c",
                "simplelink-lowpower-f3-sdk/source/ti/ble5stack_flash/hci/cc26xx/hci.c"):
        path = ble / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("int placeholder;\n", encoding="utf-8")
    return bindir, ble, log


def _run(tripwire, *flags):
    bindir, ble, log = tripwire
    # A dry run still stages the stale fixture, and since T14 that runs a real
    # `loci init` — whose escrow and context belong to the test, not to the
    # developer's `~/.loci/state` (where twelve of them were found).
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
           "HOME": os.environ.get("HOME", ""),
           "LOCI_STATE_DIR": str(Path(ble).parent / "loci-state")}
    proc = subprocess.run(
        [BASH, str(RUNNER), "--ble-root", str(ble), *flags],
        capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(PLUGIN_ROOT))
    assert not log.exists(), f"claude WAS invoked: {log.read_text()}"
    return proc


def test_list_mode_never_calls_claude(tripwire):
    proc = _run(tripwire, "--list")
    assert proc.returncode == 0
    assert "pf-critical-1" in proc.stdout


def test_the_dry_run_resolves_the_argv_and_calls_nothing(tripwire):
    proc = _run(tripwire, "--dry-run", "--eval-id", "pf-critical-1")
    assert proc.returncode == 0
    assert "no eval was executed and no model was called" in proc.stdout


def test_the_main_call_is_pinned_to_sonnet_by_default(tripwire):
    proc = _run(tripwire, "--dry-run", "--eval-id", "pf-critical-1")
    assert "Model:    sonnet (grader: sonnet)" in proc.stdout
    argv = [ln for ln in proc.stdout.splitlines() if "[dry-run]" in ln]
    assert argv and all("--model sonnet" in ln for ln in argv)


def test_the_model_flag_reaches_every_flow(tripwire):
    proc = _run(tripwire, "--dry-run", "--model", "opus")
    argv = [ln for ln in proc.stdout.splitlines() if "[dry-run]" in ln]
    flows = {ln.split("(")[-1].split(")")[0] for ln in argv}
    assert flows == {"single", "edit", "two-turn"}
    assert all("--model opus" in ln for ln in argv)


def test_an_empty_model_is_refused_rather_than_inherited(tripwire):
    proc = _run(tripwire, "--dry-run", "--model", "")
    assert proc.returncode == 1
    assert "cannot be empty" in proc.stdout


def test_the_grader_model_is_pinned_separately(tripwire):
    proc = _run(tripwire, "--dry-run", "--eval-id", "pf-critical-1",
                "--grader-model", "haiku")
    assert "Model:    sonnet (grader: haiku)" in proc.stdout


# ── the fixture is left as it was found ──────────────────────────────────────
#
# These drive the real `lib/eval-metrics.sh` functions against a real throwaway
# git checkout. A structural read of `run_evals.sh` cannot tell a restore that
# works from one that quietly does nothing, and "does nothing" is what a broken
# restore looks like from the outside — the fixture just stays dirty.

GIT = shutil.which("git")


def _git(repo: Path, *args) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run([GIT, "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", timeout=120, env=env)


@pytest.fixture
def checkout(tmp_path):
    """A committed three-file repo, plus the backup directory an eval would use."""
    if GIT is None:
        pytest.skip("git required")
    repo = tmp_path / "fixture"
    (repo / "sub").mkdir(parents=True)
    (repo / "a.c").write_text("committed a\n", encoding="utf-8")
    (repo / "b.c").write_text("committed b\n", encoding="utf-8")
    (repo / "sub" / "c.c").write_text("committed c\n", encoding="utf-8")
    _git(repo, "init", "-q", ".")
    _git(repo, "add", "-A")
    r = _git(repo, "-c", "commit.gpgsign=false", "commit", "-qm", "base")
    if r.returncode != 0:
        pytest.skip("could not build the fixture repo: " + r.stderr[:200])
    return repo, tmp_path / "bak"


def _sweep(repo: Path, bak: Path) -> list[str]:
    out = sh(f'fixture_restore "{_to_bash_path(repo)}" "{_to_bash_path(bak)}"')
    return [ln for ln in out.splitlines() if ln.strip()]


def _arm(repo: Path, bak: Path) -> None:
    sh(f'fixture_snapshot "{_to_bash_path(repo)}" "{_to_bash_path(bak)}"')


def test_a_file_the_run_edited_goes_back(checkout):
    repo, bak = checkout
    _arm(repo, bak)
    (repo / "a.c").write_text("the model edited this\n", encoding="utf-8")
    assert _sweep(repo, bak) == ["a.c"]
    assert (repo / "a.c").read_text(encoding="utf-8") == "committed a\n"


def test_a_file_the_run_deleted_comes_back(checkout):
    repo, bak = checkout
    _arm(repo, bak)
    (repo / "sub" / "c.c").unlink()
    assert _sweep(repo, bak) == ["sub/c.c"]
    assert (repo / "sub" / "c.c").read_text(encoding="utf-8") == "committed c\n"


def test_a_developers_own_edit_survives_a_run_that_did_not_touch_it(checkout):
    """The reason this never uses `git checkout --` blindly.

    The BLE fixture is a shared working copy. A restore that puts HEAD back over
    an uncommitted edit is the same damage as leaving the model's edit in place,
    only quieter — so a file that was already modified is copied aside first and
    restored from the copy, and one the run never touched is not restored at all.
    """
    repo, bak = checkout
    (repo / "b.c").write_text("a developer was working here\n", encoding="utf-8")
    _arm(repo, bak)
    (repo / "a.c").write_text("the model edited this\n", encoding="utf-8")
    assert _sweep(repo, bak) == ["a.c"]
    assert (repo / "b.c").read_text(encoding="utf-8") == "a developer was working here\n"


def test_a_run_that_stomps_a_developers_edit_restores_the_edit_not_head(checkout):
    repo, bak = checkout
    (repo / "b.c").write_text("a developer was working here\n", encoding="utf-8")
    _arm(repo, bak)
    (repo / "b.c").write_text("the model stomped it\n", encoding="utf-8")
    assert _sweep(repo, bak) == ["b.c"]
    assert (repo / "b.c").read_text(encoding="utf-8") == "a developer was working here\n"


def test_a_run_that_reverts_a_developers_edit_is_caught_too(checkout):
    """The direction `git diff` alone cannot see.

    If the run puts a modified file back to HEAD, afterwards the tree is clean
    and nothing in `git status` says anything was lost. Only the union of "dirty
    now" and "was copied aside" finds it.
    """
    repo, bak = checkout
    (repo / "b.c").write_text("a developer was working here\n", encoding="utf-8")
    _arm(repo, bak)
    _git(repo, "checkout", "--", "b.c")
    assert _sweep(repo, bak) == ["b.c"]
    assert (repo / "b.c").read_text(encoding="utf-8") == "a developer was working here\n"


def test_an_untouched_tree_restores_nothing_and_says_so(checkout):
    repo, bak = checkout
    _arm(repo, bak)
    assert _sweep(repo, bak) == []


def test_untracked_files_are_named_and_left_alone(checkout):
    """`.loci/` is what a measuring eval is SUPPOSED to leave behind."""
    repo, bak = checkout
    _arm(repo, bak)
    (repo / ".loci").mkdir()
    (repo / ".loci" / "state").write_text("x\n", encoding="utf-8")
    assert _sweep(repo, bak) == []
    listed = sh(f'fixture_untracked "{_to_bash_path(repo)}"').split()
    assert ".loci/" in listed, listed
    assert (repo / ".loci" / "state").exists(), "an untracked file was deleted"


def test_a_staged_fixture_is_not_a_checkout_and_the_restore_switches_off(tmp_path):
    """The stale tree and the fresh BLE copy are staged per run, not checkouts.

    `fixture_git_root` answering empty is the OFF switch, and everything below it
    has to be a no-op rather than an error — this runs under `set -euo pipefail`.
    """
    plain = tmp_path / "staged"
    plain.mkdir()
    (plain / "blink.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    root = _to_bash_path(plain)
    assert sh(f'fixture_git_root "{root}"').strip() == ""
    assert sh(f'fixture_tracked_dirty "{root}"').strip() == ""
    assert sh(f'fixture_untracked "{root}"').strip() == ""
    assert sh(f'fixture_restore "{root}" "{_to_bash_path(tmp_path / "bak")}"').strip() == ""
    assert (plain / "blink.c").exists()


def test_a_subdirectory_of_a_checkout_is_refused_as_a_fixture_root(checkout):
    """`git` prints repo-relative paths, so a root below the top level would
    resolve every one of them against the wrong base. Refusing is the only
    answer that cannot reach outside the tree it was pointed at."""
    repo, _ = checkout
    assert sh(f'fixture_git_root "{_to_bash_path(repo / "sub")}"').strip() == ""


@pytest.mark.parametrize("bad", ["../outside.c", "sub/../../outside.c", "/etc/passwd"])
def test_a_path_that_escapes_the_root_is_refused(bad):
    """git emits none of these. That is the point: the guard costs nothing and
    turns "the restore stays inside the fixture" into a property of the code."""
    out = subprocess.run(
        [BASH, "-c", f'set -euo pipefail\nsource "{_to_bash_path(LIB)}"\n'
                     f'if _fixture_path_is_safe "{bad}"; then echo SAFE; else echo REFUSED; fi'],
        capture_output=True, text=True, encoding="utf-8")
    assert out.stdout.strip() == "REFUSED", out.stdout + out.stderr


def test_an_ordinary_dotted_filename_is_not_mistaken_for_traversal():
    assert sh('if _fixture_path_is_safe "a/basic_ble.out.prev"; '
              'then echo SAFE; else echo REFUSED; fi').strip() == "SAFE"


def test_the_restore_never_reaches_for_a_blunt_instrument():
    """`stash`, `reset --hard` and `clean` all discard work the run did not do.

    The fixture is a shared working copy; the restore names paths and only paths.
    """
    offenders = []
    for name in ("lib/eval-metrics.sh", "run_evals.sh"):
        for i, line in enumerate((PLUGIN_ROOT / name).read_text(
                encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue        # prose about the hazard is not the hazard
            if re.search(r"git\b[^\n]*\b(stash|clean)\b", line) or \
               re.search(r"reset\s+--hard", line):
                offenders.append(f"{name}:{i}: {line.strip()[:120]}")
    assert not offenders, ("the fixture restore reaches for something that "
                           "discards uncommitted work:\n  " + "\n  ".join(offenders))


# ── the sweep runs before the early returns, in every flow ───────────────────

def _runner_lines() -> list[str]:
    return (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize("call,sweep", [
    ('timeout --kill-after=10 "$EVAL_TIMEOUT" claude "${CLAUDE_ARGS[@]}"',
     'fixture_sweep "$EVAL_CWD"'),
    ('claude "${C_ARGS[@]}" --permission-mode acceptEdits ) >"$JSON_FILE"',
     'fixture_sweep "$BLE_ROOT"'),
    # Turn 1 of the two-turn flow has its own sweep, between the turns: it is
    # the only place a plan-turn write can be told from turn 2's legitimate one.
    ('claude "${C_ARGS[@]}" --permission-mode plan',
     'fixture_sweep "$BLE_ROOT"'),
    ('--resume "$SID" --permission-mode acceptEdits ) >"$T2_JSON"',
     'fixture_sweep "$BLE_ROOT"'),
])
def test_the_sweep_runs_before_any_early_return(call, sweep):
    """Placement is the behaviour here.

    `set -euo pipefail` is on and each flow returns early on a timeout, on a
    non-zero exit and on an empty response. A timeout is exactly when a
    half-finished edit is still on disk, so a sweep below one of those returns
    would be skipped in the cases that need it most. Anchored on the real
    `claude` invocations rather than on line numbers.
    """
    lines = _runner_lines()
    at = next((i for i, ln in enumerate(lines) if call in ln), None)
    assert at is not None, f"no line invoking claude as {call!r} any more"
    ret = next((i for i in range(at + 1, len(lines))
                if lines[i].strip() == "return"), len(lines))
    swept = next((i for i in range(at + 1, ret) if sweep in lines[i]), None)
    assert swept is not None, (
        f"run_evals.sh:{at + 1} calls claude and the next `return` is at line "
        f"{ret + 1} with no `{sweep}` in between — a timeout would leave the "
        f"fixture dirty")


def test_the_single_turn_flow_backs_up_a_declared_source_file():
    """Scope 1(a): the same `source_file` key the edit flows use, so an eval that
    names a file is covered even where the sweep is switched off (a fixture root
    that is not a git checkout)."""
    text = (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8")
    assert 'SRC_BACKUP="$RESULTS_DIR/${SKILL_NAME}_eval${EVAL_ID}_src.bak"' in text
    # Four restores, not three: single-turn, edit, two-turn's end-of-run, and
    # two-turn's between-the-turns restore of a plan-turn write.
    assert text.count('cp -p "$SRC_BACKUP" "$SRC_ABS"') == 4, (
        "every flow must restore a declared source_file from its backup, and "
        "with -p — the mtime is what the staged fresh ELF is judged against")
    assert 'cp "$SRC_BACKUP" "$SRC_ABS"' not in text.replace('cp -p "$SRC_BACKUP"', ""), (
        "a restore without -p stamps `now` on a source the staged ELF's DWARF "
        "names, so the next eval measuring it is refused for staleness")


def test_every_single_turn_eval_naming_a_fixture_source_declares_it():
    """An eval that names a file under a fixture root and does not declare it as
    `source_file` is one the harness cannot put back on a non-git fixture."""
    import glob
    missing = []
    for path in sorted(glob.glob(str(PLUGIN_ROOT / "skills" / "*" / "evals" / "*.json"))):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for e in data["evals"]:
            if (e.get("flow") or "single") != "single":
                continue
            names = re.findall(r"\$LOCI_TEST_BLE_ROOT/\S+?\.(?:c|cc|cpp|h|rs|go)\b",
                               e.get("prompt", ""))
            if names and not e.get("source_file"):
                missing.append(f"{Path(path).name}:{e['id']} names {names[0]}")
    assert not missing, ("these single-turn evals name a BLE source file but "
                         "declare no source_file to restore:\n  " + "\n  ".join(missing))


# ── the fixture and the plugin reach the metrics ─────────────────────────────

def test_the_metrics_name_what_the_run_changed_and_which_plugin_answered(tmp_path):
    out = json.loads(sh(
        f'eval_metrics_json "{_to_bash_path(_stream(tmp_path))}" sonnet 9 single '
        f'"$(printf \'a.c\\nsub/b.c\\n\')" "plugin-dir:deadbee-dirty"'))
    assert out["fixture_dirty_after_run"] == ["a.c", "sub/b.c"]
    assert out["plugin_under_test"] == "plugin-dir:deadbee-dirty"


def test_a_run_that_changed_nothing_records_an_empty_list_not_a_missing_field(tmp_path):
    """Empty and absent are different claims: one says the tree was checked and
    was clean, the other says nothing was checked."""
    out = json.loads(sh(
        f'eval_metrics_json "{_to_bash_path(_stream(tmp_path))}" sonnet 9 single'))
    assert out["fixture_dirty_after_run"] == []
    assert out["plugin_under_test"] is None


def test_the_two_turn_merge_hoists_the_provenance_out_of_the_turns(tmp_path):
    t1 = tmp_path / "t1.json"
    t2 = tmp_path / "t2.json"
    t1.write_text(json.dumps({"num_turns": 2, "wall_clock_s": 1,
                              "plugin_under_test": "plugin-dir:abc1234",
                              "fixture_dirty_after_run": []}), encoding="utf-8")
    t2.write_text(json.dumps({"num_turns": 3, "wall_clock_s": 2,
                              "plugin_under_test": "plugin-dir:abc1234",
                              "fixture_dirty_after_run": ["app/app_data.c"]}),
                  encoding="utf-8")
    out = tmp_path / "merged.json"
    sh(f'eval_metrics_merge "{_to_bash_path(out)}" '
       f'"turn1={_to_bash_path(t1)}" "turn2={_to_bash_path(t2)}"')
    merged = json.loads(out.read_text(encoding="utf-8"))
    assert merged["totals"]["num_turns"] == 5
    assert merged["plugin_under_test"] == "plugin-dir:abc1234"
    assert merged["fixture_dirty_after_run"] == ["app/app_data.c"]


# ── plan mode cannot edit, and the default is this working tree ──────────────

def _argv_lines(proc) -> list[str]:
    return [ln for ln in proc.stdout.splitlines() if "[dry-run]" in ln]


def test_a_plan_mode_eval_denies_the_edit_tools(tripwire):
    """`--permission-mode plan` plus `--dangerously-skip-permissions` does not
    stop an edit — measured four times in T14 — so the tools are denied at the
    tool layer instead. `claude --help`: "--disallowedTools, --disallowed-tools
    <tools...>  Comma or space-separated list of tool names to deny"."""
    proc = _run(tripwire, "--dry-run", "--eval-id", "pf-critical-1")
    argv = _argv_lines(proc)
    assert argv, proc.stdout[-2000:]
    for ln in argv:
        assert "--permission-mode plan" in ln, ln
        assert "--disallowedTools Edit Write MultiEdit NotebookEdit" in ln, ln


def test_the_deny_list_is_not_bolted_onto_evals_that_must_edit(tripwire):
    """An edit-flow eval exists precisely to make an edit; denying Edit there
    would turn every one of them into a silent no-op that still grades."""
    proc = _run(tripwire, "--dry-run")
    for ln in _argv_lines(proc):
        flow = ln.split("(")[-1].split(")")[0]
        denied = "--disallowedTools" in ln
        if flow == "edit":
            assert not denied, ln
        elif flow == "two-turn":
            assert denied, ln          # turn 1 is the plan turn
        elif "--permission-mode plan" not in ln:
            assert not denied, ln


def test_every_flow_loads_this_working_tree_by_default(tripwire):
    """The default the whole overhaul lacked: `--plugin-dir "$SCRIPT_DIR"`.

    Without it the edit and two-turn flows load `~/.claude/plugins/...`, the skew
    guard skips them, and a branch ships with those evals never having run.
    """
    proc = _run(tripwire, "--dry-run")
    argv = _argv_lines(proc)
    flows = {ln.split("(")[-1].split(")")[0] for ln in argv}
    assert flows == {"single", "edit", "two-turn"}, flows
    want = "--plugin-dir " + _to_bash_path(PLUGIN_ROOT)
    for ln in argv:
        assert want in ln, f"{want!r} missing from:\n{ln}"
    assert "Working tree under test" in proc.stdout, proc.stdout[:1500]


def test_installed_plugin_mode_takes_the_flag_back_off(tripwire):
    """The QA mode: what users actually have, with the skew guard that belongs
    to it. It must not merely re-label the same run."""
    proc = _run(tripwire, "--dry-run", "--installed-plugin")
    argv = _argv_lines(proc)
    assert argv, proc.stdout[-2000:]
    for ln in argv:
        assert "--plugin-dir" not in ln, ln
    assert "Installed plugin:" in proc.stdout, proc.stdout[:1500]
    assert "Working tree under test" not in proc.stdout, proc.stdout[:1500]


# ── …and if it edits anyway, that is the verdict ─────────────────────────────

def _between(start_needle: str, end_needle: str) -> str:
    """The shipped lines between two anchors, so these tests read real code."""
    text = (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8")
    i = text.index(start_needle)
    j = text.index(end_needle, i)
    return text[i:j]


def test_a_plan_mode_run_that_edited_is_failed_and_the_paths_are_named():
    """The half `--disallowedTools` cannot cover.

    Denying Edit/Write/MultiEdit/NotebookEdit works, and on the first real run of
    this branch the model simply wrote the file with a **Bash** command instead —
    then reported "this was a direct edit outside /plan mode" and ran post-edit.
    Bash cannot be denied: preflight's own analysis runs through it. So the
    verdict carries the fact, and names what was written.

    The violation is DECIDED next to the evidence — immediately after the sweep,
    which is the only place that knows what the run changed — and APPLIED by
    `write_verdict` at every exit. See the tests below for the applying half.
    """
    block = _between("  PLAN_VIOLATION=\"\"\n",
                     "  # Metrics NOW,")
    assert 'if $PLAN_MODE && [[ -n "$FIXTURE_RESTORED" ]]; then' in block, block
    assert "edited the fixture under plan mode" in block, block
    # …and it is decided BEFORE the first exit that could skip it.
    text = (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8")
    assert text.index('  PLAN_VIOLATION=""\n') < text.index('write_verdict "TIMEOUT"'), (
        "the violation is decided after an exit that can return first")


def test_the_two_turn_flow_fails_a_plan_turn_that_edited():
    """Turn 1 is the plan turn; turn 2 is supposed to edit. The check therefore
    lives right after turn 1 — by the time the sweep runs, the two writes are
    indistinguishable."""
    block = _between("local T1_EDITED=\"\"", "# ── Turn 2:")
    assert 'T1_EDITED="$SOURCE_FILE"' in block, block
    override = _between("CV=$(grade_bash_combined", 'echo "${PROG_PFX} DONE     ${TAG}  ${VERDICT}"')
    assert 'if [[ -n "$T1_EDITED" ]]; then' in override, override
    assert 'VERDICT="FAIL"' in override, override


def test_the_violation_names_every_file_it_restored():
    """Driving the shipped decision block, so this fails if the reason is ever
    reduced to "the run edited something"."""
    block = _between("  PLAN_VIOLATION=\"\"\n", "  # Metrics NOW,")
    body = "\n".join(ln for ln in block.splitlines()
                     if not ln.lstrip().startswith("#"))
    script = (
        "set -euo pipefail\n"
        "PLAN_MODE=true\n"
        "FIXTURE_RESTORED=$(printf 'a/x.c\\nb/y.c\\n')\n"
        "log_eval() { :; }\n"
        # `local` is only legal inside a function and these lines ship inside
        # one, so wrap rather than neuter it — neutering would change what is
        # under test.
        "_shipped() {\n" + body + "\n}\n_shipped\n"
        'printf "%s\\n" "$PLAN_VIOLATION"\n')
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    assert out.startswith("edited the fixture under plan mode (a/x.c b/y.c)"), out


# ── what the review round found ──────────────────────────────────────────────

def test_evals_sharing_the_ble_checkout_are_forced_sequential():
    """A shared working copy cannot be restored concurrently.

    Reproduced against the real functions before this guard existed: eval B arms
    while eval A's edit is on disk, so B's backup captures A's edit as a
    developer's WIP; A sweeps clean, B sweeps and writes A's edit back for good.
    The same overlap makes a plan-mode B report `edited the fixture under plan
    mode (<A's file>)` for a write it never made. Single-turn evals were exempt
    from the two older force-sequential rules because they never touched the
    fixture — arming them is what put them in scope.
    """
    text = (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8")
    assert ('if [[ $MAX_JOBS -ne 1 && $TOTAL -gt 1 '
            '&& -n "$(fixture_git_root "$BLE_ROOT")" ]]; then') in text, (
        "nothing forces -j 1 when several evals share (and restore) the BLE "
        "checkout")


def test_the_run_forces_sequential_in_practice(tripwire):
    """Not just present in the source — reached, on a real multi-eval run.

    `--skill loci-preflight` is eight single-turn evals with no edit or two-turn
    flow among them, so neither older rule fires: it is exactly the batch that
    used to run at -j 4.
    """
    bindir, ble, log = tripwire
    subprocess.run(["git", "-C", str(ble), "init", "-q", "."],
                   capture_output=True, timeout=120)
    proc = _run(tripwire, "--dry-run", "--skill", "loci-preflight")
    assert "share the BLE checkout" in proc.stdout, proc.stdout[-2500:]
    assert "forcing sequential (-j 1)" in proc.stdout, proc.stdout[-2500:]


def test_an_unchanged_source_file_is_not_rewritten():
    """`loci build fresh` is an mtime check, and the declared `source_file`s ARE
    sources the staged fresh ELF's DWARF names.

    An unconditional copy-back stamps `now` on a file the run never touched, so
    the next eval measuring `$LOCI_TEST_BLE_FRESH/basic_ble.out` is refused for
    staleness the harness manufactured — `mr-4` declares `app_data.c` and runs
    ahead of `mr-1`/`mr-2`, which measure that ELF.
    """
    text = (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8")
    start = '  if [[ -n "$SRC_ABS" && -n "$SRC_BACKUP" ]]; then'
    assert text.count(start) == 1, "the single-turn source_file restore moved"
    i = text.index(start)
    window = text[i:text.index("\n  fi\n", i)]
    assert 'cp -p "$SRC_BACKUP" "$SRC_ABS"' in window, window
    # …and the copy is on the CHANGED branch, i.e. after the `else`. Without
    # that it runs on every eval and stamps a source the run never touched.
    assert window.index("else") < window.index('cp -p "$SRC_BACKUP"'), window
    assert 'log_eval "source_file unchanged, left untouched' in window, window
    # every backup and restore of a fixture source preserves the mtime
    assert 'cp "$SRC_ABS" "$SRC_BACKUP"' not in text.replace(
        'cp -p "$SRC_ABS" "$SRC_BACKUP"', ""), (
        "a source_file backup without -p loses the mtime, so restoring from it "
        "stamps the source `now` even when the content is right")


def test_the_staged_fresh_elf_is_restamped_before_every_eval():
    """The backstop for every other way a source's mtime can move — a restore, a
    `git checkout --`, an editor that rewrote a file and put it back."""
    text = (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8")
    i = text.index("fixture_arm() {")
    j = text.index("fixture_sweep() {", i)
    arm = text[i:j]
    assert 'touch "$FRESH_BLE_ROOT/basic_ble.out"' in arm, arm


def test_the_plan_mode_violation_survives_a_timeout(tripwire):
    """It used to be decided after grading, where the timeout, non-zero-exit and
    empty-response paths had already returned — so a run that wrote to the
    fixture and then timed out reported `TIMEOUT` and named the write nowhere.
    """
    text = (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8")
    # every single-turn exit goes through the one writer…
    for needle in ('write_verdict "TIMEOUT"',
                   'write_verdict "ERROR" "claude exited with code',
                   'write_verdict "ERROR" "empty response despite exit code 0"',
                   'write_verdict "GRADE_ERROR"',
                   'write_verdict "$VERDICT" "$REASON"'):
        assert needle in text, f"{needle!r} — an exit bypasses write_verdict"
    # …and no single-turn exit writes the verdict file behind its back.
    #
    # Seven direct writes are expected and each is accounted for: one inside
    # `write_verdict` itself, and six in the edit and two-turn flows, which
    # `return` long before `PLAN_VIOLATION` is set and so cannot skip an
    # override that does not exist yet. Counting is cruder than banning the
    # shape, and it is the point — a NEW direct write is what needs looking at,
    # because on a single-turn path it would bypass the override silently.
    assert text.count('> "$VERDICT_FILE"') == 7, (
        "the number of direct writes to $VERDICT_FILE changed; if the new one "
        "is on a single-turn path it skips the plan-mode override")


@pytest.mark.parametrize("verdict,expect_in_reason", [
    ("TIMEOUT", "the run also ended TIMEOUT"),
    ("PASS", "the run also ended PASS"),
    ("FAIL", "the grader also said"),
])
def test_write_verdict_turns_every_outcome_into_a_named_fail(verdict, expect_in_reason):
    """Driving the shipped function, so this fails if the override is softened."""
    text = (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8")
    i = text.index("  write_verdict() {")
    j = text.index("\n  }\n", i) + 4
    fn = text[i:j]
    script = (
        "set -euo pipefail\n"
        "PROG_PFX='[1/1]'; TAG='t'\n"
        f'VERDICT_FILE="$(mktemp)"; PROGRESS_LOG="$(mktemp)"\n'
        "log_eval() { :; }\n"
        "PLAN_VIOLATION='edited the fixture under plan mode (a/x.c)'\n"
        + fn +
        f'\nwrite_verdict "{verdict}" "some reason"\ncat "$VERDICT_FILE"\n')
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    assert out.startswith("FAIL|"), out
    assert "a/x.c" in out, out
    assert expect_in_reason in out, out


def test_no_violation_leaves_the_verdict_alone():
    """The override must not fire on every run — a guard that always fires is
    not a guard."""
    text = (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8")
    i = text.index("  write_verdict() {")
    j = text.index("\n  }\n", i) + 4
    script = (
        "set -euo pipefail\n"
        "PROG_PFX='[1/1]'; TAG='t'\n"
        'VERDICT_FILE="$(mktemp)"; PROGRESS_LOG="$(mktemp)"\n'
        "log_eval() { :; }\n"
        "PLAN_VIOLATION=''\n"
        + text[i:j] +
        '\nwrite_verdict "PASS" "the report is present"\ncat "$VERDICT_FILE"\n')
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          encoding="utf-8")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "PASS|the report is present", proc.stdout


def test_the_plan_turn_sweeps_the_whole_tree_not_one_declared_file():
    """`cmb-2`'s turn 1 can write a header or a second `.c` through Bash, and a
    `diff` of the declared `source_file` sees nothing. By turn 2's sweep the
    violation is indistinguishable from the edit turn 2 is supposed to make."""
    text = (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8")
    i = text.index('local T1_EDITED=""')
    j = text.index("# ── Turn 2:", i)
    block = text[i:j]
    assert 'fixture_sweep "$BLE_ROOT"' in block, block
    assert 'T1_EDITED=$(printf' in block, block
    assert 'fixture_arm "$BLE_ROOT"' in block, (
        "turn 2 needs its own snapshot after turn 1's sweep:\n" + block)


def test_the_dirty_scan_covers_the_whole_loaded_plugin():
    """`--plugin-dir` loads the whole directory. An uncommitted
    `.claude-plugin/plugin.json` is live in the session, so leaving it out of the
    scan records a clean `plugin-dir:<sha>` for a tree that is not that sha."""
    text = (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8")
    assert 'status --porcelain -- skills hooks lib .claude-plugin' in text, (
        "the dirty scan does not cover .claude-plugin")


def test_an_unresolvable_installed_sha_says_so():
    text = (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8")
    assert 'PLUGIN_UNDER_TEST="installed:unresolved"' in text, (
        'an empty sha renders as a bare "installed:" in the metrics')


def test_the_mcp_config_follows_the_tree_even_when_the_tree_declares_nothing(tripwire):
    """The predicate used to require a non-empty `mcpServers`, and no tree file
    has one — so the loop never fired and the installed cache always won, which
    is the mis-attribution the block was added to prevent. "This tree declares no
    server" is the tree's answer and the run must use it."""
    proc = _run(tripwire, "--dry-run", "--eval-id", "pf-critical-1")
    mcp = [ln for ln in proc.stdout.splitlines() if ln.startswith("MCP:")]
    assert mcp, proc.stdout[:1500]
    assert ".claude/plugins/cache" not in mcp[0], (
        "the default mode still reads the installed plugin's MCP config:\n"
        + mcp[0])
    assert _to_bash_path(PLUGIN_ROOT) in mcp[0], mcp[0]


def test_a_symlinked_destination_is_not_written_through(checkout):
    """`cp` follows a symlink at the destination. A run that replaced a tracked
    file with a link to somewhere else would have the restore write through it —
    the one way this could still reach outside the fixture."""
    repo, bak = checkout
    outside = repo.parent / "outside.txt"
    outside.write_text("do not touch\n", encoding="utf-8")
    _arm(repo, bak)
    (repo / "a.c").write_text("model edit\n", encoding="utf-8")
    _arm(repo, bak)          # a.c is now the "already dirty" file with a backup
    target = repo / "a.c"
    target.unlink()
    try:
        target.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this host")
    sh(f'fixture_restore "{_to_bash_path(repo)}" "{_to_bash_path(bak)}"')
    assert outside.read_text(encoding="utf-8") == "do not touch\n", (
        "the restore wrote through a symlink and clobbered a file outside the "
        "fixture root")
