#!/usr/bin/env bash
# PostToolUse edge adapter (Claude Code → loci): ask `loci hook edit-scan` what
# the edit changed and whether that can change a compiled function body, and if
# so emit the reminder to run loci-post-edit. No analysis here, and no payload
# parsing beyond the field the extension gate needs. Advisory: always exits 0,
# never blocks.
set -u
export PYTHONIOENCODING=utf-8
# ${HOME:-} — under `set -u` a bare $HOME exits 1 in an environment without it
# (Windows sets USERPROFILE, and hooks run non-interactive so no profile is
# sourced). Exit 1 from a PostToolUse hook reads to the model as a tool failure,
# which is the one thing this file must never do.
export PATH="${HOME:-}/.local/bin:$PATH"

# The shared logger and the forkless JSON reader. Claude Code records no
# PostToolUse hook anywhere QA can read, so these lines are the only trace this
# hook leaves. Sourced with parameter expansion rather than `dirname` (no fork),
# and stubbed when the logger is missing so no call site below has to test for
# it. `setup-steps.sh` further down sources the same files; their own guards make
# that a no-op.
case "$0" in
    */*)
        . "${0%/*}/../lib/loci_log.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_json.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_failfast.sh" 2>/dev/null || true
        ;;
esac
command -v loci_log >/dev/null 2>&1 \
    || { loci_log() { :; }; loci_log_session_from_payload() { :; }; }
command -v loci_fail_fast >/dev/null 2>&1 || loci_fail_fast() { return 1; }
# `lib/loci_json.sh` is not optional the way the logger is: it reads the field
# this hook gates on and it writes the JSON this hook prints. It ships in the
# plugin, so its absence is a broken install rather than a missing host tool.
command -v loci_json_load >/dev/null 2>&1 \
    || { loci_log ERROR post-edit "lib/loci_json.sh did not source — hook disabled"; exit 0; }

payload=$(cat)

# ONE field is read here: the path the extension gate below decides on.
# Everything else comes back from `loci hook edit-scan`, which parses the payload
# properly — bash's string operators are quadratic in the offset of the match, so
# a payload carrying a whole applied diff cannot be read in shell inside this
# hook's 5 s budget (measured: 4.9 s for one field read over 160 KB), and the
# diff is exactly what this hook has to look at.
#
# `file_path` is inside the bounded prefix `loci_json_load` parses: the harness
# writes it ahead of the edit's content and the tool response. `fp` empty on a
# payload that was TRUNCATED is therefore not "no path" but "not in the prefix",
# and the gate below lets that through to the CLI — a wasted analysis costs one
# call, a skipped one loses the measurement in silence.
loci_json_load "$payload"
fp=$(loci_json_get file_path)
_po_gate="${fp:-${_LOCI_JSON_TRUNCATED:+/unknown.c}}"
# The three fields the nudge and the reminder need, when the prefix holds them —
# which is the ordinary case. `loci hook edit-scan` reports all three back and is
# the fallback below, so a payload too big for the prefix loses nothing.
turn=$(loci_json_get prompt_id)
agent=$(loci_json_get agent_id)
_nroot=$(loci_json_get cwd)

# The session id comes off the payload just read — stdin is consumed by now and
# nothing may read it again.
loci_log_session_from_payload "$payload"
loci_log INFO post-edit "start: PostToolUse post-edit (file=$fp)"
_po_state="skipped"
trap 'loci_log INFO post-edit "end: $_po_state (hook rc=$?)"' EXIT

# Only C/C++/Rust/Go sources. Kept in step with `pre-edit-hook.sh` and with the
# CLI's `_SNAPSHOT_SOURCE_EXTS` — see the longer note there for why narrowing this
# list is a correctness bug and not a scoping choice.
case "$_po_gate" in
    *.c|*.cc|*.cpp|*.cxx|*.c++|*.rs|*.go) ;;
    *.h|*.hpp|*.hxx|*.h++|*.hh|*.inc|*.ipp|*.tcc|*.inl|*.tpp|*.def) ;;
    *.S|*.s) ;;
    *) _po_state="skipped (extension not measurable)"; exit 0 ;;
esac
# Skip plan/settings files that carry a source-ish extension.
case "$fp" in
    */.claude/plans/*|*/.claude/settings*)
        _po_state="skipped (plan/settings file)"; exit 0 ;;
