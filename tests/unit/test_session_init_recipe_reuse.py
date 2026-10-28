"""AAD-7843 — session start gives a checkout with no recipe the one the user approved.

The recipe is machine-local and gitignored, so a new checkout of a repository the
user already set up — a git worktree, where every Copilot session runs — has none,
and LOCI answered `not_initialized` with post-edit silent. `loci init` now saves
the approved recipe in state (`recipe-approved-<key>.json`), and session-init asks
`loci hook recipe-reuse` to install it here. WHAT is reused is the CLI's to decide
(loci-cli `recipe_reuse.py`); what is pinned here is WHEN the hook asks, since every
ask is a CLI start (~0.4 s on Windows): only with `loci` on PATH and an approved
copy of THIS checkout's repository whose LOCI root exists here with no recipe at
or above it — and in the session's folder, which under Copilot is the payload's
`cwd`, not where the hook was started.
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

from tests.fixtures.copilot_payloads import SESSION_START, SESSION_START_RESUME, at

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent

# Every test spawns the hook, so all run under both hosts (AAD-7790).
pytestmark = pytest.mark.usefixtures("host")

CALL = "hook recipe-reuse"


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
    s = Path(p).as_posix()
    m = re.match(r"^([A-Za-z]):/(.*)$", s)
    if m:
        return f"/{m.group(1).lower()}/{m.group(2)}"
    return s


def _pinned() -> str:
    m = re.search(r'^LOCI_CLI_VERSION="([0-9.]+)"',
                  (PLUGIN_ROOT / "lib" / "setup-steps.sh").read_text(encoding="utf-8"),
                  re.M)
    assert m, "lib/setup-steps.sh no longer declares LOCI_CLI_VERSION"
    return m.group(1)


def _stub(bin_dir: Path, calls: Path, rc: int) -> None:
    """A `loci` answering the pinned `--version`, and recording each `hook` call
    with where it ran: `in-project` when a sentinel the test put in the session
    folder is in its cwd. Asked of the stub, not computed: Git Bash spells a
    folder under %TEMP% as `/tmp/…`, so comparing paths would test the mount table."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    # Shaped like the CLI's own envelope on success: indent=2, and the skip reason
    # past the first 400 bytes until the indentation is squeezed out.
    pad = "\n" + " " * 40
    out = ((f'{{{pad}"ok": true,{pad}"data": {{{pad}"checkout": "{"x" * 120}",'
            f'{pad}"repository": "{"y" * 120}",{pad}"reused": [],{pad}"skipped": '
            f'[{{{pad}"reason": "walks_out_of_root"}}]}}}}').replace("\n", "\\n")
           if rc == 0 else '{"ok":false,"error":{"message":"boom"}}')
    # A failure prints several CRLF lines first, as argparse's usage does for a
    # CLI that lacks the verb.
    noise = "" if rc == 0 else "    printf 'usage: loci hook\\r\\nloci: error: boom\\r\\n'\n"
    shim = bin_dir / "loci"
    shim.write_text(
        "#!/bin/bash\n"
        'case "$1" in\n'
        f'  --version) echo "loci {_pinned()}" ;;\n'
        "  hook)\n"
        '    where=$(pwd); [ -e here.sentinel ] && where=in-project\n'
        f'    printf "%s|%s\\n" "$*" "$where" >> "{_to_bash_path(calls)}"\n'
        f"{noise}"
        f"    printf '%b\\n' '{out}'\n"
        f"    exit {rc} ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
        encoding="utf-8", newline="\n")
    shim.chmod(0o755)


# ── the layouts, as git and the CLI write them ──────────────────────────────

def _clone(path: Path) -> Path:
    (path / ".git").mkdir(parents=True)
    (path / "here.sentinel").write_text("", encoding="utf-8")
    return path


def _worktree(repo: Path, path: Path, name: str = "wt") -> Path:
    """`git worktree add`'s layout: a `.git` FILE pointing into
    `<repo>/.git/worktrees/<name>`, which holds `commondir` (`../..`) and git's
    `gitdir` back-pointer."""
    gitdir = repo / ".git" / "worktrees" / name
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    (gitdir / "HEAD").write_text("ref: refs/heads/topic\n", encoding="utf-8")
    path.mkdir(parents=True)
    (gitdir / "gitdir").write_text((path / ".git").as_posix() + "\n", encoding="utf-8")
    (path / ".git").write_text(f"gitdir: {gitdir.as_posix()}\n", encoding="utf-8")
    (path / "here.sentinel").write_text("", encoding="utf-8")
    return path


