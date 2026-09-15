#!/usr/bin/env bash
# LOCI plugin — forkless JSON reading and writing for the hooks.
#
# `jq` is a HOST tool the plugin does not bundle, so every hook that parsed its
# payload with one gated on `command -v jq` and skipped the whole hook where it
# was absent — a fresh machine got no guard, no baseline and no reminder, and
# said nothing about it. These helpers replace it in pure parameter expansion:
# no process at all, which is what `contract-guard.sh` needs above its prefilter
# and what keeps the edge hooks inside their timeouts.
#
# ⚠ THE BOUND IS NOT A TIDINESS RULE, it is the reason these are usable at all.
# Bash's `${v#*pat}` and `${v%%pat*}` are QUADRATIC in the offset of the match:
# measured on bash 5.2, one `${v#*"key"}` over a 160 KB payload costs 4.9 s and
# over 80 KB 1.2 s, against hook timeouts of 5 s and 8 s. `loci_json_load`
# therefore parses a bounded PREFIX (`LOCI_JSON_MAX`, 16 KB by default), which is
# exact for what the hooks read here: the harness puts `session_id`,
# `transcript_path`, `cwd`, `hook_event_name`, `tool_name` and
# `tool_input.file_path` ahead of the edit's content, and every LOCI envelope
# these read is a handful of short fields. Anything whose SIZE is the payload —
# a whole file, an applied diff — belongs in a `loci hook` verb, where Python
# parses it in one linear pass.
#
# WHAT THESE DO NOT DO: they read a NAMED KEY, not a path. `loci_json_get cwd`
# answers with the first `"cwd":` in the document at any depth. That is exact
# for these documents — every key the hooks read is unique in the prefix they
# read it from — and a document where it would not be wants the CLI.
#
# ⚠ FIRST IS LOAD-BEARING, and F19 is the record of finding that out by trying
# to change it. `json.loads` and `JSON.parse` answer a DUPLICATE NAME with the
# last key, so taking the last looks like agreement — and for two keys in one
# object it is. But these read a name at ANY DEPTH, and the CLI's envelope is
# `{"ok":…,"data":{…}}` precisely so a result field "can never collide with
# `ok`/`error`" (`src/loci/cli/_json.py`). FIRST-WINS IS WHAT DELIVERS THAT:
# the envelope's own fields are written before the nested ones. Taking the last
# hands a `data.*` field the answer instead — measured, on `loci contract draft
# show`, whose `ops[].entry` is a MODEL-AUTHORED object the CLI preserves
# unknown fields of: an entry carrying its own `ok` turned
# `draft-pending-nudge.sh`'s `[ "$(loci_json_get ok)" = "true" ]` into a silent
# exit. A depth-blind search cannot implement a parser's rule, so it does not
# pretend to: it keeps the rule that suits the documents, and the one caller
# whose VERDICT a duplicate would change asks `loci_json_dup` and refuses.
#
# A key name cannot be forged from a string value: inside a JSON string every
# `"` is escaped, so a value spelling `"cwd":` arrives as `\"cwd\":` and the
# search for `"cwd"` cannot match it.

# Idempotent guard — safe to source repeatedly / from multiple entry scripts.
[ -n "${_LOCI_JSON_SOURCED:-}" ] && return 0
_LOCI_JSON_SOURCED=1

# How many bytes of a document `loci_json_load` will parse. A caller with a
# smaller certainty (`contract-guard.sh` reads a payload that can be megabytes)
# lowers it; nothing needs to raise it.
: "${LOCI_JSON_MAX:=16384}"

# The whitespace class the scans skip, and the two sentinel bytes the unescaper
# parks an escape on. Resolved once, and spelled through variables rather than
# `$'…'` inline for the reason `_LOCI_CR` is: an editor or a patch that mangles
# the backslash leaves a literal byte inside `$'…'`, which bash does not read
# back as the escape.
#
# ⚠ SOH AND STX ARE RESERVED BYTES here, and a document carrying one raw is
# rewritten. JSON forbids a raw control character inside a string, so a payload
# with one is already malformed, and this has been true of SOH since the
# unescaper was written — but STX is NOT the same size of door, and the first
# draft of this comment said it was. SOH is rewritten one for one, to a
# backslash. STX is read as a `\u` marker, so it EATS FOUR CHARACTERS after it
# (`<STX>0041` becomes `A`) or invents a backslash that was never written
# (`<STX>zzzz` becomes `\uzzzz`), and `.loci/<STX>0063ontract.yaml` spells a
# guarded path the document does not contain. It is also conditional: a value
# with no backslash anywhere returns early and passes a raw STX through
# untouched. Five characters, not one — measured in F12's review round. The
# direction is toward more denies and the live path never sends one, but a
# future reader should not have to re-derive the blast radius.
_LOCI_JSON_WS=$' \t\n\r'
_LOCI_JSON_BS=$'\001'
_LOCI_JSON_U=$'\002'
# Two characters, and no quote among them: `_loci_json_string` replaces each
# escape with this, so the probe it searches is the same LENGTH as the document
# and an offset in one is an offset in the other.
_LOCI_JSON_FILL="$_LOCI_JSON_BS$_LOCI_JSON_BS"
# Hex, spelled out rather than as `[0-9A-Fa-f]`: a range inside a bracket
# expression is resolved by the LOCALE's collation order, and the hooks that
# source this run under both `LC_ALL=C` (route 2's byte accounting) and the
# ambient locale (route 1).
_LOCI_JSON_HEX='[0123456789ABCDEFabcdef]'
_LOCI_JSON_HEX4="$_LOCI_JSON_HEX$_LOCI_JSON_HEX$_LOCI_JSON_HEX$_LOCI_JSON_HEX"

# How much of the document the closing-quote search reads at a time. It looks
# for ONE character, so a window can never split what it is looking for, and
# the window is the whole point: `${v%%"*}` is quadratic in the offset of the
# match and takes 0.31 s to find a quote 64 KB in, against 0.008 s for the same
# answer found 4 KB at a time.
#
# Windowing does not make the find LINEAR — `${probe:$off:4096}` still costs a
# pass over the document per window, so it is Θ(n²/4096) and a full walk of
# 1 MB is 0.50 s. It is bounded by `LOCI_JSON_MAX` like everything else here,
# and at the guard's 64 KB it is 0.010 s, which is why 4096 is not tuned
# further.
_LOCI_JSON_WINDOW=4096

# The prefix an ESCAPED spelling of a name carries, and it is four characters
# rather than two. Two — a backslash and a `u` — sit in every Windows path a
# JSON document holds, because `C:\\users` spells them, and gating the second
# pass below on that would run it on every tool call in every Windows repo.
# Four narrows it to an escape naming an ASCII character, which is all a name
# these hooks read can be made of. It is the same arm, for the same reason and
# off the same measurement, as `contract-guard.sh`'s prefilter (F15).
_LOCI_JSON_UPFX='\u00'
# One literal backslash. Named rather than written inline because the spelling
# that means one differs between a quoted string, a `case` pattern and the
# pattern half of a `${v//…}`, and getting it wrong is silent.
_LOCI_JSON_BS1='\'

