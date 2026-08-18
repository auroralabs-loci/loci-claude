#!/usr/bin/env bash
# LOCI plugin — loci CLI installer (process harness around setup-steps:ensure_loci).
#
# Spawned detached from session-init.sh, the edge hooks (install-on-miss), and
# the loci:setup skill. Kept OFF the blocking SessionStart path because a cold
# `uv tool install` takes tens of seconds. This file is only the process harness
# — single-instance lock, PATH, always-exit-0; the install logic lives in
# lib/setup-steps.sh:ensure_loci, which writes the outcome to
# ${STATE_DIR}/loci-cli-status.json and detail to loci-cli-install.log.
#
# A background monitor and a per-prompt UserPromptSubmit safety net were both
# tried and dropped (monitor's stream-ended notice surfaced in the UI every
# session; per-prompt was the wrong trade for an at-most-once event).
#
# Contract: silent on stdout; ALWAYS exits 0.

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"

# Mirrors session-init.sh so both write the same status/log files. Resolved
# BEFORE sourcing the library — the shared logger keys its log path off
# LOCI_STATE_DIR at source time.
STATE_DIR="${HOME}/.loci/state"
mkdir -p "$STATE_DIR" 2>/dev/null || STATE_DIR="${PLUGIN_DIR}/state"
mkdir -p "$STATE_DIR" 2>/dev/null || exit 0
export LOCI_STATE_DIR="$STATE_DIR"

export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"

# Shared bootstrap primitives (also sources lib/loci_log.sh).
# shellcheck source=../lib/setup-steps.sh
. "${PLUGIN_DIR}/lib/setup-steps.sh" 2>/dev/null || exit 0
augment_path

# Single-instance guard: several triggers can race, and this just avoids
# redundant processes (uv serializes installs internally, so concurrency is
# safe). mkdir is the atomic primitive; a PID file inside detects stale locks.
LOCKDIR="${STATE_DIR}/ensure-loci-cli.lock"

# An installer killed mid-run leaves the lock behind — the EXIT trap does not
# run on an untrapped SIGKILL/SIGTERM. `kill -0` alone then hands a recycled PID
# veto power over every future install, silently, forever. Two extra tests: the
# PID must actually BE this script, and no lock outlives the 300s wait cap.
_lock_holder_alive() {
    local _pid="$1" _args
    [ -n "$_pid" ] || return 1
    kill -0 "$_pid" 2>/dev/null || return 1
    [ -n "$(find "$LOCKDIR" -maxdepth 0 -mmin +10 2>/dev/null)" ] && return 1
    _args=$(ps -o args= -p "$_pid" 2>/dev/null) || return 0  # no usable ps — trust the PID
    [ -n "$_args" ] || return 0
    case "$_args" in *ensure-loci-cli*) return 0 ;; *) return 1 ;; esac
}

_lock_acquired=""
_lock_waited=""
for _try in 1 2 3; do
    if mkdir "$LOCKDIR" 2>/dev/null; then _lock_acquired=1; break; fi
    _lock_pid=$(cat "$LOCKDIR/pid" 2>/dev/null)
    if _lock_holder_alive "$_lock_pid"; then
        [ -n "$_lock_waited" ] && exit 0   # already gave it a full wait
        # Wait (bounded) rather than bail: an on-demand caller needs "when this
        # returns, the install attempt is over" semantics. Then retry the
        # acquire instead of assuming the holder finished the job — it may have
        # been killed, and ensure_loci early-exits when the CLI is already good.
        _lock_waited=1
        _waited=0
        while [ "$_waited" -lt 300 ] && kill -0 "$_lock_pid" 2>/dev/null; do
            sleep 2; _waited=$((_waited + 2))
        done
        continue
    fi
    loci_log WARN ensure-loci-cli "reclaiming lock (holder pid=${_lock_pid:-none} dead or stale)"
    rm -rf "$LOCKDIR" 2>/dev/null
done
[ -n "$_lock_acquired" ] || exit 0  # lost the reclaim race — defer to the winner
echo $$ > "$LOCKDIR/pid" 2>/dev/null
# INT/TERM/HUP as well as EXIT: bash skips the EXIT trap on an untrapped signal,
# and a session teardown that kills the detached installer would strand the lock.
trap 'rm -rf "$LOCKDIR" 2>/dev/null' EXIT INT TERM HUP

loci_log INFO ensure-loci-cli "start"
ensure_loci
loci_log INFO ensure-loci-cli "end"
exit 0