def _copy(repository: Path, rel: str = ".", source: Path | None = None) -> dict:
    """An approved copy, shaped as loci-cli's `recipe_reuse.record` writes it —
    `json.dumps(indent=2, ensure_ascii=False)` — which is what the hook reads with
    no JSON parser (`test_the_cli_writes_the_copy_the_hook_reads` pins the CLI)."""
    source = source if source is not None else repository.parent / "elsewhere"
    return {"version": 1, "key": "abc123def456", "recipe_hash": "sha256:" + "0" * 64,
            "recipe": "version: 1\ntarget: armv7e-m\n", "confirmed_by_user": True,
            "initialized_at": "2026-10-14T09:00:00Z",
            "source_root": source.resolve().as_posix(), "source_key": "0123456789ab",
            "source_top": source.resolve().as_posix(),
            "repository": repository.resolve().as_posix(), "rel": rel,
            "saved_at": "2026-10-14T09:00:00+00:00"}


class Run:
    def __init__(self, calls: list[str], ctx: str, log: str):
        self.calls = calls
        self.ctx = ctx
        self.log = log


def _session(tmp_path: Path, host, folder: Path, copies: list | None = None, *,
             loci: bool = True, rc: int = 0, fail_fast: bool = False,
             start: Path | None = None, payload: dict | None = None,
             extra: dict | None = None) -> Run:
    """One SessionStart in ``folder``, with ``copies`` (dicts, or raw text) in the
    state directory. The hook is started in ``start`` (default the folder) under
    Claude Code and in the plugin root under Copilot; the payload's `cwd` is
    ``folder`` unless ``payload`` is given."""
    home = tmp_path / "home"
    state = home / ".loci" / "state"
    state.mkdir(parents=True, exist_ok=True)
    for i, copy in enumerate(copies or []):
        text = copy if isinstance(copy, str) else json.dumps(copy, indent=2,
                                                             ensure_ascii=False)
        (state / f"recipe-approved-{i:012d}.json").write_text(text, encoding="utf-8")
    calls = tmp_path / "calls.log"
    calls.unlink(missing_ok=True)
    bin_dir = tmp_path / "bin"
    if loci:
        _stub(bin_dir, calls, rc)
    # Built from nothing, as test_fail_fast does: the developer's own `loci` and
    # LOCI_* settings must not decide these tests. `_LOCI_BOOTSTRAP` keeps the
    # detached installer from installing a real CLI into this home.
    env = {
        "PATH": f"{_to_bash_path(bin_dir)}:/usr/bin:/bin:/usr/local/bin",
        "HOME": _to_bash_path(home),
        "LOCI_STATE_DIR": _to_bash_path(state),
        "_LOCI_BOOTSTRAP": "1",
        "UV_TOOL_BIN_DIR": _to_bash_path(tmp_path / "uvbin"),
        "LOCI_DEV_CLI_PATH": _to_bash_path(tmp_path / "no-dev-checkout"),
        **host.env(project=str(folder)),
    }
    if fail_fast:
        env["LOCI_FAIL_FAST"] = "1"
    if extra:
        env.update(extra)
    if payload is None:
        if host.copilot:
            payload = at(SESSION_START, str(folder))
        else:
            payload = {"hook_event_name": "SessionStart", "session_id": "s-7843",
                       "cwd": str(folder), "source": "startup"}
    proc = subprocess.run(
        [_find_bash(), _to_bash_path(PLUGIN_ROOT / "hooks" / "session-init.sh")],
        env=env, cwd=host.cwd(start or folder), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=90, input=json.dumps(payload))
    assert proc.returncode == 0, proc.stderr
    made = calls.read_text(encoding="utf-8").splitlines() if calls.is_file() else []
    reply = json.loads(proc.stdout) if proc.stdout.strip() else None
    log = state / "loci.log"
    return Run(made, host.context(reply),
               log.read_text(encoding="utf-8", errors="replace") if log.is_file() else "")


ASKED = [f"{CALL}|in-project"]


# ── when the hook asks ──────────────────────────────────────────────────────

