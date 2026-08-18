"""`lib/compile-and-read-back.sh` is what every change-measuring skill compiles
through, so it is what decides whether a "Before" column is real.

It exists because the same logic lived as a fenced recipe copied into five SKILL.md
files, and hostile review found six defects that existed *because* it was prose a
model retypes — an unquoted `--source` that broke every path containing a space with
`2>/dev/null` swallowing the only diagnostic, literal `[--phase <phase>]` brackets
reaching argparse, a sidecar path resolved against the shell's CWD while the CLI
resolved the object against the project root. None of those are expressible here:
the caller passes values, not syntax.

The two states worth naming, because both were reproduced as wrong answers before
this script existed:

* **a pair that is not a pair.** `modA/util.c` and `modB/util.c` share one stem, so
  one `util.o` and one `util.o.prev`. Editing modB reported modA's object as the
  Before — `{"added":1,"removed":1,"modified":1}` and +128.6% ROM, presented as the
  effect of the user's edit.
* **a pair built differently.** A baseline compiled from a `compile_commands.json`
  since regenerated away was reported at −52.4% ROM for an edit that added `t+=1;`.

`loci` is stubbed, so these are about the script's decisions, not the CLI's. The
end-to-end runs against both real CLI builds live in
`C:\\Playground\\loci-claude-tests\\` — see the phase 02b notes in HANDOFF.md.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_RS = ""   # the stub separates logged arguments with this

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = PLUGIN_ROOT / "lib" / "compile-and-read-back.sh"


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


pytestmark = pytest.mark.skipif(
    _find_bash() is None or shutil.which("jq") is None,
    reason="bash and jq required",
)


def _to_bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s


def _base_path() -> str:
    """jq is not in /usr/bin on a Windows checkout, and the script exits early
    without it — a hardcoded PATH would make every assertion here vacuous."""
    base = "/usr/bin:/bin:/usr/local/bin"
    jq = shutil.which("jq")
    if jq:
        base = f"{_to_bash_path(Path(jq).parent)}:{base}"
    return base


# --- the stub CLI -----------------------------------------------------------
# `MODERN` decides whether `build compile --help` advertises `--inherit-prev`, which
# is the capability probe the script branches on. Everything else is scripted per
# test so the script's *decisions* are what is under test.
#
# Arguments are logged separated by RS (\x1e), NOT as `"$*"`. `"$*"` joins on a space,
# so it renders `--source "My Src/x.c"` and an unquoted `--source My Src/x.c`
# IDENTICALLY — the space test passed with the quoting removed, which is exactly the
# defect it was written to catch. Same convention as `test_pre_edit_hook.py`.
_STUB = r"""#!/usr/bin/env bash
{ sep=''; for a in "$@"; do printf '%s%s' "$sep" "$a"; sep=$'\x1e'; done; printf '\n'; } >> "ARGS_LOG"
case "$1 $2" in
  "build compile")
      if [[ "$*" == *--help* ]]; then
          echo "usage: loci build compile"
          [ "MODERN_FLAG" = yes ] && echo "  --inherit-prev  inherit the baseline"
          [ "RECON_FLAG" = yes ] && echo "  --baseline  rebuild the pre-edit state"
          exit 0
      fi
      if [ "MODERN_FLAG" != yes ] && [[ "$*" == *--inherit-prev* ]]; then
          echo "loci: error: unrecognized arguments: --inherit-prev" >&2; exit 2
      fi
      # `--baseline` landed strictly LATER than `--inherit-prev`, so a CLI can have
      # one and not the other. One that lacks it answers on stderr with no envelope
      # at all — the state the script's separate capability probe exists to avoid,
      # and one this stub has to be able to produce or that probe is untested.
      if [ "RECON_FLAG" != yes ] && [[ "$*" == *--baseline* ]]; then
          echo "loci: error: unrecognized arguments: --baseline" >&2; exit 2
      fi
      if [[ "$*" == *--baseline* ]]; then
          BASELINE_BODY
          exit 0
      fi
      COMPILE_BODY
      ;;
  "build diff")
      echo "{\"ok\":true,\"data\":{\"match\":DIFF_MATCH}}" ;;
  *)  echo '{"ok":true,"data":{}}' ;;
