#!/usr/bin/env bash
# Compile a source through the loci CLI and print the artifact paths the analysis
# skills need. One call, one answer, no path construction by the caller.
#
# Called by every skill that measures a *change*: `loci-post-edit`, and the
# Incremental Path of `exec-trace`, `control-flow`, `stack-depth` and
# `memory-report`. It replaces the raw `<compiler> -g <flags> -c … -o …` line those
# four used to carry.
#
#   bash lib/compile-and-read-back.sh --source <file> --loci-target <t> \
#       [--context <project-context>] [--project-root <dir>] \
#       [--phase preflight|post-edit] [--turn <id>] [--reconstruct]
#
# `--reconstruct` is for the HEADER case: the file the user edited emits no object,
# so the thing being measured is a translation unit that #includes it (name them
# with `loci build affected`). That TU was not itself edited, so nothing snapshotted
# its object and there is no `.prev` — the Before has to be REBUILT from the header's
# captured pre-edit text. Needs `--turn`. Everything else about the call is the same,
# and the answer still arrives as OBJ/META/PREV/PREV_META, so the caller never
# learns that two compiles happened.
#
# Prints tab-separated key/value lines on stdout and nothing else:
#
#   OBJ        <path>    the object just compiled
#   META       <path>    its build record (the .meta.json sidecar)
#   PREV       <path>    the pre-edit baseline, or EMPTY when there is none
#   PREV_META  <path>    that baseline's build record, or EMPTY
#   NOTE       <text>    zero or more; why a baseline was withheld, or how it was
#                        established. Always report a NOTE to the user when it
#                        explains a missing Before column.
#   FAILED     <code> <message>    the compile did not produce an artifact
#
# `FAILED` is the ONLY failure signal — this script exits 0 even then, so a handled
# compile failure is never mistaken for a broken command. When FAILED is printed,
# no OBJ/META line is.
#
# ---------------------------------------------------------------------------
# Why this is a script and not a fenced recipe in each SKILL.md
#
# It was a fence first, copied into five files. Hostile review found six defects
# that existed *because* it was prose a model retypes: an unquoted `--source` broke
# every path containing a space (a Windows norm) with `2>/dev/null` swallowing the
# only diagnostic; literal `[--phase <phase>]` brackets reached argparse verbatim;
# the sidecar path was resolved against the shell's CWD while the CLI resolved the
# object against the project root, so `--meta-prev` silently missed; and four of the
# five callers never loaded the section that defined the placeholders they were
# substituting. None of those are reachable here — the caller passes values, not
# syntax.
#
# It also makes the branch below testable. `tests/unit/test_compile_read_back.py`
# drives it against a stubbed `loci`, which is how the plugin already tests its
# hooks, instead of asserting that sentences exist in a document.
# ---------------------------------------------------------------------------

set -u

SOURCE=""; TARGET=""; CONTEXT=""; PROJECT_ROOT=""; PHASE=""; TURN=""; COMPILER_PATH=""
RECONSTRUCT=no

fail() { printf 'FAILED\t%s\t%s\n' "$1" "$2"; exit 0; }

# `shift 2` on a flag with no value is a FAILED shift: `$#` never decreases, `$1` is
# re-read, and the loop spins forever — measured as a 0-byte, no-stderr hang killed
# only by an external timeout, which the model sees as a dead Bash call rather than
# an error. That breaks this file's headline promise that FAILED is the only failure
# signal, and it is reachable from the shipped prose: the contract ends its example
# with `--turn "<turn-id>"` and then says to drop the flag when there is no id, so
# deleting just the placeholder is the natural half-edit. The `compiler_not_found`
# recovery appends `--compiler-path` the same way.
need_value() { [ "$2" -ge 2 ] || fail - "$1 needs a value"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --source)        need_value "$1" $#; SOURCE="$2"; shift 2 ;;
        --loci-target)   need_value "$1" $#; TARGET="$2"; shift 2 ;;
        --context)       need_value "$1" $#; CONTEXT="$2"; shift 2 ;;
        --project-root)  need_value "$1" $#; PROJECT_ROOT="$2"; shift 2 ;;
        --phase)         need_value "$1" $#; PHASE="$2"; shift 2 ;;
        --turn)          need_value "$1" $#; TURN="$2"; shift 2 ;;
        # The documented `compiler_not_found` recovery re-runs this same command with
        # an explicit compiler, so it has to be accepted here — a skill told to
        # append it to a script that rejected it would fail on the retry instead of
        # on the original problem.
        --compiler-path) need_value "$1" $#; COMPILER_PATH="$2"; shift 2 ;;
        # No value, so no `need_value` and a single shift. Kept apart from the pairs
        # above on purpose: `shift 2` here would consume the NEXT flag silently.
        --reconstruct)   RECONSTRUCT=yes; shift ;;
        *) fail - "unknown argument: $1" ;;
    esac
