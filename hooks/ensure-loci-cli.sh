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
if ! mkdir "$LOCKDIR" 2>/dev/null; then
    _lock_pid=$(cat "$LOCKDIR/pid" 2>/dev/null)
    if [ -n "$_lock_pid" ] && kill -0 "$_lock_pid" 2>/dev/null; then
        # Wait (bounded) rather than bail: an on-demand caller needs "when this
        # returns, the install attempt is over" semantics. The holder writes
        # the status file.
        _waited=0
        while [ "$_waited" -lt 300 ] && kill -0 "$_lock_pid" 2>/dev/null; do
            sleep 2; _waited=$((_waited + 2))
        done
        exit 0
    fi
    rm -rf "$LOCKDIR" 2>/dev/null           # stale lock — reclaim
    mkdir "$LOCKDIR" 2>/dev/null || exit 0  # lost the reclaim race — defer
fi
echo $$ > "$LOCKDIR/pid" 2>/dev/null
trap 'rm -rf "$LOCKDIR" 2>/dev/null' EXIT

loci_log INFO ensure-loci-cli "start"
ensure_loci
loci_log INFO ensure-loci-cli "end"
exit 0
