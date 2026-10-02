"""No hook depends on the directory it is started in (AAD-7790, criterion 3 of AAD-7782).

Claude Code starts a plugin hook in the project; GitHub Copilot CLI starts it
in the PLUGIN root (COPILOT-PROBE-EVIDENCE.md §4: `pwd` inside the hook was the
plugin directory, not the project), and names the project only through the
payload's `cwd` and `CLAUDE_PROJECT_DIR`. A hook that resolved a relative path
against its working directory, or wrote a file "here", worked under one host
and misbehaved under the other without a test noticing.

So each hook is run three times on the same payload, from the project, from
the plugin root and from an unrelated empty directory, under each host, and:

* its stdout is the same each time (a minted turn id masked — it is random by
  design) and every `loci` call it made is the same argv on the same stdin;
* the unrelated directory is still empty afterwards and the plugin root has
  gained nothing at its top level — a hook may write only under the project,
  the state directory and `$HOME`.
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

from tests.fixtures.copilot_payloads import Host

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS = PLUGIN_ROOT / "hooks"


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


pytestmark = pytest.mark.skipif(_find_bash() is None, reason="bash required")


def _to_bash_path(p: Path) -> str:
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else s


PROMPT_ID = "7790c0de-0000-4000-8000-000000000002"
_TURN = re.compile(r"cp-[0-9]+-[0-9a-f]+")
_OK = ("echo '{\"ok\":true,\"data\":{\"report\":\"\",\"applied\":true,\"measurable\":true,"
       "\"governed\":true,\"artifact_only\":false,\"pending\":0,\"stale\":false}}'")


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".loci" / "build" / "turns").mkdir(parents=True)
    (root / ".loci" / "build.yaml").write_text("version: 1\n", encoding="utf-8")
    (root / "main.c").write_text("int f(void) { return 1; }\n", encoding="utf-8")
    return root


def _base(root: Path, event: str, **extra) -> dict:
    doc = {"hook_event_name": event, "prompt_id": PROMPT_ID, "cwd": _to_bash_path(root)}
    doc.update(extra)
    return doc


def _edit(root: Path, event: str) -> dict:
    doc = _base(root, event, tool_name="Edit",
                tool_input={"file_path": _to_bash_path(root / "main.c"),
                            "old_string": "1", "new_string": "2"})
    if event.startswith("Post"):
        doc["tool_response"] = {"structuredPatch": []}
    return doc


def _bash(root: Path, event: str) -> dict:
    doc = _base(root, event, tool_name="Bash", tool_input={"command": "make"})
    if event.startswith("Post"):
        doc["tool_response"] = {"stdout": "", "stderr": "", "interrupted": False}
    return doc


CASES = [
    ("session-init.sh", lambda r: _base(r, "SessionStart", source="new")),
    ("turn-clean.sh", lambda r: _base(r, "SessionStart", source="new")),
    ("prompt-submit-turn.sh", lambda r: _base(r, "UserPromptSubmit", prompt="hi")),
    ("contract-guard.sh", lambda r: _edit(r, "PreToolUse")),
    ("contract-guard.sh", lambda r: _bash(r, "PreToolUse")),
    ("pre-edit-hook.sh", lambda r: _edit(r, "PreToolUse")),
    ("post-edit-hook.sh", lambda r: _edit(r, "PostToolUse")),
    ("post-bash-bypass.sh", lambda r: _bash(r, "PostToolUse")),
    ("stats-flush.sh", lambda r: _base(r, "Stop", stop_hook_active=False)),
    ("draft-pending-nudge.sh", lambda r: _base(r, "Stop", stop_hook_active=False)),
    ("manifest-status-nudge.sh", lambda r: _base(r, "Stop", stop_hook_active=False)),
    ("turn-clean.sh", lambda r: _base(r, "Stop", stop_hook_active=False)),
    ("subagent-start.sh", lambda r: _base(r, "SubagentStart")),
]
_IDS = [f"{h}-{b(Path('/p'))['hook_event_name']}-{b(Path('/p')).get('tool_name', '')}".rstrip("-")
        for h, b in CASES]


class _Seen:
    """What one run produced: stdout (turn ids masked) and the stub's calls."""

    def __init__(self, stdout: str, log: Path):
        self.stdout = _TURN.sub("cp-TURN", stdout)
        self.calls: list[tuple[list[str], str]] = []
        if log.is_file():
            for chunk in log.read_bytes().split(b"\x1d"):
                if chunk:
                    args, stdin = chunk.split(b"\x1f", 1)
                    self.calls.append(([a.decode("utf-8") for a in args.split(b"\x1e")[:-1]],
                                       _TURN.sub("cp-TURN", stdin.decode("utf-8"))))

    @property
    def verbs(self) -> tuple:
        """The calls, in order, each as argv plus the stdin it was fed — less
        the `loci --version` probes, counted as "probed or not": session-init
        asks once more from a background child whose log write may or may not
        have landed when the log is read, and the probe inherits whatever stdin
        its parent had, which it never reads. Every other call counts, with its
        multiplicity: two snapshots from one directory and one from another
        would be a difference."""
        probed = any(a == ["--version"] for a, _ in self.calls)
        return (probed, tuple((tuple(a), s) for a, s in self.calls if a != ["--version"]))

    def __eq__(self, other):
        return (self.stdout, self.verbs) == (other.stdout, other.verbs)

    def __repr__(self):
        return f"stdout={self.stdout!r} calls={self.calls!r}"