esac
"""


def _mkstub(home: Path, *, modern: bool, compile_body: str, diff_match: bool = True,
            can_reconstruct: bool = False, baseline_body: str = "") -> Path:
    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    args_log = home / "args.log"
    body = (
        _STUB.replace("ARGS_LOG", _to_bash_path(args_log))
        .replace("MODERN_FLAG", "yes" if modern else "no")
        .replace("RECON_FLAG", "yes" if can_reconstruct else "no")
        .replace("DIFF_MATCH", "true" if diff_match else "false")
        .replace("BASELINE_BODY", baseline_body or "echo '{\"ok\":true,\"data\":{}}'")
        .replace("COMPILE_BODY", compile_body)
    )
    (bin_dir / "loci").write_text(body, encoding="utf-8")
    (bin_dir / "loci").chmod(0o755)
    return bin_dir


class Result:
    def __init__(self, proc: subprocess.CompletedProcess, args_log: Path):
        self.code = proc.returncode
        self.raw = proc.stdout
        self.stderr = proc.stderr
        self.fields: dict[str, str] = {}
        self.notes: list[str] = []
        self.failed: str | None = None
        for line in proc.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split("\t")
            key = parts[0]
            if key == "NOTE":
                self.notes.append("\t".join(parts[1:]))
            elif key == "FAILED":
                self.failed = "\t".join(parts[1:])
            else:
                self.fields[key] = "\t".join(parts[1:]) if len(parts) > 1 else ""
        # `.split("\n")`, NOT `.splitlines()`: Python counts RS (\x1e) as a line
        # boundary, so splitlines() shredded every logged call at its own argument
        # separator and `compiles` came back empty — thirteen tests failed for a
        # reason that had nothing to do with the script.
        log = args_log.read_text(encoding="utf-8") if args_log.is_file() else ""
        self.calls: list[list[str]] = [
            line.split(_RS) for line in log.split("\n") if line
        ]

    @property
    def prev(self) -> str:
        return self.fields.get("PREV", "")

    @property
    def compiles(self) -> list[list[str]]:
        """The real compile invocations, as argv lists — the `--help` probe excluded."""
        return [c for c in self.calls
                if c[:2] == ["build", "compile"] and "--help" not in c]

    @staticmethod
    def pair(argv: list[str], flag: str) -> str | None:
        """The value following `flag`, or None. Boundary-aware, so it cannot be
        fooled by a value that happens to contain a space."""
        return argv[argv.index(flag) + 1] if flag in argv else None

    def note_matching(self, pattern: str) -> str | None:
        for n in self.notes:
            if re.search(pattern, n, re.I):
                return n
        return None


def _ok_envelope(obj: str, meta: str, **extra: str) -> str:
    data = {"output": obj, "meta_file": meta, "compiler": "arm-none-eabi-gcc",
            "flags": ["-g", "-c"], **extra}
    return json.dumps({"ok": True, "data": data})


def _run(home: Path, *, modern: bool, compile_body: str, source: str,
         diff_match: bool = True, extra_args: list[str] | None = None,
         cwd: Path | None = None, target: str = "armv6-m",
         omit: tuple[str, ...] = (), can_reconstruct: bool = False,
         baseline_body: str = "") -> Result:
    """Drive the script.

    `cwd` defaults to the project root, which is the shape almost every caller has —
    but see `test_a_shell_above_the_project_root_still_finds_the_baseline`: with the
    two always equal, "anchored to the project root" and "resolved against the
    shell's CWD" are indistinguishable, and an assertion about the former can only
    ever see a path *spelling* difference. That vacuity was found by mutation.
    """
    bin_dir = _mkstub(home, modern=modern, compile_body=compile_body,
                      diff_match=diff_match, can_reconstruct=can_reconstruct,
                      baseline_body=baseline_body)
    args = [_find_bash(), _to_bash_path(SCRIPT), "--source", source]
    if "--loci-target" not in omit:
        args += ["--loci-target", target]
    if "--project-root" not in omit:
        args += ["--project-root", _to_bash_path(home / "proj")]
    if "--phase" not in omit:
        args += ["--phase", "post-edit"]
    args += list(extra_args or [])
    proc = subprocess.run(
        args, cwd=cwd or (home / "proj"), capture_output=True, text=True, timeout=60,
        env={"PATH": f"{_to_bash_path(bin_dir)}:{_base_path()}",
             "HOME": _to_bash_path(home)},
    )
    assert proc.returncode == 0, (
        "the script must always exit 0 — FAILED is the failure channel, so a "
        f"non-zero exit reads to the model as a broken command. stderr={proc.stderr!r}"
    )
    return Result(proc, home / "args.log")


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    """A project root with one source and the LOCI build dir the stub writes into."""
    root = tmp_path / "proj"
    (root / ".loci-build" / "armv6-m").mkdir(parents=True)
    (root / "blink.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    return tmp_path


def _artifacts(home: Path, *, prev: bool, prev_src: str | None = None) -> tuple[str, str]:
    """Create `blink.o` + sidecar, optionally with a `.prev` pair beside them.

    `prev_src` sets the baseline sidecar's `source_file`; the default matches the
    current build, so the pair looks legitimate.
    """
    d = home / "proj" / ".loci-build" / "armv6-m"
    obj, meta = d / "blink.o", d / "blink.o.meta.json"
    obj.write_bytes(b"\x7fELF-current")
    cur_src = _to_bash_path(home / "proj" / "blink.c")
    meta.write_text(json.dumps({"source_file": cur_src}), encoding="utf-8")
    if prev:
        (d / "blink.o.prev").write_bytes(b"\x7fELF-baseline")
        (d / "blink.o.meta.json.prev").write_text(
            json.dumps({"source_file": prev_src or cur_src}), encoding="utf-8")
    return _to_bash_path(obj), _to_bash_path(meta)


# ---------------------------------------------------------------------------
# The paths come back, and only one compile happens
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("modern", [True, False])
def test_reports_the_pair_and_compiles_exactly_once(proj: Path, modern: bool):
    """Post-edit compiled TWICE per edit before `--inherit-prev` — once to learn
    where the object went, then again for flag parity — rising to four with the
    escalation paths, which on cargo builds the dependency graph twice inside one
    timeout. Both branches must get there in one."""
    obj, meta = _artifacts(proj, prev=True)
    body = f"echo '{_ok_envelope(obj, meta, output_prev=obj + '.prev', meta_prev=meta + '.prev')}'" \
        if modern else f"echo '{_ok_envelope(obj, meta)}'"
    r = _run(proj, modern=modern, compile_body=body, source="blink.c")
    assert r.failed is None, r.raw
    assert r.fields["OBJ"] == obj
    assert r.fields["META"] == meta
    assert r.prev == obj + ".prev"
    assert r.fields["PREV_META"] == meta + ".prev"
    assert len(r.compiles) == 1, f"expected one compile, got {r.compiles}"


def test_modern_branch_inherits_the_baseline_flags(proj: Path):
    obj, meta = _artifacts(proj, prev=True)
    r = _run(proj, modern=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta, output_prev=obj + '.prev', meta_prev=meta + '.prev')}'")
    assert "--inherit-prev" in r.compiles[0]
    assert "--meta-prev" not in r.compiles[0], (
        "--meta-prev names a pre-edit sidecar by hand; the modern branch must let "
        "the CLI discover it, which is what makes the pair checkable"
    )


def test_legacy_branch_names_the_sidecar_under_the_project_root(proj: Path):
    """The flat sidecar path is correct for exactly the CLIs that take this branch —
    a `loci` without `--inherit-prev` also predates the source-keyed layout. But it
    must be anchored to the PROJECT ROOT: resolved against the shell's CWD instead,
    `--meta-prev` silently missed on every run started from anywhere else, and the
    compile fell back to re-detected flags while still reporting a baseline."""
    obj, meta = _artifacts(proj, prev=True)
    r = _run(proj, modern=False, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    assert "--inherit-prev" not in r.compiles[0]
    got = Result.pair(r.compiles[0], "--meta-prev")
    assert got, f"legacy compile did not pass --meta-prev: {r.compiles[0]}"
    assert got == meta + ".prev"
    assert got.startswith(_to_bash_path(proj / "proj")), (
        "the sidecar path is not anchored to the project root"
    )


def test_project_root_is_always_passed(proj: Path):
    """Without it the CLI falls back to its own CWD, so a shell above the project
    root compiles into a SECOND `.loci-build/` tree: the real baseline is never seen
    and the stray tree is later ranked as a measurement candidate."""
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    assert "--project-root" in r.compiles[0]


def test_project_root_comes_from_the_context_when_not_given(tmp_path: Path):
    root = tmp_path / "proj"
    (root / ".loci-build" / "armv6-m").mkdir(parents=True)
    (root / "blink.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    ctx = tmp_path / "ctx.json"
    ctx.write_text(json.dumps({"project_root": _to_bash_path(root)}), encoding="utf-8")
    obj, meta = _artifacts(tmp_path, prev=False)
    bin_dir = _mkstub(tmp_path, modern=True,
                      compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(SCRIPT), "--source", "blink.c",
         "--loci-target", "armv6-m", "--context", _to_bash_path(ctx)],
        cwd=root, capture_output=True, text=True, timeout=60,
        env={"PATH": f"{_to_bash_path(bin_dir)}:{_base_path()}",
             "HOME": _to_bash_path(tmp_path)},
    )
    assert proc.returncode == 0, proc.stderr
    r = Result(proc, tmp_path / "args.log")
    assert Result.pair(r.compiles[0], "--project-root") == _to_bash_path(root), r.compiles


def test_a_project_root_of_unknown_is_treated_as_absent(tmp_path: Path):
    """`session-init` can write the literal string "unknown", and the CLI treats it
    as absent. Passing it through would make every path resolve under a directory
    named `unknown`."""
    root = tmp_path / "proj"
    (root / ".loci-build" / "armv6-m").mkdir(parents=True)
    (root / "blink.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    ctx = tmp_path / "ctx.json"
    ctx.write_text(json.dumps({"project_root": "unknown"}), encoding="utf-8")
    obj, meta = _artifacts(tmp_path, prev=False)
    bin_dir = _mkstub(tmp_path, modern=True,
                      compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(SCRIPT), "--source", "blink.c",
         "--loci-target", "armv6-m", "--context", _to_bash_path(ctx)],
        cwd=root, capture_output=True, text=True, timeout=60,
        env={"PATH": f"{_to_bash_path(bin_dir)}:{_base_path()}",
             "HOME": _to_bash_path(tmp_path)},
    )
    r = Result(proc, tmp_path / "args.log")
    assert Result.pair(r.compiles[0], "--project-root") != "unknown", r.compiles


# ---------------------------------------------------------------------------
# A pair that is not a pair
# ---------------------------------------------------------------------------

def test_legacy_withholds_a_baseline_built_from_another_source(proj: Path):
    """THE defect this phase exists to close, on the branch that runs today.
    `modA/util.c` and `modB/util.c` share one stem, so one object and one `.prev`.
    This CLI's `build diff` compares compiler, version, target, architecture, flags
    and flag_source kind — but NOT the source — so nothing else catches it."""
    obj, meta = _artifacts(proj, prev=True,
                           prev_src=_to_bash_path(proj / "proj" / "other" / "util.c"))
    r = _run(proj, modern=False, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    assert r.prev == "", "a baseline built from another source was served as the Before"
    assert r.note_matching(r"built from"), r.notes
    assert "util.c" in (r.note_matching(r"built from") or ""), (
        "the note must name the file it actually belonged to"
    )


def test_legacy_withholds_a_baseline_built_with_other_flags(proj: Path):
    obj, meta = _artifacts(proj, prev=True)
    r = _run(proj, modern=False, source="blink.c", diff_match=False,
             compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    assert r.prev == "", "a baseline with different build flags was served as the Before"
    assert r.note_matching(r"flags"), r.notes


def test_legacy_adopts_a_baseline_that_passes_both_checks(proj: Path):
    """The positive control for the two tests above: if nothing is wrong with the
    candidate it must be USED, or the checks have simply disabled the feature."""
    obj, meta = _artifacts(proj, prev=True)
    r = _run(proj, modern=False, source="blink.c", diff_match=True,
             compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    assert r.prev == obj + ".prev", r.raw
    assert r.note_matching(r"checked locally"), r.notes


def test_modern_absent_field_is_never_overridden(proj: Path):
    """On a current CLI an absent `output_prev` means *checked and rejected*.
    Reaching around it re-creates the mismatched pair those checks exist to
    withhold — which is a delta between two different flag sets rendered as the
    effect of the user's edit."""
    obj, meta = _artifacts(proj, prev=True)          # a .prev IS on disk
    r = _run(proj, modern=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'")   # but not reported
    assert r.prev == "", "the script overrode a CLI that deliberately withheld the pair"
    assert r.note_matching(r"withheld by the CLI"), r.notes


def test_modern_explains_a_withheld_pair_it_can_diagnose(proj: Path):
    """Withholding silently is the failure this design is about. When the CLI says
    nothing, the script still says what it can see."""
    obj, meta = _artifacts(proj, prev=True,
                           prev_src=_to_bash_path(proj / "proj" / "other" / "util.c"))
    r = _run(proj, modern=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    assert r.prev == ""
    assert r.note_matching(r"built from"), r.notes


def test_no_baseline_on_disk_says_so(proj: Path):
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=False, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    assert r.prev == ""
    assert r.note_matching(r"no pre-edit baseline"), r.notes


# ---------------------------------------------------------------------------
# Turn scoping
# ---------------------------------------------------------------------------

def test_turn_is_forwarded_on_a_cli_that_understands_it(proj: Path):
    obj, meta = _artifacts(proj, prev=True)
    r = _run(proj, modern=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta, output_prev=obj + '.prev', meta_prev=meta + '.prev')}'",
             extra_args=["--turn", "turn-A"])
    assert Result.pair(r.compiles[0], "--turn") == "turn-A", r.compiles
    assert r.note_matching(r"turn was NOT verified") is None, (
        "the turn WAS verified here; the caveat must not fire"
    )