@needs_bash
def test_a_new_worktree_of_a_set_up_repository_is_offered_its_recipe(tmp_path, host):
    repo = _clone(tmp_path / "tonelock")
    wt = _worktree(repo, tmp_path / "copilot-worktrees" / "aurora")
    assert _session(tmp_path, host, wt, [_copy(repo)]).calls == ASKED


@needs_bash
def test_the_main_checkout_and_its_subfolders_are_offered_it_too(tmp_path, host):
    """Any checkout of the repository reuses: the main one included, from any
    folder in it (the CLI finds the LOCI roots)."""
    repo = _clone(tmp_path / "repo")
    sub = repo / "firmware" / "app"
    sub.mkdir(parents=True)
    (sub / "here.sentinel").write_text("", encoding="utf-8")
    wt = repo.parent / "wt-source"
    assert _session(tmp_path, host, repo, [_copy(repo, source=wt)]).calls == ASKED
    assert _session(tmp_path, host, sub, [_copy(repo, source=wt)]).calls == ASKED


@needs_bash
def test_a_monorepo_root_below_the_session_folder_is_offered(tmp_path, host):
    repo = _clone(tmp_path / "mono")
    (repo / "fw").mkdir()
    wt = _worktree(repo, tmp_path / "wt")
    (wt / "fw").mkdir()
    assert _session(tmp_path, host, wt, [_copy(repo, "fw")]).calls == ASKED


@needs_bash
def test_the_session_folder_is_the_payloads_not_where_the_hook_started(tmp_path, host):
    """Copilot starts every hook in the plugin root (AAD-7810); Claude Code's
    payload names the folder too. Started elsewhere, the call still runs there."""
    repo = _clone(tmp_path / "repo")
    wt = _worktree(repo, tmp_path / "wt")
    elsewhere = _clone(tmp_path / "elsewhere")
    (elsewhere / "here.sentinel").unlink()
    run = _session(tmp_path, host, wt, [_copy(repo), _copy(elsewhere)], start=elsewhere)
    assert run.calls == ASKED, run.calls


@needs_bash
def test_with_no_cwd_in_the_payload_the_project_dir_comes_before_pwd(tmp_path, host):
    repo = _clone(tmp_path / "repo")
    wt = _worktree(repo, tmp_path / "wt")
    elsewhere = _clone(tmp_path / "elsewhere")
    (elsewhere / "here.sentinel").unlink()
    payload = (at(SESSION_START, str(wt)) if host.copilot
               else {"hook_event_name": "SessionStart", "session_id": "s"})
    payload.pop("cwd", None)
    run = _session(tmp_path, host, wt, [_copy(repo), _copy(elsewhere)], payload=payload,
                   start=elsewhere, extra={"CLAUDE_PROJECT_DIR": str(wt)})
    assert run.calls == ASKED, run.calls


@needs_bash
def test_a_copy_the_hook_cannot_read_is_skipped_not_asked_about(tmp_path, host):
    """Asking about it would send every checkout on the machine to the CLI, every
    session, for good: the CLI never writes one, and clears one it meets."""
    repo = _clone(tmp_path / "repo")
    wt = _worktree(repo, tmp_path / "wt")
    truncated = json.dumps(_copy(repo), indent=2)[:40]
    control = json.dumps(_copy(repo), indent=2, ensure_ascii=True).replace(
        '"repository": "', '"repository": "\\u0001')
    assert _session(tmp_path, host, wt, [truncated, control]).calls == []


@needs_bash
@pytest.mark.skipif(sys.platform == "win32", reason="a quote or a backslash in a "
                    "folder name is POSIX's")
def test_a_repository_path_with_a_quote_or_a_backslash_is_decoded(tmp_path, host):
    repo = _clone(tmp_path / 're"po\\x')
    wt = _worktree(repo, tmp_path / "wt")
    assert _session(tmp_path, host, wt, [_copy(repo)]).calls == ASKED


# ── when it does not: each costs the session no CLI start ────────────────────

@needs_bash
def test_no_approved_copy_on_the_machine_no_call(tmp_path, host):
    wt = _worktree(_clone(tmp_path / "repo"), tmp_path / "wt")
    assert _session(tmp_path, host, wt).calls == []