done

[ -n "$SOURCE" ] || fail - "--source is required"
[ -n "$TARGET" ] || fail - "--loci-target is required"
command -v loci >/dev/null 2>&1 || fail - \
    "loci is not on PATH; run /loci:setup"
command -v jq >/dev/null 2>&1 || fail - \
    "jq is not on PATH; it is a plugin prerequisite (see the runtime contract)"
# --- where the project is ---------------------------------------------------
# The CLI resolves the object under --project-root, else the context's
# project_root, else its own CWD. Left to that fallback, a skill whose shell sits
# one directory above the project root compiles into a SECOND `.loci-build/` tree:
# the real baseline is never seen (Case B, "no preflight baseline"), and the stray
# tree is later ranked as a measurement candidate by `find_loci_artifacts`.
# So the root is resolved once, here, and passed explicitly.
if [ -z "$PROJECT_ROOT" ] && [ -n "$CONTEXT" ] && [ -f "$CONTEXT" ]; then
    # `session-init` can write the literal string "unknown"; the CLI treats that as
    # absent, and so must we.
    PROJECT_ROOT=$(jq -r '
        .project_root // empty | select(. != "unknown" and . != "")' \
        "$CONTEXT" 2>/dev/null)
fi
[ -n "$PROJECT_ROOT" ] || PROJECT_ROOT=$(pwd)
[ -d "$PROJECT_ROOT" ] || fail - "project root is not a directory: $PROJECT_ROOT"

