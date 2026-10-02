"""Every hook in `hooks/hooks.json` is launched in a form both hosts run (AAD-7780).

Claude Code hands a hook's `command` string to bash on every OS. GitHub Copilot
CLI hands the same string to PowerShell on Windows and to a POSIX shell
elsewhere. `bash "$CLAUDE_PLUGIN_ROOT/hooks/x.sh"` is a valid PowerShell line in
which `$CLAUDE_PLUGIN_ROOT` is an EMPTY PowerShell variable, so under Copilot on
Windows bash was told to run `/hooks/x.sh`, all eleven hooks failed, and because
Copilot's `preToolUse` is fail-closed, every file edit and every shell command
was denied for as long as the plugin was loaded. The environment variable itself
is exported by both hosts; only the shell that reads the line differs.

The form that survives both:

    bash -c 'exec bash "$CLAUDE_PLUGIN_ROOT/hooks/x.sh"'

Single quotes are literal in PowerShell and in POSIX sh, so the string reaches
bash unexpanded and bash expands the variable itself. `exec bash <path>` rather
than running the script by its own path: no dependency on the executable bit or
the shebang (a zip install, a checkout on a mount without modes), and `$0` is
still the script's path — every hook sources its library through `${0%/*}` and
`lib/bash-compat.sh` re-execs `"$0" "$@"` on bash 3. On Unix `exec` replaces the
outer bash; on MSYS (Git Bash) the outer process stays as an exit-code relay,
about 8 ms per hook. Precondition on Windows: the PowerShell reading the line is
pwsh 7.3+ with its default (`Windows`) native-argument passing, which Copilot
requires anyway. Windows PowerShell 5.1 and `Legacy` passing drop the embedded
double quotes and word-split a path with a space in it.

`timeout` is Claude Code's key (seconds); Copilot reads `timeoutSec` (30 s when
absent). Each host ignores the other's key — `claude plugin validate` and a
headless session both accept the extra one — so every entry carries both, with
one value. `statusMessage` is Claude-only and harmless.

`setup/setup.sh` copies these commands into `<project>/.claude/settings.json`
with `$CLAUDE_PLUGIN_ROOT` expanded to the absolute plugin dir, a file Copilot's
documented hook sources include, so the form has to survive the expansion: the
absolute path lands inside the single quotes, so a quote in it would end the
literal early, and setup.sh declines such a dir by name. Its "already registered"
probe must recognise the NEW form only: it used to grep for a `capture-action.sh`
that had not existed for months (so every setup re-wrote the hooks, which is what
kept old installs current by accident), and a probe that also matched the old
launcher would leave an old settings.json PowerShell-broken forever.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
HOOKS = PLUGIN_ROOT / "hooks"

#: The one launcher form. The captured group is the script's file name.
LAUNCHER = re.compile(r"""bash -c 'exec bash "\$CLAUDE_PLUGIN_ROOT/hooks/([\w.-]+\.sh)"'""")

#: What `setup/setup.sh` turns the same line into (`$pd` is an absolute dir).
LAUNCHER_EXPANDED = re.compile(r"""bash -c 'exec bash "/[^"']+/hooks/[\w.-]+\.sh"'""")

#: The keys an entry may carry: Claude's, Copilot's timeout, and nothing either
#: host has not been seen to tolerate. A `bash`/`powershell`/`exec`/`args` key
#: is Copilot's native form and unprobed inside a Claude-format file.
ALLOWED_KEYS = {"type", "command", "timeout", "timeoutSec", "statusMessage"}

#: The launcher before AAD-7780, as setup.sh expands it: what the probe must NOT
#: take for a registered install.
OLD_EXPANDED = 'bash "/abs/plugin/hooks/session-init.sh"'

_SINGLE_QUOTED = re.compile(r"'[^']*'")
_PREFIX_ASSIGNMENT = re.compile(r"^\s*\w+=")