# What `_loci_json_unicode` may spend, summed over its jumps, before it stops
# decoding and leaves the rest of the escapes as they were written. Charged the
# way `contract-guard.sh` charges `_R2_MAX_SCAN`: each `\u` costs one pass over
# what is LEFT, so the price is escapes × length and a flat count of escapes
# would be the wrong meter.
#
# Measured on this machine, a value that is NOTHING BUT `\uXXXX` escapes, read
# end to end through `loci_json_get`, at four caps:
#
#     escapes  chars       3 M      6 M     12 M     24 M
#         500   3 000   0.072 s  0.075 s  0.074 s  0.077 s   (whole, every cap)
#       1 000   6 000   0.170 s  0.178 s  0.177 s  0.178 s   (whole, every cap)
#       4 000  24 000   0.197 s  0.354 s  0.497 s  0.903 s
#      10 900  65 400   0.778 s  0.886 s  1.019 s  1.398 s
#
# — against the 5 s in `hooks.json`, after which the hook is killed and
# PreToolUse fails OPEN. The last row is the ceiling: the contract guard sets
# `LOCI_JSON_MAX` to its own 64 KB, so no value read anywhere is longer.
#
# 6 M costs 0.886 s on that ceiling and decodes whole every value that is a
# PATH — a 280-character all-Cyrillic path is 0.014 s and a 4 KB value that is
# entirely escapes is 0.106 s, both complete. Note the floor the table shows at
# 3 M: below about this the cap stops buying anything, because what is left is
# the marker pass and the restore, not the loop.
#
# ⚠ THE CAP IS A DOOR, and it opens on the hole this decode was written to
# close — say so here rather than only in cost terms. Over it the remaining
# escapes stay as the six characters that were written, and for the contract
# guard's route 1 that reads as "matches no guarded file", which is ALLOW. The
# meter is escapes × REMAINING LENGTH, so the number of escapes needed falls as
# the value grows: ~187 of them anywhere in a 64 KB value trips it, not the
# 10 900 the ceiling row above might suggest. A `file_path` padded to 12 KB
# with escaped `./` — which `realpath -m` collapses back to the guarded file —
# is the shape, and it is ALLOWED. Measured in F12's review round.
#
# IT IS STILL THE RIGHT TRADE, and the reason is which fail-open each side
# buys. Raising the cap to 24 M closes that shape and costs 1.398 s on the
# ceiling, where route 2 can spend 0.95 s of the same 5 s budget — and past
# 5 s the hook is KILLED and PreToolUse fails open too. So both directions end
# in a fail-open, and they are not equally reachable: the wall-clock one is
# F13, which an ORDINARY PASTE from Claude Code reaches, while the cap one
# needs a producer that escapes ASCII — the shape above escapes `.` and `/`,
# and `JSON.stringify` escapes neither, nor does `json.dumps`, which escapes
# only non-ASCII (that was F12, and it is far under this cap). The cap is
# sized for the attack that is reachable.
#
# RE-EXAMINED FOR F15 WITH F14 IN HAND, and LEFT AS IT IS — that was the
# decision F15 was told to make, and the condition for moving the number was
# that `lib/loci_json.sh` stop being O(escapes x length). It has not: F14
# windowed `_loci_json_seek`, and the loop below still takes one slice of the
# remainder per escape, so raising the cap buys the wall-clock fail-open back.
# Door 1 of F15 — the contract guard's prefilter exiting above this decode —
# is closed instead, in `hooks/contract-guard.sh`.
#
# ⚠ AND THE 25 s IN F15'S OWN TABLE WAS NOT THIS CAP. It read "ALLOW 25.43 s"
# against a `./`x1000 pad and credited both the verdict and the time here. The
# verdict is the cap's; the time is `realpath`, which on MSYS is super-linear
# in the number of path COMPONENTS — and the escapes this branch hands back are
# full of backslashes, which `contract-guard.sh`'s Windows mask turns into
# components. Timed per stage the decode is flat at 0.15 s across the rows that
# read 0.5 s, 2.5 s and 25 s. That half is F16; nothing here changes for it.
# Attributing a cost to the wrong line is how a cap gets moved for no gain, so:
# measure the stage, not the payload.
#
# WHAT IS LEFT HERE, named so it is not rediscovered: the hand-back below is
# itself O(k x n) — one `${rest//marker/\u}` copying the tail once per
# replacement — and it is the cheaper half of what this cap costs the contract
# guard, the dearer half being the `realpath` above. It is the next cut if this
# file ever has to be cheaper; it is not taken now because a value this branch
# has given up on no longer spells any guarded path, so the cost buys an ALLOW
# that was always going to be an ALLOW.
_LOCI_JSON_UMAX=6000000

_LOCI_JSON_DOC=""
_LOCI_JSON_AT=""
# Where `_loci_json_rewrite` leaves its copy. A global rather than a
# substitution because `loci_json_get` and its relatives are already called
# from inside a `$( )` and a second one would fork per read — and because
# `loci_json_dup` is NOT called from one, so a substitution there would add the
# fork the rest of this file exists to avoid. Cleared by the caller that takes
# it, and on entry, so no path leaves a copy of the document behind.
_LOCI_JSON_REWRITE=""
# Whether `loci_json_load` dropped anything. A caller that reads a field the
# harness does not put in the prefix can say so instead of reading an absent
# key as a recorded empty value.
_LOCI_JSON_TRUNCATED=""

# Public API: loci_json_load <json-text>
loci_json_load() {
    _LOCI_JSON_AT=""
    _LOCI_JSON_TRUNCATED=""
    if [ "${#1}" -gt "$LOCI_JSON_MAX" ]; then
        _LOCI_JSON_DOC="${1:0:$LOCI_JSON_MAX}"
        _LOCI_JSON_TRUNCATED=1
    else
        _LOCI_JSON_DOC="${1:-}"
    fi
}

# The text just past `"<key>" :`, or non-zero. An occurrence of the name that is
# a string VALUE rather than a key (`"tool_name": "file_path"`) is skipped —
# the colon is what makes it a key.
#
# THE SEARCH IS WINDOWED, for the reason `_loci_json_string`'s find is. The walk
# this replaced re-sliced the document once per occurrence of the NAME that is
# not a key, so it was quadratic in that count. Measured at the guard's own
# 64 KB cap, with the name as an array element — the walk, then this:
#
#      occurrences   chars      walk    windowed    the live shapes, for scale
#              500   3 027   0.053 s     0.011 s    key at the front  0.002 s
#            1 000   6 027   0.176 s     0.040 s    key ABSENT        0.001 s
#            2 000  12 027   0.638 s     0.087 s    key behind 60 KB  0.041 s
#            4 000  24 027   2.440 s     0.194 s
#            8 000  48 027   9.497 s     0.406 s
#
# and the shape that costs most — the name with whitespace after it, so the
# pattern below has a run to backtrack over — 9.310 s against 1.914 s. The point
# is the CLASS, not the constant: sixteen times the input costs the walk 211
# times the work and this 13.5 times, which is linear.
#
# Reaching it needs the name thousands of times UNESCAPED and with no colon
# after it, which a string value cannot do — inside one every `"` arrives as
# `\"`, so `"cwd"` is spelled `\"cwd\"` and does not match at all. It takes a
# model-controlled ARRAY OF STRINGS, for a key otherwise absent from the
# payload; `hooks.json` then kills the hook at 5 s and PreToolUse fails OPEN.
#
# WHY NOT A WORK CAP, recorded as a decision rather than left open: over a cap
# this function can only answer "key absent", which is a silent ALLOW in
# `contract-guard.sh` and a silent "nothing recorded" in six other hooks — one
# fail-open traded for another. A window keeps the answer exact.
#
# THE WINDOW BOUNDARY IS THE WHOLE CORRECTNESS ARGUMENT, and it is harder here
# than in `_loci_json_string`, which looks for ONE character a window cannot
# split. Two things can straddle an edge, and they are handled separately:
#
#   1. THE KEY ITSELF, `"<key>"`, several characters wide. The step overlaps by
#      `klen`, so a key cut by one edge is whole inside the next window.
#   2. THE WHITESPACE between the key and its colon, which has NO width to
#      overlap by — `"cwd"` then 9 000 spaces then `:` is a legal key. The
#      second glob catches exactly that: a window ENDING in the key and
#      whitespace, resolved against the document rather than against the
#      window. It is guarded by a one-character test, so a window that cannot
#      hold such a candidate never pays for the glob.
#
# Both globs are parameter expansions and NOT `case` patterns, which is forced
# rather than chosen: bash parses a function's body when the function is
# DEFINED, so an extglob `*(…)` in a `case` here is a syntax error unless
# extglob is already on at parse time. A parameter expansion takes the setting
# at EXPANSION time instead, which is what this wrapper turns on and puts back.
#
# ⚠ IT READS BYTES, AND THAT IS CORRECTNESS AND NOT SPEED. Every offset below
# is computed from one window and then used to index the DOCUMENT, so the two
# have to agree on what an offset means. Under a multibyte locale they do not:
# a RAW INVALID BYTE desynchronises bash's scan, and `${doc:off:4096}` then
# comes back short, or shifted, or both — measured on Git Bash under
# `en_US.UTF-8`, where a 4 094-character window was returned for a 4 096
# request with 9 000 characters still to go. Two ways to be wrong, both found
# in F14's review round: the short window read as end-of-document and the key
# answered ABSENT — a silent ALLOW in `contract-guard.sh` — and the shifted one
# put `_LOCI_JSON_AT` two characters early, so `loci_json_get file_path`
# returned `: ` instead of the path.
#
# `local LC_ALL=C` makes every length and every slice a BYTE count, which
# cannot desynchronise, and bash puts the locale back when the local goes out
# of scope (a plain assignment would leak it to the seven hooks that source
# this). It changes no answer: the patterns here are ASCII literals and a
# four-character whitespace set, `_LOCI_JSON_AT` is a slice of the document
# either way, and route 1's case folding — the one thing that DOES need the
# ambient locale — happens in `contract-guard.sh` on the value this returns,
# not in here. It is also faster, which is why it was not noticed as missing.
#
# This is the same hazard `_loci_json_string`'s length assertion exists for,
# and the test above it (`test_an_invalid_byte_cannot_make_the_read_over_run_
# the_value`) is the one that named the class: 542 of 4 000 random byte-soup
# values under `en_US.UTF-8`, none under `LC_ALL=C`.
#
# ⚠ AND `_loci_json_seek_win` LOOKS FOR THE LITERAL TEXT `"<key>"`, which is
# not what a JSON key is. A NAME may be spelled with escapes exactly as a value
# may — `"\u0066ile_path"` is read as `file_path` by `json.loads` and by
# `JSON.parse` — and for one release this function answered ABSENT for every
# one of those. That is a silent ALLOW in `contract-guard.sh` (no `file_path`,
# so route 1 never runs, and the guarded file is written) and a silent "nothing
# recorded" in the six other hooks that read a field through here. Filed and
# closed as F17; `_loci_json_seek_escaped` is the close, and carries the cost
# argument for why it is a second pass rather than a wider first one.
#
# WHICH SPELLING WINS WHEN BOTH ARE PRESENT is a decision, not an accident, and
# it is written down because a guard cannot rest on "whichever the search found
# first": **a name used as a key in its LITERAL spelling is the answer wherever
# in the document it sits; an escaped spelling is read only when no literal one
# is used as a key anywhere.** Two keys spelling one name is a duplicate name,
# which RFC 8259 leaves to the parser, and `json.loads` and `JSON.parse` both
# take the LAST. So `{"file_path":"a","\u0066ile_path":"b"}` answers `a` here and
# `b` there, while `{"\u0066ile_path":"a","file_path":"b"}` answers `b` in all
# three. Preferring the literal is therefore CLOSER to both parsers than
# preferring the first in document order would be — it agrees in one of the two
# orders where first-in-order agrees in neither — which is why it is the rule
# and not merely the cheap one.
#
# The residue is this library's own first-wins rule and not an escape defect:
# two LITERAL `"file_path"` keys already answer with the first where both
# parsers answer with the last, measured on `dea825d`. Closing that means never
# stopping at the first key, which is exactly what F14 bought. Recorded here,
# and filed, rather than fixed under this one.
_loci_json_seek() {
    local LC_ALL=C
    local _eg=0 _rc
    shopt -q extglob || { _eg=1; shopt -s extglob; }
    _loci_json_seek_win "$1"; _rc=$?
    [ "$_rc" -eq 0 ] || { _loci_json_seek_escaped "$1"; _rc=$?; }
    [ "$_eg" -eq 1 ] && shopt -u extglob
    return "$_rc"
}