# The source-exists check runs AFTER the root is known, and falls back to resolving a
# relative path against it. `loci build affected` reports translation units
# project-relative, and the header path tells a model to hand one of them straight
# back as `--source` — so `src/app.c` from a shell that is not sitting at the project
# root failed with "source not found" on a path that is perfectly correct relative to
# the root this script had just finished computing. Absolute paths are unaffected.
if [ ! -f "$SOURCE" ]; then
    case "$SOURCE" in
        /*|[A-Za-z]:[/\\]*) ;;                      # absolute: nothing to try
        *) [ -f "$PROJECT_ROOT/$SOURCE" ] && SOURCE="$PROJECT_ROOT/$SOURCE" ;;
    esac
fi
[ -f "$SOURCE" ] || fail - "source not found: $SOURCE"

# --- which CLI generation is installed --------------------------------------
# A capability probe, not a version comparison: `--inherit-prev` and `--turn`
# arrived in the same change, so one probe covers both, and no table of
# which-flag-landed-when has to be kept in sync. `--help` is argparse-only, so it
# answers without a session.
MODERN=no
if loci build compile --help 2>/dev/null | grep -q -- '--inherit-prev'; then
    MODERN=yes
fi
# `--baseline` is a SEPARATE probe, not an inference from the one above. It landed
# strictly later, so "can inherit" does not imply "can reconstruct" — and reading one
# as the other would send `--baseline` to a CLI that answers argparse's usage error
# on stderr with no envelope at all, which this script would then report as a compile
# failure of the user's code.
CAN_RECONSTRUCT=no
if loci build compile --help 2>/dev/null | grep -q -- '--baseline'; then
    CAN_RECONSTRUCT=yes
fi

# --- what kind of source this is -------------------------------------------
# Only matters on the legacy branch, and only for `.rs`, where the two Rust routes
# behave in OPPOSITE directions on a CLI that predates `--inherit-prev`:
#
#   cargo      `build compile` already reports the pair by itself, and deliberately
#              WITHHOLDS it when the baseline sidecar records a different package or
#              target ("must not be offered as one"). Overriding that is how a
#              package rename gets rendered as the effect of an edit.
#   standalone auto-inherit is gated on the flag this CLI does not have, so nothing
#              inherits and nothing is reported — while the flat object path is
#              exactly right. Skipping it there loses real parity (measured: the
#              baseline built at opt-level 0, the new object at 2).
KIND=c
case "$SOURCE" in
    *.rs)
        KIND=rust-standalone
        # Walk up for a Cargo.toml — a workspace member's manifest can be several
        # levels above the source — but STOP AT THE PROJECT ROOT, because that is
        # where the CLI's own `find_manifest` stops. The job here is to predict which
        # route the CLI took, so any disagreement is a wrong prediction. Unbounded,
        # one unrelated `Cargo.toml` anywhere above the project (a sibling checkout, a
        # tools dir, `$HOME`) classified a standalone `.rs` as a crate: parity was
        # silently dropped and the run reported a cargo refusal the CLI never made.
        _root=$(cd -- "$PROJECT_ROOT" 2>/dev/null && pwd) || _root=""
        _d=$(cd -- "$(dirname -- "$SOURCE")" 2>/dev/null && pwd) || _d=""
        while [ -n "$_d" ]; do
            if [ -f "$_d/Cargo.toml" ]; then KIND=rust-cargo; break; fi
            # `-ef` (same device+inode), NOT a string compare: one directory has
            # more than one spelling. Git Bash mounts `%TEMP%` at `/tmp`, so the
            # root arrives as `/c/Users/…/Temp/x` while walking up from the source
            # yields `/tmp/x` — the same directory, textually unequal, and the bound
            # silently failed so the walk escaped the project and found an unrelated
            # `Cargo.toml` above it. Symlinked checkouts and Windows case differences
            # do the same thing.
            [ -n "$_root" ] && [ "$_d" -ef "$_root" ] && break
            _p=$(dirname -- "$_d")
            [ "$_p" = "$_d" ] && break       # filesystem root: stop regardless
            _d="$_p"
        done
        ;;
esac

# --- the compile ------------------------------------------------------------
# stderr is CAPTURED, not discarded. The replaced raw compiler line did not hide
# it, and a usage error goes to stderr with no envelope at all — so discarding it
# left `FAILED - no envelope` as the entire diagnosis of a fixable mistake.
ERRFILE=$(mktemp 2>/dev/null || printf '%s' "${TMPDIR:-/tmp}/loci-crb.$$")
# `BASELINE_ERRFILE` is set only on the reconstruct path, and is reaped here as well
# as inline: the inline `rm` never runs if the script is killed mid-compile, which on
# a hook budget is not hypothetical.
BASELINE_ERRFILE=""
cleanup() { rm -f "$ERRFILE" ${BASELINE_ERRFILE:+"$BASELINE_ERRFILE"}; }
trap cleanup EXIT

set -- --source "$SOURCE" --loci-target "$TARGET" --project-root "$PROJECT_ROOT"
[ -n "$CONTEXT" ]       && set -- "$@" --context "$CONTEXT"
[ -n "$PHASE" ]         && set -- "$@" --phase "$PHASE"
[ -n "$COMPILER_PATH" ] && set -- "$@" --compiler-path "$COMPILER_PATH"

STEM=$(basename -- "$SOURCE"); STEM="${STEM%.*}"
# The one path this script spells, and it is spelled for a CLI generation that is
# not the newest one. Since phase 02c the object path mirrors the source's own
# directories, so a CLI carrying that change would not put a `.prev` here at all.
#
# What keeps it correct is a ONE-WAY implication, and only that: a CLI too old to
# have `--inherit-prev` is also too old to have 02c, because 02c landed strictly
# later. So the legacy branch — the only branch that reaches this line — always
# faces a flat layout. The converse is NOT true and must not be read in: the probe
# proves "can inherit", never "writes nested", and anything that starts treating it
# as a phase-02c capability check is wrong the moment a release ships one without
# the other. (An earlier version of this comment cited the pinned `0.1.113` as a CLI
# that has `--inherit-prev` and still writes flat. It does not have it — verified on
# the published tag — so the rule stands but that witness does not. No released CLI
# has `--inherit-prev` yet, which means the legacy branch below is the ONLY one
# production takes today.)
FLAT_META_PREV="$PROJECT_ROOT/.loci-build/$TARGET/$STEM.o.meta.json.prev"

if [ "$MODERN" = yes ]; then
    set -- "$@" --inherit-prev
    [ -n "$TURN" ] && set -- "$@" --turn "$TURN"
elif [ "$KIND" != rust-cargo ] && [ -f "$FLAT_META_PREV" ]; then
    # Absolute, anchored to the same root the CLI resolves the object against.
    set -- "$@" --meta-prev "$FLAT_META_PREV"
fi

ENV_JSON=$(loci build compile "$@" 2>"$ERRFILE")
RC=$?

if [ "$(jq -r '.ok // false' <<<"$ENV_JSON" 2>/dev/null)" != "true" ]; then
    CODE=$(jq -r '.error.code // empty' <<<"$ENV_JSON" 2>/dev/null)
    MSG=$(jq -r '.error.message // empty' <<<"$ENV_JSON" 2>/dev/null)
    if [ -z "$MSG" ]; then
        # No envelope: argparse or a crash. The captured stderr IS the diagnosis.
        MSG=$(tr '\n' ' ' <"$ERRFILE" 2>/dev/null | sed 's/  */ /g; s/^ //; s/ $//')
        [ -n "$MSG" ] || MSG="loci build compile produced no envelope (exit $RC)"
    fi
    fail "${CODE:--}" "$MSG"