def _run_from(tmp_path: Path, host: Host, hook: str, payload: dict, cwd: Path,
              tag: str) -> _Seen:
    """One run, from `cwd`, with its own state and home so that the three runs
    do not see each other's files (a first-write-wins turn file, a welcome
    marker) — what a hook does must not depend on the directory, and the only
    thing that varies between the runs is the directory."""
    root = tmp_path / "proj"
    home = tmp_path / f"home-{tag}"
    state = tmp_path / f"state-{tag}"
    bin_dir = home / ".local" / "bin"
    for d in (home, state, bin_dir):
        d.mkdir(parents=True, exist_ok=True)
    log = home / "calls.log"
    (bin_dir / "loci").write_text(
        "#!/usr/bin/env bash\n"
        f'{{ for a in "$@"; do printf "%s\\036" "$a"; done; printf "\\037"; cat; '
        f'printf "\\035"; }} >> "{_to_bash_path(log)}"\n'
        f"{_OK}\n", encoding="utf-8", newline="\n")
    (bin_dir / "loci").chmod(0o755)
    env = {
        "PATH": f"{_to_bash_path(bin_dir)}:/usr/bin:/bin:/usr/local/bin",
        "HOME": _to_bash_path(home),
        "LOCI_STATE_DIR": _to_bash_path(state),
        "CLAUDE_PROJECT_DIR": _to_bash_path(root),
        "_LOCI_BOOTSTRAP": "1",
    }
    env.update(host.env(project=_to_bash_path(root)))
    doc = host.respell(payload)
    host.seed(state)
    proc = subprocess.run([_find_bash(), _to_bash_path(HOOKS / hook)],
                          input=json.dumps(doc), capture_output=True, text=True,
                          encoding="utf-8", timeout=120, env=env, cwd=str(cwd))
    assert proc.returncode == 0, f"{hook} from {cwd}: {proc.returncode} {proc.stderr!r}"
    assert proc.stderr == "", f"{hook} from {cwd}: {proc.stderr!r}"
    # The plugin root is the one directory a hook may name (`plugin dir:` in
    # the session context) — it is the hook's own, found through `$0`, not
    # through the working directory; the three-way comparison below is what
    # proves that. An unrelated directory in a reply is a cwd leak.
    if cwd.resolve() not in (root.resolve(), PLUGIN_ROOT.resolve()):
        for spelling in {str(cwd), cwd.as_posix(), _to_bash_path(cwd)}:
            assert spelling not in proc.stdout, (
                f"{hook}: its working directory {spelling!r} is in its reply: {proc.stdout!r}")
    return _Seen(proc.stdout, log)


@pytest.mark.parametrize("hook,build", CASES, ids=_IDS)
def test_a_hook_behaves_the_same_from_any_directory(tmp_path, host, hook, build):
    root = _project(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    before = _plugin_root_entries()
    payload = build(root)

    from_project = _run_from(tmp_path, host, hook, payload, root, "project")
    from_plugin = _run_from(tmp_path, host, hook, payload, PLUGIN_ROOT, "plugin")
    from_elsewhere = _run_from(tmp_path, host, hook, payload, elsewhere, "elsewhere")

    assert from_plugin == from_project, (
        f"{hook} under {host}: from the plugin root it did something else.\n"
        f"  project:  {from_project!r}\n  plugin:   {from_plugin!r}")
    assert from_elsewhere == from_project, (
        f"{hook} under {host}: from an unrelated directory it did something else.\n"
        f"  project:   {from_project!r}\n  elsewhere: {from_elsewhere!r}")
    assert os.listdir(elsewhere) == [], (
        f"{hook} wrote into its working directory: {os.listdir(elsewhere)}")
    assert _plugin_root_entries() == before, (
        f"{hook} wrote into the plugin root: {_plugin_root_entries() - before}")


#: Top-level entries of the plugin root that something other than a hook
#: writes while a suite runs — pytest's cache, Python's, an editor's swap —
#: and so cannot be evidence of a hook writing where it ran.
_NOT_A_HOOKS = {".pytest_cache", "__pycache__", ".git", ".venv", ".idea", ".vscode"}


def _plugin_root_entries() -> set:
    return {n for n in os.listdir(PLUGIN_ROOT)
            if n not in _NOT_A_HOOKS and not n.endswith((".swp", "~", ".tmp"))}


def test_the_table_names_every_hook_in_hooks_json():
    """A hook added to `hooks.json` without a row here is a hook whose working
    directory nobody checks."""
    doc = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
    registered = set()
    for groups in doc["hooks"].values():
        for g in groups:
            for h in g["hooks"]:
                m = re.search(r"hooks/([\w.-]+\.sh)", h["command"])
                assert m, h["command"]
                registered.add(m.group(1))
    covered = {h for h, _ in CASES}
    # The CLI installer is launched by other hooks, never by the host.
    exempt = {"ensure-loci-cli.sh"}
    assert registered - exempt <= covered, (
        f"hooks registered but not run from three directories: "
        f"{sorted(registered - exempt - covered)}")