# `_LOCI_JSON_AT` = the document from $1, leading whitespace stripped a window
# at a time. Bounded for the same reason as everything else here: `${v%%[!ws]*}`
# over the rest of a 64 KB document is one pass per call, and a value sitting
# behind 60 KB of whitespace cost 3.499 s before this was windowed, against
# 0.026 s after.
_loci_json_seek_at() {
    local at=$1 sl ws
    while :; do
        sl="${_LOCI_JSON_DOC:$at:$_LOCI_JSON_WINDOW}"
        ws="${sl%%[!$_LOCI_JSON_WS]*}"
        at=$(( at + ${#ws} ))
        [ "${#ws}" -eq "${#sl}" ] && [ -n "$sl" ] || break
    done
    _LOCI_JSON_AT="${_LOCI_JSON_DOC:$at}"
}

_loci_json_seek_win() {
    local key="\"$1\"" klen off=0 wlen step win pre tail ws abs nxt
    klen=${#key}
    _LOCI_JSON_AT=""
    # The membership test first, and it is not redundant — it IS the hot path.
    # `file_path` is absent from every Bash payload the guard reads, and one
    # glob answers that in 0.001 s where walking every window to find nothing
    # costs 0.219 s.
    case "$_LOCI_JSON_DOC" in
        *"$key"*) ;;
        *) return 1 ;;
    esac
    wlen=$_LOCI_JSON_WINDOW
    [ "$wlen" -gt $(( klen + klen )) ] || wlen=$(( klen + klen ))
    step=$(( wlen - klen + 1 ))
    while :; do
        win="${_LOCI_JSON_DOC:$off:$wlen}"
        [ -n "$win" ] || return 1
        # The key, any whitespace, and its colon — all inside this window. ONE
        # glob whatever the window holds: an occurrence that is a string VALUE
        # rather than a key (`"tool_name": "file_path"`) has no colon after it,
        # and the pattern steps over it without a turn of the loop. That is the
        # fix — the walk took a turn, and a re-slice, for each one.
        pre="${win%%"$key"*([$_LOCI_JSON_WS]):*}"
        if [ "${#pre}" -ne "${#win}" ]; then
            tail="${win:$(( ${#pre} + klen ))}"
            ws="${tail%%[!$_LOCI_JSON_WS]*}"
            _loci_json_seek_at $(( off + ${#pre} + klen + ${#ws} + 1 ))
            return 0
        fi
        # Boundary case 2, above. A candidate can only exist when the window
        # ends in whitespace, or ends with the key itself.
        abs=-1
        case "${win: -1}" in
            [$_LOCI_JSON_WS])
                pre="${win%%"$key"*([$_LOCI_JSON_WS])}"
                [ "${#pre}" -eq "${#win}" ] || abs=$(( off + ${#pre} + klen ))
                ;;
            *)
                case "$win" in
                    *"$key") abs=$(( off + ${#win} )) ;;
                esac
                ;;
        esac
        if [ "$abs" -ge 0 ]; then
            # Where the search resumes if this candidate is NOT a key, fixed
            # before the whitespace walk moves `abs`. One character back from
            # the end of the key, because a key name cannot hold an unescaped
            # quote: the only way two occurrences of `"<key>"` can overlap is
            # the closing quote doubling as the next opening one. Resuming at
            # `abs` instead skipped that occurrence — `…"cwd"cwd" : 1` answered
            # absent. It still advances by at least `klen - 1`, so the loop
            # cannot stall, and it cannot crawl over a long whitespace run
            # either: the run is behind `nxt`, not in front of it.
            nxt=$(( abs - 1 ))
            while :; do
                tail="${_LOCI_JSON_DOC:$abs:$wlen}"
                ws="${tail%%[!$_LOCI_JSON_WS]*}"
                abs=$(( abs + ${#ws} ))
                [ "${#ws}" -eq "${#tail}" ] && [ -n "$tail" ] || break
            done
            case "${_LOCI_JSON_DOC:$abs:1}" in
                :) _loci_json_seek_at $(( abs + 1 )); return 0 ;;
            esac
            off=$nxt
            continue
        fi
        # The end of the document is asked of the DOCUMENT, never inferred from
        # a window coming back shorter than it was asked for. Under `LC_ALL=C`
        # the two agree; the inference was still the wrong thing to write, and
        # it was half of the invalid-byte defect above.
        [ $(( off + ${#win} )) -lt "${#_LOCI_JSON_DOC}" ] || return 1
        off=$(( off + step ))
    done
}

# The same search, over a copy of the document in which every escape that
# spells a character of `$1` has been decoded. A SECOND PASS, and the shape is
# the whole cost argument:
#
#   * the common payload pays ONE GLOB. `_loci_json_seek_win` answers first and
#     this is never entered when it does; and when it is entered — `file_path`
#     is absent from every Bash payload the contract guard reads, so that is
#     every one of them — the `\u00` gate answers "nothing escaped here"
#     without a walk, in 0.0003 s at the guard's 64 KB cap.
#   * a payload that DOES carry `\u00` pays one rewrite and one more windowed
#     search, and the rewrite is charged PER NAME: nothing is cached between
#     reads, because every field read in `hooks/` and `lib/` but one is a
#     command substitution and a cache built inside `$( )` dies with the
#     subshell. So the bound is (absent names) x (one rewrite), and which cap a
#     hook reads under is what keeps it affordable — see the table below.
#
# ⚠ EVERY NUMBER HERE IS `LC_ALL=C`, WHICH IS WHAT `_loci_json_seek` SETS.
# Saying so because the first draft of this comment did not, and quoted the
# ambient-locale figures: bash's parameter expansion is about five times slower
# under `en_US.UTF-8`, so the claim below reads 0.648 s there and 0.131 s in the
# locale the code actually runs in. Measuring the right stage in the wrong
# locale is the same class of mistake as measuring the wrong stage.
#
# Measured at the guard's 64 KB cap, min of 5, in-process:
#
#     stage                              worst document      no matches
#     the `\u00` gate                       0.0003 s          0.0003 s
#     claim `\\` onto the sentinel         0.131 s           0.010 s
#     the 11 substitutions `file_path` takes  0.051 s           0.014 s
#     put the sentinel back                   0.136 s           (not run)
#
# and end to end through `contract-guard.sh`, which reads THREE names of which
# two are absent: 1.406 s against the 5 s `hooks.json` gives it, where `main`
# is 0.860 s. Twelve names over the same document cost +0.265 s at the 16 KB
# DEFAULT the edge hooks read under, and +2.781 s at 64 KB — a combination
# nothing reaches, because `contract-guard.sh` is the only caller that raises
# `LOCI_JSON_MAX` and it reads three names. Anyone raising the cap elsewhere
# owes that arithmetic again.
#
# ⚠ AND THE CLASS IS Θ(n²) IN THE BOUNDED LENGTH, not linear — said plainly
# because `${v//pat/rep}` has looked linear to this file twice before
# (`_loci_json_string`'s "AND IT IS STILL O(k·n)", `loci_json_count`'s "reads
# better and is CUBIC"). Doubling the document roughly quadruples every stage,
# measured under `LC_ALL=C`:
#
#     document      8 KB     16 KB     32 KB     64 KB
#     the claim   0.013 s   0.019 s   0.042 s   0.131 s
#     the subs    0.011 s   0.013 s   0.021 s   0.051 s
#
# `LOCI_JSON_MAX` is what makes that affordable, exactly as it is for
# `_loci_json_string`. It is not a walk per occurrence — the loop below is over
# the NAME's characters, a fixed eleven for `file_path`, whatever the document
# holds.
#
# ⚠ IT DOES NOT DECODE THE DOCUMENT TO SEARCH IT. `_loci_json_unicode` is
# O(escapes x length) — the cap above it exists for that — and running it over
# 64 KB to find a key would give back twice what F14 bought. This decodes only
# the handful of escapes that could spell THIS name, one fixed-string `${v//…}`
# each, which is why the loop below is over the name's characters and not over
# the document's escapes.
#
# ⚠ AND ITS DECODING IS NOT CHARGED TO `_LOCI_JSON_UMAX`. That cap stops
# `_loci_json_unicode` after escapes x length of work and hands the rest back
# as written; the substitutions here are not metered at all. So one value can
# be read two ways: at the guard's cap a 64 KB value holding 400 `\u0063`
# escapes decodes 97 of them under a LITERAL `command` key and all 400 under an
# escaped one. The direction is toward MORE decoding, so toward more matches
# and more denies, and the cap is a documented ALLOW door — but its width now
# depends on how the key was spelled, which is worth knowing before either
# number is moved. Pinned in
# `test_the_second_pass_decodes_past_the_cap_the_first_one_stops_at`.
#
# THE ORDER IS `_loci_json_unwrap`'S ORDER, for its reason: `\\` is claimed FIRST,
# onto the sentinel pair, because `C:\\u0066oo` is an escaped backslash and then
# the LETTERS `u0066oo`, and decoding the escape sitting inside it invents a
# `\f` — a form feed — the document never carried. Claiming pairs the backslashes
# off left to right, which is how JSON reads them, and the sentinel is two
# characters wide so nothing moves.
#
# THE CLAIM IS GATED, because it is the dearer half and the gate is exact:
# with no `\\u00` anywhere, no `\u00` in the document has a backslash in front of
# it, so none can be the tail of a pair, so there is nothing to claim. The undo
# is gated with it.
#
# ⚠ AND A DOCUMENT THAT DOES TRIP THE GATE HAS SOH AS A RESERVED BYTE — say it
# here, because the first draft of this comment claimed the gating made that
# impossible and it does not. The claim parks on a PAIR of SOH bytes, so a pair
# the document itself carried comes back out as `\\`: `{"z":"q\\u0041",
# "\u0074arget":"<SOH><SOH>x"}` reads `\x` where the same document with a literal
# `target` reads the two SOH bytes. `_loci_json_unwrap`'s own note is the first
# record of this class and it is per-VALUE and skipped when a value holds no
# backslash, so it does not cover this; this is a second, narrower place —
# narrower because it needs the `\\u00` gate to trip as well. JSON forbids a raw
# control character inside a string, so such a document is rejected outright by
# `json.loads` and by `JSON.parse` and names no file at all. Pinned in
# `test_a_raw_sentinel_pair_survives_only_a_document_that_misses_the_claim`.
#
# ONLY A NAME OF `[A-Za-z0-9_]` IS SEARCHED FOR THIS WAY, and the restriction
# is load-bearing three times over: the escape table is built with
# `printf '%02x'`, which reads a BYTE, so a non-ASCII name would be escaped
# wrong AND could answer a key that is not there (`{"\u00c3\u00a9":"v"}` is the
# two-character name `Ã©`, and a byte-wise table finds `é` in it); a name
# holding a `"` or a backslash can be spelled with a SHORT escape as well as a
# `\uXXXX` one, which this does not look for; and pre-decoding either of those two
# inside a VALUE is the one thing that could forge or move a string boundary.
# Declining is the OLD behaviour for such a name, not a new hole. Pinned in
# `test_a_name_outside_the_alphabet_is_declined_rather_than_answered_wrongly`,
# and every name the seven field-reading scripts ask for is inside it, pinned by
# `test_every_name_the_hooks_read_is_one_the_escaped_pass_can_see`.
#
# ⚠ `[!A-Za-z0-9_]` IS A RANGE INSIDE A BRACKET EXPRESSION, which this file
# refuses everywhere else (`_LOCI_JSON_HEX` spells its sixteen characters out,
# because a range is resolved by the LOCALE's collation order). It is safe here
# only because `_loci_json_seek` holds `local LC_ALL=C` over this whole call,
# which is the same thing that makes every length below a byte count.
_loci_json_seek_escaped() {
    _loci_json_rewrite "$1" || return 1
    # Dynamic scope, the instrument `local LC_ALL=C` above is: the search reads
    # the document out of this global, so shadowing it here makes the rewrite
    # visible to the search and gone again on return. The VALUE `_LOCI_JSON_AT`
    # then carries is a slice of the rewritten copy, which is what the caller
    # wants — every difference between the two is an escape `_loci_json_unwrap`
    # would have decoded to the same character anyway (up to `_LOCI_JSON_UMAX`,
    # which is the exception recorded above), and the two it must not
    # pre-decode, a quote and a backslash, are exactly the two a name of
    # `[A-Za-z0-9_]` cannot contain.
    local _LOCI_JSON_DOC="$_LOCI_JSON_REWRITE"
    _LOCI_JSON_REWRITE=""
    _loci_json_seek_win "$1"
}

# `_LOCI_JSON_REWRITE` = the document with every escape that spells a character
# of $1 decoded, or non-zero and nothing written when there is no such escape
# to find (or the name is one this cannot spell). Split out of the pass above
# so `loci_json_dup` can COUNT over the same copy the search reads — one
# rewrite, one definition of what an escaped spelling of a name is.
#
# ⚠ IT RETURNS A GLOBAL, not a substitution, and that is forced: every caller
# is already inside a `$( )` and a second one here would fork per read. The
# caller shadows `_LOCI_JSON_DOC` with it and clears it.
_loci_json_rewrite() {
    local key="$1" doc seen="" claimed=0 i c hh hu a b
    _LOCI_JSON_REWRITE=""

    # The name first: it is a handful of characters and the gate under it is a
    # glob over the whole document, so a name this pass cannot spell should not
    # pay for one.
    case "$key" in
        ""|*[!A-Za-z0-9_]*) return 1 ;;
    esac
    case "$_LOCI_JSON_DOC" in
        *"$_LOCI_JSON_UPFX"*) ;;
        *) return 1 ;;
    esac

    doc="$_LOCI_JSON_DOC"
    case "$doc" in
        *"$_LOCI_JSON_BS1$_LOCI_JSON_UPFX"*)
            doc="${doc//"$_LOCI_JSON_BS1$_LOCI_JSON_BS1"/"$_LOCI_JSON_FILL"}"
            claimed=1 ;;
    esac
    i=0
    while [ "$i" -lt "${#key}" ]; do
        c="${key:$i:1}"
        i=$(( i + 1 ))
        # A name may repeat a character. The second pass over it would find
        # nothing, and cost a pass over the document to discover that.
        case "$seen" in *"$c"*) continue ;; esac
        seen="$seen$c"
        # Both hex spellings of the byte, and only the ones that differ:
        # `\u006C` and `\u006c` are one character and JSON calls neither
        # canonical. The `00` cannot vary — those are digits — and neither can
        # the `u`: RFC 8259 spells the escape with a lowercase one, so
        # `\U0063` names no character and `json.loads` rejects the document.
        #
        # The OUTER loop runs once for every name this pass accepts: the first
        # hex digit of a byte in `[A-Za-z0-9_]` is 3-7, always a digit, so it
        # has no second spelling. It is written for both anyway because the
        # restriction above is the only thing making that true.
        printf -v hh '%02x' "'$c"
        printf -v hu '%02X' "'$c"
        for a in "${hh:0:1}" "${hu:0:1}"; do
            for b in "${hh:1:1}" "${hu:1:1}"; do
                doc="${doc//"$_LOCI_JSON_UPFX$a$b"/"$c"}"
                # `if`, not `[ … ] && break`: that leaves the loop body at
                # status 1 on the turn it does not break, and nine scripts
                # SOURCE this file — `lib/detect-project.sh` runs `set -eu`.
                if [ "${hh:1:1}" = "${hu:1:1}" ]; then break; fi
            done
            if [ "${hh:0:1}" = "${hu:0:1}" ]; then break; fi
        done
    done
    # Only what the claim parked, and only when it ran.
    if [ "$claimed" -eq 1 ]; then
        doc="${doc//"$_LOCI_JSON_FILL"/"$_LOCI_JSON_BS1$_LOCI_JSON_BS1"}"
    fi
    _LOCI_JSON_REWRITE="$doc"
}

# The escapes JSON defines, and the ORDER IS THE WHOLE CORRECTNESS ARGUMENT.
#
# `\\` is claimed FIRST, onto a sentinel byte, because it is the one escape whose
# payload can look like the start of another: a Windows path arrives as
# `C:\\proj\\firmware`, and resolving `\f` before `\\` reads the second backslash
# of `\\f` as a form-feed escape — `C:\proj\<FF>irmware`, a path that then matches
# no guarded file. Measured, in the contract guard, on the spelling a real
# payload carries. Do not "simplify" this back into one pass.
#
# `\uXXXX` is claimed SECOND, onto the other sentinel, and decoded LAST — after
# the first sentinel has been turned back into a backslash. Both halves of that
# matter:
#
#   * claimed early, so `\\u0041` — an escaped backslash and then the letters
#     `u0041` — is not read as a `\u` escape. The `\\` above has already taken
#     the backslash by then, and the marker pass finds nothing to take.
#   * decoded last, so nothing re-reads what it emits. `\u005C` is a BACKSLASH
#     arriving as an escape: emitted while the chain is still running, the
#     `\u005Cn` in a Windows path would be re-read as `\n` and become a
#     newline. Nothing runs after the decode, so it cannot.
#
# It used to be left as written, on the grounds that `json.dumps(ensure_ascii=
# False)` and `jq -r` emit it for C0 control characters only. That was true of
# the PRODUCERS this reads today and false as a rule: `json.dumps` escapes every
# non-ASCII character by DEFAULT, and the jq the three-rung field read used to
# run at the top decoded `\uXXXX`. So a path like `…/Проект/.loci/contract.yaml`
# arriving in escaped form matched no guarded file and the contract guard let
# the write through — a regression on every machine that had jq, filed as F12.
_loci_json_unwrap() {
    local s="$1"
    case "$s" in
        *\\*) ;;
        *) printf '%s' "$s"; return 0 ;;
    esac
    s="${s//\\\\/$_LOCI_JSON_BS}"
    s="${s//\\u/$_LOCI_JSON_U}"
    s="${s//\\n/$'\n'}"
    s="${s//\\t/$'\t'}"
    s="${s//\\r/$'\r'}"
    s="${s//\\b/$'\b'}"
    s="${s//\\f/$'\f'}"
    s="${s//\\\//\/}"
    s="${s//\\\"/\"}"
    s="${s//$_LOCI_JSON_BS/\\}"
    case "$s" in
        *"$_LOCI_JSON_U"*) _loci_json_unicode "$s"; s="$_LOCI_JSON_UOUT" ;;
    esac
    printf '%s' "$s"
}

# Every `\uXXXX` in "$1", as UTF-8; sets `_LOCI_JSON_UOUT`. Called with the
# escapes already parked on `$_LOCI_JSON_U` by `_loci_json_unwrap`, which is the
# only caller.
#
# THE BYTES ARE BUILT BY HAND, and that is not fussiness. `printf '\uXXXX'` is a
# bash builtin and converts through the CURRENT LOCALE's charmap; the contract
# guard sets `LC_ALL=C` for route 2's byte accounting and restores the ambient
# locale for route 1, so both are reachable from the same library. Measured on
# this machine's bash 5.3, under `LC_ALL=C`:
#
#   * `printf '\U0001F600'` emits the TEN CHARACTERS of the escape, in every
#     spelling tried — so the astral plane, which is exactly what a surrogate
#     pair decodes to, breaks in the locale the guard sets.
#   * `printf '\u041f'` emits UTF-8 when bash is started from another MSYS
#     process, and the LITERAL `\u041f` when a native process starts `bash.exe`
#     with `LC_ALL=C` in its environment — which is how the pytest harness
#     launches these hooks, and how Claude Code launches them on Windows. So
#     even the BMP half of the builtin is not a property of the machine, it is
#     a property of who spawned the shell. Re-measured in F12's review round,
#     which found the first draft of this comment recording only the
#     MSYS-spawned half, as a flat fact.
#
# `printf '\xNN'` is the same bytes under every locale and every spawn, and
# `printf -v` keeps it forkless.
#
# What comes back is therefore UTF-8 BYTES whatever the locale, which is what
# the filesystem gives a path comparison on both Linux and Git for Windows. In
# a UTF-8 locale bash reads them as characters; under `C` it reads them as
# bytes; a comparison against another UTF-8 path is the same answer either way.
#
# Three shapes are deliberately NOT decoded, and each is left as the six
# characters that were written rather than turned into something else:
#
#   * a LONE SURROGATE (`\uD83D` with no low half after it, or a low half with
#     no high one). It encodes no character; CESU-8 style bytes for one are
#     invalid UTF-8 and would match nothing on any filesystem.
#   * an escape whose four characters are NOT HEX, and an escape cut short by
#     the prefix `loci_json_load` parses. Both are the truncation case, and the
#     honest answer to "I could not read this" is to hand back what was there.
#
# and one is decoded to NOTHING: `\u0000`. A bash string cannot hold a NUL —
# it is the terminator — so the choice is between dropping the character and
# truncating the value at it. Dropping is the direction the guard needs:
# `.loci/contract\u0000.yaml` drops to `.loci/contract.yaml` and is DENIED,
# where truncating leaves `.loci/contract`, which matches no guarded file.
# ONE SLICE OF `rest` PER ESCAPE, and that is what the cap is set against.
# Dropping the front of a string copies what is left of it, so an escape that
# took three of them — past the marker, past the hex, past a low surrogate —
# paid three times over. The advance is decided first and taken once.
_LOCI_JSON_UOUT=""
_loci_json_unicode() {
    local rest="$1" out="" pre hex nxt cp lo b at adv work=0 len=${#1}
    while :; do
        case "$rest" in
            *"$_LOCI_JSON_U"*) ;;
            *) out+="$rest"; break ;;
        esac
        pre="${rest%%"$_LOCI_JSON_U"*}"
        at=${#pre}
        len=$(( len - at - 1 ))
        # Charged before the work is done, the way route 2 charges a jump:
        # this escape costs one pass over what is LEFT, and so will the next.
        work=$(( work + len ))
        if [ "$work" -gt "$_LOCI_JSON_UMAX" ]; then
            rest="${rest:$at}"
            out+="$pre${rest//"$_LOCI_JSON_U"/\\u}"
            break
        fi
        hex="${rest:$(( at + 1 )):4}"
        b=""
        adv=$(( at + 1 ))
        case "$hex" in
            $_LOCI_JSON_HEX4)
                adv=$(( adv + 4 ))
                cp=$(( 16#$hex ))
                if [ "$cp" -ge 55296 ] && [ "$cp" -le 56319 ]; then
                    # A high surrogate is a character only with its low half
                    # IMMEDIATELY after it — `😀` is one emoji, not
                    # two characters. D800–DBFF then DC00–DFFF.
                    nxt="${rest:$adv:5}"
                    case "$nxt" in
                        "$_LOCI_JSON_U"[Dd][CDEFcdef]$_LOCI_JSON_HEX$_LOCI_JSON_HEX)
                            lo=$(( 16#${nxt:1:4} ))
                            cp=$(( 65536 + (cp - 55296) * 1024 + (lo - 56320) ))
                            adv=$(( adv + 5 ))
                            ;;
                        *) b="\\u$hex"; cp=-1 ;;
                    esac
                elif [ "$cp" -ge 56320 ] && [ "$cp" -le 57343 ]; then
                    b="\\u$hex"; cp=-1
                fi
                ;;
            *) b="\\u"; cp=-1 ;;
        esac
        if [ "$cp" -gt 0 ]; then
            if [ "$cp" -lt 128 ]; then
                printf -v b '\\x%02x' "$cp"
            elif [ "$cp" -lt 2048 ]; then
                printf -v b '\\x%02x\\x%02x' \
                    $(( 192 | (cp >> 6) )) $(( 128 | (cp & 63) ))
            elif [ "$cp" -lt 65536 ]; then
                printf -v b '\\x%02x\\x%02x\\x%02x' \
                    $(( 224 | (cp >> 12) )) $(( 128 | ((cp >> 6) & 63) )) \
                    $(( 128 | (cp & 63) ))
            else
                printf -v b '\\x%02x\\x%02x\\x%02x\\x%02x' \
                    $(( 240 | (cp >> 18) )) $(( 128 | ((cp >> 12) & 63) )) \
                    $(( 128 | ((cp >> 6) & 63) )) $(( 128 | (cp & 63) ))
            fi
            printf -v b '%b' "$b"
        fi
        out+="$pre$b"
        len=$(( len - adv + at + 1 ))
        rest="${rest:$adv}"
    done
    _LOCI_JSON_UOUT="$out"
}

# The string that starts at "$1" (the text after the opening quote), up to its
# first UNESCAPED quote.
#
# ⚠ NOT A WALK, and the difference is a fail-open. This used to step to each
# `"` in turn and re-slice the document behind it, which is one pass over the
# REST of the document per escaped quote — O(k·n), with nothing bounding k but
# `LOCI_JSON_MAX`. `contract-guard.sh` sets that to its own 64 KB cap, so the
# worst case was reached by an ORDINARY paste: `cat <<'EOF' > Vault.sol`, three
# thousand lines of source, and 13 000 escaped quotes cost 4.2 s of that hook's
# 5 s budget — past which it is killed and PreToolUse fails OPEN and the write
# is allowed. Measured against four guards; filed as F13.
#
# ⚠ AND IT IS STILL O(k·n). Say it plainly, because the first draft of this
# comment said "linear" and "never re-copies a tail" and both are false:
# bash's `${v//pat/rep}` copies the remaining tail once per replacement, the
# same shape as the walk it replaces. What changed is the CONSTANT, by about
# 60× — one C-level substitution pass per escape against a shell loop doing a
# `%%`, a `${v:n}` and a `body+=` each time. Measured at the guard's cap, one
# whole read of a 64 KB value: 25.4 s → 0.397 s worst case.
#
# WHAT MAKES IT SAFE IS THEREFORE `LOCI_JSON_MAX`, not the shape of this
# function, and anyone raising that cap has to re-measure: at 256 KB a
# quote-dense read is back at 4–6 s and F13 is back with it. Nor is this the
# dominant term any more — `_loci_json_unwrap` is. On a 64 KB value that is
# all escaped backslashes the split is probe 0.001 s, find 0.003 s,
# unwrap 0.204 s, and that chain is quadratic in the same way and untouched.
#
# Three steps:
#
#   1. THE COMMON CLOSE, and it must stay first. The first `"` in the document
#      IS the closing one unless a backslash sits immediately in front of it,
#      and that is one `case`. Every payload the hooks actually read stops
#      here — a short `file_path` beside a 60 KB escaped `content` field costs
#      0.002 s, against 0.19 s to probe the whole document for the same answer.
#   2. THE PROBE, only when a backslash IS in front of it. `\\` is claimed
#      first and `\"` second — the same order, and for the same reason, as
#      `_loci_json_unwrap` above: resolving `\"` first reads the second
#      backslash of `\\"` as escaping a quote that is really the close. Each
#      becomes two fill characters: TWO, so the probe is the same LENGTH as
#      the document and an offset in one is an offset in the other; FILL, so
#      no escaped quote survives to be mistaken for the close.
#
#      BOTH PATTERNS ARE FIXED ASCII, and that is load-bearing rather than
#      incidental — see the assertion below for the one-pass spelling that
#      reads better and is wrong.
#   3. THE FIND, one window at a time, for the reason `_LOCI_JSON_WINDOW` gives.
#
# THE PARITY COUNTING WENT WITH THE WALK, and it was right in a way worth
# recording before it is gone: it counted the backslash run on the SEGMENT and
# never on the accumulated body, because an earlier escaped quote is already in
# the body, so the body's last character is a quote, the parity reads as even
# and the string closes one escape early. The probe has no parity in it — a run
# of backslashes pairs off left to right in step 2 exactly as JSON reads it —
# so there is nothing left to get wrong, and no `${t%\\}` loop, which is itself
# quadratic in the length of a trailing backslash run.
_loci_json_string() {
    local rest="$1" probe seg off=0 win
    # The membership test before the `%%`, and it is not redundant: a value the
    # prefix cut short has NO closing quote, and answering that with one glob
    # pass is cheaper than copying 64 KB to discover the same thing. It also
    # makes the `%%` below total — past here there IS a quote, so `seg` is a
    # proper prefix and needs no length comparison.
    case "$rest" in
        *'"'*) ;;
        *) return 1 ;;
    esac
    seg="${rest%%\"*}"
    case "$seg" in
        *\\) ;;
        *) _loci_json_unwrap "$seg"; return 0 ;;
    esac
    probe="${rest//\\\\/$_LOCI_JSON_FILL}"
    probe="${probe//\\\"/$_LOCI_JSON_FILL}"
    # The invariant the offset mapping rests on, asserted rather than argued.
    # It cannot fail as the passes above are written — but it FAILED in the
    # spelling they replaced, and silently: `${rest//\\?/…}` is one pass and
    # reads as the grammar itself, and `?` is a wildcard bash matches against
    # one CHARACTER. An invalid byte desynchronises its multibyte scan, `?`
    # then takes one byte of the character after the backslash, and the probe
    # comes out LONGER than the document — measured at 542 of 4 000 random
    # byte-soup values under `en_US.UTF-8` (0 under `LC_ALL=C`), each one an
    # over-read of the document past the end of the value. A value that
    # cannot be located is the truncation answer, which every caller already
    # handles.
    [ "${#probe}" -eq "${#rest}" ] || return 1
    while :; do
        win="${probe:$off:$_LOCI_JSON_WINDOW}"
        [ -n "$win" ] || return 1
        case "$win" in
            *'"'*)
                seg="${win%%\"*}"
                _loci_json_unwrap "${rest:0:$(( off + ${#seg} ))}"
                return 0
                ;;
        esac
        off=$(( off + ${#win} ))
    done
}

# Public API: loci_json_get <key>
#
# A string value comes back unescaped. A number, `true`, `false` or `null` comes
# back as written, so `[ "$(loci_json_get ok)" = true ]` reads the way the jq it
# replaces did. Non-zero, and empty, when the key is absent or its value is cut
# short.
loci_json_get() {
    _loci_json_seek "$1" || return 1
    local v="$_LOCI_JSON_AT"
    case "$v" in
        '"'*) _loci_json_string "${v#\"}" ;;
        *)
            v="${v%%,*}"; v="${v%%\}*}"; v="${v%%]*}"
            v="${v%%[$_LOCI_JSON_WS]*}"
            printf '%s' "$v"
            ;;
    esac
}

# Public API: loci_json_has <key> — is the name used as a key at all?
#
# Its own question, and the hooks need it separately: `.x // ""` cannot tell a
# recorded empty string from an absent key, and which of the two it is decides
# whether the post-edit nudge fires and whether an edit is a whole file.
loci_json_has() { _loci_json_seek "$1"; }

# Public API: loci_json_is_object <json-text>
#
# Does this text hold exactly ONE JSON object? Both tests run on a
# whitespace-free copy so the layout cannot change the answer:
#
#   not `{`…`}`   a truncated write, an empty file, or a JSON array.
#   `}{` anywhere two concatenated objects — an interrupted write, or a doubled
#                 append. Inside ONE object a `}` is always followed by a `,`, a
#                 `]` or the closing brace, so the pair can only mean two
#                 documents.
#
# A shape test and not a parse: what the callers need is the difference between
# "nothing is recorded here" and "I cannot read this", because those two are
# different answers and reading the second as the first is how a project with a
# recorded status came to be told it had none.
loci_json_is_object() {
    case "${1//[$' \t\n\r']/}" in
        '{'*'}{'*) return 1 ;;
        '{'*'}') return 0 ;;
        *) return 1 ;;
    esac
}