@needs_bash
def test_a_repository_nobody_set_up_is_not_offered_anothers(tmp_path, host):
    other = _clone(tmp_path / "other")
    repo = _clone(tmp_path / "repo")
    wt = _worktree(repo, tmp_path / "wt")
    assert _session(tmp_path, host, wt, [_copy(other)]).calls == []
    assert _session(tmp_path, host, repo, [_copy(other)]).calls == []


@needs_bash
def test_roots_that_all_have_a_recipe_here_are_not_offered_again(tmp_path, host):
    """The monorepo session at the top, every time it starts: its roots are set up."""
    repo = _clone(tmp_path / "mono")
    for name in ("a", "b"):
        (repo / name / ".loci").mkdir(parents=True)
        (repo / name / ".loci" / "build.yaml").write_text("version: 1\n", encoding="utf-8")
    copies = [_copy(repo, "a", source=repo / "a"), _copy(repo, "b", source=repo / "b")]
    assert _session(tmp_path, host, repo, copies).calls == []


@needs_bash
def test_a_root_under_the_checkouts_own_recipe_is_not_offered(tmp_path, host):
    repo = _clone(tmp_path / "mono")
    (repo / "fw").mkdir()
    wt = _worktree(repo, tmp_path / "wt")
    (wt / "fw").mkdir()
    (wt / ".loci").mkdir()
    (wt / ".loci" / "build.yaml").write_text("version: 1\n", encoding="utf-8")
    assert _session(tmp_path, host, wt, [_copy(repo, "fw")]).calls == []


@needs_bash
def test_a_root_this_checkout_lacks_is_not_offered(tmp_path, host):
    repo = _clone(tmp_path / "mono")
    (repo / "fw").mkdir()
    wt = _worktree(repo, tmp_path / "wt")
    assert _session(tmp_path, host, wt, [_copy(repo, "fw")]).calls == []


@needs_bash
def test_the_folder_it_was_approved_in_is_not_offered_its_own(tmp_path, host):
    """Its lost recipe (`git clean -xdf`) is `/loci:init`'s to restore."""
    repo = _clone(tmp_path / "repo")
    assert _session(tmp_path, host, repo, [_copy(repo, source=repo)]).calls == []


@needs_bash
def test_a_folder_outside_any_checkout_or_in_a_submodule_is_not_offered(tmp_path, host):
    repo = _clone(tmp_path / "repo")
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _session(tmp_path, host, plain, [_copy(repo)]).calls == []
    # A submodule's `.git` points into `<super>/.git/modules/…`, which has no
    # `commondir`: no main checkout, and the CLI would answer nothing.
    modgit = repo / ".git" / "modules" / "sub"
    modgit.mkdir(parents=True)
    sub = repo / "sub"
    sub.mkdir()
    (sub / ".git").write_text(f"gitdir: {modgit.as_posix()}\n", encoding="utf-8")
    assert _session(tmp_path, host, sub, [_copy(repo)]).calls == []
    # A worktree of a bare repository: its `commondir` is no `.git`, so there is
    # no main checkout either — even one a copy happens to name.
    bare = tmp_path / "repo.git"
    (bare / "worktrees" / "w").mkdir(parents=True)
    (bare / "worktrees" / "w" / "commondir").write_text("../..\n", encoding="utf-8")
    bw = tmp_path / "bare-wt"
    bw.mkdir()
    (bw / ".git").write_text(f"gitdir: {(bare / 'worktrees' / 'w').as_posix()}\n",
                             encoding="utf-8")
    (bw / "here.sentinel").write_text("", encoding="utf-8")
    assert _session(tmp_path, host, bw, [_copy(tmp_path)]).calls == []


@needs_bash
def test_without_loci_on_path_the_session_starts_as_before(tmp_path, host):
    repo = _clone(tmp_path / "repo")
    wt = _worktree(repo, tmp_path / "wt")
    run = _session(tmp_path, host, wt, [_copy(repo)], loci=False)
    assert run.calls == [] and run.ctx
    # Not even attempted: fast-fail would report a missing `loci` (127) otherwise.
    run = _session(tmp_path / "ff", host, wt, [_copy(repo)], loci=False, fail_fast=True)
    assert "recipe-reuse" not in run.ctx


