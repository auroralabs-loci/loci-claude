"""The session context under GitHub Copilot CLI, and Claude Code's unchanged (AAD-7784).

`hooks/session-init.sh` writes the rules every skill relies on, for Claude Code:
it names `Edit/Write`, says Claude Code prompts for out-of-project writes, and
reports a `plugin dir:` found by scanning Claude's `~/.claude/plugins/cache/loci/
<version>/` layout for the newest sibling. Under Copilot (Epic AAD-7779) three
things are different, each proven on Windows with Copilot CLI 1.0.91:

* **The plugin lives where the host says.** Copilot exports `CLAUDE_PLUGIN_ROOT`
  and `COPILOT_PLUGIN_ROOT` (a `C:\\…` path on Windows) and keeps the plugin at
  `~/.copilot/installed-plugins/<marketplace>/<name>/` or at a `--plugin-dir`:
  no version siblings, so the scan has nothing to find — and a dotted-numeric
  neighbour of a `--plugin-dir` checkout would be taken for a newer install.
* **Every SessionStart entry runs whatever its `matcher`.** The entry registered
  for `startup` fired for `source: new` and again for `source: resume` on
  `--continue`, so without a gate the context is re-injected and the CLI
  bootstrap relaunched on every prompt of a resumed session.
* **`systemMessage` reaches nobody** (AAD-7783), so the one-time welcome and
  the install banner ride in the context, fenced as text to display.

The Copilot branch is gated on `COPILOT_CLI`, inside `lib/loci_host.sh` and
never in the hook (`test_host_reply_envelope` lints that). Without it the
hook's bytes are the ones they have always been — the Claude Code tests here
pin the exact sentences the Copilot branch rewrites.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest


PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent

CLAUDE_EDIT_SENTENCE = "After an Edit/Write to a C/C++/Rust/Go source"
CLAUDE_PERMISSION_SENTENCE = (
    "outside the working directory — Claude Code prompts the user for permission "
    "on every out-of-project access, halting automated preflight/post-edit/eval "
    "runs. Always write inside the project")
COPILOT_EDIT_SENTENCE = "After an edit/create to a C/C++/Rust/Go source"
COPILOT_PATH_SENTENCE = ("outside the working directory. Always write inside the "
                         "project")
CLAUDE_PLAN_CLAUSE = ("In /plan mode, when the user describes new C/C++/Rust/Go logic "
                      "in a project a LOCI recipe governs, you MUST invoke "
                      "loci:loci-preflight on its existing callees before proposing edits.")
COPILOT_PLAN_CLAUSE = ("In plan mode (a [[PLAN]] prompt), when the user describes new "
                       "C/C++/Rust/Go logic in a project a LOCI recipe governs, you MUST "
                       "invoke loci:loci-preflight on the functions it changes and the "
                       "existing callees of its new code (a new function changes its "
                       "caller) before proposing edits.")
NOTICE_OPEN = "--- LOCI notice ---"
NOTICE_CLOSE = "--- end of LOCI notice ---"

#: What the Copilot rewrites may add to the block, measured against the Claude
#: Code block of the same run: the one `host:` line and the edit-tool words
#: (AAD-7784, +75 B net of the shorter path sentence) and the preflight clause
#: of the auto-run rule (AAD-7787, +100 B). The bytes are paid on every Copilot
#: session start, so they are bounded like the rest of the block
#: (test_project_detection_gate's budget covers the Claude Code half).
HOST_LINE_ALLOWANCE = 260


def _find_bash() -> str | None:
    if sys.platform == "win32":
        for cand in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
        ):
            if Path(cand).is_file():
                return cand
    return shutil.which("bash")


needs_bash = pytest.mark.skipif(_find_bash() is None, reason="bash not available")


def _to_bash_path(p: Path) -> str:
    s = p.as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    if m:
        return f"/{m.group(1).lower()}/{m.group(2)}"
    return s


def _host_path(p: Path) -> str:
    """The path as the host exports it: `C:\\…` from Copilot on Windows, POSIX
    elsewhere — the spelling `loci_host_plugin_dir` has to normalise."""
    return str(p)


def _bash_spelling(p: Path) -> str:
    """The exported root as bash spells it after `cd` — what `plugin dir:` must
    say. Asked of bash rather than computed: on Git Bash a Windows path under
    `%TEMP%` comes back as `/tmp/…`, not `/c/Users/…/Temp/…`, and the point of
    the line is that it names the directory the way the hook's own `$0` would."""
    out = subprocess.run([_find_bash(), "-c", 'cd "$1" && pwd', "_", _host_path(p)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def _stage_plugin(d: Path, version: str) -> Path:
    """A plugin at ``d`` with the real hook and lib, and a manifest at ``version``."""
    (d / "hooks").mkdir(parents=True)
    (d / "lib").mkdir(parents=True)
    (d / ".claude-plugin").mkdir(parents=True)
    shutil.copy(PLUGIN_ROOT / "hooks" / "session-init.sh", d / "hooks")
    # The detached installer the hook launches on a first start: staged so the
    # launch is real (and logged), inert through conftest's `_LOCI_BOOTSTRAP`.
    shutil.copy(PLUGIN_ROOT / "hooks" / "ensure-loci-cli.sh", d / "hooks")
    for src in (PLUGIN_ROOT / "lib").iterdir():
        if src.is_file():
            shutil.copy(src, d / "lib")
        elif src.is_dir() and src.name != "__pycache__":
            shutil.copytree(src, d / "lib" / src.name)
    (d / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "loci", "version": version}))
    return d