fi

OBJ=$(jq -r '.data.output // empty' <<<"$ENV_JSON")
META=$(jq -r '.data.meta_file // empty' <<<"$ENV_JSON")
PREV=$(jq -r '.data.output_prev // empty' <<<"$ENV_JSON")
PREV_META=$(jq -r '.data.meta_prev // empty' <<<"$ENV_JSON")
# The two are read independently, so a truncated envelope can carry one without the
# other — and half a pair is not a baseline. A `PREV` with an empty `PREV_META` flows
# into `loci build diff --prev ""`; a `PREV_META` with no `PREV` gives the parity step
# something to compare and the diff step nothing to diff. They move together or not
# at all, which is also what the CLI's own `_baseline_pair` guarantees.
if [ -z "$PREV" ] || [ -z "$PREV_META" ]; then
    PREV=""; PREV_META=""
fi


[ -n "$OBJ" ] && [ -n "$META" ] || fail - \
    "loci build compile reported ok with no artifact path"

NOTES=""
# Newlines are SCRUBBED, not passed through. This file's headline promise is
# "tab-separated key/value lines on stdout and nothing else", and several notes
# interpolate a CLI `error.message`, which is free to be multi-line — a two-line
# message emits one `NOTE<TAB>…` line followed by a bare line that parses as nothing.
# Done in the one helper rather than at each call site, because the call sites are
# where it was already missed once.
note() {
    NOTES="${NOTES}NOTE	$(printf '%s' "$1" | tr '\n\r\t' '   ' | sed 's/  */ /g; s/^ //; s/ $//')"$'\n'
}