# Public API: loci_json_kind <key> — `string`, `null`, `number`, `bool`,
# `array`, `object`, or nothing when the key is absent.
#
# The TYPE, separately from the value, because three of them mean three
# different things to the post-edit nudge and `loci_json_get` renders two of them
# identically: a recorded `null` is the same claim as an absent key and nudges,
# while a recorded number is a file this version cannot read and must stay
# quiet — and a status of the literal string `"null"` is neither.
loci_json_kind() {
    _loci_json_seek "$1" || return 1
    case "$_LOCI_JSON_AT" in
        '"'*)          printf 'string' ;;
        '['*)          printf 'array' ;;
        '{'*)          printf 'object' ;;
        null*)         printf 'null' ;;
        true*|false*)  printf 'bool' ;;
        *)             printf 'number' ;;
    esac
}

# Public API: loci_json_array_len <key> — how many string elements the array at
# <key> holds. The scan steps over each element's own text, so a `]` inside a
# path is not read as the array closing.
#
# THIS WALK IS QUADRATIC IN THE ELEMENT COUNT and is left that way, measured
# rather than assumed (F14 scope item 4): 200 elements 0.028 s, 400 0.064 s,
# 800 0.197 s, and 0.676 s at the 16 KB ceiling. What makes that affordable is
# the PRODUCER, not the reader — the one caller is `setup-steps.sh` reading
# `subproject_roots` out of `lib/detect-project.sh`, which builds that array
# behind a `head -20`. Twenty elements is 0.001 s. Anyone pointing this at an
# array a model or a user sizes owes it the same treatment `loci_json_count`
# got above.
loci_json_array_len() {
    if ! _loci_json_seek "$1"; then printf '0'; return 1; fi
    case "$_LOCI_JSON_AT" in
        '['*) ;;
        *) printf '0'; return 1 ;;
    esac
    local rest="${_LOCI_JSON_AT#[}" n=0 seg
    while :; do
        case "$rest" in
            *'"'*) ;;
            *) break ;;
        esac
        seg="${rest%%\"*}"
        case "$seg" in
            *']'*) break ;;
        esac
        rest="${rest:$(( ${#seg} + 1 ))}"
        seg="${rest%%\"*}"
        rest="${rest:$(( ${#seg} + 1 ))}"
        n=$((n + 1))
    done
    printf '%s' "$n"
}