def _payload(source: str | None = "new", session: str = "sess-1") -> str:
    doc = {"hook_event_name": "SessionStart", "session_id": session,
           "timestamp": "2026-10-01T11:54:04.356Z", "cwd": "C:\\proj"}
    if source is not None:
        doc["source"] = source
    return json.dumps(doc)


def _run(plugin: Path, home: Path, *, copilot: bool, payload: str | None = None,
         plugin_root: Path | None = None, version: str = "1.0.91-1",
         extra: dict | None = None) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "HOME": _to_bash_path(home),
        "LOCI_STATE_DIR": _to_bash_path(home / ".loci" / "state"),
    }
    env.pop("COPILOT_CLI", None)
    env.pop("COPILOT_PLUGIN_ROOT", None)
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    if copilot:
        env["COPILOT_CLI"] = "1"
        env["COPILOT_CLI_BINARY_VERSION"] = version
        root = plugin_root or plugin
        env["CLAUDE_PLUGIN_ROOT"] = _host_path(root)
        env["COPILOT_PLUGIN_ROOT"] = _host_path(root)
    if extra:
        env.update(extra)
    # _LOCI_BOOTSTRAP=1 (conftest) keeps the hook from installing the CLI.
    feed = {"input": payload} if payload is not None else {"stdin": subprocess.DEVNULL}
    return subprocess.run(
        [_find_bash(), _to_bash_path(plugin / "hooks" / "session-init.sh")],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=60,
        **feed,
    )


def _doc(res: subprocess.CompletedProcess) -> dict:
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _nested(doc: dict) -> str:
    return doc["hookSpecificOutput"]["additionalContext"]


def _line(ctx: str, label: str) -> str:
    lines = [ln for ln in ctx.splitlines() if ln.startswith(label)]
    assert len(lines) == 1, f"{label!r} appears {len(lines)} times:\n{ctx}"
    return lines[0]


# ── where the plugin is ──────────────────────────────────────────────────────

@needs_bash
def test_copilot_plugin_dir_is_the_exported_root_not_the_newest_sibling(tmp_path):
    """Claude's cache layout with a newer sibling, run from the older dir: Claude
    Code advertises the newer (test_session_init_stale_version); Copilot, which
    exported the older as the root it loaded, gets the older — the one whose
    skills and hooks are the session's."""
    cache = tmp_path / "cache" / "loci" / "loci"
    older = _stage_plugin(cache / "0.1.10", "0.1.10")
    _stage_plugin(cache / "0.1.20", "0.1.20")
    home = tmp_path / "home"
    home.mkdir()

    claude = _nested(_doc(_run(older, home, copilot=False)))
    assert _line(claude, "plugin dir:") == f"plugin dir: {_to_bash_path(cache / '0.1.20')}"
    assert "loci version: 0.1.20" in claude

    copilot = _doc(_run(older, home, copilot=True, payload=_payload()))["additionalContext"]
    assert _line(copilot, "plugin dir:") == f"plugin dir: {_bash_spelling(older)}"
    assert "loci version: 0.1.10" in copilot, (
        "the version is read off the advertised dir's manifest, so it follows it")