# Why is the artifact beside this object not usable as a Before? Empty answer means
# "nothing disqualifying found here". Used to EXPLAIN on every CLI, and to DECIDE
# only on one that cannot decide for itself.
#
#  * source_file — this CLI's `diff_metas` compares compiler, version, target,
#    architecture, flags and flag_source kind, but NOT the source. On the CLI
#    generation that DECIDES here, objects are still keyed on the STEM, so
#    `modA/util.c` and `modB/util.c` share one `util.o` and therefore one `.prev`.
#    Unchecked, editing modB reports modA's object as the Before — measured at
#    `{"added":1,"removed":1,"modified":1}` and +128.6% ROM. (A CLI with phase 02c
#    keys the object on the source's path and the collision cannot arise; there this
#    check only ever EXPLAINS, and the note's "here" is what scopes it.)
#    Both strings are written by the same CLI, so comparing them literally is
#    meaningful and no path canonicalisation is involved.
#  * build diff — compiler and flag parity. A baseline built from a
#    `compile_commands.json` since regenerated away was otherwise kept and reported
#    at -52.4% ROM for an edit that added `t+=1;`.
disqualifier() {
    local psrc csrc diff_env match
    # Source identity. Skipped for a cargo crate on purpose: one object serves every
    # module of its target, so `source_file` there is just whichever module triggered
    # the build. Editing `src/util.rs` against a baseline captured from `src/main.rs`
    # of the SAME crate is a perfectly comparable pair, and comparing the field
    # produced a confident note about a stem collision that cannot happen in a crate.
    if [ "$KIND" != rust-cargo ]; then
        psrc=$(jq -r '.source_file // empty' "$META.prev" 2>/dev/null)
        csrc=$(jq -r '.source_file // empty' "$META" 2>/dev/null)
        if [ -z "$psrc" ] || [ -z "$csrc" ]; then
            # Unknown must not read as "fine". Objects are still stem-keyed, so this
            # is the one check that tells `modA/util.c`'s baseline from `modB`'s — and
            # a sidecar torn by the pre-edit hook's 8 s timeout is unreadable, which
            # looks exactly like this. Treating it as clean published the collision
            # under a note claiming the source had been verified.
            printf 'its build record does not say which source it was built from, so it cannot be told apart from another file'"'"'s baseline — objects still share a name per stem here. A truncated or torn sidecar reads exactly like this.'
            return
        fi
        if [ "$psrc" != "$csrc" ]; then
            printf 'it was built from %s, not %s. Two sources share one object name here, so it is another file'"'"'s baseline.' "$psrc" "$csrc"
            return
        fi
    fi
    # Compiler and flag parity. "Could not answer" is NOT "answered mismatch": a
    # failed or unparseable `build diff` was reported to the user as a toolchain
    # change, with a remediation command that fails the same way.
    diff_env=$(loci build diff --prev "$META.prev" --curr "$META" 2>/dev/null)
    match=$(jq -r 'if (.data? | objects | has("match")) then (.data.match | tostring) else empty end' \
            <<<"$diff_env" 2>/dev/null)
    if [ -z "$match" ]; then
        printf 'its build record could not be compared with this one — `loci build diff` returned no verdict, so one of the two sidecars is unreadable. A copy torn by the pre-edit hook'"'"'s timeout looks like this.'
        return
    fi
    if [ "$match" != true ]; then
        printf 'it was not built with this compiler and these flags, so a diff against it would measure the toolchain change as well as the edit. Run: loci build diff --verbose --prev "%s" --curr "%s"' \
            "$META.prev" "$META"
        return
    fi
}

# --- the header case: rebuild the Before instead of looking for one -----------
#
# Ordered after the ordinary pair on purpose. A real pre-edit OBJECT is normally the
# better Before: it was compiled from the tree as it actually stood, with no
# reproduction to lose the include search and no `__FILE__` shift.
#
# But "normally" is doing work there, and the review found the case where it is
# wrong — created BY this feature. `build snapshot` copies an object already on disk;
# it does not compile. So: edit `cfg.h`; the header route runs and its After compile
# writes a fresh `app.o` that already contains the header edit; then edit `app.c` in
# the same turn; the pre-edit hook snapshots that object as `app.o.prev`, stamped with
# this turn so every check passes. The `.c`'s delta then silently excludes the
# header's contribution. Before 06d that first compile never happened.
#
# Detecting it from here is not possible — nothing in the envelope says when the
# object was built — so the pair is still preferred and the ambiguity is STATED.
RECON_EXPLAINED=no
if [ "$RECONSTRUCT" = yes ] && [ -n "$PREV" ]; then
    note "this unit already had a pre-edit object, so that was used as the Before rather than rebuilding one from the edited header's captured text. If this unit was ALSO edited this turn, that object may already contain the header's effect (or predate both edits), so read the delta as the turn's, not as the header's alone."