@needs_bash
def test_a_resumed_copilot_session_does_not_ask_again(tmp_path, host):
    if not host.copilot:
        pytest.skip("Claude Code's `startup` matcher never runs the hook on resume")
    repo = _clone(tmp_path / "repo")
    wt = _worktree(repo, tmp_path / "wt")
    run = _session(tmp_path, host, wt, [_copy(repo)],
                   payload=at(SESSION_START_RESUME, str(wt)))
    assert run.calls == []


# ── what the session sees ───────────────────────────────────────────────────

@needs_bash
def test_the_block_is_the_same_whether_or_not_a_recipe_was_reused(tmp_path, host):
    """Reuse is silent: a recipe not reused is today's behaviour, and one reused
    is found by the edit hooks like any other."""
    repo = _clone(tmp_path / "repo")
    wt = _worktree(repo, tmp_path / "wt")
    asked = _session(tmp_path / "a", host, wt, [_copy(repo)])
    quiet = _session(tmp_path / "b", host, wt)
    assert asked.calls and not quiet.calls
    assert asked.ctx == quiet.ctx


@needs_bash
def test_a_failed_call_is_silent_unless_fail_fast_is_on(tmp_path, host):
    repo = _clone(tmp_path / "repo")
    wt = _worktree(repo, tmp_path / "wt")
    quiet = _session(tmp_path / "quiet", host, wt)
    off = _session(tmp_path / "off", host, wt, [_copy(repo)], rc=1)
    assert off.calls and off.ctx == quiet.ctx

    on = _session(tmp_path / "on", host, wt, [_copy(repo)], rc=1, fail_fast=True)
    assert "`loci hook recipe-reuse` failed with exit 1" in on.ctx, on.ctx
    assert '"message":"boom"' in on.ctx, "the output is reported verbatim"
    clean = _session(tmp_path / "clean", host, wt, [_copy(repo)], fail_fast=True)
    assert "loci hook recipe-reuse" not in clean.ctx


@needs_bash
def test_the_call_is_one_log_line_whatever_the_cli_printed(tmp_path, host):
    repo = _clone(tmp_path / "repo")
    wt = _worktree(repo, tmp_path / "wt")
    run = _session(tmp_path, host, wt, [_copy(repo)], rc=1, extra={"LOCI_HOOK_LOG": "1"})
    lines = [ln for ln in run.log.splitlines() if "recipe-reuse in" in ln]
    assert len(lines) == 1 and "rc=1" in lines[0], run.log
    # The CLI's later lines ride on that one, not on lines of their own.
    assert "loci: error: boom" in lines[0] and '"message":"boom"' in lines[0], run.log


@needs_bash
def test_the_log_line_keeps_the_skip_reason_past_the_indentation(tmp_path, host):
    repo = _clone(tmp_path / "repo")
    wt = _worktree(repo, tmp_path / "wt")
    run = _session(tmp_path, host, wt, [_copy(repo)], extra={"LOCI_HOOK_LOG": "1"})
    lines = [ln for ln in run.log.splitlines() if "recipe-reuse in" in ln]
    assert len(lines) == 1 and "walks_out_of_root" in lines[0], run.log


# ── the CLI writes what the hook reads ──────────────────────────────────────

def _cli_src() -> Path | None:
    roots = [os.environ[v] for v in ("LOCI_DEV_CLI_PATH", "LOCI_CLI_SRC")
             if os.environ.get(v)] + [str(PLUGIN_ROOT.parent / "loci-cli")]
    for raw in roots:
        for candidate in (Path(raw) / "src" / "loci" / "cli", Path(raw)):
            if (candidate / "recipe_reuse.py").is_file():
                return candidate
    return None


@pytest.mark.skipif(_cli_src() is None, reason="no loci-cli checkout with recipe_reuse.py")
def test_the_cli_writes_the_copy_the_hook_reads():
    """The hook matches `"repository": "…"`, `"rel": "…"` and `"source_root": "…"`
    in text. That holds only while the CLI writes those keys unescaped, with
    `json.dumps`'s `": "` separator (indent=2)."""
    src = _cli_src()
    reuse = (src / "recipe_reuse.py").read_text(encoding="utf-8")
    for key in ('"repository": posix(main)', '"rel": posix(', '"source_root": posix('):
        assert key in reuse, key
    assert "ensure_ascii=False" in reuse
    recipe = (src / "recipe.py").read_text(encoding="utf-8")
    assert re.search(r"json\.dumps\(payload, indent=2, ensure_ascii=ensure_ascii\)", recipe)