def test_turn_is_not_forwarded_to_a_cli_that_would_reject_it(proj: Path):
    """`--turn` arrived with `--inherit-prev`, so the same probe covers both. Sent to
    an older CLI it is a usage error and the compile produces no artifact at all."""
    obj, meta = _artifacts(proj, prev=True)
    r = _run(proj, modern=False, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             extra_args=["--turn", "turn-A"])
    assert r.failed is None, r.raw
    assert "--turn" not in r.compiles[0], r.compiles


@pytest.mark.parametrize("modern,args", [
    (True, []),                          # capable CLI, caller passed no id
    (False, ["--turn", "turn-A"]),       # caller has an id, CLI cannot use it
])
def test_an_unverified_turn_is_always_declared(proj: Path, modern: bool, args: list[str]):
    """A baseline reported without its turn checked can belong to a PREVIOUS turn,
    making the delta span two turns — measured at +77.8% ROM for an edit whose true
    effect was 0. If the check did not happen, the caveat must say so."""
    obj, meta = _artifacts(proj, prev=True)
    extra = {"output_prev": obj + ".prev", "meta_prev": meta + ".prev"} if modern else {}
    r = _run(proj, modern=modern, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta, **extra)}'",
             extra_args=args)
    assert r.prev, "expected a baseline in this scenario"
    assert r.note_matching(r"turn was NOT verified"), r.notes


# ---------------------------------------------------------------------------
# Rust: the two routes behave oppositely on a CLI predating --inherit-prev
# ---------------------------------------------------------------------------

def _rust_proj(tmp_path: Path, *, cargo: bool) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / ".loci-build" / "armv6-m").mkdir(parents=True)
    (root / "src" / "main.rs").write_text("fn main(){}\n", encoding="utf-8")
    if cargo:
        (root / "Cargo.toml").write_text(
            '[package]\nname = "alpha"\nversion = "0.1.0"\n', encoding="utf-8")
    return tmp_path


def test_legacy_does_not_second_guess_a_cargo_refusal(tmp_path: Path):
    """On a CLI predating `--inherit-prev` the cargo route still reports the pair
    itself, gated on having actually inherited it — and deliberately WITHHOLDS it
    when the baseline sidecar records a different package or target ("must not be
    offered as one"). Overriding that renders a package rename as the effect of an
    edit. Reproduced on a real cargo project before this branch existed."""
    home = _rust_proj(tmp_path, cargo=True)
    d = home / "proj" / ".loci-build" / "armv6-m"
    obj, meta = d / "app.o", d / "app.o.meta.json"
    obj.write_bytes(b"\x7fELF"); meta.write_text("{}", encoding="utf-8")
    (d / "app.o.prev").write_bytes(b"\x7fELF-base")
    (d / "app.o.meta.json.prev").write_text("{}", encoding="utf-8")
    r = _run(home, modern=False, source="src/main.rs",
             compile_body=f"echo '{_ok_envelope(_to_bash_path(obj), _to_bash_path(meta))}'")
    assert r.prev == "", "overrode the cargo route's own refusal"
    assert r.note_matching(r"deliberate refusal|crate"), r.notes
    assert "--meta-prev" not in r.compiles[0], (
        "naming a sidecar by hand is what overrides the refusal"
    )


def test_legacy_does_inherit_for_a_standalone_rs(tmp_path: Path):
    """The opposite case, and the reason the `.rs` skip cannot be blanket: the
    standalone route gates auto-inherit on the flag this CLI does not have, so
    nothing inherits and nothing is reported — while the flat object path is exactly
    right. Skipping it there loses real parity (measured: baseline at opt-level 0,
    the new object at 2)."""
    home = _rust_proj(tmp_path, cargo=False)
    d = home / "proj" / ".loci-build" / "armv6-m"
    obj, meta = d / "main.o", d / "main.o.meta.json"
    obj.write_bytes(b"\x7fELF")
    src = _to_bash_path(home / "proj" / "src" / "main.rs")
    meta.write_text(json.dumps({"source_file": src}), encoding="utf-8")
    (d / "main.o.prev").write_bytes(b"\x7fELF-base")
    (d / "main.o.meta.json.prev").write_text(
        json.dumps({"source_file": src}), encoding="utf-8")
    r = _run(home, modern=False, source="src/main.rs",
             compile_body=f"echo '{_ok_envelope(_to_bash_path(obj), _to_bash_path(meta))}'")
    assert "--meta-prev" in r.compiles[0], (
        "a standalone .rs got no parity: this CLI cannot auto-inherit without the flag"
    )
    assert r.prev == _to_bash_path(obj) + ".prev", r.raw


# ---------------------------------------------------------------------------
# Failure reporting
# ---------------------------------------------------------------------------

def test_an_error_envelope_is_surfaced_with_its_code(proj: Path):
    body = ("echo '{\"ok\":false,\"error\":{\"code\":\"compiler_not_found\","
            "\"message\":\"no usable compiler found for blink.c\"}}'; exit 127")
    r = _run(proj, modern=True, compile_body=body, source="blink.c")
    assert r.failed is not None
    assert "compiler_not_found" in r.failed
    assert "no usable compiler" in r.failed
    assert "OBJ" not in r.fields, "no artifact path may be printed alongside FAILED"


def test_a_usage_error_surfaces_stderr_rather_than_nothing(proj: Path):
    """argparse writes to stderr and produces no envelope. Discarding it left
    `FAILED - no envelope` as the entire diagnosis of a fixable mistake — a
    diagnosability regression against the raw compiler line this replaced, which
    did not redirect stderr."""
    body = "echo 'loci: error: unrecognized arguments: --nope' >&2; exit 2"
    r = _run(proj, modern=True, compile_body=body, source="blink.c")
    assert r.failed is not None
    assert "unrecognized arguments" in r.failed, r.failed


def test_ok_with_no_artifact_path_is_a_failure_not_a_null_path(proj: Path):
    """`jq -r '.data.output'` on such an envelope prints the literal string `null`,
    which flows into `--elf null` and replaces the CLI's own remediation with a
    file-not-found about a file nobody named."""
    r = _run(proj, modern=True, compile_body="echo '{\"ok\":true,\"data\":{}}'",
             source="blink.c")
    assert r.failed is not None, r.raw
    assert "null" not in r.fields.get("OBJ", ""), r.raw


def test_a_missing_source_fails_before_compiling(proj: Path):
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             source="does-not-exist.c")
    assert r.failed is not None
    assert not r.compiles, "compiled a source that is not there"


def test_an_unknown_argument_is_reported_not_forwarded(proj: Path):
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             source="blink.c", extra_args=["--frobnicate", "1"])
    assert r.failed is not None and "frobnicate" in r.failed
    assert not r.compiles


def test_compiler_path_is_forwarded_for_the_recovery(proj: Path):
    """The documented `compiler_not_found` recovery re-runs this same command with an
    explicit compiler. A script that rejected the flag would fail on the retry
    instead of on the original problem."""
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             source="blink.c", extra_args=["--compiler-path", "/usr/bin/arm-none-eabi-gcc"])
    assert r.failed is None, r.raw
    assert Result.pair(r.compiles[0], "--compiler-path") == "/usr/bin/arm-none-eabi-gcc"


# ---------------------------------------------------------------------------
# Paths with spaces — a Windows norm, and the defect that motivated the script
# ---------------------------------------------------------------------------

def test_a_source_path_containing_a_space_is_not_a_usage_error(tmp_path: Path):
    """`C:\\Users\\First Last\\…` and `My Project\\…` are ordinary. Unquoted, the
    compile died with a usage error and no envelope, and the message was discarded —
    so the whole diagnosis was `FAILED - no envelope`."""
    root = tmp_path / "proj"
    (root / "My Src").mkdir(parents=True)
    (root / ".loci-build" / "armv6-m").mkdir(parents=True)
    (root / "My Src" / "blink.c").write_text("int main(void){return 0;}\n",
                                             encoding="utf-8")
    d = root / ".loci-build" / "armv6-m"
    obj, meta = d / "blink.o", d / "blink.o.meta.json"
    obj.write_bytes(b"\x7fELF"); meta.write_text("{}", encoding="utf-8")
    r = _run(tmp_path, modern=True, source="My Src/blink.c",
             compile_body=f"echo '{_ok_envelope(_to_bash_path(obj), _to_bash_path(meta))}'")
    assert r.failed is None, r.raw
    # Boundary-aware on purpose: the value must arrive as ONE argument. A
    # space-joined log renders the quoted and unquoted forms identically, and this
    # assertion passed with the quoting deleted until the stub was fixed.
    assert Result.pair(r.compiles[0], "--source") == "My Src/blink.c", r.compiles