@needs_bash
def test_copilot_plugin_dir_for_an_installed_plugin_and_a_plugin_dir_checkout(tmp_path):
    """The two places Copilot keeps a plugin. `installed-plugins/loci/loci` has no
    dotted-numeric sibling and the scan would fall back to `$0`'s dir anyway;
    a `--plugin-dir` checkout beside a dotted-numeric directory that LOOKS like
    a plugin version is the case the scan gets wrong, and the exported root
    gets right."""
    home = tmp_path / "home"
    home.mkdir()
    installed = _stage_plugin(
        tmp_path / ".copilot" / "installed-plugins" / "loci" / "loci", "0.2.30")
    ctx = _doc(_run(installed, home, copilot=True, payload=_payload()))["additionalContext"]
    assert _line(ctx, "plugin dir:") == f"plugin dir: {_bash_spelling(installed)}"
    # (Not compared with the Claude Code run's line: that run is launched by a
    # POSIX `$0` while Copilot's root is the host's spelling, and on Git Bash a
    # `%TEMP%` directory is `/tmp/…` by one road and `/c/Users/…/Temp/…` by the
    # other. Under Copilot `$0` is built from the same exported root.)

    projects = tmp_path / "Projects"
    checkout = _stage_plugin(projects / "loci-claude-dev", "0.2.74")
    _stage_plugin(projects / "9.9.9", "9.9.9")        # a neighbour the scan would take
    ctx = _doc(_run(checkout, home, copilot=True, payload=_payload()))["additionalContext"]
    assert _line(ctx, "plugin dir:") == f"plugin dir: {_bash_spelling(checkout)}"
    assert "loci version: 0.2.74" in ctx
    # …and that neighbour IS what the scan answers without the host's word, which
    # is why the exported root wins under Copilot.
    claude = _nested(_doc(_run(checkout, home, copilot=False)))
    assert _line(claude, "plugin dir:") == f"plugin dir: {_to_bash_path(projects / '9.9.9')}"


@needs_bash
def test_copilot_with_no_usable_exported_root_falls_back_to_the_hooks_own_dir(tmp_path):
    """An exported root that is not a directory (unset, or stale) answers nothing,
    and the hook's own location stands in — never an empty `plugin dir:`."""
    home = tmp_path / "home"
    home.mkdir()
    plugin = _stage_plugin(tmp_path / "plugin", "0.2.74")
    ctx = _doc(_run(plugin, home, copilot=True, payload=_payload(),
                    plugin_root=tmp_path / "gone"))["additionalContext"]
    assert _line(ctx, "plugin dir:") == f"plugin dir: {_to_bash_path(plugin)}"


# ── what the context says ────────────────────────────────────────────────────