esac

# Install-on-miss: loci is needed now but absent — kick a background install
# (self-locking) and skip this run. Covers plugins installed mid-session.
if ! command -v loci >/dev/null 2>&1; then
    if loci_fail_fast; then
        _po_state="halted (loci absent)"
        loci_fail_fast_absent post-edit PostToolUse "loci hook edit-scan"
        exit 0
    fi
    _po_state="skipped (loci absent — install kicked)"
    nohup bash "$(dirname "$0")/ensure-loci-cli.sh" </dev/null >/dev/null 2>&1 &
    exit 0
fi

# WAS IT APPLIED? Asked here as well as in the verb, because two of the three
# shapes are top-level fields the prefix always holds, and answering them without
# a spawn keeps a failed edit from paying for one. The verb's answer is the
# backstop — it sees the whole payload, including a `tool_response` the prefix cut
# off — and either one saying "no" ends the hook.
_po_applied=1
case "$(loci_json_get hook_event_name)" in
    *Failure) _po_applied="" ;;
esac
case "$(loci_json_kind error)" in
    ""|null) ;;
    *) _po_applied="" ;;
esac
if [ -z "$_po_applied" ]; then
    _po_state="skipped (the edit was not applied)"
    exit 0
fi

# ── one question, one call: what did this edit change, and can it matter? ───
#
# `loci hook edit-scan` reads the payload — unread by this hook — and answers all
# of it: whether the harness applied the edit, what the changed code is, what
# KIND of code that is, and `scan`'s verdict on it. The three payload fields the
# nudge and the reminder need (`prompt_id`, `agent_id`, `cwd`) come back on the
# same envelope, so the payload is parsed once, in the one place that can parse
# it at all.
#
# WHY IT IS NOT SEVEN `jq` READS ANY MORE, beyond the host dependency: the
# interesting input is the APPLIED DIFF, and bash's string operators are
# quadratic in the offset of the match — 4.9 s for a single field read over a
# 160 KB payload, against this hook's 5 s timeout. The reasoning those reads
# encoded is not lost, it moved: see `_edit_applied` (the deny-list, and why an
# unknown event must fall through and remind) and `_edit_code` (why the diff is
# preferred over `new_string`, and why the -/+ markers are kept) in the CLI's
# `hook.py`.
#
# `remind` carries the verdict past this block instead of an `exit`. The
# first-edit nudge below is about the PROJECT, not about this edit, so it has to
# stay reachable on an edit that turns out not to be measurable — a comment-only
# first edit is still a first edit, and the user who needs the nudge is the one
# who has never run an analysis at all.
#
# On a USAGE error (exit 2) we FAIL OPEN and remind, and say that we did.
# Under-triggering loses a measurement and says nothing; over-triggering costs one
# wasted analysis — and failing open matches the skill's own frontmatter, which asks
# for it after any source edit. Exit 2 is a broken hook/CLI contract rather than an
# older CLI, so it is surfaced instead of absorbed.
remind=1
cli_note=""
route=""
envelope=$(printf '%s' "$payload" | loci hook edit-scan 2>&1)
rc=$?
# Fast-fail: neither branch below runs. Exit 2 is reported as measurable without
# a classification and any other failure silences the reminder — one carries on
# with less, the other carries on with nothing, and both are the papering-over
# this mode exists to end.
if [ "$rc" -ne 0 ] && loci_fail_fast; then
    _po_state="halted (edit-scan rc=$rc)"
    loci_fail_fast_emit post-edit PostToolUse "loci hook edit-scan" "$rc" "$envelope"
    exit 0
fi
if [ "$rc" -eq 2 ]; then
    cli_note="LOCI: \`loci hook edit-scan\` rejected this hook's arguments (exit 2) — the plugin and the CLI are out of step. The edit is being reported as measurable without classification; run /loci:setup if it repeats."
elif [ "$rc" -ne 0 ]; then
    remind=0                # CLI broken or unavailable: the skill could not run either