def test_a_project_root_containing_a_space_survives(tmp_path: Path):
    home = tmp_path / "My Projects"
    root = home / "proj"
    (root / ".loci-build" / "armv6-m").mkdir(parents=True)
    (root / "blink.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    d = root / ".loci-build" / "armv6-m"
    obj, meta = d / "blink.o", d / "blink.o.meta.json"
    obj.write_bytes(b"\x7fELF")
    meta.write_text(json.dumps(
        {"source_file": _to_bash_path(root / "blink.c")}), encoding="utf-8")
    (d / "blink.o.prev").write_bytes(b"\x7fELF-base")
    (d / "blink.o.meta.json.prev").write_text(json.dumps(
        {"source_file": _to_bash_path(root / "blink.c")}), encoding="utf-8")
    r = _run(home, modern=False, source="blink.c",
             compile_body=f"echo '{_ok_envelope(_to_bash_path(obj), _to_bash_path(meta))}'")
    assert r.failed is None, r.raw
    assert r.prev == _to_bash_path(obj) + ".prev", r.raw


# ---------------------------------------------------------------------------
# Missing prerequisites
# ---------------------------------------------------------------------------

def test_a_missing_loci_is_reported_as_such(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir(parents=True)
    (root / "blink.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(SCRIPT), "--source", "blink.c",
         "--loci-target", "armv6-m"],
        cwd=root, capture_output=True, text=True, timeout=60,
        env={"PATH": _base_path(), "HOME": _to_bash_path(tmp_path)},
    )
    assert proc.returncode == 0, proc.stderr
    r = Result(proc, tmp_path / "nonexistent.log")
    assert r.failed is not None and "loci" in r.failed.lower()
    assert "setup" in r.failed.lower(), "say what the user should run"


# ---------------------------------------------------------------------------
# Everything below closes a mutation that SURVIVED an independent campaign.
# Each names the mutation it exists for, because a test whose subject is not
# written down is a test the next editor deletes as redundant.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("flag", ["--source", "--loci-target", "--context",
                                  "--project-root", "--phase", "--turn",
                                  "--compiler-path"])
def test_a_flag_with_no_value_fails_instead_of_hanging(proj: Path, flag: str):
    """`shift 2` on a trailing flag is a FAILED shift: `$#` never decreases, `$1` is
    re-read, and the loop spins forever. Measured before the fix: rc=124 under an
    external `timeout`, **zero bytes on stdout and stderr** — which the model reads as
    a dead Bash call, not an error. It also broke the file's headline promise that
    FAILED is the only failure signal.

    Reachable straight from the shipped prose: the contract's example ends with
    `--turn "<turn-id>"` and then says to drop the flag when there is no id, so
    deleting only the placeholder is the natural half-edit; the `compiler_not_found`
    recovery appends `--compiler-path` the same way.
    """
    obj, meta = _artifacts(proj, prev=False)
    bin_dir = _mkstub(proj, modern=True,
                      compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(SCRIPT), "--source", "blink.c",
         "--loci-target", "armv6-m", flag],
        cwd=proj / "proj", capture_output=True, text=True, timeout=20,
        env={"PATH": f"{_to_bash_path(bin_dir)}:{_base_path()}",
             "HOME": _to_bash_path(proj)},
    )
    assert proc.returncode == 0, f"exit {proc.returncode}; stderr={proc.stderr!r}"
    r = Result(proc, proj / "args.log")
    assert r.failed is not None, f"no FAILED line; stdout={proc.stdout!r}"
    assert flag in r.failed and "needs a value" in r.failed, r.failed


@pytest.mark.parametrize("flag,expected", [
    ("--loci-target", "armv7e-m"),
    ("--phase", "post-edit"),
])
def test_the_callers_value_reaches_the_cli(proj: Path, flag: str, expected: str):
    """Mutations that hardcoded `--loci-target armv6-m`, or dropped `--phase` and
    `--context` entirely, all left the suite green: every test used the same target,
    so nothing pinned that the caller's value is forwarded at all. A wrong target is a
    wrong ISA — a silently wrong timing model and a wrong `.loci-build/<target>/`
    tree."""
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, source="blink.c", target="armv7e-m",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    assert Result.pair(r.compiles[0], flag) == expected, r.compiles


def test_the_context_reaches_the_cli(tmp_path: Path):
    """`--context` feeds the CLI's flag cascade, so losing it changes which compiler
    and flags are chosen. Dropping it survived the whole suite."""
    root = tmp_path / "proj"
    (root / ".loci-build" / "armv6-m").mkdir(parents=True)
    (root / "blink.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    ctx = tmp_path / "ctx.json"
    ctx.write_text(json.dumps({"project_root": _to_bash_path(root)}), encoding="utf-8")
    obj, meta = _artifacts(tmp_path, prev=False)
    r = _run(tmp_path, modern=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             extra_args=["--context", _to_bash_path(ctx)])
    assert Result.pair(r.compiles[0], "--context") == _to_bash_path(ctx), r.compiles


# `--phase` is not in this list: its values never contain a space, so the assertion
# below could only ever pass vacuously for it. Its forwarding is pinned by
# `test_the_callers_value_reaches_the_cli` instead.
@pytest.mark.parametrize("flag", ["--project-root", "--meta-prev", "--context",
                                  "--turn"])
def test_every_forwarded_value_arrives_as_one_argument(tmp_path: Path, flag: str):
    """Only `--source` was boundary-checked, so unquoting any of the others survived.
    Concrete input for `--project-root`: a root of `C:\\Users\\First Last\\proj`
    becomes two argv entries, argparse rejects it, and there is no envelope."""
    home = tmp_path / "My Home"
    root = home / "My Proj"
    (root / ".loci-build" / "armv6-m").mkdir(parents=True)
    (root / "blink.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    ctx = home / "My Ctx.json"
    ctx.write_text(json.dumps({"project_root": _to_bash_path(root)}), encoding="utf-8")
    d = root / ".loci-build" / "armv6-m"
    obj, meta = d / "blink.o", d / "blink.o.meta.json"
    obj.write_bytes(b"\x7fELF")
    src = _to_bash_path(root / "blink.c")
    meta.write_text(json.dumps({"source_file": src}), encoding="utf-8")
    (d / "blink.o.prev").write_bytes(b"\x7fELF-base")
    (d / "blink.o.meta.json.prev").write_text(
        json.dumps({"source_file": src}), encoding="utf-8")

    bin_dir = _mkstub(home, modern=False,
                      compile_body=f"echo '{_ok_envelope(_to_bash_path(obj), _to_bash_path(meta))}'")
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(SCRIPT),
         "--source", "blink.c", "--loci-target", "armv6-m",
         "--project-root", _to_bash_path(root), "--phase", "post-edit",
         "--context", _to_bash_path(ctx), "--turn", "turn A"],
        cwd=root, capture_output=True, text=True, timeout=60,
        env={"PATH": f"{_to_bash_path(bin_dir)}:{_base_path()}",
             "HOME": _to_bash_path(home)},
    )
    assert proc.returncode == 0, proc.stderr
    r = Result(proc, home / "args.log")
    assert r.failed is None, r.raw
    got = Result.pair(r.compiles[0], flag)
    if flag == "--turn":
        assert got is None, "--turn must not reach a CLI that predates it"
        return
    assert got is not None, f"{flag} was not forwarded at all: {r.compiles}"
    assert " " in got, (
        f"{flag} arrived as {got!r} — a value with a space was split into two "
        "arguments, which argparse rejects with no envelope"
    )