@needs_bash
def test_copilot_context_names_the_host_and_its_tools_and_never_claude_code(tmp_path):
    """Acceptance criterion 1: the host line, Copilot's tool names in the auto-run
    rule, `/plan`, no "Claude Code" — and the text rides in the top-level
    `additionalContext` Copilot injects (AAD-7783)."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".loci").mkdir()
    (home / ".loci" / ".welcome-shown").touch()          # the context alone
    plugin = _stage_plugin(tmp_path / "plugin", "0.2.74")
    doc = _doc(_run(plugin, home, copilot=True, payload=_payload()))
    ctx = doc["additionalContext"]

    host = _line(ctx, "host:")
    assert host.startswith("host: GitHub Copilot CLI 1.0.91-1.")
    for mapping in ("Edit/Write is edit/create", "Bash is bash or powershell",
                    "Read is view", "the question tool is ask_user"):
        assert mapping in host, mapping
    assert "/plan, /loci:<skill> and ! <command> work as written" in host
    assert COPILOT_EDIT_SENTENCE in ctx
    assert COPILOT_PATH_SENTENCE in ctx
    assert COPILOT_PLAN_CLAUSE in ctx
    assert "Claude Code" not in ctx, [ln for ln in ctx.splitlines() if "Claude Code" in ln]
    assert CLAUDE_EDIT_SENTENCE not in ctx
    # The same text goes out in both places (Copilot drops the nested one).
    assert _nested(doc) == ctx
    # The Claude Code path has no host line at all.
    claude = _nested(_doc(_run(plugin, home, copilot=False)))
    assert not [ln for ln in claude.splitlines() if ln.startswith("host:")]


@needs_bash
def test_the_preflight_clause_is_host_shaped(tmp_path):
    """AAD-7787. Copilot's plan mode reaches its model as a `[[PLAN]]` prefix on
    the prompt, so the Copilot rule names the prefix; and it says what preflight
    runs on, because "its existing callees" read as nothing for a new function
    that calls nothing (a live `--plan` run skipped on that reading, while the
    plan modified the caller). Claude Code's clause is byte for byte the one it
    has always been — `[[PLAN]]` is a Copilot word and never reaches Claude."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".loci").mkdir()
    (home / ".loci" / ".welcome-shown").touch()
    plugin = _stage_plugin(tmp_path / "plugin", "0.2.74")
    copilot = _doc(_run(plugin, home, copilot=True, payload=_payload()))["additionalContext"]
    claude = _nested(_doc(_run(plugin, home, copilot=False)))
    rule = _line(copilot, "LOCI auto-run rules:")
    assert COPILOT_PLAN_CLAUSE in rule
    assert "(a new function changes its caller)" in rule
    assert "In /plan mode" not in rule
    assert "on its existing callees" not in rule
    claude_rule = _line(claude, "LOCI auto-run rules:")
    assert CLAUDE_PLAN_CLAUSE in claude_rule
    assert "[[PLAN]]" not in claude
    assert "changes its caller" not in claude


