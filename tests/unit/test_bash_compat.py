"""The bash a hook runs under (`lib/bash-compat.sh`, AAD-7771).

Stock macOS ships /bin/bash 3.2.57, and `hooks.json` starts every hook as
`bash -c 'exec bash <script>'`. Two facts about 3.2 drove this file, both
measured on the mac-mini and reproduced on a 3.2.57 built from source:

  * `${v//pat/rep}` costs ~2 ms PER MATCH over 16 KB and grows with the square
    of the length (31 ms per match at 64 KB; 19 s for a 16 KB string of
    matches, against 85 ms on bash 5). The contract guard's 5 s budget over a
    64 KB field was 150 matches from a kill, and a killed PreToolUse fails open.
  * a quoted replacement keeps its quote characters — `test_loci_json.py`
    pins that one.

So a hook under bash 3 re-executes itself under a newer bash when one is in a
place macOS package managers put it, and otherwise runs a LEGACY PROFILE: the
JSON prefix and the guard's tokenise cap drop to 4 KB, commands above it take
the coarse arm, a `file_path` that did not close inside the prefix is denied,
and `session-init.sh` says so once.

The suite cannot run bash 3.2 here, so these tests pin the MECHANICS on the
bash they have: the probe picks a 4+ candidate and nothing else, the prologue
sits ahead of every hook's stdin read, and the legacy branches are reachable
by forcing the version the code reads. The behaviour under a real 3.2 is the
mac-mini's job (and WSL's, with a 3.2 built from source).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS = sorted((PLUGIN_ROOT / "hooks").glob("*.sh"))
COMPAT = PLUGIN_ROOT / "lib" / "bash-compat.sh"


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (r"C:\Program Files\Git\usr\bin\bash.exe",
                     r"C:\Program Files (x86)\Git\usr\bin\bash.exe"):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


def _to_bash_path(p: Path) -> str:
    s = str(p).replace("\\", "/")
    if sys.platform == "win32" and len(s) > 1 and s[1] == ":":
        s = "/" + s[0].lower() + s[2:]
    return s


pytestmark = pytest.mark.skipif(_find_bash() is None, reason="bash not found")


def _stub_bash(dir_: Path, name: str, major: str) -> Path:
    """A `bash` that answers the version probe with `major` and nothing else."""
    p = dir_ / name
    p.write_text("#!/usr/bin/env bash\n"
                 # The probe runs `-c 'printf %s "${BASH_VERSINFO[0]}"'`; a
                 # stub cannot have a BASH_VERSINFO of its own, so it prints
                 # the number it stands for.
                 f"printf %s {major}\n", encoding="utf-8", newline="\n")
    p.chmod(0o755)
    return p


def _probe(tmp_path: Path, major: int, *candidates: Path) -> tuple[int, str]:
    script = (f". '{_to_bash_path(COMPAT)}'\n"
              f"_loci_bash_newer {major} "
              + " ".join(f"'{_to_bash_path(c)}'" for c in candidates)
              + "\nrc=$?\nprintf '%s|%s' \"$rc\" \"$_LOCI_BASH_NEWER\"\n")
    out = subprocess.run([_find_bash(), "-c", script], capture_output=True,
                         text=True, timeout=30, cwd=str(tmp_path))
    assert out.returncode == 0, out.stderr
    rc, chosen = out.stdout.split("|", 1)
    return int(rc), chosen


def test_the_probe_picks_the_first_candidate_that_is_bash_4_or_newer(tmp_path):
    old = _stub_bash(tmp_path, "bash3", "3")
    new = _stub_bash(tmp_path, "bash5", "5")
    newer = _stub_bash(tmp_path, "bash6", "6")
    rc, chosen = _probe(tmp_path, 3, tmp_path / "absent", old, new, newer)
    assert rc == 0
    assert chosen == _to_bash_path(new), chosen


def test_the_probe_declines_when_no_candidate_is_newer(tmp_path):
    old = _stub_bash(tmp_path, "bash3", "3")
    junk = tmp_path / "notbash"
    junk.write_text("#!/usr/bin/env bash\necho hello\n", encoding="utf-8",
                    newline="\n")
    junk.chmod(0o755)
    rc, chosen = _probe(tmp_path, 3, old, junk, tmp_path / "absent")
    assert rc == 1 and chosen == "", (rc, chosen)


def _driving_bash_major() -> int:
    out = subprocess.run([_find_bash(), "-c", 'printf %s "${BASH_VERSINFO[0]}"'],
                         capture_output=True, text=True, timeout=30)
    return int(out.stdout.strip() or 0)


def test_a_modern_bash_never_probes(tmp_path):
    """On bash 4+ the file costs one arithmetic test and forks nothing: the
    probe is not consulted even when a candidate exists, and the profile flag
    stays empty. Sourced under a bash 3 (the WSL bed, the mac-mini) the same
    line sets the flag — with no newer bash at the fixed paths, which is what
    those two hosts have."""
    new = _stub_bash(tmp_path, "bash5", "5")
    rc, chosen = _probe(tmp_path, 5, new)
    assert rc == 1 and chosen == "", (rc, chosen)
    out = subprocess.run(
        [_find_bash(), "-c",
         f". '{_to_bash_path(COMPAT)}'; printf '%s' \"${{_LOCI_BASH_LEGACY:-}}\""],
        capture_output=True, text=True, timeout=30)
    want = "1" if _driving_bash_major() < 4 else ""
    assert out.stdout == want, (f"legacy flag {out.stdout!r} under bash "
                                f"{_driving_bash_major()}; wanted {want!r}")


def test_every_hook_sources_the_prologue_before_it_reads_stdin():
    """The re-exec hands the payload on: it is still on stdin only if nothing
    has read it. So the prologue is the first statement of every hook, ahead of
    `payload=$(cat)` / `read -r -d ''`, and every hook has one."""
    stdin_read = re.compile(r"\$\(cat\)|IFS= read -r -d ''")
    problems = []
    for hook in HOOKS:
        lines = hook.read_text(encoding="utf-8").splitlines()
        src = next((i for i, l in enumerate(lines) if "lib/bash-compat.sh" in l
                    and not l.lstrip().startswith("#")), None)
        first_stmt = next(i for i, l in enumerate(lines)
                          if l.strip() and not l.lstrip().startswith("#"))
        read = next((i for i, l in enumerate(lines) if stdin_read.search(l)
                     and not l.lstrip().startswith("#")), None)
        if src is None:
            problems.append(f"{hook.name}: no prologue")
        elif src != first_stmt:
            problems.append(f"{hook.name}: prologue at line {src + 1}, first "
                            f"statement at {first_stmt + 1}")
        elif read is not None and read < src:
            problems.append(f"{hook.name}: reads stdin at line {read + 1}, "
                            f"before the prologue at {src + 1}")
    assert not problems, "\n".join(problems)


def test_the_caps_drop_to_4_kb_under_bash_3():
    """The two readers with a size-dependent cost key their legacy cap off the
    same version test the prologue uses, and the number is the measured one."""
    lib = (PLUGIN_ROOT / "lib" / "loci_json.sh").read_text(encoding="utf-8")
    guard = (PLUGIN_ROOT / "hooks" / "contract-guard.sh").read_text(encoding="utf-8")
    assert re.search(r'if \[ "\$\{BASH_VERSINFO\[0\]:-0\}" -lt 4 \]; then\s*\n'
                     r'\s*: "\$\{LOCI_JSON_MAX:=4096\}"', lib), \
        "lib/loci_json.sh: no 4 KB prefix under bash 3"
    assert re.search(r'if \[ "\$\{BASH_VERSINFO\[0\]:-0\}" -lt 4 \]; then\s*\n'
                     r'\s*_R2_MAX_TOKENISE=4096', guard), \
        "contract-guard.sh: no 4 KB tokenise cap under bash 3"
    assert "REASON_UNREAD=" in guard and 'deny "$REASON_UNREAD"' in guard, \
        "an unread file_path under bash 3 must be denied, with its own reason"


def test_session_init_names_the_profile_once_and_only_under_it():
    src = (PLUGIN_ROOT / "hooks" / "session-init.sh").read_text(encoding="utf-8")
    lines = [l for l in src.splitlines()
             if "_LOCI_BASH_LEGACY" in l and not l.lstrip().startswith("#")]
    assert len(lines) == 1, lines
    assert "brew install bash" in lines[0]
    assert lines[0].lstrip().startswith('[ -z "${_LOCI_BASH_LEGACY:-}" ] ||'), \
        "the line is emitted only when the profile is on"


def test_the_eval_harness_does_not_call_gnu_timeout_directly():
    """Stock macOS has no `timeout`; `run_evals.sh` exited 127 before `claude`
    ran and every eval came back "empty response". One wrapper, every site."""
    src = (PLUGIN_ROOT / "run_evals.sh").read_text(encoding="utf-8")
    direct = [l for l in src.splitlines()
              if re.search(r"(^|[|;&(\s])timeout\s+--kill-after", l)
              and not l.lstrip().startswith("#")
              and "command -v timeout" not in l
              and "with_timeout()" not in l]
    # The wrapper's own two arms are the only callers of the binary.
    assert len(direct) == 2, "\n".join(direct)
    assert src.count('| with_timeout "$EVAL_TIMEOUT"') == 4