def test_a_shell_above_the_project_root_still_finds_the_baseline(tmp_path: Path):
    """The third vacuity. Every other test runs with the shell's CWD **equal to** the
    directory it passes as `--project-root`, so "anchored to the project root" and
    "resolved against the CWD" are the same path, and an assertion about the former can
    only see a spelling difference. Here they genuinely differ."""
    home = tmp_path
    root = home / "proj"
    (root / ".loci-build" / "armv6-m").mkdir(parents=True)
    (root / "blink.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    obj, meta = _artifacts(home, prev=True)
    r = _run(home, modern=False, source="proj/blink.c", cwd=home,
             compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    assert r.failed is None, r.raw
    got = Result.pair(r.compiles[0], "--meta-prev")
    assert got == meta + ".prev", (
        f"--meta-prev came back {got!r}: resolved against the shell's CWD "
        f"({home}) instead of the project root"
    )
    assert r.prev == obj + ".prev", r.raw
    assert not (home / ".loci-build").exists(), (
        "a stray .loci-build tree was created beside the project root"
    )


def test_an_unrelated_cargo_toml_above_the_project_does_not_make_it_a_crate(tmp_path: Path):
    """The walk-up must stop where the CLI's own `find_manifest` stops — the project
    root. Unbounded, a `Cargo.toml` in any ancestor (a sibling checkout, a tools dir,
    `$HOME`) classified a standalone `.rs` as a crate: `--meta-prev` was dropped, so
    parity was silently lost, and the run reported a cargo refusal the CLI never
    made."""
    home = tmp_path
    (home / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")   # unrelated
    root = home / "proj"
    (root / "src").mkdir(parents=True)
    d = root / ".loci-build" / "armv6-m"
    d.mkdir(parents=True)
    (root / "src" / "main.rs").write_text("fn main(){}\n", encoding="utf-8")
    obj, meta = d / "main.o", d / "main.o.meta.json"
    obj.write_bytes(b"\x7fELF")
    src = _to_bash_path(root / "src" / "main.rs")
    meta.write_text(json.dumps({"source_file": src}), encoding="utf-8")
    (d / "main.o.prev").write_bytes(b"\x7fELF-base")
    (d / "main.o.meta.json.prev").write_text(
        json.dumps({"source_file": src}), encoding="utf-8")

    r = _run(home, modern=False, source="src/main.rs",
             compile_body=f"echo '{_ok_envelope(_to_bash_path(obj), _to_bash_path(meta))}'")
    assert "--meta-prev" in r.compiles[0], (
        "a standalone .rs was treated as a crate because of a Cargo.toml above the "
        f"project root; parity was dropped: {r.compiles}"
    )
    assert r.prev == _to_bash_path(obj) + ".prev", r.raw
    assert r.note_matching(r"deliberate refusal|crate") is None, (
        f"reported a cargo refusal the CLI never made: {r.notes}"
    )


def test_a_baseline_sidecar_with_no_source_file_is_withheld(proj: Path):
    """Unknown must not read as fine. Objects are still stem-keyed, so this is the one
    check that tells `modA/util.c`'s baseline from `modB`'s — and a sidecar torn by the
    pre-edit hook's 8 s timeout is unreadable, which looks exactly like a missing
    field. Treating it as clean served modA's object as modB's Before **under a note
    claiming the source had been verified**."""
    d = proj / "proj" / ".loci-build" / "armv6-m"
    obj, meta = d / "blink.o", d / "blink.o.meta.json"
    obj.write_bytes(b"\x7fELF")
    meta.write_text(json.dumps(
        {"source_file": _to_bash_path(proj / "proj" / "blink.c")}), encoding="utf-8")
    (d / "blink.o.prev").write_bytes(b"\x7fELF-base")
    (d / "blink.o.meta.json.prev").write_text("{}", encoding="utf-8")   # no source_file
    r = _run(proj, modern=False, source="blink.c",
             compile_body=f"echo '{_ok_envelope(_to_bash_path(obj), _to_bash_path(meta))}'")
    assert r.prev == "", "adopted a baseline whose build record names no source"
    assert r.note_matching(r"does not say which source"), r.notes
    assert r.note_matching(r"verified here instead") is None, (
        "claimed the source was verified when the field was absent"
    )


def test_a_cargo_crate_is_not_accused_of_a_stem_collision(tmp_path: Path):
    """A crate has one object per *target*, so `source_file` records whichever module
    triggered the build. Editing `src/util.rs` against a baseline captured from
    `src/main.rs` of the SAME crate is comparable — yet the source comparison produced
    a confident note about a collision that cannot happen in a crate, displacing the
    accurate explanation."""
    home = tmp_path
    root = home / "proj"
    (root / "src").mkdir(parents=True)
    (root / "Cargo.toml").write_text('[package]\nname = "alpha"\n', encoding="utf-8")
    d = root / ".loci-build" / "armv6-m"
    d.mkdir(parents=True)
    (root / "src" / "util.rs").write_text("pub fn u(){}\n", encoding="utf-8")
    obj, meta = d / "app.o", d / "app.o.meta.json"
    obj.write_bytes(b"\x7fELF")
    meta.write_text(json.dumps(
        {"source_file": _to_bash_path(root / "src" / "util.rs")}), encoding="utf-8")
    (d / "app.o.prev").write_bytes(b"\x7fELF-base")
    (d / "app.o.meta.json.prev").write_text(json.dumps(
        {"source_file": _to_bash_path(root / "src" / "main.rs")}), encoding="utf-8")

    r = _run(home, modern=True, source="src/util.rs",
             compile_body=f"echo '{_ok_envelope(_to_bash_path(obj), _to_bash_path(meta))}'")
    assert r.note_matching(r"another file's baseline") is None, (
        f"accused a crate of a stem collision: {r.notes}"
    )
    assert r.note_matching(r"withheld by the CLI"), r.notes


def test_a_build_diff_that_cannot_answer_is_not_called_a_flag_mismatch(proj: Path):
    """Anything other than `match: true` was treated as a flags mismatch, including
    *no answer at all* — so a run reported a toolchain change that was never
    established, and offered a remediation command that fails the same way. Inside the
    design's own threat model: a `.prev` sidecar torn by the hook's timeout is invalid
    JSON, so the diff cannot answer."""
    obj, meta = _artifacts(proj, prev=True)
    bin_dir = _mkstub(proj, modern=False,
                      compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    stub = bin_dir / "loci"
    text = stub.read_text(encoding="utf-8")
    marker = '  "build diff")'
    assert marker in text, "the stub no longer has a build diff arm to break"
    head, _, tail = text.partition(marker)
    tail = tail.split("\n", 2)[2]          # drop the arm's echo line
    stub.write_text(head + marker + '\n      echo "loci: boom" >&2; exit 1 ;;\n' + tail,
                    encoding="utf-8")
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(SCRIPT), "--source", "blink.c",
         "--loci-target", "armv6-m", "--project-root", _to_bash_path(proj / "proj")],
        cwd=proj / "proj", capture_output=True, text=True, timeout=60,
        env={"PATH": f"{_to_bash_path(bin_dir)}:{_base_path()}",
             "HOME": _to_bash_path(proj)},
    )
    assert proc.returncode == 0, proc.stderr
    r = Result(proc, proj / "args.log")
    assert r.prev == "", "adopted a baseline whose parity was never established"
    assert r.note_matching(r"no verdict|could not be compared"), r.notes
    assert r.note_matching(r"not built with this compiler") is None, (
        "reported a flags mismatch that was never established"
    )


def test_prev_and_prev_meta_are_reported_together_or_not_at_all(proj: Path):
    """They come from independent reads, so a truncated envelope can carry one without
    the other — and half a pair is not a baseline. A `PREV` with an empty `PREV_META`
    flows into `loci build diff --prev ""`."""
    obj, meta = _artifacts(proj, prev=True)
    for extra in ({"output_prev": obj + ".prev"}, {"meta_prev": meta + ".prev"}):
        r = _run(proj, modern=True, source="blink.c",
                 compile_body=f"echo '{_ok_envelope(obj, meta, **extra)}'")
        assert r.prev == "" and r.fields["PREV_META"] == "", (
            f"half a pair was reported: {r.raw}"
        )


def test_the_four_keys_are_always_printed_even_with_no_baseline(proj: Path):
    """Case B is the case every skill branches on, and it was the one place the output
    shape stopped being pinned: removing the `PREV`/`PREV_META` lines entirely when
    there is no baseline left the whole suite green."""
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    for key in ("OBJ", "META", "PREV", "PREV_META"):
        assert key in r.fields, f"{key} line is missing from a no-baseline run: {r.raw}"
    assert r.fields["PREV"] == "" and r.fields["PREV_META"] == ""


def test_a_baseline_object_without_its_sidecar_is_not_adopted(proj: Path):
    """Concrete input: the pre-edit hook killed on its 8 s budget after copying the
    object but before the sidecar. Removing this check left the suite green."""
    obj, meta = _artifacts(proj, prev=True)
    (proj / "proj" / ".loci-build" / "armv6-m" / "blink.o.meta.json.prev").unlink()
    r = _run(proj, modern=False, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    assert r.prev == "", "adopted a baseline object with no build record"
    assert r.note_matching(r"no pre-edit baseline"), r.notes


def test_the_turn_caveat_does_not_fire_without_a_baseline(proj: Path):
    """The caveat is about a baseline that might belong to another turn. With no
    baseline at all there is no delta to mis-scope, and saying otherwise trains the
    reader to ignore it."""
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    assert r.prev == ""
    assert r.note_matching(r"turn was NOT verified") is None, r.notes


def test_a_missing_jq_is_reported_like_a_missing_loci(tmp_path: Path):
    """`jq` is a documented prerequisite and every envelope read needs it. The `loci`
    precheck had a test and this one did not."""
    root = tmp_path / "proj"
    root.mkdir(parents=True)
    (root / "blink.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    bin_dir = tmp_path / "onlyloci"
    bin_dir.mkdir()
    (bin_dir / "loci").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    (bin_dir / "loci").chmod(0o755)
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(SCRIPT), "--source", "blink.c",
         "--loci-target", "armv6-m"],
        cwd=root, capture_output=True, text=True, timeout=60,
        env={"PATH": _to_bash_path(bin_dir), "HOME": _to_bash_path(tmp_path)},
    )
    assert proc.returncode == 0, proc.stderr
    r = Result(proc, tmp_path / "nonexistent.log")
    assert r.failed is not None and "jq" in r.failed.lower(), r.raw


def test_loci_target_is_required(proj: Path):
    """Without it the CLI cannot resolve an ISA at all, and argparse would reject the
    call with no envelope. Saying so here is cheaper than a usage error downstream."""
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, source="blink.c", omit=("--loci-target",),
             compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    assert r.failed is not None and "--loci-target" in r.failed, r.raw
    assert not r.compiles


# ---------------------------------------------------------------------------
# `--reconstruct` — the header case (phase 06d)
#
# The file the user edited emits no object, so what gets measured is a translation
# unit that #includes it. That TU was not itself edited, so nothing snapshotted its
# object and there is no `.prev` anywhere: the Before has to be REBUILT from the
# header's captured pre-edit text, via `loci build compile --baseline`.
#
# Two of these are about codes that are ANSWERS rather than failures. The runtime
# contract says `FAILED` is surfaced verbatim and stops, so routing
# `baseline_unaffected` — a true statement about the user's code — through it would
# report a working build as a broken one.
# ---------------------------------------------------------------------------

def _baseline_ok(obj: str, meta: str, verified: bool = True) -> str:
    return "echo '" + json.dumps({
        "ok": True,
        "data": {"output": obj, "meta_file": meta,
                 "baseline_reconstruction": {"verified": verified, "mode": "in-place"}},
    }) + "'"


def _baseline_err(code: str, message: str) -> str:
    return "echo '" + json.dumps(
        {"ok": False, "error": {"code": code, "message": message}}) + "'"


def _recon_paths(home: Path) -> tuple[str, str]:
    """Where a reconstruction lands: under the turn tree, never beside the object."""
    d = home / "proj" / ".loci-build" / "turns" / "abc123" / "obj" / "armv6-m"
    d.mkdir(parents=True, exist_ok=True)
    return _to_bash_path(d / "blink.o"), _to_bash_path(d / "blink.o.meta.json")


def test_reconstruct_rebuilds_the_before_and_reports_it_as_the_pair(proj: Path):
    """The caller asked for a measurement and gets OBJ/META/PREV/PREV_META, exactly
    as for an ordinary edit. That two compiles happened is not its problem."""
    obj, meta = _artifacts(proj, prev=False)
    b_obj, b_meta = _recon_paths(proj)
    r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             baseline_body=_baseline_ok(b_obj, b_meta),
             extra_args=["--turn", "abc123", "--reconstruct"])
    assert r.failed is None, r.raw
    assert r.fields["OBJ"] == obj
    assert r.prev == b_obj
    assert r.fields["PREV_META"] == b_meta
    assert len(r.compiles) == 2, f"expected an After and a Before, got {r.compiles}"
    before = r.compiles[1]
    assert "--baseline" in before
    assert Result.pair(before, "--turn") == "abc123"
    assert "--output" not in before, (
        "--baseline must never be told where to write: pointed at the real object it "
        "overwrites the very artifact being measured"
    )


def test_reconstruct_does_nothing_without_a_turn_id(proj: Path):
    """The pre-edit copies are stored per turn, so with no id there is nothing to
    identify which capture to build against. Say so rather than compiling."""
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             extra_args=["--reconstruct"])
    assert r.failed is None, r.raw
    assert r.prev == ""
    assert len(r.compiles) == 1
    assert r.note_matching("without a turn id"), r.notes