def _entries() -> list[tuple[str, dict]]:
    doc = json.loads((HOOKS / "hooks.json").read_text(encoding="utf-8"))
    return [(event, h) for event, groups in doc.get("hooks", {}).items()
            for g in groups for h in g.get("hooks", [])]


def _ids() -> list[str]:
    """Collection-time labels; a malformed entry gets a label, not a KeyError,
    so the tests below fail with their message instead of the collector."""
    out = []
    for event, h in _entries():
        cmd = str(h.get("command", ""))
        m = LAUNCHER.search(cmd)
        out.append(f"{event}:{m.group(1) if m else cmd[:30] or '<no command>'}")
    return out


def shell_dependent(command: str) -> str | None:
    """Why `command` would read differently under PowerShell than under bash,
    or None. Anything outside single quotes is read by the HOST's shell, so a
    `$` there is a PowerShell variable on Windows and a POSIX one elsewhere; a
    leading `VAR=value` is a POSIX-only prefix ("The term 'VAR=value' is not
    recognized" on pwsh)."""
    if _PREFIX_ASSIGNMENT.match(command):
        return "leading VAR=value assignment (PowerShell has no such prefix)"
    if command.count("'") % 2:
        return "unbalanced single quotes"
    outside = _SINGLE_QUOTED.sub("", command)
    if "$" in outside:
        return "`$` outside single quotes (an empty PowerShell variable on Windows)"
    if "`" in outside:
        return "backtick outside single quotes (PowerShell's escape character)"
    return None


# ── the form ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("event,hook", _entries(), ids=_ids())
def test_every_command_is_the_host_neutral_launcher(event, hook):
    cmd = hook.get("command")
    assert isinstance(cmd, str), f"{event}: entry has no `command` string: {hook!r}"
    m = LAUNCHER.fullmatch(cmd)
    assert m, (
        f"{event}: {cmd!r} is not the one launcher form "
        f"bash -c 'exec bash \"$CLAUDE_PLUGIN_ROOT/hooks/<x>.sh\"'. Under Copilot "
        f"on Windows the command is read by PowerShell first; see the module doc")
    assert (HOOKS / m.group(1)).is_file(), f"{event}: hooks/{m.group(1)} does not exist"


@pytest.mark.parametrize("event,hook", _entries(), ids=_ids())
def test_no_command_expands_outside_single_quotes(event, hook):
    """The property behind the form, checked on its own so that a future
    rewrite of the launcher still has to keep it."""
    cmd = str(hook.get("command", ""))
    why = shell_dependent(cmd)
    assert why is None, f"{event}: {cmd!r}: {why}"


def test_the_expansion_lint_bites():
    """Non-vacuous: the shipped form passes, the two forms Copilot on Windows
    broke on fail."""
    assert shell_dependent("""bash -c 'exec bash "$CLAUDE_PLUGIN_ROOT/hooks/x.sh"'""") is None
    assert shell_dependent('bash "$CLAUDE_PLUGIN_ROOT/hooks/x.sh"')
    assert shell_dependent('bash "${CLAUDE_PLUGIN_ROOT}/hooks/x.sh"')
    assert shell_dependent("""PROBE_OUT=/tmp/x bash -c '"$CLAUDE_PLUGIN_ROOT/hooks/x.sh"'""")
    assert shell_dependent("""bash -c 'exec bash "$CLAUDE_PLUGIN_ROOT/hooks/x.sh"' $EXTRA""")


# ── the keys ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("event,hook", _entries(), ids=_ids())
def test_every_entry_carries_both_timeout_keys_with_one_value(event, hook):
    assert isinstance(hook.get("timeout"), int) and hook["timeout"] > 0, (
        f"{event}: no `timeout` (Claude Code's key, seconds)")
    assert hook.get("timeoutSec") == hook["timeout"], (
        f"{event}: `timeoutSec` (Copilot's key) must equal `timeout`; Copilot "
        f"defaults to 30 s when it is absent, which is less than session-init "
        f"needs and more than the contract guard is budgeted for")