# Public API: loci_json_count <key> [value]
#
# How many times <key> is used as a key — with the given string value, when one
# is named. The count of a repeated field, which is what the callers want out of
# a `map(select(.op == $k)) | length` over an array of small objects.
#
# WINDOWING IS NOT THE FIX HERE, and the difference from `_loci_json_seek` is
# the point: that one stops at the first key, so bounding the search bounds the
# work. This must VISIT every occurrence, so the loop count IS the answer and
# only the re-slice behind each hit is waste. Measured at the 16 KB default its
# callers read under — `draft-pending-nudge.sh` makes FIVE of these calls per
# invocation against a 10 s timeout in `hooks.json`:
#
#       ops     walk       this      five calls, as the nudge makes them
#       200   0.040 s   0.007 s      walk 5.933 s   this 0.034 s
#       400   0.107 s   0.007 s
#       800   0.372 s   0.007 s
#     1 260   0.870 s   0.007 s      (1 260 is the ceiling: 16 KB of ops)
#
# So the tally spent 59% of the hook's budget, and the shape that costs it is a
# contract draft with a few hundred pending bounds — nothing hostile.
#
# THE COUNT IS ARITHMETIC, NOT A WALK: removing a FIXED-length string and
# dividing the length it took with it by that length counts occurrences in two
# passes. It needs the canonical spelling `"key":`, which is what every producer
# these hooks read emits; a document with whitespace between the name and its
# colon falls through to the walk below, which is kept for exactly that.
#
# ⚠ THE SUBSTITUTIONS MUST STAY FIXED-STRING. An extglob `${v//"$key"*(ws):/…}`
# reads better and is CUBIC: 0.048 s over 660 B, 0.342 s over 1 310 B, 3.76 s
# over 2 610 B — measured, after it looked linear. `%%` is cheap because it
# stops at the first match; `//` has to try every position and rebuild.
# ⚠ AND IT READS THE LITERAL NAME ONLY, where `_loci_json_seek` has seen an
# escaped one since F17. So `loci_json_has file_path` and `loci_json_count
# file_path` disagree on `{"\u0066ile_path": "x"}` — yes and 0. Deliberate: this
# function is arithmetic over a fixed literal and teaching it the second pass
# would cost it that, its one caller counts `op` in a document LOCI itself
# wrote with `json.dumps` (ASCII names, never escaped), and an under-count
# there drops a nudge summary rather than permitting anything.
#
# ⚠ SO IT IS NOT THE DUPLICATE DETECTOR, and that was F19's whole question.
# `loci_json_count file_path` is blind to a duplicate whose second spelling is
# escaped — F17 and F19 composed, and measured ALLOW — so the detector
# `contract-guard.sh` asks is `loci_json_dup` below, which runs this tally over
# the rewritten copy when the document carries a `\u00`. This one stays
# arithmetic over a fixed literal, because its own caller counts `op` in a
# document LOCI wrote with `json.dumps` and an under-count there drops a nudge
# summary rather than permitting anything.
loci_json_count() {
    local key="\"$1\"" want="${2:-}" doc lit t i
    case "$_LOCI_JSON_DOC" in
        *"$key"*) ;;
        *) printf '0'; return 0 ;;
    esac
    doc="$_LOCI_JSON_DOC"
    case "$doc" in
        *"$key"[$_LOCI_JSON_WS]*) _loci_json_count_walk "$1" "$want"; return 0 ;;
    esac
    lit="$key:"
    if [ -n "$want" ]; then
        # `json.dumps` writes `"op": "add"`, so normalise that one space to keep
        # the literal fixed-length; anything else about the spelling and the
        # walk answers instead.
        doc="${doc//"$lit" /"$lit"}"
        case "$doc" in
            *"$lit"[$_LOCI_JSON_WS]*) _loci_json_count_walk "$1" "$want"; return 0 ;;
        esac
        # The closing quote is load-bearing: without it `"op":"add"` would also
        # count `"op":"added"`.
        lit="$lit\"$want\""
        # …and it is also what lets this literal OVERLAP ITSELF, since it now
        # begins and ends with a quote. `${doc//…}` deletes non-overlapping
        # matches left to right, so `{"op":"add"op":"add"}` — where one `"` is
        # both the closing quote of a value and the opening quote of the next
        # name — counts 1 where the walk counts 2. Malformed JSON, and the
        # direction is safe (the nudge drops a summary it cannot reconcile),
        # but two paths in one function must not disagree.
        #
        # A pair can only sit `d` apart when the literal's tail from `d` is
        # also its head — a BORDER. `"op":"add"` has one at d = 9 and
        # `"op":"op"` another at d = 5, so looking only for the one-character
        # case missed the second. The borders are string compares over ten
        # characters; only a `d` that survives them costs a look at the
        # document, and only a document that really holds the pair falls
        # through to the walk, which re-scans from just past the NAME.
        i=1
        while [ "$i" -lt "${#lit}" ]; do
            if [ "${lit:$i}" = "${lit:0:$(( ${#lit} - i ))}" ]; then
                case "$doc" in
                    *"${lit:0:$i}$lit"*)
                        _loci_json_count_walk "$1" "$want"; return 0 ;;
                esac
            fi
            i=$(( i + 1 ))
        done
    fi
    t="${doc//"$lit"/}"
    printf '%s' $(( (${#doc} - ${#t}) / ${#lit} ))
}

# The walk `loci_json_count` used to be, kept for the document its fast path
# will not answer: whitespace between a name and its colon, or more than the one
# space after it that `json.dumps` writes. Quadratic in the occurrence count, as
# the table above records — which is affordable only because reaching it takes a
# producer none of these hooks read from.
_loci_json_count_walk() {
    local rest="$_LOCI_JSON_DOC" key="\"$1\"" want="${2:-}" n=0 pre at
    while :; do
        case "$rest" in
            *"$key"*) ;;
            *) break ;;
        esac
        pre="${rest%%"$key"*}"
        rest="${rest:$(( ${#pre} + ${#key} ))}"
        at="${rest#"${rest%%[!$_LOCI_JSON_WS]*}"}"
        case "$at" in
            :*) ;;
            *) continue ;;
        esac
        at="${at#:}"
        at="${at#"${at%%[!$_LOCI_JSON_WS]*}"}"
        if [ -z "$want" ]; then
            n=$((n + 1))
        else
            case "$at" in
                "\"$want\""*) n=$((n + 1)) ;;
            esac
        fi
    done
    printf '%s' "$n"
}