fi
if [ "$RECONSTRUCT" = yes ] && [ -z "$PREV" ]; then
    RECON_EXPLAINED=yes
    if [ -z "$TURN" ]; then
        note "cannot rebuild a Before without a turn id: the pre-edit copies are stored per turn, so there is nothing to identify which capture to build against. The post-edit hook's reminder carries the id — pass it as --turn."
    elif [ "$CAN_RECONSTRUCT" != yes ]; then
        # No `/loci:setup` here. The pin is an exact `==`, so `/loci:setup` reinstalls
        # the SAME version — advising it sends the user round a loop that cannot
        # terminate until the pin itself moves.
        note "the installed loci is too old to rebuild a header edit's Before (it has no --baseline), so this is an After-only measurement. Nothing the user can run fixes it: the plugin pins one exact CLI version, and that version needs to move first."
        RECON_EXPLAINED=no   # fall through: a local .prev may still be adoptable
    else
        B_ERR=$(mktemp 2>/dev/null || printf '%s' "${TMPDIR:-/tmp}/loci-crb-b.$$")
        BASELINE_ERRFILE="$B_ERR"    # so the EXIT trap reaps it if we are killed
        # An explicit array, not `${CONTEXT:+--context "$CONTEXT"}`: that form is
        # subject to word splitting, so it breaks every path containing a space —
        # which on Windows is the norm, and is one of the six defects that made this
        # a script instead of a fenced recipe in the first place.
        B_ARGS=(--source "$SOURCE" --loci-target "$TARGET"
                --project-root "$PROJECT_ROOT" --turn "$TURN" --baseline)
        [ -n "$CONTEXT" ]       && B_ARGS+=(--context "$CONTEXT")
        [ -n "$COMPILER_PATH" ] && B_ARGS+=(--compiler-path "$COMPILER_PATH")
        # Forwarded so the rebuilt Before's sidecar records the phase it was built
        # for. Nothing compares the field today — `diff_metas` ignores it — but this
        # is the one artifact whose provenance is the entire point, and a sidecar
        # saying `preflight` (argparse's default) for an object built during a
        # post-edit measurement is a small lie in the wrong file.
        [ -n "$PHASE" ]         && B_ARGS+=(--phase "$PHASE")
        B_JSON=$(loci build compile "${B_ARGS[@]}" 2>"$B_ERR")
        B_OK=$(jq -r '.ok // false' <<<"$B_JSON" 2>/dev/null)
        if [ "$B_OK" = true ]; then
            B_OBJ=$(jq -r '.data.output // empty' <<<"$B_JSON")
            B_META=$(jq -r '.data.meta_file // empty' <<<"$B_JSON")
            if [ -z "$B_OBJ" ] || [ -z "$B_META" ]; then
                # `ok:true` with a missing or half-present pair. Every OTHER
                # no-baseline route in this file emits a note; without one here the
                # answer renders as an ordinary After-only measurement with nothing
                # to explain the missing Before — the chain going quiet again, in the
                # new code. The After has had an explicit guard for this exact shape
                # since 02b (`reported ok with no artifact path`); the Before did not.
                note "the rebuilt Before came back without a usable artifact path, so there is no Before. A truncated or torn envelope from a killed process looks like this."
            else
                PREV="$B_OBJ"; PREV_META="$B_META"
                # 06a's own verdict, surfaced rather than swallowed. `verified:false`
                # means the CLI could not PROVE the rebuild read the captured copies
                # — the object may still be right, but the one failure mode that
                # matters here (the reproduction lost the include search, so the
                # "Before" is really the After) is exactly what it could not rule out.
                if [ "$(jq -r '.data.baseline_reconstruction.verified // empty' <<<"$B_JSON")" != "true" ]; then
                    note "the rebuilt Before could not be verified as having read the pre-edit copies. If the reproduction lost the include search, this Before is the current build and the comparison will understate the change. Treat a reported 'no change' with suspicion."
                fi
            fi
        else
            B_CODE=$(jq -r '.error.code // empty' <<<"$B_JSON" 2>/dev/null)
            B_MSG=$(jq -r '.error.message // empty' <<<"$B_JSON" 2>/dev/null)
            # These two are ANSWERS, not failures, and conflating them with a broken
            # compile is why they are named here rather than left to the generic
            # `FAILED` path — which the runtime contract says is surfaced verbatim and
            # STOPS. `baseline_unaffected` is a true statement about the code; telling
            # the user their build is broken instead would be a wrong answer about
            # their edit.
            case "$B_CODE" in
                baseline_unaffected)
                    note "this translation unit does not read anything that was edited this turn, so its Before and After are the same build and there is nothing to compare. Report it as unaffected, not as a failed measurement." ;;
                baseline_not_reconstructible)
                    note "the pre-edit state of this translation unit could not be rebuilt, so there is no Before. ${B_MSG:-No reason was given.}" ;;
                *)
                    [ -n "$B_MSG" ] || B_MSG=$(tr '\n' ' ' <"$B_ERR" 2>/dev/null \
                        | sed 's/  */ /g; s/^ //; s/ $//')
                    note "rebuilding the Before failed: ${B_MSG:-loci build compile --baseline produced no envelope}" ;;
            esac
        fi
        rm -f "$B_ERR"
    fi
fi