def test_reconstruct_is_gated_on_the_capability_probe_not_on_an_empty_field(proj: Path):
    """The pin is an exact `==`, so an older `loci` is a normal state — and one
    without `--baseline` answers argparse's usage error on stderr with NO envelope,
    which this script would otherwise report as the user's compile failing. The probe
    is what keeps the flag away from it."""
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, can_reconstruct=False, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             extra_args=["--turn", "abc123", "--reconstruct"])
    assert r.failed is None, r.raw
    assert r.prev == ""
    assert len(r.compiles) == 1, (
        f"the probe should have kept --baseline away from this CLI: {r.compiles}")
    # Matched on `--baseline`, NOT on the words "cannot rebuild": both this note and
    # the no-turn one used that phrase, so a mutation giving this branch the no-turn
    # TEXT verbatim survived the campaign — and a user on an old CLI was then told to
    # pass a `--turn` they had already passed, with the suite still green.
    note = r.note_matching(r"--baseline")
    assert note, r.notes
    assert "turn id" not in note, (
        "this branch must not be described as a missing turn id — the turn was "
        "supplied and the CLI is simply too old"
    )
    assert "/loci:setup" not in note, (
        "the pin is an exact `==`, so /loci:setup reinstalls the SAME version; "
        "advising it sends the user round a loop that cannot terminate"
    )


def test_an_unaffected_unit_is_an_answer_not_a_failure(proj: Path):
    """`baseline_unaffected` is a true statement about the code: this TU reads
    nothing that was edited this turn. The contract routes `FAILED` to "surface
    verbatim and stop", so sending this there reports a working build as broken."""
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             baseline_body=_baseline_err(
                 "baseline_unaffected",
                 "this translation unit does not read anything edited in this turn"),
             extra_args=["--turn", "abc123", "--reconstruct"])
    assert r.failed is None, "an unaffected unit must not end the compile path"
    assert r.fields["OBJ"] == obj, "the After is still a real measurement"
    assert r.prev == ""
    assert r.note_matching("does not read anything that was edited"), r.notes


def test_an_unreconstructible_before_names_the_reason(proj: Path):
    """The other code, and it means the OPPOSITE thing — the pre-edit state could not
    be rebuilt at all. Both produce an empty Before, so a caller that conflated them
    would report one as the other."""
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             baseline_body=_baseline_err(
                 "baseline_not_reconstructible",
                 "the pre-edit copy did not win the include search: this compile read "
                 "inc/cfg.h from the working tree"),
             extra_args=["--turn", "abc123", "--reconstruct"])
    assert r.failed is None, r.raw
    assert r.prev == ""
    note = r.note_matching("could not be rebuilt")
    assert note, r.notes
    assert "did not win the include search" in note, (
        "the CLI's own reason is the actionable half — dropping it leaves the user "
        "with an unexplained missing Before, which is the failure this plan is about"
    )
    assert r.note_matching("does not read anything that was edited") is None, (
        "an unreconstructible Before must not read as an unaffected unit"
    )


def test_an_unverified_reconstruction_is_reported_as_such(proj: Path):
    """`verified:false` means the CLI could not PROVE the rebuild read the captured
    copies. The object may still be right — but the failure it could not rule out is
    precisely the one that renders as "your edit changed nothing"."""
    obj, meta = _artifacts(proj, prev=False)
    b_obj, b_meta = _recon_paths(proj)
    r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             baseline_body=_baseline_ok(b_obj, b_meta, verified=False),
             extra_args=["--turn", "abc123", "--reconstruct"])
    assert r.prev == b_obj, "an unverified rebuild is still reported, with a caveat"
    assert r.note_matching("could not be verified"), r.notes


def test_a_verified_reconstruction_carries_no_caveat(proj: Path):
    """The positive control for the test above: without it, the caveat could be
    unconditional and both tests would pass."""
    obj, meta = _artifacts(proj, prev=False)
    b_obj, b_meta = _recon_paths(proj)
    r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             baseline_body=_baseline_ok(b_obj, b_meta, verified=True),
             extra_args=["--turn", "abc123", "--reconstruct"])
    assert r.prev == b_obj
    assert r.note_matching("could not be verified") is None, r.notes


def test_a_real_pre_edit_object_beats_a_rebuilt_one(proj: Path):
    """When the TU was itself edited this turn AND a header it includes was too, the
    snapshotted object is the better Before: it was compiled from the tree as it
    actually stood, with no reproduction to lose the include search and no shifted
    `__FILE__`. So the rebuild does not run at all."""
    obj, meta = _artifacts(proj, prev=True)
    r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta, output_prev=obj + '.prev', meta_prev=meta + '.prev')}'",
             baseline_body=_baseline_ok(*_recon_paths(proj)),
             extra_args=["--turn", "abc123", "--reconstruct"])
    assert r.prev == obj + ".prev"
    assert len(r.compiles) == 1, f"the rebuild should not have run: {r.compiles}"


def test_without_the_flag_nothing_reconstructs(proj: Path):
    """The positive control for the whole feature: an ordinary edit must not gain a
    second compile just because the CLI can do one."""
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             baseline_body=_baseline_ok(*_recon_paths(proj)),
             extra_args=["--turn", "abc123"])
    assert len(r.compiles) == 1, f"expected one compile, got {r.compiles}"
    assert r.prev == ""


def test_an_unexpected_baseline_error_is_a_note_not_a_failure(proj: Path):
    """The After is a real measurement whatever went wrong with the Before. Ending
    the compile path here throws away a result the user can still use."""
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             baseline_body=_baseline_err("compiler_not_found", "no usable compiler"),
             extra_args=["--turn", "abc123", "--reconstruct"])
    assert r.failed is None, r.raw
    assert r.fields["OBJ"] == obj
    assert r.note_matching("no usable compiler"), r.notes