@needs_bash
def test_copilot_host_line_is_unversioned_when_the_host_exports_no_version(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    plugin = _stage_plugin(tmp_path / "plugin", "0.2.74")
    ctx = _doc(_run(plugin, home, copilot=True, payload=_payload(),
                    version=""))["additionalContext"]
    assert _line(ctx, "host:").startswith("host: GitHub Copilot CLI. ")


@needs_bash
def test_claude_code_context_is_the_text_it_has_always_been(tmp_path):
    """Acceptance criteria 2 and 4: without `COPILOT_CLI` the two rewritten
    sentences are Claude Code's, nothing Copilot-only appears, and the reply is
    the nested shape alone. The byte-for-byte proof against `main` is the
    parity gate (`tools/claude-parity.sh`); this pins the sentences."""
    home = tmp_path / "home"
    home.mkdir()
    plugin = _stage_plugin(tmp_path / "plugin", "0.2.74")
    doc = _doc(_run(plugin, home, copilot=False))
    ctx = _nested(doc)
    assert CLAUDE_EDIT_SENTENCE in ctx
    assert CLAUDE_PERMISSION_SENTENCE in ctx
    assert COPILOT_EDIT_SENTENCE not in ctx
    assert "host:" not in ctx
    assert NOTICE_OPEN not in ctx and "[loci] Between the markers" not in ctx
    assert "additionalContext" not in doc, "the top-level field is Copilot's alone"
    # The welcome stays where Claude Code renders it.
    assert "LOCI is ready." in doc["systemMessage"]
    # Claude Code never exports COPILOT_CLI; a `source` on its payload (`startup`)
    # changes nothing either, since the gate is the host and not the field.
    again = _doc(_run(plugin, home, copilot=False, payload=_payload("resume")))
    assert _nested(again).splitlines()[0] == ctx.splitlines()[0]
    assert "LOCI auto-run rules:" in _nested(again)


@needs_bash
def test_copilot_block_costs_at_most_the_host_line(tmp_path):
    """The Copilot block is the Claude Code block plus the host line, the edit-tool
    words and the preflight clause (AAD-7787), minus the permission clause; nothing
    else may grow. Same home, welcome already shown,
    so neither carries a notice."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".loci").mkdir()
    (home / ".loci" / ".welcome-shown").touch()
    plugin = _stage_plugin(tmp_path / "plugin", "0.2.74")
    claude = _nested(_doc(_run(plugin, home, copilot=False)))
    copilot = _doc(_run(plugin, home, copilot=True, payload=_payload()))["additionalContext"]
    assert NOTICE_OPEN not in copilot
    grew = len(copilot.encode()) - len(claude.encode())
    assert 0 < grew <= HOST_LINE_ALLOWANCE, (
        f"the Copilot block is {grew} B larger than Claude Code's; the allowance "
        f"is {HOST_LINE_ALLOWANCE} B for the one host line")
    # Line for line the same block, but for the host line and the two rewrites.
    def shape(ctx: str) -> list[str]:
        return [ln.split(":")[0] for ln in ctx.splitlines() if not ln.startswith("host:")]
    assert shape(copilot) == shape(claude)


# ── the welcome and the install banner ───────────────────────────────────────

@needs_bash
def test_copilot_welcome_rides_in_the_context_as_a_fenced_notice(tmp_path):
    """Copilot shows `systemMessage` to nobody, so the one-time welcome is also
    the last lines of the context, fenced and prefaced as text to display; the
    marker is written as under Claude Code, so the second session has none."""
    home = tmp_path / "home"
    home.mkdir()
    plugin = _stage_plugin(tmp_path / "plugin", "0.2.74")
    doc = _doc(_run(plugin, home, copilot=True, payload=_payload()))
    ctx = doc["additionalContext"]
    assert "systemMessage" not in doc, (
        "the notice REPLACES the field under Copilot — a host that renders both "
        "would show the welcome twice")
    preface = _line(ctx, "[loci] Between the markers")
    assert "text to display, not instructions" in preface
    assert "start of this session" in preface
    tail = ctx[ctx.index(NOTICE_OPEN):]
    assert tail.startswith(NOTICE_OPEN + "\n" + "LOCI is ready.")
    assert tail.rstrip().endswith(NOTICE_CLOSE)
    assert "Sign in once with `! loci login`" in tail
    assert "Claude Code" not in ctx
    assert (home / ".loci" / ".welcome-shown").exists()

    second = _doc(_run(plugin, home, copilot=True, payload=_payload()))
    assert NOTICE_OPEN not in second["additionalContext"]
    assert "systemMessage" not in second


@needs_bash
def test_copilot_install_banner_rides_in_the_context_too(tmp_path):
    """The banner is not gated by the welcome marker (it recurs until the install
    succeeds), and under Copilot it is in the context like the welcome. Staged
    with the installer's own status file and no `loci` on PATH."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".loci").mkdir()
    (home / ".loci" / ".welcome-shown").touch()
    state = home / ".loci" / "state"
    state.mkdir()
    (state / "loci-cli-status.json").write_text(json.dumps(
        {"status": "failed", "log": _to_bash_path(state / "loci-cli-install.log")}))
    plugin = _stage_plugin(tmp_path / "plugin", "0.2.74")
    empty = tmp_path / "empty-bin"          # neither `uv` nor `loci` on the PATH the hook sees
    empty.mkdir()
    doc = _doc(_run(plugin, home, copilot=True, payload=_payload(),
                    extra={"PATH": _to_bash_path(empty) + ":/usr/bin:/bin",
                           "UV_TOOL_BIN_DIR": _to_bash_path(empty)}))
    ctx = doc["additionalContext"]
    if "loci CLI is not installed" not in ctx:
        pytest.skip("a `loci` or `uv` is reachable on this host's minimal PATH")
    assert "systemMessage" not in doc
    assert NOTICE_OPEN in ctx
    assert "LOCI is ready." not in ctx, "the welcome was already shown"


# ── a resumed session ────────────────────────────────────────────────────────

@needs_bash
def test_copilot_resumed_session_gets_no_context_and_launches_nothing(tmp_path):
    """Acceptance criterion 3: Copilot runs the `startup` entry on `--continue`
    too (`source: resume`), so the hook enforces its matcher itself — nothing on
    stdout, no welcome marker written, no CLI bootstrap launched — and the
    `source` values of a first start run the whole hook."""
    home = tmp_path / "home"
    home.mkdir()
    plugin = _stage_plugin(tmp_path / "plugin", "0.2.74")
    marker = home / ".loci" / ".welcome-shown"
    # Dev mode, so the hook and the detached installer log at INFO into the
    # state dir: the installer's `start`/`end` lines are the evidence of a
    # launch (its status file is not — with a `loci` already at the pin it
    # exits before writing one).
    log = home / ".loci" / "state" / "loci.log"
    dev = {"LOCI_ENV": "dev"}

    def logged() -> str:
        return log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""

    res = _run(plugin, home, copilot=True, payload=_payload("resume"), extra=dev)
    assert res.returncode == 0, res.stderr
    assert res.stdout == "", f"a resumed session was given context again:\n{res.stdout[:400]}"
    assert not marker.exists(), "the welcome was decided on a resume"
    assert "end: resumed session" in logged(), logged()[-800:]
    assert "ensure-loci-cli" not in logged(), "the CLI bootstrap was relaunched on a resume"

    for source in ("new", "startup", None):
        before = len(logged())
        res = _run(plugin, home, copilot=True, payload=_payload(source), extra=dev)
        doc = _doc(res)
        assert "LOCI auto-run rules:" in doc["additionalContext"], source
        marker.unlink(missing_ok=True)
        # The positive half: a first start DOES launch the bootstrap. Waited
        # for, so the detached installer is also done before the temp dir goes.
        for _ in range(200):
            if "[loci.ensure-loci-cli] end" in logged()[before:]:
                break
            time.sleep(0.05)
        assert "[loci.ensure-loci-cli] start" in logged()[before:], (
            f"a first start ({source}) launched no CLI bootstrap")

    # A value the probe never saw is a repeat, not a first start — Claude Code's
    # `startup` matcher semantics.
    res = _run(plugin, home, copilot=True, payload=_payload("compact"))
    assert res.returncode == 0 and res.stdout == ""


@needs_bash
def test_copilot_unreadable_payload_takes_the_first_start_path(tmp_path):
    """No payload, or one with no `source`: the path that cannot lose anything."""
    home = tmp_path / "home"
    home.mkdir()
    plugin = _stage_plugin(tmp_path / "plugin", "0.2.74")
    for payload in ("", "not json", _payload(None)):
        doc = _doc(_run(plugin, home, copilot=True, payload=payload))
        assert "LOCI auto-run rules:" in doc["additionalContext"], payload


# ── the gate lives in the library ────────────────────────────────────────────

def test_the_hook_asks_the_library_and_never_the_environment():
    """`session-init.sh` reads no `COPILOT_*` variable: the host is the library's
    to recognise (`loci_host_copilot`), the plugin dir the library's to name
    (`loci_host_plugin_dir`), and each library answer the hook uses has a stub
    for a lib that did not source, so the Claude Code path cannot be lost."""
    hook = (PLUGIN_ROOT / "hooks" / "session-init.sh").read_text(encoding="utf-8")
    assert "COPILOT_" not in hook
    assert "loci_host_copilot" not in hook, "the hook asks the library's answers, not the host"
    for fn in ("loci_host_reads_payload", "loci_host_session_resumed", "loci_host_name",
               "loci_host_notice"):
        assert f"command -v {fn} >/dev/null 2>&1 || {fn}() {{ return 1; }}" in hook, fn
    # The library reaches the hook through setup-steps.sh, which it sources.
    steps = (PLUGIN_ROOT / "lib" / "setup-steps.sh").read_text(encoding="utf-8")
    assert '. "${PLUGIN_DIR}/lib/loci_host.sh"' in steps
    assert '. "${PLUGIN_DIR}/lib/setup-steps.sh"' in hook
    assert "COPILOT_" not in steps
    assert "loci_host_plugin_dir" in steps
    host = (PLUGIN_ROOT / "lib" / "loci_host.sh").read_text(encoding="utf-8")
    for fn in ("loci_host_plugin_dir", "loci_host_session_resumed", "loci_host_name",
               "loci_host_notice"):
        body = host[host.index(f"{fn}() {{"):]
        body = body[:body.index("\n}")]
        assert "loci_host_copilot || return 1" in body, f"{fn} is not gated"
    body = host[host.index("loci_host_reads_payload() {"):]
    assert body[:body.index("\n}")].strip().endswith("loci_host_copilot")