@pytest.mark.parametrize("event,hook", _entries(), ids=_ids())
def test_every_entry_uses_only_keys_both_hosts_tolerate(event, hook):
    extra = set(hook) - ALLOWED_KEYS
    assert not extra, (
        f"{event}: unprobed key(s) {sorted(extra)}. A key one host has not been "
        f"seen to accept inside a Claude-format hooks.json is a probe away from "
        f"a plugin that fails to load")
    assert hook.get("type") == "command"


def test_hooks_json_has_twelve_entries():
    """The count the Epic's evidence was recorded against (`hookCount=11`),
    plus the `SubagentStart` entry AAD-7788 added."""
    assert len(_entries()) == 12


# ── setup.sh's copy of the same lines ───────────────────────────────────────

def _setup_text() -> str:
    return (PLUGIN_ROOT / "setup" / "setup.sh").read_text(encoding="utf-8")


_JQ_PROGRAM = re.compile(
    r'--arg pd "\$\{PLUGIN_DIR\}" \'(.*?)\' "\$\{PLUGIN_DIR\}/hooks/hooks.json"', re.S)


def _expanded_by_setup() -> list[str]:
    """The commands as setup.sh writes them into `.claude/settings.json`: its
    own jq program run on the shipped hooks.json when a jq is on PATH, else the
    same two substitutions done here (the program is two `gsub`s; the fallback
    pins that they are still there)."""
    text = _setup_text()
    m = _JQ_PROGRAM.search(text)
    assert m, "setup.sh no longer rewrites hooks.json through a jq program"
    jq = shutil.which("jq")
    if jq:
        out = subprocess.run([jq, "--arg", "pd", "/abs/plugin", m.group(1),
                              str(HOOKS / "hooks.json")],
                             capture_output=True, text=True, check=True).stdout
        doc = json.loads(out)
        return [h["command"] for groups in doc["hooks"].values()
                for g in groups for h in g["hooks"]]
    assert 'gsub("\\\\$\\\\{CLAUDE_PLUGIN_ROOT\\\\}"; $pd)' in m.group(1)
    assert 'gsub("\\\\$CLAUDE_PLUGIN_ROOT"; $pd)' in m.group(1)
    return [str(h.get("command", ""))
            .replace("${CLAUDE_PLUGIN_ROOT}", "/abs/plugin")
            .replace("$CLAUDE_PLUGIN_ROOT", "/abs/plugin")
            for _, h in _entries()]


def test_setup_expansion_keeps_the_launcher_form():
    expanded = _expanded_by_setup()
    assert len(expanded) == len(_entries())
    for cmd in expanded:
        assert LAUNCHER_EXPANDED.fullmatch(cmd), cmd
        assert shell_dependent(cmd) is None, cmd


def _registered_probe() -> str:
    m = re.search(r'grep -q "([^"]+)" "\$SETTINGS_FILE"', _setup_text())
    assert m, "setup.sh no longer probes settings.json for a registered hook"
    return m.group(1)


def test_setup_registered_probe_recognises_the_shipped_form():
    probe = _registered_probe()
    expanded = _expanded_by_setup()
    assert any(re.search(probe, cmd) for cmd in expanded), (
        f"setup.sh decides 'already registered' by grepping for {probe!r}, which "
        f"no command it writes matches — so it never matches and every setup "
        f"re-writes the hooks")


def test_setup_registered_probe_rejects_the_old_form():
    """An install registered before AAD-7780 carries `bash "/abs/hooks/x.sh"`,
    which PowerShell breaks on. Reported as registered, it would never be
    upgraded."""
    probe = _registered_probe()
    assert not re.search(probe, OLD_EXPANDED), (
        f"setup.sh's probe {probe!r} also matches the pre-AAD-7780 launcher "
        f"{OLD_EXPANDED!r}, so an old settings.json is never rewritten")


def test_setup_declines_a_plugin_dir_with_a_single_quote():
    assert "*\"'\"*" in _setup_text(), (
        "setup.sh writes the plugin dir inside the launcher's single quotes; a "
        "dir containing one must be declined by name, not written broken")