def test_the_rebuild_survives_a_path_containing_a_space(proj: Path):
    """A `${CONTEXT:+--context "$CONTEXT"}` form is subject to word splitting, so it
    breaks every path with a space — the Windows norm, and one of the six defects
    that made this a script rather than a fenced recipe. Asserted on the argv list,
    because `"$*"` renders a quoted and an unquoted argument identically."""
    src = proj / "proj" / "My Src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "blink.c").write_text("int main(void){return 0;}\n", encoding="utf-8")
    ctx = proj / "proj" / "My Ctx" / "context.json"
    ctx.parent.mkdir(parents=True, exist_ok=True)
    ctx.write_text(json.dumps({"project_root": _to_bash_path(proj / "proj")}),
                   encoding="utf-8")
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, can_reconstruct=True, source="My Src/blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             baseline_body=_baseline_ok(*_recon_paths(proj)),
             extra_args=["--context", _to_bash_path(ctx),
                         "--turn", "abc123", "--reconstruct"])
    assert r.failed is None, r.raw
    before = r.compiles[1]
    assert Result.pair(before, "--source") == "My Src/blink.c"
    assert Result.pair(before, "--context") == _to_bash_path(ctx)


def test_reconstruct_does_not_claim_the_turn_was_unverified(proj: Path):
    """A reconstruction REQUIRES `--turn` and the CLI checks it, so the generic
    "the baseline's turn was NOT verified" caveat would be false here — and a caveat
    that is always present is one a reader learns to skip."""
    obj, meta = _artifacts(proj, prev=False)
    b_obj, b_meta = _recon_paths(proj)
    r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             baseline_body=_baseline_ok(b_obj, b_meta),
             extra_args=["--turn", "abc123", "--reconstruct"])
    assert r.prev == b_obj
    assert r.note_matching("turn was NOT verified") is None, r.notes


def test_reconstruct_takes_no_value_and_does_not_eat_the_next_flag(proj: Path):
    """`--reconstruct` is a bare switch beside seven flags that all take values, so a
    copied `shift 2` would silently swallow whatever follows it — here the turn id,
    which would then be absent and the rebuild skipped with a confusing note."""
    obj, meta = _artifacts(proj, prev=False)
    b_obj, b_meta = _recon_paths(proj)
    r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             baseline_body=_baseline_ok(b_obj, b_meta),
             extra_args=["--reconstruct", "--turn", "abc123"])
    assert r.failed is None, r.raw
    assert r.prev == b_obj, r.notes
    assert Result.pair(r.compiles[1], "--turn") == "abc123"


# ---------------------------------------------------------------------------
# What the two hostile reviews of 06d found. Each of these was a live defect.
# ---------------------------------------------------------------------------

def test_a_rebuild_that_returns_no_artifact_path_still_says_something(proj: Path):
    """`ok:true` with a missing (or half-present) `output`/`meta_file`.

    Both lenses found this independently: the branch had no `else`, and the
    `RECONSTRUCT=yes` arm below it was a bare `:` commented "already explained
    above" — when nothing had been. The answer rendered as an ordinary After-only
    measurement with ZERO notes, which is the chain going silent again inside the
    code written to stop it going silent. The tell was the asymmetry: the After has
    had an explicit guard for this shape since 02b.

    Note the shape is this suite's own default `BASELINE_BODY`, so the fixture
    constructed the state and nothing asserted on it."""
    obj, meta = _artifacts(proj, prev=False)
    for body in ('echo \'{"ok":true,"data":{}}\'',
                 'echo \'{"ok":true,"data":{"output":"/tmp/x.o"}}\''):
        r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
                 compile_body=f"echo '{_ok_envelope(obj, meta)}'",
                 baseline_body=body,
                 extra_args=["--turn", "abc123", "--reconstruct"])
        assert r.failed is None, r.raw
        assert r.prev == ""
        assert r.notes, f"no note at all for {body!r} — the chain went quiet"
        assert r.note_matching("without a usable artifact path"), r.notes


def test_using_an_existing_object_instead_of_rebuilding_is_stated(proj: Path):
    """The rule "a real pre-edit object beats a rebuilt one" is normally right, and
    06d created the case where it is not. `build snapshot` COPIES an object already
    on disk; it does not compile. So: edit `cfg.h`; the header route's After compile
    writes a fresh `app.o` that already contains the header edit; then edit `app.c`
    in the same turn; the pre-edit hook snapshots THAT object as `app.o.prev`,
    stamped with this turn so every check passes. The `.c`'s delta then silently
    excludes the header's contribution.

    Nothing in the envelope says when an object was built, so the pair is still
    preferred — and the ambiguity is stated rather than left for the reader to not
    know about."""
    obj, meta = _artifacts(proj, prev=True)
    r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta, output_prev=obj + '.prev', meta_prev=meta + '.prev')}'",
             baseline_body=_baseline_ok(*_recon_paths(proj)),
             extra_args=["--turn", "abc123", "--reconstruct"])
    assert r.prev == obj + ".prev"
    assert len(r.compiles) == 1
    assert r.note_matching("already had a pre-edit object"), r.notes

    # The control: an ordinary measurement must NOT carry that caveat, or it becomes
    # a line every report prints and every reader learns to skip.
    plain = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
                 compile_body=f"echo '{_ok_envelope(obj, meta, output_prev=obj + '.prev', meta_prev=meta + '.prev')}'",
                 extra_args=["--turn", "abc123"])
    assert plain.note_matching("already had a pre-edit object") is None, plain.notes


def test_an_old_cli_still_adopts_a_local_baseline_it_can_verify(proj: Path):
    """Adding `--reconstruct` must not DESTROY a usable Before.

    The `RECONSTRUCT=yes` arm short-circuited the whole ladder below it, including
    the legacy local-adoption path — so on a CLI without `--baseline`, the same
    source with the same files on disk produced a real `.prev` without the flag and
    nothing with it, blaming "no --baseline" while a source-and-flag-verified
    candidate sat right there."""
    obj, meta = _artifacts(proj, prev=True)
    r = _run(proj, modern=False, can_reconstruct=False, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             extra_args=["--turn", "abc123", "--reconstruct"])
    assert r.failed is None, r.raw
    assert r.prev == obj + ".prev", (
        f"a verified local baseline was thrown away by --reconstruct: {r.notes}")
    assert r.note_matching("checked locally"), r.notes
    # …and the reason the rebuild did not happen is still stated.
    assert r.note_matching(r"--baseline"), r.notes


def test_a_multi_line_error_message_stays_one_note_line(proj: Path):
    """The file's headline promise is "tab-separated key/value lines on stdout and
    nothing else", and several notes interpolate a CLI `error.message`, which is free
    to be multi-line. Two lines out meant one `NOTE<TAB>…` followed by a bare line
    that parses as nothing."""
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             baseline_body=_baseline_err(
                 "baseline_not_reconstructible", "line one\nline two\nline three"),
             extra_args=["--turn", "abc123", "--reconstruct"])
    for line in r.raw.splitlines():
        if line.strip():
            assert "\t" in line, f"a bare line reached stdout: {line!r}"
    note = r.note_matching("could not be rebuilt")
    assert note and "line one line two line three" in note, r.notes


def test_a_project_relative_source_is_resolved_against_the_project_root(proj: Path):
    """`loci build affected` reports translation units project-relative, and the
    header path hands one straight back as `--source`. From a shell that is not
    sitting at the project root that failed with "source not found" — on a path that
    is perfectly correct relative to the root this script had just computed, two
    lines earlier, for exactly this class of reason."""
    obj, meta = _artifacts(proj, prev=False)
    (proj / "elsewhere").mkdir(exist_ok=True)
    r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             baseline_body=_baseline_ok(*_recon_paths(proj)),
             cwd=proj / "elsewhere",
             extra_args=["--turn", "abc123", "--reconstruct"])
    assert r.failed is None, r.raw
    assert r.fields["OBJ"] == obj
    # …and an absolute path is untouched by the fallback.
    absolute = _to_bash_path(proj / "proj" / "blink.c")
    r2 = _run(proj, modern=True, can_reconstruct=True, source=absolute,
              compile_body=f"echo '{_ok_envelope(obj, meta)}'",
              baseline_body=_baseline_ok(*_recon_paths(proj)),
              cwd=proj / "elsewhere",
              extra_args=["--turn", "abc123", "--reconstruct"])
    assert r2.failed is None, r2.raw
    assert Result.pair(r2.compiles[0], "--source") == absolute


def test_a_source_that_is_nowhere_is_still_refused(proj: Path):
    """The positive control for the fallback above: it must not turn a genuinely
    missing file into a compile of something else."""
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, can_reconstruct=True, source="no/such/file.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'",
             extra_args=["--turn", "abc123", "--reconstruct"])
    assert r.failed is not None and "source not found" in r.failed, r.raw
    assert not r.compiles