# Public API: loci_json_dup <name> — is <name> used as a key MORE THAN ONCE?
#
# The question `contract-guard.sh` asks before it decides anything, and the
# answer F19 settled on. RFC 8259 leaves a duplicate name to the parser;
# `json.loads` and `JSON.parse` take the LAST key and these helpers take the
# FIRST, so on such a payload the guard's verdict is about a different write
# from the one Claude Code performs — it read `<root>/ok.c`, matched no guarded
# file and ALLOWED, while the harness wrote `.loci/contract.yaml`. Measured on
# all three guarded files, and on route 2's `command`, at `4fbbc2a`.
#
# WHY THE GUARD REFUSES INSTEAD OF THE LIBRARY AGREEING, which is the question
# and not an aside: **taking the last key does not agree with the parsers, it
# only trades which documents it disagrees on.** These read a name at ANY
# DEPTH, and a parser reads a PATH, so "last" matches `json.loads` for two keys
# in ONE OBJECT and contradicts it for a nested one — and the nested reading is
# the one LOCI's own envelopes rest on (see the header of this file, and the
# `draft show` measurement in it). Written, measured and reverted: it put a
# model-authored draft entry's `ok` in front of the envelope's own and turned
# `draft-pending-nudge.sh` into a silent exit. A depth-blind search cannot
# implement a parser's rule; so the guard asks whether the question is
# ambiguous and, where its verdict turns on the answer, declines to give one.
#
# IT SEES AN ESCAPED SPELLING, which is the half `loci_json_count` alone cannot
# do — F17 taught `_loci_json_seek` to read `"\u0066ile_path"` as `file_path`
# and deliberately left the tally literal, so the tally alone would be blind to
# a duplicate whose second spelling is escaped (F17 and F19 composed, measured
# ALLOW). It runs the tally over the rewritten copy the escaped search reads,
# so the two agree on what an escaped name is by construction.
#
# ⚠ IT ASKS THE SEARCH AND NOT `loci_json_count`, and both halves of that are
# load-bearing — the tally was the first thing written here and it was wrong
# twice over.
#
#   * WRONG ON COST. `loci_json_count`'s arithmetic fast path is abandoned for
#     a quadratic walk the moment a quoted name is followed by WHITESPACE
#     anywhere in the document (its own `case "$doc" in *"$key"[ws]*` — read
#     its header, which says the walk is affordable only because "reaching it
#     takes a producer none of these hooks read from"). A guard payload is
#     exactly such a producer. One newline after one array element took
#     `contract-guard.sh` from 0.109 s to 3.846 s at 55 KB — and `hooks.json`
#     kills it at 5 s, after which PreToolUse fails OPEN and the guarded write
#     proceeds. That is a worse hole than the one this closes, reachable by
#     `json.dumps(indent=2)`. Measured against `4fbbc2a`, which denies the same
#     payload in 0.082 s.
#   * WRONG ON MEANING. The tally has its OWN notion of a key — a fixed literal
#     `"key":`, plus a walk for the shapes that literal misses — while the
#     guard acts on what the FIELD READ returns. Two definitions of "a key" in
#     one verdict is the class of defect this whole file keeps being fixed for.
#     Asking `_loci_json_seek_win` twice makes the detector and the reader the
#     same code by construction.
#
# TWO SEEKS ARE ONE PASS. The first stops at the first key; the second runs
# over `_LOCI_JSON_AT`, which IS the document from just past that key's colon,
# so between them they walk the document once — and each is the windowed,
# LINEAR search F14 bought (0.219 s to walk a whole 64 KB document finding
# nothing; 0.001 s when the name is absent, which is the membership glob and
# the hot path for every Bash payload).
#
# ⚠ IT CLOBBERS `_LOCI_JSON_AT`, like every other seek in this file. Nothing
# reads that across calls — `loci_json_get` and its relatives seek and read in
# one breath — but a caller that starts doing so owes this a look.
loci_json_dup() {
    local LC_ALL=C
    local _eg=0 _rc=1
    case "$_LOCI_JSON_DOC" in
        *"$_LOCI_JSON_UPFX"*)
            if _loci_json_rewrite "$1"; then
                # Shadowed for both seeks below, and only here: the rewritten
                # copy carries BOTH spellings as the literal name, so a mixed
                # duplicate is the two keys it is.
                local _LOCI_JSON_DOC="$_LOCI_JSON_REWRITE"
                _LOCI_JSON_REWRITE=""
            fi ;;
    esac
    # `_loci_json_seek_win`'s first glob is an extglob, and bash parses a
    # function body when it is DEFINED — so the option has to be on at
    # EXPANSION time and put back after, exactly as `_loci_json_seek` does it.
    shopt -q extglob || { _eg=1; shopt -s extglob; }
    if _loci_json_seek_win "$1"; then
        local _LOCI_JSON_DOC="$_LOCI_JSON_AT"
        if _loci_json_seek_win "$1"; then _rc=0; fi
    fi
    [ "$_eg" -eq 1 ] && shopt -u extglob
    return "$_rc"
}