else
    loci_json_load "$envelope"
    [ "$(loci_json_get applied)" = false ] && {
        _po_state="skipped (the edit was not applied)"; exit 0; }
    [ "$(loci_json_get measurable)" = true ] || remind=0
    # AAD-7607. Two different questions, and the reminder needs both answered.
    # `measurable` is about the FILE — can this edit change compiled code — and
    # is deliberately blind to the project. `artifact_only` is about the PROJECT:
    # its recipe records a linked binary and no compile database, so
    # `loci analyse prepare` answers `compdb_absent` for every source in it and
    # the skill this reminder demands can do nothing but relay that refusal. It
    # did exactly that on every C edit, for ever, and a refusal repeated per edit
    # is what a session then works around — in the run this ticket came from, by
    # rebuilding the artifact, which is the only pre-edit binary such a project
    # has. Silence is the honest answer; `session-init.sh` states the scope once,
    # at the top of the session, along with what DOES measure here.
    [ "$(loci_json_get artifact_only)" = true ] && remind=0
    if [ "$remind" = "1" ]; then
        # HOW to measure it, carried through to the skill. `measurable` says the edit can
        # change compiled code; `measure_via` says whether this file can be compiled at
        # all — a header cannot, and is measured through the units that #include it.
        # Passed along rather than left to the skill re-deriving it from its own list of
        # header suffixes: that list would be the FOURTH copy of the same set, and the
        # only one no test can compare against the CLI's.
        route=$(loci_json_get measure_via)
    fi
    [ -n "$turn" ]   || turn=$(loci_json_get turn)
    [ -n "$agent" ]  || agent=$(loci_json_get agent)
    [ -n "$_nroot" ] || _nroot=$(loci_json_get cwd)
    [ -n "$fp" ]     || fp=$(loci_json_get file_path)
fi