# ---------------------------------------------------------------------------
# Phase 02e: the CLI's own reason, when it gives one
# ---------------------------------------------------------------------------
# Before 02e an absent `output_prev` said nothing, so this script re-derived the
# decision: it re-read both sidecars and spent a `loci build diff` to guess at a
# judgement the CLI had already made — and where it could not guess it told the user
# the "likely cause". A CLI carrying 02e reports `data.baseline_withheld{code,reason}`
# instead, and the reason is relayed verbatim.
#
# The gate is the FIELD, not the `--inherit-prev` probe: the two landed in different
# changes, so "modern" does not imply "says why". Every modern test above emits an
# envelope without the field and still exercises the local diagnosis — that is the
# generation this script will meet on the first release carrying `--inherit-prev`.

def _withheld(code: str, reason: str) -> dict:
    return {"code": code, "reason": reason}


def test_the_clis_own_reason_is_relayed_verbatim(proj: Path):
    obj, meta = _artifacts(proj, prev=True)
    # No apostrophe: the stub echoes its envelope out of a single-quoted shell
    # string, so one there breaks the FIXTURE rather than the script. The real
    # reasons do contain them ("another file's baseline") and travel fine —
    # `test_a_reason_containing_a_quote_survives` below drives that from a file
    # instead, because a fixture that cannot express a character is not evidence
    # the character is handled.
    reason = ("the pre-edit copy on disk was captured for a different user turn, "
              "so comparing against it would report the sum of two turns of edits "
              "as the effect of this one.")
    r = _run(proj, modern=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta, baseline_withheld=_withheld('other_turn', reason))}'")
    assert r.prev == "", "a baseline the CLI refused was served as the Before"
    assert reason in r.notes, r.notes


def test_the_clis_reason_replaces_the_scripts_guess(proj: Path):
    """The two would otherwise BOTH be printed, and they disagree by construction:
    the script cannot see the capture marker, the turn or the digests, which is why
    its own fallback names a *likely* cause. Two explanations of one refusal, one of
    them speculative, is worse than either alone."""
    obj, meta = _artifacts(proj, prev=True)
    r = _run(proj, modern=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta, baseline_withheld=_withheld('capture_modified', 'the pre-edit copy no longer matches its recorded digests.'))}'")
    assert r.note_matching(r"withheld by the CLI") is None, (
        f"the script's guess was printed alongside the CLI's answer: {r.notes}")
    assert r.note_matching(r"digests"), r.notes


def test_the_clis_reason_costs_no_build_diff(proj: Path):
    """The local diagnosis spends a `loci build diff` per withheld baseline, inside
    a post-edit budget. When the owner has already answered there is nothing to
    diagnose — and a subprocess that cannot change the outcome is one the hook pays
    for anyway."""
    obj, meta = _artifacts(proj, prev=True)
    r = _run(proj, modern=True, source="blink.c", diff_match=False,
             compile_body=f"echo '{_ok_envelope(obj, meta, baseline_withheld=_withheld('build_differs', 'it was not built with this compiler and these flags.'))}'")
    assert not [c for c in r.calls if c[:2] == ["build", "diff"]], (
        f"the CLI had already decided, and the script re-derived it: {r.calls}")


def test_a_stated_reason_never_makes_the_script_adopt_a_baseline(proj: Path):
    """The adoption arm is the one place this script can put a Before back, and it
    is gated on the legacy branch. A stated refusal must not reach it however the
    ladder is reordered — reaching around a checked refusal is the mismatched-pair
    defect the whole design exists to prevent."""
    obj, meta = _artifacts(proj, prev=True)          # a candidate that looks fine
    r = _run(proj, modern=True, source="blink.c", diff_match=True,
             compile_body=f"echo '{_ok_envelope(obj, meta, baseline_withheld=_withheld('other_source', 'it was built from src/other.c, not blink.c.'))}'")
    assert r.prev == "" and r.fields["PREV_META"] == "", r.raw
    assert r.note_matching("checked locally") is None, r.notes


def test_a_reason_is_not_read_as_a_pair(proj: Path):
    """`baseline_withheld` beside a reported pair is a malformed envelope — the two
    are exclusive. The pair still wins, because the fields naming actual artifacts
    are the ones the measurement runs on, and a note contradicting them would be
    read as a caveat on a real Before."""
    obj, meta = _artifacts(proj, prev=True)
    r = _run(proj, modern=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta, output_prev=obj + '.prev', meta_prev=meta + '.prev', baseline_withheld=_withheld('not_captured', 'no pre-edit copy was captured.'))}'")
    assert r.prev == obj + ".prev"
    assert r.note_matching("no pre-edit copy") is None, r.notes


def test_an_old_cli_still_gets_the_local_diagnosis(proj: Path):
    """The positive control for the gate: the field is absent on every generation
    before 02e — including modern ones — and reading that absence as "nothing to
    say" would delete the local diagnosis on exactly the installs that still need
    it. This is the whole legacy path, in one assertion."""
    obj, meta = _artifacts(proj, prev=True,
                           prev_src=_to_bash_path(proj / "proj" / "other" / "util.c"))
    r = _run(proj, modern=False, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta)}'")
    assert r.prev == ""
    assert r.note_matching(r"built from"), r.notes


def test_a_multi_line_reason_stays_one_note_line(proj: Path):
    """`reason` is the CLI's text, and the CLI is free to change it. The note helper
    scrubs it for the same reason it scrubs an `error.message`: one newline turns
    the answer into a bare line the caller parses as nothing."""
    obj, meta = _artifacts(proj, prev=True)
    r = _run(proj, modern=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta, baseline_withheld=_withheld('build_differs', 'first line\nsecond line'))}'")
    assert r.prev == ""
    assert any("first line second line" in n for n in r.notes), r.notes
    for line in r.raw.splitlines():
        assert not line or line.split("\t")[0] in {"OBJ", "META", "PREV",
                                                   "PREV_META", "NOTE"}, repr(line)


def test_a_reason_containing_a_quote_survives(proj: Path):
    """The CLI's own `other_source` reason says "another file's baseline", and the
    stub above cannot express an apostrophe — so this one hands the envelope over
    from a FILE rather than an `echo '…'`. A fixture that cannot produce a
    character is not evidence that the character is handled; the shipped text
    contains one, so it gets its own route in."""
    obj, meta = _artifacts(proj, prev=True)
    reason = ("the pre-edit copy beside this object was built from src/other.c, "
              "not blink.c, so it is another file's baseline.")
    envfile = proj / "env.json"
    envfile.write_text(
        _ok_envelope(obj, meta, baseline_withheld=_withheld("other_source", reason)),
        encoding="utf-8")
    r = _run(proj, modern=True, source="blink.c",
             compile_body=f'cat "{_to_bash_path(envfile)}"')
    assert r.failed is None, r.raw
    assert r.prev == ""
    assert reason in r.notes, r.notes


def test_the_reconstruct_explanation_is_not_joined_by_the_clis(proj: Path):
    """Ordering, and it is load-bearing: the new arm sits BELOW the reconstruct arm.

    Under `--reconstruct` the absent `.prev` is the premise — the translation unit
    being measured was not itself edited, so of course nothing snapshotted it — and
    the CLI's `not_captured` is a true sentence about a state that is not the
    problem. Printed beside the reconstruct arm's own explanation it is a second
    answer to a question that has one, and the second is a distraction from a real
    failure the user needs to act on.

    Found by a review mutation that moved the arm one position up: the whole suite
    stayed green.
    """
    obj, meta = _artifacts(proj, prev=False)
    r = _run(proj, modern=True, can_reconstruct=True, source="blink.c",
             compile_body=f"echo '{_ok_envelope(obj, meta, baseline_withheld=_withheld('not_captured', 'no pre-edit baseline was captured for this object.'))}'",
             baseline_body='echo \'{"ok":false,"error":{"code":"baseline_not_reconstructible","message":"the header was not captured this turn"}}\'',
             extra_args=["--turn", "abc123", "--reconstruct"])
    assert r.failed is None, r.raw
    assert r.prev == ""
    assert r.note_matching("could not be rebuilt"), r.notes
    assert r.note_matching("no pre-edit baseline was captured") is None, (
        f"the CLI's answer to a different question was printed too: {r.notes}")


def test_an_old_probe_with_a_new_field_still_never_adopts(proj: Path):
    """The gate is the field, so the two capability axes can disagree: a CLI whose
    `--help` does not advertise `--inherit-prev` may still report a reason. There
    the arm REPLACES the local ladder, including its adoption arm — which is the
    only place this script can put a Before back.

    That is the safe direction and it is the point: the CLI saw the capture marker,
    the turn and the digests; this script sees none of them, and its local checks
    would have adopted a copy the CLI had just refused for failing a digest.
    """
    obj, meta = _artifacts(proj, prev=True)      # a candidate its own checks like
    r = _run(proj, modern=False, source="blink.c", diff_match=True,
             compile_body=f"echo '{_ok_envelope(obj, meta, baseline_withheld=_withheld('capture_modified', 'the pre-edit object no longer matches the digest recorded when it was captured.'))}'")
    assert r.prev == "", "a baseline the CLI refused was adopted by the legacy ladder"
    assert r.note_matching("checked locally") is None, r.notes
    assert r.note_matching("no longer matches the digest"), r.notes
