#!/usr/bin/env bash
# PreToolUse edge adapter (Claude Code → loci): gate on the file's extension,
# then hand the payload to `loci hook edit-scan` (static call-graph pre-scan of
# the incoming code) and `loci build snapshot` (freeze the source's .o for
# post-edit diffing). No analysis here, and no payload parsing beyond the one
# field the gate needs. Advisory: always exits 0, never blocks.
# The bash this runs under is decided first, while the payload is still on
# stdin: on bash 3 (stock macOS) this re-executes under a newer bash when one
# is installed, else sets `_LOCI_BASH_LEGACY=1` (AAD-7771; lib/bash-compat.sh).
. "${0%/*}/../lib/bash-compat.sh" 2>/dev/null || :

set -u
export PYTHONIOENCODING=utf-8
# ${HOME:-} — a bare $HOME under `set -u` exits 1 where HOME is unset, and a
# non-zero exit from an edge hook reads to the model as a tool failure.
export PATH="${HOME:-}/.local/bin:$PATH"

# The shared logger and the forkless JSON reader. Claude Code records no
# PreToolUse hook anywhere QA can read, so these lines are the only trace this
# hook leaves. Sourced with parameter expansion rather than `dirname` (no fork),
# and stubbed when a library is missing so no call site below has to test for
# it.
case "$0" in
    */*)
        . "${0%/*}/../lib/loci_log.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_json.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_failfast.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_host.sh" 2>/dev/null || true
        ;;
esac
command -v loci_log >/dev/null 2>&1 \
    || { loci_log() { :; }; loci_log_session_from_payload() { :; }; }
command -v loci_fail_fast >/dev/null 2>&1 || loci_fail_fast() { return 1; }
command -v loci_host_adapt >/dev/null 2>&1 || loci_host_adapt() { return 1; }
# `lib/loci_json.sh` is not optional the way the logger is: it reads the field
# this hook gates on and it writes the JSON this hook prints. It ships in the
# plugin, so its absence is a broken install rather than a missing host tool —
# said in the log, and never a wrong answer.
command -v loci_json_load >/dev/null 2>&1 \
    || { loci_log ERROR pre-edit "lib/loci_json.sh did not source — hook disabled"; exit 0; }

payload=$(cat)
# Copilot sends no `prompt_id`: the host adapter injects the turn id it recorded
# at UserPromptSubmit, so the `--turn` below and the payload `loci hook
# edit-scan` reads carry it as they do under Claude Code (lib/loci_host.sh,
# AAD-7781). Outside Copilot the line touches nothing.
loci_host_adapt "$payload" && payload="$LOCI_HOST_PAYLOAD"

# ONE field is read here, and only one: the path the extension gate below
# decides on. Everything else this hook needs comes back from
# `loci hook edit-scan`, which parses the payload properly — bash's string
# operators are quadratic in the offset of the match, so a payload carrying a
# whole file cannot be read in shell inside this hook's 8 s budget (measured:
# 4.9 s for one field read over 160 KB).
#
# `file_path` is inside the bounded prefix `loci_json_load` parses: the harness
# writes it ahead of the edit's content. `fp` empty on a payload that was
# TRUNCATED is therefore not "no path" but "not in the prefix", and the gate
# below lets that through to the CLI rather than skipping the measurement — an
# unmeasurable edit costs one no-op `loci` call, a skipped one costs the turn its
# baseline in silence.
fp=""
turn=""
root=""
# What the extension gate below decides on. `fp` when there is one; a stand-in
# that PASSES the gate when there cannot be one, i.e. a truncated prefix — the
# CLI is then the only thing that can still read the payload.
loci_json_load "$payload"
fp=$(loci_json_get file_path)
# `prompt_id` and `cwd` when the prefix holds them, which is the ordinary case;
# `loci hook edit-scan` reports both back and is the fallback below.
turn=$(loci_json_get prompt_id)
root=$(loci_json_get cwd)
_pe_gate="${fp:-${_LOCI_JSON_TRUNCATED:+/unknown.c}}"

# The session id comes off the payload just read — stdin is consumed by now and
# nothing may read it again.
loci_log_session_from_payload "$payload"
loci_log INFO pre-edit "start: PreToolUse pre-edit (file=$fp)"
_pe_snap="not run"
_pe_scan="not run"
trap 'loci_log INFO pre-edit "end: snapshot=$_pe_snap scan=$_pe_scan (hook rc=$?)"' EXIT

# Only C/C++/Rust/Go sources — skips plan files, markdown, configs, etc.
#
# This list must not be NARROWER than the CLI's `_SNAPSHOT_SOURCE_EXTS`, and that is
# a correctness rule rather than tidiness. The CLI now captures a pre-edit copy of
# every extension it lists, and rebuilds a header edit's baseline out of those
# copies. A file this hook filters out is one that never gets captured — and the
# reconstruction then reads it at its CURRENT, edited content while every other
# check passes, producing an object that mixes pre-edit and post-edit sources and is
# reported as a clean Before. Widening costs one no-op `loci` call per edit of a
# file that turns out to have no object; narrowing costs a silent wrong number.
case "$_pe_gate" in
    *.c|*.cc|*.cpp|*.cxx|*.c++|*.rs) ;;
    # Go. Its unit is the LINKED BINARY rather than a translation unit, so what
    # the CLI freezes for a `.go` edit is the recipe's `artifacts.elf` — but the
    # gate is the same one, and being narrower here means a Go project's baseline
    # is never captured at all.
    *.go) ;;
    *.h|*.hpp|*.hxx|*.h++|*.hh|*.inc|*.ipp|*.tcc|*.inl|*.tpp|*.def) ;;
    # `.S` is preprocessed (so it reads headers) and `.s` is not, but a
    # case-insensitive filesystem cannot tell the two names apart — so both, and the
    # CLI decides.
    *.S|*.s) ;;
    *) _pe_snap="skipped (extension not measurable)"; exit 0 ;;
esac
# Skip plan/settings files that carry a source-ish extension. Kept in step with
# `post-edit-hook.sh`, which has had this since phase 04 — the two hooks are a pair
# and a file one of them acts on while the other ignores it is a state neither was
# designed for. The concrete cost of the gap was small (a snapshot and a pre-scan
# over a file with no object), but the shape is the one 06d found five copies of:
# two filters that are meant to agree and are separately maintained.
# GitHub Copilot CLI's plan mode writes `plan.md` and any other planning
# artifact into `~/.copilot/session-state/<session>/`, as a Windows path on
# Windows (AAD-7787): a source-named draft there is a plan file, not an edit.
case "$fp" in
    */.claude/plans/*|*/.claude/settings*)
        _pe_snap="skipped (plan/settings file)"; exit 0 ;;
    */.copilot/session-state/*|*\\.copilot\\session-state\\*)
        _pe_snap="skipped (plan/settings file)"; exit 0 ;;
esac

# Install-on-miss: loci is needed now but absent — kick a background install
# (self-locking) and skip this run. Covers plugins installed mid-session.
if ! command -v loci >/dev/null 2>&1; then
    if loci_fail_fast; then
        _pe_snap="halted (loci absent)"
        loci_fail_fast_absent pre-edit PreToolUse "loci hook edit-scan"
        exit 0
    fi
    _pe_snap="skipped (loci absent — install kicked)"
    nohup bash "$(dirname "$0")/ensure-loci-cli.sh" </dev/null >/dev/null 2>&1 &
    exit 0
fi

# ── the static pre-scan, and the payload facts it reports back ──────────────
#
# The payload goes over unread. WHAT the incoming code is — a Write's `content`
# is a whole translation unit, an Edit's `new_string` is the replacement text
# alone — decides which of `scan`'s rules apply, and this hook used to answer
# that with two `jq` reads of its own. It is one question about one payload, so
# it is answered once, in the CLI, and both edge hooks ask the same verb.
#
# It runs BEFORE the snapshot, which it did not use to: the verb reports the
# payload's `prompt_id` and `cwd` back, and those are the snapshot's `--turn` and
# `--project-root`. A CLI too broken to answer takes the snapshot's own call down
# with it anyway, so there is nothing to lose by asking first.
notices=""
_note() {
    notices="${notices}${notices:+$'\n'}LOCI: $1"
    declare -F loci_log >/dev/null 2>&1 && loci_log WARN pre-edit "$1" || true
}

report=""
_pe_governed=""
_pe_project=""
envelope=$(printf '%s' "$payload" | loci hook edit-scan 2>&1)
rc=$?
_pe_scan="rc=$rc"
if [ "$rc" -ne 0 ]; then
    # Fast-fail: the scan failed, so the snapshot below is not attempted and no
    # notice softens it into an advisory. The edit still goes ahead — a
    # PreToolUse hook that blocked it would be exiting non-zero, which this hook
    # never does.
    if loci_fail_fast; then
        _pe_scan="halted (rc=$rc)"
        loci_fail_fast_emit pre-edit PreToolUse "loci hook edit-scan" "$rc" "$envelope"
        exit 0
    fi
    loci_json_load "$envelope"
    msg=$(loci_json_get message)
    [ -n "$msg" ] || msg=$(printf '%s' "$envelope" | tail -1)
    _note "pre-edit scan of $fp failed (exit $rc): ${msg:-no output}. No static pre-scan for this edit."
else
    loci_json_load "$envelope"
    report=$(loci_json_get report)
    [ -n "$fp" ]   || fp=$(loci_json_get file_path)
    [ -n "$turn" ] || turn=$(loci_json_get turn)
    [ -n "$root" ] || root=$(loci_json_get cwd)
    _pe_governed=$(loci_json_get governed)
    _pe_project=$(loci_json_get project_root)
fi

# Where the project starts, as the payload states it — not as this process's CWD
# happens to be, and not as the git top level would say.
#
# `build snapshot` resolves its root from `--project-root` or `Path.cwd()`, and
# passing neither is how the capture and every reader of it came to be guessing
# separately: `hooks/turn-clean.sh` had to reproduce the writer's guess to sweep the
# right tree, and `build compile --baseline` refuses when the two disagree (it fails
# closed, but it fails). A session running in a subdirectory of a repo is all it
# takes. Stating it once, here, is what lets both of those stop guessing.
#
# The payload's NATIVE spelling, passed through unchanged: a `/c/...` conversion
# reaches Python on Windows as a rooted path on the CURRENT DRIVE, i.e. `C:\c\...`.
# Same rule, same reason, as `turn-clean.sh`.
# No recipe governs the file: there is no LOCI object to freeze.
if [ "$_pe_governed" = false ]; then
    _pe_snap="skipped (no recipe governs $fp)"
    ctx="$report"
    [ -n "$notices" ] && ctx="${ctx}${ctx:+$'\n\n'}${notices}"
    [ -n "$ctx" ] && loci_json_hook_output PreToolUse "$ctx"
    exit 0
fi
[ "$_pe_governed" = true ] && [ -n "$_pe_project" ] && root="$_pe_project"
[ -n "$root" ] || root="${CLAUDE_PROJECT_DIR:-}"

# `--turn` makes the capture first-write-wins for the turn. Without it the baseline
# is destroyed by the turn's second edit: this hook runs on every edit and the
# post-edit skill recompiles the .o in between, so edit 2 froze edit 1's output and
# called it "pre-edit". `prompt_id` is on every hook payload, identical for every
# event within one user turn, distinct across turns, and — verified — still the
# PARENT turn's id for edits made inside a subagent, which is exactly what a
# per-turn baseline wants. Under Copilot, where a subagent is a session of its
# own, the host adapter resolves the same parent id for it (AAD-7788).

# `--flag=value`, joined: a `prompt_id` or a `file_path` beginning with `-` is
# otherwise read by argparse as the next option. `--source` keeps the separate form
# it has always had.
snap=( --source "$fp" )
[ -n "$root" ]   && snap+=( "--project-root=$root" )
[ -n "$turn" ]   && snap+=( "--turn=$turn" )

# Freeze the current .o → .o.prev (no-op unless a LOCI-built .o + meta exist).
#
# No version-skew retry. The plugin and the CLI ship in lockstep and session-init
# already reports a stale, shadowed or failed install, so a usage error here is a
# broken hook/CLI contract — and dropping `--turn` to survive one cost the turn its
# whole first-write-wins baseline, silently, for the rest of the turn.
#
# stderr folded into stdout: argparse writes usage errors there, while a LociError
# renders as a JSON envelope on stdout — so one capture has to hold either.
snap_out=$(loci build snapshot "${snap[@]}" 2>&1)
rc=$?
_pe_snap="rc=$rc"
if [ "$rc" -ne 0 ] && loci_fail_fast; then
    _pe_snap="halted (rc=$rc)"
    loci_fail_fast_emit pre-edit PreToolUse "loci build snapshot" "$rc" "$snap_out"
    exit 0
fi
loci_json_load "$snap_out"
if [ "$rc" -ne 0 ]; then
    msg=$(loci_json_get message)
    [ -n "$msg" ] || msg=$(printf '%s' "$snap_out" | tail -1)
    _note "pre-edit snapshot of $fp failed (exit $rc): ${msg:-no output}. This turn has no pre-edit baseline for it, so regression bounds on it cannot be judged."
fi

ctx="$report"
[ -n "$notices" ] && ctx="${ctx}${ctx:+$'\n\n'}${notices}"
[ -n "$ctx" ] && loci_json_hook_output PreToolUse "$ctx"
loci_log INFO pre-edit "context: ${#ctx} bytes injected (report=${#report} notices=${#notices})"
exit 0