# Public API: loci_json_escape <string> — the BODY of a JSON string, no quotes.
#
# The escapes JSON requires; any other C0 control byte is DROPPED rather than
# passed through, because a raw one makes the hook's stdout invalid JSON and the
# harness then discards the whole document — the reminder, or the deny reason,
# with it.
loci_json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\t'/\\t}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\b'/\\b}"
    s="${s//$'\f'/\\f}"
    s="${s//[$'\001'-$'\010'$'\013'$'\016'-$'\037']/}"
    printf '%s' "$s"
}

# Public API: loci_json_object <key> <value> [<key> <value> …]
#
# One flat JSON object on stdout, keys in the order given. Every value is
# written as a string — which is what the state files LOCI's shell writes hold —
# and a pair whose value is empty is still written, because an absent key and a
# recorded empty string are different answers to every reader here.
loci_json_object() {
    local sep=""
    printf '{'
    while [ "$#" -ge 2 ]; do
        printf '%s"%s": "%s"' "$sep" "$(loci_json_escape "$1")" \
                              "$(loci_json_escape "$2")"
        sep=", "
        shift 2
    done
    printf '}\n'
}

# Public API: loci_json_hook_output <event> <additionalContext> [<systemMessage>]
#
# The shape every LOCI hook prints. Written once, here, rather than in each
# hook: stdout is what the harness parses and injects into the model's context,
# so one stray byte costs the whole document.
loci_json_hook_output() {
    local event="$1" ctx="$2" sys="${3:-}"
    printf '{"hookSpecificOutput":{"hookEventName":"%s","additionalContext":"%s"}' \
        "$(loci_json_escape "$event")" "$(loci_json_escape "$ctx")"
    [ -n "$sys" ] && printf ',"systemMessage":"%s"' "$(loci_json_escape "$sys")"
    printf '}\n'
}