# ── the first-edit nudge ───────────────────────────────────────────────────
#
# Report §6.3 gives an uninitialized project a suggestion line in the session
# banner. This delivers the same fact where a user who does not read banners will
# meet it: the first time they edit compiled source in a project LOCI has no
# recipe for. Once, and then never again.
#
# ONCE PER PROJECT, not per session. The marker sits beside the keyed context in
# the state directory and is keyed the same way, so it survives `--continue`,
# `/clear`, `/compact` and a reboot — which is what acceptance criterion 3 asks
# for. Nothing resets it, and nothing needs to: a project that later gets a recipe
# stops matching the status test long before the marker is consulted.
#
# THE ALLOW-LIST IS THE POINT, and each excluded value is excluded for its own
# reason. `unsupported` and `needs_user` are what the CLI records once it has
# already decided, so pointing at `/loci:init` there sends the user at a question
# that was asked and answered. `failed` is transient for ARMING (§6.2) — the
# session re-arms and the next analysis routes whichever coded error the compile
# answers with, which will not be `not_initialized` — so a nudge naming
# `/loci:init` would be a guess about a state the CLI is about to correct. Note
# that the VALUE is not transient: session-init arms the project but never
# rewrites what the CLI recorded, so a project whose one auto-init attempt failed
# stays out of the nudge until `loci init` writes something else. That is the
# right trade only because such a project is armed and will be measured; if that
# ever stops being true, this arm is where to look. A value this version does not
# recognise takes the same quiet path, because the nudge is a claim and nothing
# here can check it.
#
# The empty case DOES nudge, and that is not an oversight. session-init writes
# `init_status: uninitialized` only in the armed branch; a project the cheap gate
# declined (`no_project`, `multi_project`, a detector that could not run) gets a
# context file with no `init_status` at all — and that is precisely the
# population report §6.3 hands to `/loci:init`, since a project with sources and no
# declared build no longer arms on its own. A file that does not exist yet reads
# the same way for the same reason.
#
# Every extension this hook already accepted, not a narrower C/C++/Rust/Go list: the
# filter at the top of this file is the one the CLI's `_SNAPSHOT_SOURCE_EXTS`
# agrees with, and a second, shorter copy here would be the fourth spelling of a
# set that has already cost this repo a defect.
#
# Cost. Measured INTERLEAVED against the pre-task hook (alternate runs, so drift
# and load hit both sides), median of 9, real branch CLI, Git Bash under Windows
# — the slow platform — against this hook's registered 5 s budget:
#
#   initialized, session at the recipe root   2 272 -> 2 076 ms   -196
#   initialized, session in a subdirectory    2 013 -> 2 324 ms   +311
#   uninitialized                             2 370 -> 2 833 ms   +463
#   initialized, unmeasurable edit            1 183 -> 1 198 ms    +15
#
# The common shape got FASTER: one `[ -f ]` decides it. What the other rows buy
# is `hash_cwd` (two `stat` probes and a `sha256sum`) plus one context read and
# one failed `O_EXCL` create, on every edit, for the life of a project that never
# gets a recipe. The upward walk is NOT in those numbers per edit: it runs once,
# for whichever edit claims the marker. The numbers predate the jq removal, which
# took seven spawns out of this file and left the rows only cheaper.
#
# Worst case 2 833 ms of 5 000, beside the one `loci` call that is most of it. An
# earlier measurement of this block reported +351 ms and was taken
# non-interleaved on a quiet machine; a hostile reviewer measured +700 the other
# way. Interleaving is what makes the two agree, and these are the numbers.
# ── the three payload fields this file needs ───────────────────────────────
#
# `prompt_id`, `agent_id` and `cwd`, all three off the ONE envelope read above.
# They used to be a payload read of their own, merged into a single spawn because
# one costs ~150 ms under Git Bash on Windows against a 5 s budget; now they ride
# back from the verb that had to read the payload anyway, and cost nothing.
#
# Why each is needed, and what breaks without it:
#
# `agent_id` is present on a subagent's tool payloads and absent from the main
# agent's — established by probing a live session, because every other field is
# identical: same `session_id`, same `transcript_path`, and the same `prompt_id`
# (the PARENT turn's, which is what the shared per-turn baseline wants). Both
# edit hooks fire inside a subagent, and for a long time nothing here knew it:
# the reminder went to an agent whose transcript the user never reads, so the
# measurement ran and its numbers went into the subagent's own report, which the
# parent may summarise, paraphrase, or drop. The fix is a sentence, not a
# suppression — skipping the reminder inside subagents would make a subagent's
# edits the one kind that is never measured, and subagents are where bulk edits
# happen.
#
# `prompt_id` rides along so the skill can pass it as `--turn`. The pre-edit hook
# stamps the baseline with this same value, but nothing ever CHECKED it: the
# compile that reads the baseline was not told which turn it wanted, so a capture
# left by a PREVIOUS turn was served as this edit's Before and the delta silently
# spanned two turns (measured: +77.8% ROM reported for an edit whose true effect
# was 0). Reachable whenever the pre-edit hook did not capture for this turn —
# killed on its 8 s budget, `loci` briefly absent, or the file changed outside
# Claude Code. Degrading is safe at every step: no `prompt_id`, or a model that
# drops it, means the skill omits `--turn` and the compile simply does not verify
# the turn.
#
# `cwd` is where the nudge below starts looking. A directory name may legally
# contain a newline, and that used to shift the three fields when they arrived as
# three lines of text — a `cwd` of `/c/weird\ndir` made the turn id `dir` and told
# a MAIN-agent edit it was a subagent. A JSON string cannot be misread that way,
# so the ordering rule that guarded it is gone with the text format.
#
# The reminder text is the channel because there is no other one: a PostToolUse
# hook cannot call the skill it asks for.
nudge=""
# Each rung is tested for being a DIRECTORY, not merely non-empty. A payload
# `cwd` that names nothing on this disk is not the end of the ladder — it is a
# rung that did not answer, and stopping there took the nudge with it.
[ -d "$_nroot" ] || _nroot="${CLAUDE_PROJECT_DIR:-}"
[ -d "$_nroot" ] || _nroot="$PWD"
# A recipe BESIDE the session is initialization whatever the state file says, and
# testing for it costs no process. It is the fast path, not the rule: a session
# opened in a subdirectory of the recipe root has no `.loci/build.yaml` next to
# it, and the walk further down is what covers that.
# `hash_cwd`'s `realpath` fallback keys a path that does not exist just as
# happily as one that does, so a rung that is not a directory must never reach
# it: the same project would be nudged twice, once under each key.
if [ -d "$_nroot" ] && [ ! -f "$_nroot/.loci/build.yaml" ]; then
    PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
    # The same ladder as session-init.sh, pre-edit-hook.sh and the CLI's
    # `stats._resolve_state_dir`: the fallback is taken when the directory cannot
    # be CREATED, not when it does not exist yet.
    STATE_DIR="${LOCI_STATE_DIR:-${HOME:-}/.loci/state}"
    mkdir -p "$STATE_DIR" 2>/dev/null || STATE_DIR="${PLUGIN_DIR}/state"
    export LOCI_STATE_DIR="$STATE_DIR"
    # Sourcing only DEFINES — no installs, no writes, and the shared logger is
    # inert outside dev mode. `hash_cwd` and `_loci_find_recipe` are the writer's
    # and the CLI's own rules rather than second copies of them; a missing or
    # broken library leaves them undefined and the nudge simply unsent, which is
    # what every install does today.
    . "${PLUGIN_DIR}/lib/setup-steps.sh" 2>/dev/null || true
    _nkey=""
    declare -F hash_cwd >/dev/null 2>&1 && { _nkey=$(hash_cwd "$_nroot" 2>/dev/null) || _nkey=""; }
    _nmark="${STATE_DIR}/init-nudge-${_nkey}"
    # WHAT THE MARKER SAYS, in one builtin read and at most one `[ -f ]`:
    #   empty        this project was nudged; there is nothing left to decide.
    #   a path       a recipe answered for it. Still there — still answered.
    #                Gone — the project is uninitialized again, so the marker is
    #                stale and the question re-opens.
    # `read` is a builtin and the file is one line, so the steady state of an
    # initialized subdirectory session costs no process at all.
    if [ -n "$_nkey" ] && [ -f "$_nmark" ]; then
        _nprev=""
        IFS= read -r _nprev < "$_nmark" 2>/dev/null || _nprev=""
        if [ -n "$_nprev" ] && [ ! -f "$_nprev" ]; then
            rm -f "$_nmark" 2>/dev/null || true
        fi
    fi
    if [ -n "$_nkey" ] && [ ! -e "$_nmark" ]; then
        # ABSENT AND UNREADABLE ARE NOT THE SAME ANSWER. A bare
        # `init_status // ""` collapses "no file", "not JSON", "not an object"
        # and "a directory at that path" into the one value that nudges — so a
        # truncated context file over a recorded `unsupported` produced exactly
        # the line the task forbids. `lib/setup-steps.sh` has its own guard for
        # the directory case, so it is a state the authors have met. Only an
        # object with a string or a null `init_status` is a recorded answer;
        # anything else is `?`, which matches no arm below and stays quiet.
        _nctx="${STATE_DIR}/project-context-${_nkey}.json"
        if [ -f "$_nctx" ]; then
            # THE SHAPE IS CHECKED BEFORE THE FIELD IS. `loci_json_is_object`
            # answers two of the four failures above — a truncated or empty file
            # and a JSON array, and a file holding two concatenated objects —
            # and `lib/setup-steps.sh` asks it about this same file for the same
            # reason, so there is one spelling of the question.
            #
            # Then the field's TYPE, which is why `loci_json_kind` exists: a
            # recorded `null` is the same claim as an absent key and nudges, a
            # recorded number is a file this version cannot read and must stay
            # quiet, and a status of the literal string `"null"` is neither of
            # those. `loci_json_get` renders the first and the third alike, so
            # asking it alone cannot answer this.
            _ndoc="$(<"$_nctx")"
            _nstatus="?"
            if loci_json_is_object "$_ndoc"; then
                loci_json_load "$_ndoc"
                case "$(loci_json_kind init_status)" in
                    string) _nstatus=$(loci_json_get init_status) ;;
                    null|"") _nstatus="" ;;
                esac
            fi
        elif [ -e "$_nctx" ]; then
            _nstatus="?"                          # a directory, a device, a socket
        else
            _nstatus=""                           # genuinely nothing recorded
        fi
        # A null `init_status` reads as "" above, deliberately: it is the same
        # claim as an absent key.
        case "$_nstatus" in
            ""|uninitialized)
                # IS THERE A RECIPE ABOVE THIS SESSION? The status file cannot
                # say: `detect_and_write_context` records NO `init_status` in its
                # `initialized_degraded`/`state` branch — a recipe on disk that
                # `loci init` has not seen, or a wiped state directory — and
                # `session-init.sh` is registered `startup` only, so a `--continue`
                # session never repairs it. A subdirectory session in that state
                # read "nothing recorded" and was told its initialized project is
                # not initialized.
                #
                # TWO STEPS, and the split is the whole point. `_loci_find_recipe`
                # is the authority — it is the CLI's own walk, with the `$HOME` and
                # `.git` ceilings that stop it adopting `~/.loci/build.yaml` for
                # every project under the home directory — but it spends a `stat`
                # PER LEVEL on those ceilings. Called unconditionally it took this
                # hook from 2.2 s to 3.6 s median and 5.0 s worst, against a
                # registered 5 s timeout: measured, 3 of 15 runs were killed, and a
                # kill here loses the MANDATORY post-edit reminder as well as the
                # nudge, because the emit is downstream of it. That is the silent
                # skip this whole file is built to prevent, traded for an advisory
                # line — a bad bargain in any state.
                #
                # So a CANDIDATE SCAN runs first, and it spawns nothing: a
                # parameter-expansion walk up the tree testing one `[ -f ]` per
                # level. It decides only whether there is anything to ask the
                # authority ABOUT. No candidate — the overwhelmingly common case for
                # a project that has never been initialized — and the authority is
                # never called, which is what keeps the first edit inside budget.
                # A candidate, and `_loci_find_recipe` says whether it is one this
                # session may adopt. The scan is deliberately more permissive than
                # the rule: it can only cause the question to be ASKED.
                _nseen=""
                # THE PHYSICAL PATH, because that is what the authority walks:
                # `_loci_find_recipe` starts with `cd "$1" && pwd -P`. Walking the LOGICAL
                # parent chain instead made this scan STRICTER than the rule it is only
                # allowed to be more permissive than — and a scan that finds nothing never
                # asks. Reproduced with an ordinary Windows directory junction (no privilege
                # needed) pointing INTO a project: `outer/link -> real/proj/src`, recipe at
                # `real/proj/.loci/`, session at `outer/link/drv`. The authority finds it; the
                # logical walk leaves the project at `outer` and never comes back. That is the
                # CRITICAL this whole branch exists for, reached through a link. Confirmed the
                # same way under WSL with a symlink.
                #
                # `cd` + `pwd -P` is a SUBSHELL, not an exec, so it costs a fork and no
                # process. The normalised literal path is the fallback for a directory `cd`
                # cannot enter, where the scan is no worse than it was.
                _nreal=$(cd "$_nroot" 2>/dev/null && pwd -P) || _nreal=""
                _nd="${_nreal:-$_nroot}"
                # BOTH SEPARATORS. A payload carries `C:\Users\...`; Git Bash resolves that in
                # a FILE TEST but not in a parameter expansion, so a `/`-only step stopped this
                # walk dead at the first level. ⚠ a bracket class does not fix it either:
                # inside a pattern `[/\\]` reads the backslash as escaping the `]`, the class
                # never closes, and the expansion silently matches nothing. `pwd -P` already
                # returns forward slashes; this is for the fallback.
                _nd="${_nd//\\//}"
                _ni=0
                _nrecipe=""
                while [ "$_ni" -lt 64 ]; do
                    if [ -f "$_nd/.loci/build.yaml" ]; then _nseen=1; break; fi
                    _nup="${_nd%/*}"
                    [ "$_nup" != "$_nd" ] && [ -n "$_nup" ] || break
                    _nd="$_nup"
                    _ni=$((_ni + 1))
                done
                if [ -n "$_nseen" ] && declare -F _loci_find_recipe >/dev/null 2>&1 \
                   && _nrecipe=$(_loci_find_recipe "$_nroot" 2>/dev/null) \
                   && [ -n "$_nrecipe" ]; then
                    # Initialized after all — and the marker RECORDS WHICH FILE
                    # said so, rather than recording nothing or recording a bare
                    # "answered".
                    #
                    # Nothing was the first attempt, because the answer can stop
                    # being true: `git clean -xdf` removes a gitignored `.loci/`, a
                    # branch switch takes it away, a fresh clone never had it, and a
                    # session that had recorded "initialized" would then be silent
                    # for ever while a session at the project root still nudged.
                    # But recording nothing means the AUTHORITY runs again on every
                    # edit — the scan always finds the same candidate — which is a
                    # `stat` per level, for ever, in exactly the state the two-step
                    # was built to make cheap. Measured at +0.7 s to +1.2 s per
                    # edit; round 1's first-edit cost, made permanent.
                    #
                    # The path is the evidence, so the next edit re-checks it with
                    # ONE `[ -f ]` (below) instead of re-deriving it. The recipe
                    # going away invalidates the marker, which is the property
                    # recording nothing was reaching for.
                    printf '%s\n' "$_nrecipe" > "$_nmark" 2>/dev/null || true
                # `set -C` is an atomic create-or-fail and the ONLY gate on the
                # nudge: the test and the claim are one operation, so two subagents
                # editing inside one turn cannot both win it, an already-claimed
                # project costs one failed syscall, and a marker that cannot be
                # WRITTEN (a read-only state dir) yields no nudge at all rather
                # than one on every edit for ever. A plain `[ -e ]` in front of it
                # would undo all three — it answers "does it exist", which is not
                # the question — so there is deliberately nothing in front of it.
                elif ( set -C; : > "$_nmark" ) 2>/dev/null; then
                    nudge="LOCI is not initialized for this project; run /loci:init to enable measurements."
                fi
                ;;
        esac
    fi