if [ -z "$PREV" ]; then
    # Why the CLI reported no pair — from the CLI, when it says. A generation
    # carrying phase 02e reports exactly one of `output_prev` or
    # `baseline_withheld{code,reason}`, so a non-empty reason here is the OWNER's
    # answer about the candidate it actually examined, not this script's
    # reconstruction of one.
    #
    # Read as a POSITIVE test, never as an absence: an empty `WITHHELD` means only
    # "this CLI did not say", which is true of every generation before 02e —
    # including modern ones, since `--inherit-prev` and this landed in different
    # changes. So it SELECTS the branch below rather than being inferred from the
    # capability probe. (The mirror of that read is the recorded `role`-is-null
    # defect, where a criterion phrased around a field that may be absent passed
    # vacuously on exactly the installs most likely to have the problem.)
    #
    # Where the CLI does answer, this arm REPLACES the local ladder — including
    # the adoption arm at the bottom, which is the only place this script can put
    # a Before back. That is deliberate and it is the safe direction: the CLI saw
    # the capture marker, the turn and the digests, and adopting a candidate it
    # refused is the mismatched-pair defect 02a exists to prevent.
    #
    # Read INSIDE this block, not beside the compile: on the ordinary path (a pair
    # reported) the value is never used, and an unconditional `jq` there was
    # measured at +47 ms on every post-edit call.
    WITHHELD=$(jq -r '.data.baseline_withheld.reason // empty' <<<"$ENV_JSON" 2>/dev/null)
    if [ "$RECON_EXPLAINED" = yes ]; then
        # Ordered ABOVE the CLI's reason on purpose. Under `--reconstruct` the
        # absent `.prev` is the PREMISE — the unit was not itself edited — so the
        # CLI's "no pre-edit baseline was captured for this object" is a true
        # sentence about a state that is not the problem, and printing it beside
        # the reconstruct arm's own explanation gives the user two answers to one
        # question, the second one a distraction.
        :   # already explained above; the ordinary .prev advice does not apply here
    elif [ -n "$WITHHELD" ]; then
        # The CLI examined the candidate and said why. Relay it and stop: every
        # branch below re-derives a decision that has already been made, with less
        # evidence than the CLI had — it cannot see the capture marker, the turn, or
        # the digests, which is why its own fallback message could only name a
        # *likely* cause. Measured: this also drops a `loci build diff` subprocess,
        # worth ~700 ms, from the withheld post-edit path.
        note "$WITHHELD"
    elif [ "$KIND" = rust-cargo ] && [ "$MODERN" != yes ]; then
        # Do not second-guess it: this CLI reports the cargo pair on its own, so an
        # absent field here is its own refusal, and it is the informed one.
        note "no comparable baseline for this crate — the CLI reported none, and on a cargo project that is a deliberate refusal (the recorded package or target differs). Not overriding it."
    elif [ ! -f "$OBJ.prev" ] || [ ! -f "$META.prev" ]; then
        note "no pre-edit baseline was captured for this source, so there is no Before side."
    else
        # A candidate is on disk. Say what is wrong with it either way: a baseline
        # that vanishes without explanation is the failure this whole design is
        # about, and "absolute values only" with no reason reads to the user as the
        # tool having nothing to say.
        WHY=$(disqualifier)
        if [ -n "$WHY" ]; then
            note "baseline withheld: $WHY"
        elif [ "$MODERN" = yes ]; then
            note "baseline withheld by the CLI. Its source and build flags check out here, so the likely cause is its capture marker no longer matching its contents (a foreign writer, or a copy torn by the pre-edit hook's timeout), or a capture belonging to a different turn. Not overriding it."
        else
            PREV="$OBJ.prev"; PREV_META="$META.prev"
            note "baseline checked locally: this loci predates the CLI-side pair check, so the source and the build flags were verified here instead."
        fi
    fi
fi

# One caveat, both generations. `--turn` is the only thing that establishes the
# baseline belongs to THIS user turn; without it a capture left by a previous turn
# is accepted and the delta silently spans two turns. On a CLI predating the flag
# there is no marker to check at all, so the caveat is if anything stronger there.
if [ -n "$PREV" ] && { [ "$MODERN" != yes ] || [ -z "$TURN" ]; }; then
    note "the baseline's turn was NOT verified, so a capture left by a previous user turn would be accepted here — the delta could span two turns. Mention this if the numbers look larger than the edit."
fi

printf 'OBJ\t%s\nMETA\t%s\nPREV\t%s\nPREV_META\t%s\n' \
    "$OBJ" "$META" "$PREV" "$PREV_META"
[ -n "$NOTES" ] && printf '%s' "$NOTES"
exit 0