fi
# Nothing measurable and nothing to nudge about is the silence this hook has
# always kept on a comment-only edit.
if [ "$remind" != "1" ] && [ -z "$nudge" ] && [ -z "$cli_note" ]; then
    _po_state="silent (remind=0, nothing to nudge about)"
    exit 0
fi

fname=$(basename -- "$fp")

# Alone, the nudge carries the prefix the reminder would otherwise have supplied;
# appended, it is a second sentence and needs the space between them.
if [ -n "$nudge" ]; then
    if [ "$remind" = "1" ]; then nudge=" $nudge"; else nudge="[loci] $nudge"; fi
fi

# Its own line: a contract failure is not a sentence of the reminder.
if [ -n "$cli_note" ] && { [ "$remind" = "1" ] || [ -n "$nudge" ]; }; then
    cli_note=$'\n'"$cli_note"
fi

# THE REMINDER, assembled in the order the model reads it. One `+`-chain in jq
# until now; the same sentences, appended, with the same emptiness rules — a
# clause whose field is empty contributes nothing rather than a stray space.
_ctx=""
if [ "$remind" = "1" ]; then
    _ctx="[loci] ${fname} was modified. You MUST invoke the loci:loci-post-edit skill NOW — do not proceed to the next edit or respond to the user first. "
    [ -n "$turn" ] && _ctx="${_ctx}Pass turn id ${turn} to the skill as its --turn value. "
    [ "$route" = dependents ] && _ctx="${_ctx}This file emits no object of its own; pass it to \`loci analyse prepare --source\` like any file and it is measured through the translation units that #include it — Step 0b. "
    [ -n "$agent" ] && _ctx="${_ctx}You are running as a subagent: the user sees your final report, not this transcript. Include the LOCI verdict (the timing/energy delta, or why it could not be measured) in that report. "
    _ctx="${_ctx}EXCEPTION: if this edit was made as part of a loci-preflight pass (predictive measurement of a candidate function), do NOT invoke loci-post-edit — preflight will report the analysis itself."
fi
loci_json_hook_output PostToolUse "${_ctx}${nudge}${cli_note}"
_po_state="emitted (remind=$remind route=${route:-none} nudge=${nudge:+yes})"
exit 0
