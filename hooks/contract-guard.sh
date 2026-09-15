#!/usr/bin/env bash
# PreToolUse guard: the files that decide what a measurement MEANS are read-only
# to the agent.
#
#   .loci/contract.yaml     the bounds the agent's own work is judged against.
#                           The agent drafts (`loci contract draft …`), the USER
#                           applies (`loci contract accept`). ADR-0016/17.
#   .loci/build.yaml        the build recipe — target ISA, compiler, flags. It
#                           decides what the numbers are numbers OF, so an agent
#                           that can edit it can move its own goalposts (report
#                           §6.5). Written by `loci init`; one knob at a time by
#                           `loci init set`, which stays ALLOWED.
#   .loci/build/flags.json  the user's own flag pin. A `mode:"replace"` pin
#                           OUTRANKS the recipe, so guarding the recipe without
#                           it would leave a higher-precedence side door. One
#                           spelling since T14: the CLI reads no other.
#
# What backstops each one AFTER the fact differs, and that is why the deny before
# the fact matters most for the third: contract.yaml is committed, so its diff
# shows a shell write; build.yaml has the escrow hash (`recipe_tampered`);
# flags.json is gitignored, has no escrow and no validator — nothing notices.
# §6.5 says the design *reduces* goalpost-moving rather than closing it.
#
# The one LOCI hook that BLOCKS — do not merge it into the advisory pre-edit hook,
# and do not soften the deny into a warning. Two routes: an Edit/Write whose
# file_path is a guarded file, and a Bash contract-writing verb.
set -u

# Byte semantics for ROUTE 2, and it is a correctness line rather than a
# performance one. Route 2's scan charges itself for the characters it walks,
# but bash pays for BYTES plus a decode per character, so in a UTF-8 locale a
# 4-byte filler buys four times the work per unit charged: 22 KB of `😀(` — under
# the byte cap, under the work cap — took the hook past the 5 s in `hooks.json`
# on both Git Bash and Linux, and past that it is KILLED and PreToolUse fails
# open, which allows the write. Under `LC_ALL=C` `${#var}` counts bytes, so the
# cap charges what is actually spent.
#
# It also removes a locale dependence nobody was measuring: every timing harness
# in this repo hands the hook a minimal env with no `LANG`, i.e. C, while a real
# session runs `en_US.UTF-8`, where the same work costs about twice as much. The
# route 2 numbers in this file are now the numbers that ship.
#
# NOT for route 1, and the ambient locale is kept so it can be put back. Route 1
# compares PATHS with `nocasematch`, and case folding under C is ASCII-only: a
# project at `…/Проект` stopped matching an edit spelled `…/ПРОЕКТ/.loci/./
# contract.yaml`, so the guarded file became writable on exactly the
# case-insensitive filesystems the top of this file exists for. `José` does it
# too, and a Windows home directory is a normal place to find one. Measured
# 2026-09-09 against `bb4a547` and `addcd22`, both of which denied it.
_LC_AMBIENT="${LC_ALL:-}"
LC_ALL=C
export LC_ALL

# The PATH repair comes FIRST, before anything is read or run. It used to sit
# forty lines below the payload read, which is what made a missing `cat` a
# silent total fail-open: empty payload, prefilter exits 0, every write
# allowed. Hook PATH is often minimal — that is the whole reason this line
# exists — so nothing may depend on PATH above it.
#
# ${HOME:-}: under `set -u` an unset HOME kills the hook, and PreToolUse is
# fail-open, so the write would proceed.
PATH="$PATH:/usr/local/bin:/opt/homebrew/bin:${HOME:-}/.local/bin"

# The shared logger and the forkless JSON reader, sourced with parameter
# expansion rather than `dirname` and kept BELOW the PATH repair, so this file's
# rule that nothing forks above the prefilter still holds — sourcing only
# defines. Stubbed when a library is missing, so no call site below has to test
# for it; outside dev mode every logger call is an immediate return.
case "$0" in
    */*)
        . "${0%/*}/../lib/loci_log.sh" 2>/dev/null || true
        . "${0%/*}/../lib/loci_json.sh" 2>/dev/null || true
        ;;
esac
command -v loci_log >/dev/null 2>&1 \
    || { loci_log() { :; }; loci_log_session_from_payload() { :; }; }

# `cat`, with a builtin fallback — the third form tried here, and the reasons
# matter because two plausible ones are wrong. `IFS= read -r -d ''` forks
# nothing but issues one read(2) PER BYTE on a pipe: 3.98 s for 3 MB against
# this hook's 5 s timeout, which fails open, so a large enough write to a
# guarded file walked straight through and size alone flipped the verdict.
# `$(</dev/stdin)` is forkless and fast on a FILE and reads nothing at all
# from a PIPE on MSYS — which is what production delivers. `cat` is 0.23 s on
# 3 MB and correct on both; the builtin is kept for the host that lacks it,
# where slow beats blind.
payload=""
if command -v cat >/dev/null 2>&1; then
    payload=$(cat)
else
    IFS= read -r -d '' payload || true
fi

# Claude Code records neither PreToolUse nor its verdict anywhere QA can read, so
# the log is the only trace this hook leaves. The session id comes off the payload
# just read — stdin is consumed by now and nothing may read it again.
loci_log_session_from_payload "$payload"
loci_log INFO contract-guard "start: PreToolUse guard"
_cg_verdict="allow"
_cg_what=""
trap 'loci_log INFO contract-guard "end: verdict=$_cg_verdict (hook rc=$?)"' EXIT

# Runs on every Bash call in every repo, so nothing may fork before this.
#
# NTFS and APFS are case-insensitive, so `.loci/build/FLAGS.JSON` IS the guarded
# file: it opens it, the CLI's `build_dir(root)/"flags.json"` reads it straight
# back, and a byte-exact prefilter never even parses the payload. Hence the
# case-variant arms.
#
# Every one of them is a whole FILE NAME and requires a `file_path`, and both
# halves matter. Route 1 is the only route they serve and it needs a file_path,
# so without that gate a `cat .loci/build.yaml` or a Dart `build_runner` paid the
# whole field parse to reach a route that could not deny it — three jq forks when
# it was measured, 43 ms → 479 ms, and forkless now but not free. And a
# bare `*[Cc]ontract*` matched `ContractService.ts`, `CONTRACT_ADDRESS` and a
# `// Contract:` code comment — ordinary in Solidity, .NET and TypeScript trees —
# putting 0.5-0.7 s on every tool call in such a repo (measured 7x). Matching
# `contract.yaml` costs nothing and catches the same file.
#
# The plain lowercase `*contract*` arm stays ungated: route 2's verbs are
# lowercase shell commands and a Bash payload has no `file_path`.
#
# THE FIFTH ARM IS NOT A LITERAL, and it is here because the field read below
# DECODES `\uXXXX`. Four arms matching the text as written can therefore exit
# ALLOW above a path that would have RESOLVED to a guarded file, which is this
# hook deciding on payload text — the thing the field read's own header forbids
# forty lines down. Eight spellings did it, and they are parametrised in
# `ESCAPED_GUARDED_SPELLINGS` (tests/unit/test_contract_guard.py). Route 2 had
# the same door: a `command` spelling the verb `\u0063ontract` matched nothing
# here either, although route 2's own scan denies all five of those spellings
# once this arm admits them. One arm closes both routes, which is why it is NOT
# gated on `"file_path"` the way the three above it are.
#
# `\u00` IS THE WIDTH, and both edges of it are measured rather than picked.
#
#   * NARROWER does not hold. An escaped LETTER is always `\u004X`-`\u007X`,
#     so a band arm looks sufficient and is not: arms 2-4 each demand a LITERAL
#     `.` before the extension and arm 1 is lowercase-only, so
#     `build\u002eyaml` and `CONTRACT\u002eYAML` beat all four with no escaped
#     letter at all, and so does `build\u0000.yaml`, which the decode drops to
#     `build.yaml`.
#   * WIDER is the expensive mistake. `\u` alone matches every Windows path in
#     JSON — `C:\\users` carries those two characters — and would put the
#     whole field read on every tool call in every Windows repo, which is the
#     over-work twin of the over-deny this guard avoids everywhere else.
#
# WHAT IT ADMITS is therefore narrow but NOT empty, and the first draft of this
# comment said "a component spelled `u00…`, which no tree has", which is only
# the path half of it. The arm sees the WHOLE payload, `new_string` and
# `content` included, so two ordinary classes come through: a file whose own
# text spells `\u00XX` (this repo's `.venv` has several, and so do these two
# files), and — reachably, from Claude Code itself — any file carrying a raw
# control character, because `JSON.stringify` escapes every C0 byte without a
# short form as `\u00XX`. An ANSI colour code in a fixture is enough. Measured
# in round 1: +0.08 s and one `realpath` fork on such a call.
#
# What it COSTS, measured on this machine: the payloads it does not admit are
# unchanged (64 KB of Windows paths 0.048 s, 1 MB 0.097 s, both still one fork
# — the payload read), and a payload it does admit pays the field read, which
# is 0.12 s at 64 KB. Pinned by
# `test_an_ordinary_payload_still_exits_at_the_prefilter`.
#
# ⚠ ITS WORST CASE WAS 45 s, and the reason it is 0.37 s is worth carrying,
# because the cost was never this arm's. A `file_path` that is nothing but
# escapes decodes WHOLE up to about 1 000 of them (0.22 s); past the work meter
# at `_LOCI_JSON_UMAX` the decode stops and hands the rest back as text —
# backslashes and all — and the Windows mask below turns every one of those
# into a path component. So the bill went to whatever resolves the path, and
# `realpath` was super-linear in path components on MSYS: 2 000 escapes cost
# 46.88 s where the prefilter used to exit on it in 0.05 s, and 10 900 — the
# most that fits under `LOCI_JSON_MAX` — did not finish inside two minutes.
#
# It shipped anyway, because it was NOT a hole: a value the decode gave up on
# keeps its escapes as text, so it spells no guarded path, and the write that
# proceeds when the hook is killed goes to a file named `AAAA…`. It was not
# reachable either — no serializer escapes an ASCII letter, which is the same
# argument that makes door 1 a gap. What made it worth fixing is that the same
# cost was reachable WITHOUT any escape, by padding a path with `ab/`x1000 and
# `../`x1000 — and that one resolves to the guarded file and IS reachable from
# the product. Both were F16, and F16 replaced `realpath` with a walk that is
# linear in components: the two rows above are now 0.19 s and 0.37 s. Pinned by
# `test_an_escape_dense_payload_is_decided_inside_the_hook_budget` and
# `test_a_padded_path_is_decided_inside_the_hook_budget`.
#
# NOT COVERED, deliberately: an escape naming a NON-ASCII character. It cannot
# spell a guarded name — all three are ASCII throughout — and reaching it costs
# the `\u` arm above. `\U0063` is not JSON at all: `json.loads` rejects the
# document, so it names no file.
case "$payload" in
    *contract*) ;;
    *'"file_path"'*[Cc][Oo][Nn][Tt][Rr][Aa][Cc][Tt].[Yy][Aa][Mm][Ll]*) ;;
    *'"file_path"'*[Bb][Uu][Ii][Ll][Dd].[Yy][Aa][Mm][Ll]*) ;;
    *'"file_path"'*[Ff][Ll][Aa][Gg][Ss].[Jj][Ss][Oo][Nn]*) ;;
    *'\u00'*) ;;
    *) _cg_verdict="allow (prefilter: no guarded token)"; exit 0 ;;
esac

deny() {
    _cg_verdict="deny (${_cg_what:-unknown})"
    # Fixed literal, so nothing has to escape it.
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$1"
    exit 0
}

# Not a fixed literal like the others: `%s` takes the field name, from the
# list in the duplicate check below and never from the payload. Both are
# `[a-z_]`, so the printf in `deny` still has nothing to escape — and it takes
# the reason as an ARGUMENT rather than as a format, so that claim is about the
# JSON, not about printf.
REASON_DUPLICATE_FMT="This payload uses the JSON name '%s' twice as a key, and that is a question with two answers: JSON leaves a duplicate name to the parser, so this guard and the tool that performs the write do not have to agree on which one they are reading — and a guard whose verdict is about a different write is not a guard. Nothing produced this by serialising an object; send one '%s' and the decision is unambiguous."
REASON_FILE="The Contract Envelope (.loci/contract.yaml) is read-only to you. It states the bounds your work is judged against, so only the user changes it. Draft the change instead: echo '<entry json>' | loci contract draft add   then hand the user exactly this line to run: ! loci contract accept"
REASON_VERB="That verb writes the Contract Envelope, which is the user's to apply. Draft with 'loci contract draft add|edit|disable|enable', then hand the user this line to run: ! loci contract accept"
# These two name the sanctioned write path, and the consent that goes with it.
# `loci init set` is agent-runnable on purpose — it is the interface these files
# lost — so the reason has to carry the question that precedes it, or the guard is
# just advertising its own bypass with one extra step.
REASON_RECIPE="The build recipe (.loci/build.yaml) is written by 'loci init', never edited. It records the target ISA, compiler and flags every LOCI measurement is made with, so an edit here changes what your own numbers mean — and the recipe's integrity record turns a hand edit into a 'recipe_tampered' refusal for every file in the project, not just this one. Ask the user first, then record the knob: loci init set <key>=<value>   (switch targets with: loci init --refresh --target=<isa>)"
REASON_FLAGS="That flag pin (.loci/build/flags.json) is the user's own, and a 'replace' pin OUTRANKS the recipe — so it is not yours to write either. Ask the user, then record the knob in the recipe: loci init set <key>=<value>   (e.g. loci init set rust.features=max-pure). If they want a raw flag pin instead, hand them the file and let them edit it themselves."

# Above this many bytes of command text, route 2 stops tokenising and falls back
# to the SUBSTRING test it replaced. Tokenising is more work per byte than that
# test was — `${var//…}` in bash is not linear at this scale — and `hooks.json`
# gives the hook 5 s, after which it is KILLED and PreToolUse fails open. So the
# cap is not a performance nicety: past it, the choice is between a coarse answer
# and no answer at all.
#
# Measured on this machine, a heredoc-shaped command with the verb on its last
# line (`runs/F07-2026-09-09/route2-scaling.txt` in the follow-ups repo). The
# two columns were the jq and the jq-less RUNG, which is what deciding the cap
# meant while the field read was a ladder; there is one rung now, so read them
# as history:
#
#             jq rung             no-jq rung
#     64 KB   0.57 s              0.48 s
#    274 KB   2.02 s              3.84 s
#    1.1 MB   (not measured)     49.8 s   ← and the BASE version took 6.4 s here,
#                                           so it was over budget before this too
#
# ⚠ THIS CAP IS ALSO THE FIELD READ'S BOUND, and after F13 that is the more
# load-bearing of its two jobs. `lib/loci_json.sh` is O(escapes × length) in
# what it parses — F13 made the constant ~60× smaller and did NOT change the
# exponent — and `LOCI_JSON_MAX` is set from this constant a few lines below,
# so this number is what keeps a quote-dense read at 0.4 s. Raise it to 256 KB
# and the same read is 4–6 s, inside a 5 s budget, and F13 is back. Measured;
# do not move this cap without re-running `runs/F13-2026-09-11/guard_timing.py`.
#
# Re-measured after the forkless field read landed
# (`runs/F10-2026-09-09/megabyte_timing.py`): a 1 MB heredoc is 0.17 s and a
# 3 MB one 0.38 s.
#
# For one release the margin was far thinner on a command carrying thousands of
# DOUBLE QUOTES, and the cap could not help, because the price was paid getting
# the field out: `_loci_json_string` walked one escaped quote at a time and cost
# 4.2 s on an ordinary Solidity heredoc before route 2 was reached at all. That
# was F13; it is fixed in `lib/loci_json.sh`, and these are five shapes end to
# end, `3a10684` against the fix:
#
#                                    3a10684    fixed
#     1 MB heredoc, plain             0.17 s    0.17 s
#     1 MB heredoc, quote-dense       4.36 s    0.24 s
#     3 MB heredoc, quote-dense       4.65 s    0.54 s
#     36 KB, quote-dense (not cut)    2.52 s    0.55 s
#     64 KB, quote-dense              4.22 s    0.12 s
#
# The 36 KB row is the worst one left, and it is the one to re-measure if this
# cap is ever touched: under the cap the escaped value still CLOSES, so route 2
# runs on the whole of it, where every row above it is cut short by
# `LOCI_JSON_MAX` and answered by `_R2_OVERSIZE_RE` instead.
#
# 64 KB leaves about ten times the headroom at the cap, and the budget test's own
# docstring records that this hook has gone red under load and green idle. A real
# command that long is a heredoc dumping a file; deciding it by substring
# over-denies a mention of the verb inside such a file, which is the direction
# this guard errs in everywhere else — and it is exactly what `bb4a547` did to
# every command of every size.
_R2_MAX_TOKENISE=65536

# THE FIELDS, forklessly. This used to be a three-rung ladder — jq, then sed,
# then parameter expansion — and the bottom rung existed because the top two are
# host tools nobody guarantees: Git for Windows installs `Git\cmd` on PATH and
# `Git\usr\bin` only optionally, and the PATH repair above adds POSIX-shaped
# directories that cannot supply either. So the guard's verdict rested on
# whichever rung the machine happened to have, and the two absent-tool rungs were
# strictly weaker than the one the tests exercised. There is one rung now, it
# needs no binary at all, and it is the one every machine runs. `lib/loci_json.sh`
# reads the FIELD, so the `_payload_as_commands` segmenter the jq-less rungs fed
# route 2 is gone with them — there is no rung left that decides on payload text.
#
# ⚠ BOUNDED, and route 2 knows it. `loci_json_load` parses a prefix
# (`LOCI_JSON_MAX`, set to route 2's own cap here), because bash's string
# operators are quadratic in the offset of the match and this hook is fail-open
# on a 5 s budget over payloads that reach megabytes — an unbounded read is the
# same silent fail-open the payload read above was fixed for. `cwd` and
# `tool_input.file_path` are inside the prefix by construction (the harness
# writes them ahead of the edit's content); a `command` is not guaranteed to be,
# so a truncated payload falls back below and route 2 answers it with
# `_R2_OVERSIZE_RE`, exactly as it answers an over-cap command.
#
# Decide on the FIELD, never on the payload text: matching the text denies an
# edit for its own content.
LOCI_JSON_MAX=$_R2_MAX_TOKENISE
fp=""; cmd=""; cwd=""
if command -v loci_json_load >/dev/null 2>&1; then
    loci_json_load "$payload"
    fp=$(loci_json_get file_path)
    cmd=$(loci_json_get command)
    cwd=$(loci_json_get cwd)
    # A `command` the prefix cut short is worse than no field at all — route 2
    # would read the first 64 KB of a verb it is meant to deny. `loci_json_get`
    # answers empty on a value it could not close, so this is that case. Fall
    # back to the whole payload, which over-matches in the guard's own direction
    # and is what the retired bottom rung did for every payload.
    if [ -n "${_LOCI_JSON_TRUNCATED:-}" ] && [ -z "$cmd" ]; then
        case "$payload" in *'"command"'*) cmd="$payload" ;; esac
    fi
else
    # The library did not source. Route 1 cannot run without a field, but route 2
    # still can, and a guard that denies nothing while saying nothing is the one
    # failure this file has twice been fixed for.
    loci_log ERROR contract-guard "lib/loci_json.sh did not source — route 1 is off"
    case "$payload" in *'"command"'*) cmd="$payload" ;; esac
fi

# ⚠ A NAME USED TWICE IS NOT A NAME THIS GUARD CAN DECIDE ON — before either
# route runs, because both of them rest on a field read.
#
# `lib/loci_json.sh` answers with the FIRST use of a name as a key; `json.loads`
# and `JSON.parse` answer with the LAST. RFC 8259 leaves the choice to the
# parser, so neither is wrong — but this guard and Claude Code are two parsers
# reading ONE document, and only one of them writes the file. At `4fbbc2a` a
# payload naming `file_path` twice with the guarded path second was read here as
# `<root>/ok.c`, matched no guarded file and was ALLOWED, while the harness
# performed the write to `.loci/contract.yaml`; measured on all three guarded
# files. `command` had it too — the invocation second RAN the verb — and so did
# the mixed spelling F17 left behind, a literal name and then an escaped one.
#
# WHY REFUSING RATHER THAN AGREEING. Teaching the library to take the LAST key
# looks like the honest fix and is not: these read a name at ANY DEPTH, so
# "last" matches a parser for two keys in one object and contradicts it for a
# nested one — and it breaks the invariant LOCI's own envelopes rest on, where
# `{"ok":…,"data":{…}}` exists so a result field cannot collide with `ok`. It
# was written and measured; `lib/loci_json.sh`'s header carries the result.
# So the guard asks whether the question is ambiguous instead of inventing an
# answer to it, which is also the only form that covers BOTH routes at once.
#
# THE TWO NAMES ARE THE TWO THIS FILE'S VERDICT IS ABOUT: `file_path` is route
# 1's subject and `command` is route 2's. Neither is written by the harness
# anywhere but inside `tool_input`, so a second one is a payload no product
# produces.
#
# ⚠ `cwd` WAS THE THIRD AND IS DELIBERATELY NOT, which is worth the four lines
# because it looks like an omission. It is where route 1's project root comes
# from when `CLAUDE_PROJECT_DIR` is unset, so a second one moves the guarded
# side of the comparison — but no shape built for it is a fail-open: every one
# is denied at `4fbbc2a` and here, because the suffix arms decide a path
# carrying a guarded basename without consulting the root at all. And the
# over-deny is real rather than theoretical: the harness writes `cwd` at the
# TOP LEVEL of every payload, so any tool whose `tool_input` carries its own
# `cwd` — ordinary for an MCP shell or editor server — is a nested duplicate,
# and screening it would refuse that call in any repo with the word `contract`
# in it. A screen with no measured defect behind it and a measured over-deny in
# front of it is the wrong trade for this hook. Pinned in
# `test_a_tool_input_may_carry_its_own_cwd`.
#
# ⚠ IT OVER-DENIES A NESTED KEY, deliberately: `{"file_path":"ok.c","meta":
# {"file_path":"<guarded>"}}` resolves to `ok.c` for `json.loads` and is refused
# here. That is the direction this guard errs in everywhere else, the payload is
# one no product sends — `JSON.stringify` serialises an object whose keys are
# unique, and the harness puts `file_path` only under `tool_input` — and the
# reason tells the model exactly what to change. Pinned in
# `test_a_nested_name_is_refused_too_and_that_is_deliberate`.
#
# ⚠ AND IT IS BOUNDED BY `LOCI_JSON_MAX` LIKE EVERY OTHER READ HERE. A second
# `file_path` past the 64 KB prefix is invisible to this check exactly as it is
# to the field read above it — the truncation door, older than this and
# unchanged by it, and the reason `cmd` has its own fallback twenty lines up.
if command -v loci_json_dup >/dev/null 2>&1; then
    for _cg_dup in file_path command; do
        if loci_json_dup "$_cg_dup"; then
            _cg_what="duplicate $_cg_dup"
            loci_log WARN contract-guard "duplicate JSON name: $_cg_dup"
            # shellcheck disable=SC2059
            printf -v _cg_reason "$REASON_DUPLICATE_FMT" "$_cg_dup" "$_cg_dup"
            deny "$_cg_reason"
        fi
    done
fi

# ── route 1: a direct write to one of the guarded files ──────────────────────

# WHAT ROUTE 1 COMPARES, and why it is not two strings.
#
# It used to resolve the `file_path` and the guarded path SEPARATELY and compare
# the results as text — `resolved=$(_resolve "$fp")` against
# `want=$(_resolve "$root/$1")` — over a `_resolve` that was a three-rung ladder
# (`realpath -m`, then `realpath`, then a lexical collapse plus `cd`+`pwd -P`).
# Two independently-produced strings are not a comparison, and the three rungs
# did not agree with each other. That was F16, and it was four holes:
#
#   1. MSYS `realpath` PRESERVES THE SPELLING IT IS GIVEN. `C:/proj/…` answers
#      `C:/…` and `/c/proj/…` answers `/c/…`, for one file. Claude Code sets
#      `CLAUDE_PROJECT_DIR` to the native spelling, which is where `want` came
#      from, while a `file_path` in the MSYS spelling is what every tool inside
#      Git Bash produces — so the two sides routinely disagreed and only the
#      suffix arm below saved the guard. Pad the path past that arm with 60
#      `./` and all three guarded files were writable. No escape, no symlink,
#      no third-party producer.
#   2. MSYS `realpath` IS SUPER-LINEAR IN THE COMPONENTS IT MUST CANONICALISE
#      — named ones, not `.` or `..`, which are cheap for it: `ab/`x1000 +
#      `../`x1000 is 12.47 s and x1500 is 28.18 s (coreutils 8.32; GNU 9.4
#      answers the last in 0.002 s), and 21 780 distinct missing components do
#      not finish in two minutes. `hooks.json` kills this hook at 5 s and
#      PreToolUse then fails OPEN, so `<root>/.loci/` + `ab/`xN + `../`xN +
#      `contract.yaml` — which `realpath -m`, `json.loads` and Win32 all read as
#      the Contract Envelope — was a write that went through. At the 64 KB the
#      field read caps at, that shape is 20.18 s and the verdict it eventually
#      reaches is ALLOW anyway, which is item 1: the padding that makes it slow
#      is the padding that gets it past the suffix arm.
#   3. THE SHELL RUNG COLLAPSED `..` LEXICALLY AND ONLY THEN `cd`-ed, so a
#      symlink followed by `..` was erased before it could be resolved and the
#      rung answered a different file than `realpath` does. That rung is not a
#      fallback on macOS and the BSDs — there is no `realpath -m` there and
#      `realpath` fails on a path that does not exist yet, which is every
#      `Write`, so it is the ONLY rung and this was live on every such host.
#   4. AND CHOOSING A RUNG PER CALL MAKES THE COMPARISON MEANINGLESS. Bounding
#      the `realpath` fork by component count and falling back to the shell rung
#      — the obvious fix for 2 — resolves the padded `file_path` with one rung
#      and the short `<root>/…` it is compared against with another. Written,
#      measured, and reverted: all three files writable with 60 `./` segments.
#
# So the answer is not a better string. THERE IS NO CANONICAL STRING FORM ON
# MSYS, measured three ways on this machine: `realpath` keeps whatever spelling
# it was handed; `cd -P` + `pwd -P` re-spells through the mount table, so
# `C:/Users/…/Temp/x` comes back `/tmp/x` while `/c/Users/…/Temp/x` comes back
# `/c/…` — the same directory, two answers; and `pwd -W`, which IS canonical,
# exists only on MSYS. A guard that compares strings has to pick one of those
# per host, which is item 4 again with a different name.
#
# WHAT IT COMPARES INSTEAD is a pair, produced by ONE function on ONE call:
#
#   * the deepest EXISTING directory of each side, compared by `-ef` — the
#     filesystem's own identity test, which is blind to the drive spelling, to
#     the mount table, to a junction or symlink, and to case. A bash builtin:
#     nothing forks, and there is no host tool to be missing.
#   * the remainder that does not exist yet, compared as TEXT. It is relative
#     and it carries none of the above — `/contract.yaml` is `/contract.yaml`
#     on every host — and it is the half that has to be text, because a `Write`
#     to a file that is not there yet is the ordinary case.
#
# `_walk` produces both, by descending with `cd -P` into every component that
# exists and collapsing only what is left. `cd -P` resolves a symlink BEFORE
# the next `..` is applied, which is what `realpath` does and what item 3's rung
# did not, and it is the same `cd`+`pwd -P` subshell `post-edit-hook.sh` uses.
# There is ONE rung, so there is no per-call rung choice to get wrong.
#
# The residual, written down rather than implied: the walk does not follow the
# LAST component, because reading a link's target needs `readlink` (a fork, and
# BSD's has no `-f`) — so a link NAMED `contract.yaml` pointing elsewhere is
# compared as itself. That is the same gap
# `test_a_symlink_that_hides_the_name_is_a_known_gap` already pins from the
# other direction, and it errs toward denying the guarded name.
#
# WHAT `-ef` CLOSES THAT `main` DOES NOT, because it is the argument for why it
# may never be bounded: a HARD link to the guarded file (`realpath` sees no link
# at all — one inode, one Contract Envelope), and the NTFS 8.3 SHORT NAME.
# `<root>/.loci/CONTRA~1.YAM` opens the Contract Envelope, needs no link and no
# third-party producer, and `main` allows it; measured, with the write performed
# and the file read back.
#
# COST, measured on this machine (Git for Windows, coreutils 8.32), end to end
# through the hook, against the 5 s in `hooks.json`. Every shape is sized to
# just under `LOCI_JSON_MAX`, because past that the field read truncates and
# route 1 never runs — so these are the worst a payload can actually ask for:
#
#                                           before           after
#     `ab/`x10890 then `../`x10890        20.18 s ALLOW    0.32 s deny
#       …at 1 000 each, mixed spellings   11.15 s ALLOW    0.23 s deny
#     60 `./` pads, mixed spellings        0.14 s ALLOW    0.09 s deny
#     21 780 distinct missing components  over 120 s       0.25 s deny
#     one missing component, then `../`    5.88 s ALLOW    0.25 s deny
#     10 900 escaped components           20.16 s          0.37 s
#     2 000 escaped components            46.88 s          0.19 s
#     `A/../` x13068                       0.74 s deny     0.25 s deny
#     `deep/../` x8167                     0.37 s deny     0.97 s deny
#     `../` x21740                         0.25 s deny     0.21 s deny
#     `./` x32670                          0.26 s deny     0.34 s deny
#     4 070 x 15-byte names                    —           0.91 s  ← the worst
#     an Edit to the guarded file          0.20 s deny     0.09 s deny
#     an Edit to a C file                  0.04 s          0.04 s
#
# ⚠ THE `after` COLUMN IS F16's, at `_ROUTE1_MAX_SEGMENTS=4096`. F18 added a
# second reading of `..` and a second spelling of a trailing dot, and paid for
# both by quartering that bound — so the two rows the bound decides now read
# 0.24 s (4 070 names, denied unwalked) and 0.92 s (the F18 worst, which is a
# shape no row here has). The table above is kept as F16 measured it, because
# what it is evidence FOR is the `before` column; the current numbers are at
# `_ROUTE1_MAX_SEGMENTS`.
#
# ⚠ READ THE `before` COLUMN CAREFULLY: it does not say `realpath` was slow on
# long paths, and the first four rows are why. It is super-linear in components
# it has to CANONICALISE — 21 780 distinct missing ones do not finish in two
# minutes — while `./` and `../` are cheap for it, so the bottom four rows were
# never the problem. What made the top rows a fail-open is the combination: the
# shape that is over budget is also the shape that is ALLOWED, because it is the
# padded spelling that gets past the suffix arm and lands on a comparison
# between two independently-resolved strings.
#
# The 0.91 s row is the one that was worth re-measuring whenever this moved, and
# the name length is not a detail: it is the longest name that still fits 4 070
# components — one short of what the bound was then — into the 64 KB, so it is
# the most of the quadratic that bound would let through. 12, 13 and 14 bytes
# are 0.19-0.29 s because they run out of budget bytes before they run out of
# components; 16 and 20 are 0.87 s and 0.74 s because they run out of components
# first. Everything past the bound is denied instead, which is why the rows
# above are flat. (Round 2 of review caught this comment naming 16 bytes.) The
# shape that costs the most at the bound this file carries TODAY is a different
# one, and it is in the table beside `_ROUTE1_MAX_SEGMENTS`.
#
# Two cheap things do the rest. `[ -d ]` screens the `cd`, because a failed
# `cd` is 123 us on MSYS against 63 us for the test; and a `..` that leaves us
# where we already were sets `_w_root`, after which the rest are free — without
# that, a `file_path` of nothing but `../` paid a stat AND a `cd` per component,
# 0.16 ms each.
#
# The `-ef` fast path is NOT one of them, and this comment said it was for one
# release: round 2 of F16's own review deleted that gate — a `stat` is Win32
# canonicalising the whole string and three of them on a 64 KB path cost 0.71 s,
# but skipping them past 4 KB is a bound that allows past itself, which is the
# whole argument written out at the call site. The sentence outlived the code;
# F18 caught it.

# The guarded files, in one place. `guard_path` names each again with the reason
# it is denied with — that list is what `test_the_guarded_paths_this_lint_
# screens_match_the_guard` (tests/unit/test_freshness_contract.py) reads — and
# `test_route_1_walks_every_path_it_denies` pins the two equal. No name here may
# contain a space: this is word-split on purpose.
_ROUTE1_GUARDED=".loci/contract.yaml .loci/build.yaml .loci/build/flags.json"

# Components, after which a `file_path` is DENIED without being walked.
#
# The collapse is quadratic in the length of what it accumulates — `${t}/$seg`
# copies the whole tail, and `${t%/*}` scans it — and at the 64 KB the field
# read caps at that is 21 780 components and 3.74 s against a 5 s kill that
# fails OPEN. Every linear alternative costs more than it is worth here: a bash
# array is O(1) amortised only from 4.3, and stock macOS ships 3.2, where
# `array_insert` walks the element list per index and turns the same shape into
# the cliff this bound exists to remove; a chunked accumulator needs a second
# one for the `..` side and is a lot of machinery for a hook.
#
# SO THE BOUND DENIES, and that is the whole of why it is safe. F16 item 4 is
# the record of a bound that ALLOWED past itself: it made the two sides of one
# comparison take different code and all three guarded files became writable.
# This one decides nothing about the comparison — it is a property of the
# `file_path` alone, it short-circuits before any comparison happens, and its
# answer is the direction this guard errs in everywhere else. What it costs is
# an over-deny of a path that carries a guarded basename AND more than 1 024
# components, which is not a path anything writes: a real one has fewer than
# fifty, and 1 024 of them do not fit in Linux's PATH_MAX to begin with.
#
# ⚠ IT WAS 4 096, AND F18 BOUGHT ITS OWN COST WITH IT. The number was sized to
# ONE walk, and F18 asks for as many as four on one `file_path`: two readings of
# `..` (see `_walk`) times two spellings of a trailing dot (see `_win32_dots`).
#
# THE TWO QUESTIONS ARE DIFFERENT and both belong here, because answering only
# the first is how review round 1 caught this comment over-claiming.
#
#   1. What is the worst a payload can ASK FOR? Each hook is bounded by its own
#      constant, so this is the number that matters against the 5 s in
#      `hooks.json` — past it the hook is killed and PreToolUse fails OPEN.
#
#          main (at 4 096)   1.00 s      `deep/../` x8167, `-ef` stats
#          here (at 1 024)   1.00 s      the same shape, same reason
#
#      — equal, because the shape that costs the most on either is one the bound
#      denies unwalked and what it pays for is the `-ef` stats, which are
#      unbounded on purpose.
#
#   2. What does ONE path cost, here against there? Up to 2.2x, and that is the
#      honest price of a second reading. At 1 007 components of 63 bytes — the
#      most that fills the 64 KB cap under this bound:
#
#          plain names, no dot, no `..`    main 0.39 s   here 0.35 s
#          dotted names                         0.40 s        0.54 s
#          `src/../` then plain names           0.39 s        0.50 s
#          `src/../` then dotted names          0.41 s        0.89 s  ← 2.2x
#
# So the second reading is not free, it is PAID FOR — by denying, four times
# earlier, a path nothing writes. At 4 096 that same shape is 2.71 s, which is
# half the budget for a path with no file at the end of it. The sweeps are
# `runs/F18-2026-09-12/worst_shapes_{fix,main}.txt` and
# `worst_bytes_*.txt` in the follow-ups repo — the second exists because the
# first sizes its shapes by BYTES and they run out of them before they run out
# of components, so they never asked for the whole quadratic. Re-run both if
# anything in either reading moves.
_ROUTE1_MAX_SEGMENTS=1024

_w_tail=""     # the part of the path that does not exist: "" or "/a/b"
_w_home=""     # where a RELATIVE path is anchored
_w_root=""     # set once `..` has stopped moving: `/..` is `/`
_w_desc=""     # the descent as TEXT: `_w_desc$_w_tail` is Win32's reading of it
_w_phys=""     # a `..` was applied by CDing, so the two readings can differ

# Walk $1 the way the filesystem does. Leaves the shell in the deepest existing
# directory of $1 and `_w_tail` holding the rest, collapsed lexically — which is
# what `realpath -m` does for a component that does not exist. Returns 1 if it
# cannot reach its own anchor, which the caller reads as "no match".
#
# ⚠ IT IS THE POSIX ANSWER, and Win32's differs — on `..`. Win32 canonicalises
# `..` TEXTUALLY, before any I/O, so it applies one across a junction where this
# walk resolves the junction first. With `L -> <root>/deep/sub`, the two readings
# of `<root>/L/../.loci/contract.yaml` are different files:
#
#     POSIX (this walk, and `realpath`):  <root>/deep/.loci/contract.yaml
#     Win32 (what actually opens):        <root>/.loci/contract.yaml
#
# and on Windows the write lands in the second. That is F18 item 2: ALLOWED on
# `main` and measured with the write performed and the guarded file read back.
#
# So the walk carries the OTHER reading beside it, in `_w_desc`: the components
# it has descended through, as TEXT, with `..` popping the name rather than the
# directory. `_w_desc$_w_tail` is then the whole path exactly as Win32 collapses
# it, and `_guarded_match` walks that too and denies on EITHER reading. Denying
# on either is the only way to be right on both platforms at once — the two
# readings genuinely name different files, and nothing in a hook can know which
# of them the tool that performs the write will open.
#
# ⚠ `_w_desc` IS NOT A SECOND PASS OVER THE COMPONENTS, and the difference is
# 1.2 s of a 5 s budget. It is appended to once per DESCENT — a component that
# exists — and popped by `..`, so it never grows past the real directory depth,
# where `_w_tail` grows with every component that does not exist and is
# quadratic in its own length. Collapsing the path a second time from the string
# was the first version of this and cost as much again as the first walk.
#
# `_w_phys` keeps the resolution of that reading off every other path: unless a
# `..` was applied by CDing, the two readings are the same string. A `..` that
# lands in `_w_tail` is already collapsed textually — that IS Win32's rule — so
# the shapes this skips are not shapes it would have changed.
#
# ⚠ The trailing-DOT half of F18 is NOT here. It is four substitutions over the
# whole path, done once at the call site above both arms, so this function only
# ever sees the stripped spelling — see `_win32_dots`.
#
# `//` IS NOT A UNC ANCHOR HERE, deliberately. `cd -P //` makes MSYS enumerate
# the network — 2.79 s of a 5 s budget measured on an ordinary path, and a
# `file_path` naming a server that does not answer can block far longer, which
# is the fail-open this whole task is about. A leading `//` is therefore walked
# from `/` like any other absolute path. Both sides go through the same arm, so
# a project that really is on a share still compares consistently; it is only
# the physical resolution of the share itself that is given up.
#
# Must be run inside a subshell: it CDs, and the hook must not.
_walk() {
    _w_p=$1
    _w_tail=""
    _w_root=""
    _w_phys=""
    case $_w_p in
        /*)          _w_a="/"  ; _w_p=${_w_p#/}  ;;
        [A-Za-z]:/*) _w_a="${_w_p%%/*}/" ; _w_p=${_w_p#*/} ;;
        *)           _w_a=$_w_home ;;
    esac
    # The textual reading starts AT the anchor, so a `..` at the top pops into it
    # the way Win32 pops it — `C:/..` is `C:/` and `/..` is `/`, which is
    # `${…%/*}` on `C:` and on the empty string. It is the anchor as the CALLER
    # spelled it, not as `cd -P` re-spells it: Win32 applies `..` before any I/O,
    # so nothing here may have been resolved first.
    _w_desc=${_w_a%/}
    cd -P -- "$_w_a" 2>/dev/null || return 1
    # `IFS` unset is not the same as `IFS=""` — the first splits on the default
    # set, the second not at all — and reading it bare would abort under
    # `set -u`, which in a fail-open hook is an ALLOW.
    _w_ifs=${IFS-$' \t\n'}
    case $- in *f*) _w_glob=1 ;; *) _w_glob="" ;; esac
    IFS="/"
    # `set -f` so a segment containing a glob character is not expanded by `set`.
    set -f
    # shellcheck disable=SC2086
    set -- $_w_p
    [ -n "$_w_glob" ] || set +f
    IFS=$_w_ifs
    # Over the bound, before a single component is walked. 2, not 1: the caller
    # reads it as "deny without comparing", where 1 is "this is not that file".
    [ $# -le "$_ROUTE1_MAX_SEGMENTS" ] || return 2
    for _w_s; do
        case $_w_s in ""|".") continue ;; esac
        # Descend only while nothing is pending: past the first component that
        # is not there, every later one is relative to a directory that does not
        # exist either. The test resumes when `..` brings the tail back to
        # empty, because then we ARE standing where the path now points —
        # `<root>/nope/../.loci/contract.yaml` has to reach `.loci` the same way
        # the guarded path does, or the two sides split in different places and
        # the comparison below is measuring nothing.
        #
        # `..` skips the `[ -d ]` (it is always a directory where a `cd` could
        # work) and, once one of them has left us where we already were, every
        # later one at the top is a no-op: `/..` is `/`. Without that memo a
        # `file_path` of nothing but `../` paid a stat AND a `cd` per component
        # — 0.16 ms each, 3.5 s at the 64 KB the field read caps at, and past
        # the 5 s in `hooks.json` under any concurrent load.
        if [ -z "$_w_tail" ]; then
            if [ "$_w_s" = ".." ]; then
                # This one is applied to the FILESYSTEM, where the text below
                # gets the other reading: `_w_desc` pops the NAME we descended
                # through, which is what Win32 does and what `cd -P ..` does not
                # once that name is a link. Popped in every arm below, the memo
                # included — there `_w_desc` is already the anchor and `${…%/*}`
                # leaves it alone.
                _w_phys=1
                _w_desc=${_w_desc%/*}
                [ -n "$_w_root" ] && continue
                _w_was=$PWD
                if cd -P -- .. 2>/dev/null; then
                    [ "$PWD" = "$_w_was" ] && _w_root=1
                    continue
                fi
                # It could not go up — an unreadable parent, not the root. Fall
                # through: with nothing pending, the collapse below is a no-op,
                # which is where a `cd` that cannot move leaves us anyway.
            elif [ -d "$_w_s" ] && cd -P -- "$_w_s" 2>/dev/null; then
                _w_desc=$_w_desc/$_w_s
                _w_root=""
                continue
            fi
        fi
        if [ "$_w_s" = ".." ]; then
            _w_tail=${_w_tail%/*}
        else
            _w_tail=$_w_tail/$_w_s
        fi
    done
    return 0
}

# $1 = the file_path, $2 = the project root, $3… = the guarded repo-relative
# paths. Prints the one $1 IS, or nothing. One subshell for all of them, and
# BOTH SIDES OF EVERY COMPARISON ARE WALKED BY `_walk` INSIDE IT — that is the
# whole point, and the thing not to undo.
_guarded_match() (
    # `cd` consults CDPATH and PRINTS the directory when it uses it.
    CDPATH=
    _w_home=$PWD
    _gm_fp=$1
    # A trailing `/` would turn `$root/$g` into `$root//$g`. Harmless to the
    # walk, which skips empty segments, and stripped anyway so the string
    # comparison at the bottom has one spelling. Also done at the call site,
    # where `-ef` needs it.
    _gm_root=${2%/}
    shift 2

    # Is (directory $1, tail $2) the same file as (directory $3, tail $4)?
    #
    # `-ef` on the directory plus the tail as text is the answer. The joined
    # string beside it only ever WIDENS the deny and it is there for two things
    # `-ef` cannot do: a filesystem whose inode emulation has nothing to answer
    # with (a network share, FAT), and CASE — `[[ ]]` folds it under
    # `nocasematch` in the ambient locale, so a project at `…/Проект` still
    # matches an edit spelled `…/ПРОЕКТ/…` on a host where the filesystem does
    # not fold it for us. That is what the guard did before F16 and it is
    # pinned; the locale restore above is what makes it work.
    #
    # It is not the two-independent-strings defect returning. Every operand
    # comes from `_walk`, in this subshell — a second reason to deny, never a
    # second rung to disagree with.
    #
    # ⚠ DELETING THE JOINED STRING LEAVES THE SUITE GREEN ON WINDOWS, measured.
    # NTFS folds the case itself, so `-ef` answers first and all 385 tests pass.
    # The one test that holds it is
    # `test_route_1_still_folds_case_the_way_the_filesystem_does`, and it only
    # bites on a case-SENSITIVE host: on WSL the same deletion fails it. Read a
    # green Windows run as saying nothing about this line.
    _gm_same() {
        { [ "$1" -ef "$3" ] && [[ $2 == "$4" ]]; } || [[ "$1$2" == "$3$4" ]]
    }

    # Walk $1 BOTH ways, into `_gm_d`/`_gm_t` (POSIX) and `_gm_ld`/`_gm_lt`
    # (Win32). The two are the same pair unless a `..` was applied by CDing —
    # see the note above `_walk` — so the second walk is paid for only by a path
    # that has one, and `<root>/.loci/contract.yaml` has none.
    #
    # The Win32 reading is RE-WALKED rather than compared as the string
    # `_w_desc$_w_tail` is: that string is anchored the way the caller spelled
    # the path, and on MSYS `C:/proj/…` and `/c/proj/…` are two spellings of one
    # directory. Comparing them as text is F16 item 1, which is how three
    # guarded files became writable with nothing but a `./` pad. Walking it puts
    # it back on `-ef`, and on the same split point as the other side.
    #
    # Defined HERE, inside the subshell, for the same reason `_guarded_match`
    # is one: everything that CDs stays in it, and `_walk` has no caller outside
    # it (`test_the_walk_is_only_ever_run_inside_a_subshell`).
    _gm_walk2() {
        _walk "$1" || return $?
        _gm_d=$PWD
        _gm_t=$_w_tail
        _gm_ld=$_gm_d
        _gm_lt=$_gm_t
        [ -n "$_w_phys" ] || return 0
        # Two spellings of the collapsed path are not ones a `cd` accepts:
        # empty is the filesystem root, and a bare `C:` is the drive's.
        _gm_lp=$_w_desc$_w_tail
        case $_gm_lp in "") _gm_lp=/ ;; [A-Za-z]:) _gm_lp=$_gm_lp/ ;; esac
        _walk "$_gm_lp"
        case $? in
            0) _gm_ld=$PWD; _gm_lt=$_w_tail ;;
            2) return 2 ;;
            # It could not reach its own anchor. Keep the POSIX pair rather
            # than propagating, because rc 1 out of here is "this is not that
            # file" and the reading that was ADDED may never narrow the answer
            # the guard already had. Hard to reach — this path is anchored
            # wherever the walk above was — and cheap to be right about.
            *) ;;
        esac
    }

    _gm_walk2 "$_gm_fp"
    case $? in
        0) ;;
        2) return 2 ;;          # over `_ROUTE1_MAX_SEGMENTS`: deny, unwalked
        *) return 1 ;;
    esac
    _gm_dir=$_gm_d   ; _gm_tail=$_gm_t
    _gm_ldir=$_gm_ld ; _gm_ltail=$_gm_lt
    for _gm_g; do
        # The bound applies to THIS side too, and it has to answer the same way
        # or the file's own claim about it is false: swallowing rc 2 as "not
        # this file" is a bound that denies on one side and ALLOWS on the other.
        # Reached when `$root` is itself enormous — no `CLAUDE_PROJECT_DIR` and
        # a payload `cwd` of thousands of segments, which is low but not nil.
        _gm_walk2 "$_gm_root/$_gm_g"
        case $? in
            0) ;;
            2) return 2 ;;
            *) continue ;;
        esac
        # Like against like: the POSIX reading of the `file_path` against the
        # POSIX reading of the guarded path, and Win32's against Win32's. Each
        # is a self-consistent answer to "which file does this name open"; a
        # cross pair is an answer to no question at all. The `$root` side gets
        # the same treatment because it can carry a `..` too — nothing stops a
        # payload `cwd` from spelling the project root through a junction.
        if _gm_same "$_gm_dir"  "$_gm_tail"  "$_gm_d"  "$_gm_t" ||
           _gm_same "$_gm_ldir" "$_gm_ltail" "$_gm_ld" "$_gm_lt"
        then
            printf '%s' "$_gm_g"
            return 0
        fi
    done
    return 1
)

_w_dots=""     # $1 as Win32 spells it: one trailing dot off every component

# WIN32 STRIPS A TRAILING DOT FROM EVERY INTERIOR COMPONENT, and MSYS's `stat`
# strips none — it hands NT the name verbatim, where a trailing dot is literal.
# So `<root>/.loci./contract.yaml` opens the Contract Envelope while `[ -d
# ".loci." ]` is false, `-ef` is false, and the walk puts `.loci.` in the tail
# and compares it against a different file. That is F18 item 1: three guarded
# files writable, on `main` and after F16, from nothing but a `file_path`.
#
# EXACTLY ONE DOT, and interior components only. Measured 2026-09-12 through
# Win32 itself (`open()`), every row written and the guarded file read back —
# `runs/F18-2026-09-12/probe_win32.py` in the follow-ups repo:
#
#     <root>/.loci./contract.yaml        opens the Contract Envelope
#     <root>/.loci/build./flags.json     opens the flag pin
#     <root>/.loci./CONTRA~1.YAM         opens it too — the 8.3 short name,
#                                        which no basename gate below admits
#     <root>/.loci../contract.yaml       ENOENT  ← a RUN of dots is not stripped
#     <root>/.loci..../contract.yaml     ENOENT
#     <root>/.loci /contract.yaml        ENOENT  ← nor is a trailing SPACE
#     <root>/.loci/.../contract.yaml     ENOENT
#     <root>/.loci/contract.yaml.        opens it  ← the END of the whole path
#     <root>/.loci/contract.yaml..       opens it     loses the whole run, and
#                                                     both dots and spaces
#
# The last two are the `*.|*" "` loop at the call site, which is a different
# rule for a different position and stays exactly as it is.
#
# HOW, and why it is not the obvious loop over components. That loop accumulates
# `$out/$seg`, which copies what it has accumulated once per component: 7.4 s on
# a 64 KB path here, against the 5 s in `hooks.json` that fails OPEN. These five
# substitutions are 0.08 s on the same path and 0.02 s on the padded one — one
# pass each, and the size is bounded by the field read's own cap rather than by
# anything this function decides, which matters because the `-ef` that consumes
# it may not be bounded (see the call site).
#
# ⚠ THE ESCAPE IS A DOT, and that is the whole trick. `.` and `..` are the two
# components Win32 reads as instructions rather than as names, and the strip
# would quietly turn `/../` into `/./`. So each is given ONE MORE DOT first —
# `/../` becomes `/.../` and `/./` becomes `/../` — and the strip, which takes
# exactly one dot off a component, is its own inverse. Nothing is held aside in
# a sentinel and there is nothing to collide with.
#
# ⚠ IT WAS A SENTINEL BYTE FOR ONE ROUND, and that was a HOLE, not a style
# choice. Two control bytes stood in for `.` and `..`, and a path already
# carrying one was returned unchanged — justified by "Win32 forbids both bytes
# in a name, so the write cannot land". That is false for the reason this whole
# task exists: Win32 collapses `..` TEXTUALLY, before it validates any name, so
# a component carrying the byte is popped away and never reaches the
# filesystem. `<root>/x/../.loci./contract.yaml` therefore wrote the
# Contract Envelope and was ALLOWED, on all three files, and the payload
# prefilter's `\u00` arm exists precisely to admit the escape that carries it.
# Round 1 of review found it; `test_win32_dots_is_not_switched_off_by_a_byte_in
# _the_path` pins it.
#
#   * the leading `/` makes the FIRST component interior like every other one,
#     so a relative `./x` is not turned into an absolute `/x` — which is exactly
#     what the retired `_lexical` used to do to every relative path. The
#     trailing one does the same for the LAST component, which matters for the
#     `$root` this is also applied to: the root's last component is interior in
#     the `$root/$g` it is joined into, and a root is the one operand that has
#     NOT been through the end-of-string strip at the call site.
#   * twice each: `${var//…}` does not rescan what it has consumed, so `/../../`
#     hands one pass every other one, and the second pass finds what is left.
#   * LONGEST FIRST — `...`, then `..`, then `.` — or the `/../` that escaping a
#     `.` produces is escaped again as if it had been a `..` all along.
#   * `...` is escaped for the same reason `..` is, and it is the last one that
#     needs it: the strip turns a run of k dots into k-1, and only k=3 lands on
#     an INSTRUCTION. Without it `<root>/x/.../.loci/contract.yaml` read as
#     `<root>/.loci/contract.yaml` and was denied — on POSIX, where `...` is an
#     ordinary directory name and the write goes somewhere else entirely, and
#     on Windows, where Win32 opens no spelling of it at all. Round 1 of review
#     found that over-deny; four dots and up survive as NAMES either way, which
#     is what both platforms do with them.
_win32_dots() {
    _wd=/$1/
    _wd=${_wd//\/...\//\/....\/}
    _wd=${_wd//\/...\//\/....\/}
    _wd=${_wd//\/..\//\/...\/}
    _wd=${_wd//\/..\//\/...\/}
    _wd=${_wd//\/.\//\/..\/}
    _wd=${_wd//\/.\//\/..\/}
    _wd=${_wd//.\//\/}
    _wd=${_wd#/}
    _w_dots=${_wd%/}
}

# WHICH guarded file the `file_path` $1 IS, under the project root $2, into
# `$_hit` — the two forkless arms, then the walk. Empty for none of them.
#
# A FUNCTION because route 1 asks it twice: once for the path as the payload
# spells it, and once for the spelling Win32 opens (`_win32_dots`). The two are
# the same strings for every path that has no dotted component, which is every
# ordinary one, and the call site skips the second call there. The arms are
# documented at that call site, where the order they run in is the point.
#
# ⚠ THE ROOT IS A PARAMETER, not the global, and that is the whole of what the
# second call re-decides. Both sides of every comparison have to be spelled by
# the same rules or the comparison is between two different questions — F16's
# defect, in one sentence. A `$root` with a dotted component is not a Windows
# path (Win32 cannot create one) but it IS a reachable payload: with no
# `CLAUDE_PROJECT_DIR` and no git checkout, `root` falls back to the payload's
# own `cwd`, which the model writes — the same reachability
# `test_the_segment_bound_denies_on_the_guarded_side_too` rests on.
_route1_hit() {
    for _g in $_ROUTE1_GUARDED; do
        if [ "$1" -ef "$2/$_g" ]; then _hit=$_g; return 0; fi
        case $1 in */"$_g"|"$_g"|"./$_g") _hit=$_g; return 0 ;; esac
    done
    # Past the arms, nothing resolves to a guarded file without carrying that
    # file's own basename — a link that does not is caught by the `-ef` above,
    # and `test_a_symlink_that_hides_the_name_is_a_known_gap` pins what is left
    # (a link the payload-level prefilter never admits). So this is the whole of
    # route 1 for a payload that merely says `contract` somewhere: no subshell,
    # no fork, and one `case` per guarded file.
    for _g in $_ROUTE1_GUARDED; do
        case $1 in
            *"${_g##*/}"*)
                _hit=$(_guarded_match "$1" "$2" $_ROUTE1_GUARDED)
                # 2 is `_ROUTE1_MAX_SEGMENTS`: too long to walk inside the
                # budget, so deny it under the name it actually carries
                # rather than hand the model a reason about another file.
                [ $? -eq 2 ] && _hit=$_g
                return 0 ;;
        esac
    done
    return 0
}

# Case-insensitive throughout, via `nocasematch` — a SHELL OPTION, not a `tr`.
# The first version of this lowercased both sides through `printf | tr`, which
# put an external binary on the verdict path: with `tr` off a minimal PATH both
# strings came back empty, every comparison failed, and all four files became
# writable with exit 0 and no output. That is the same silent fail-open the
# payload read was just fixed for, and worse — the payload parses fine and the
# guard simply declines to act. `nocasematch` is a bash 3.1 builtin and forks
# nothing.
guard_path() {
    # $1 = repo-relative guarded path, lowercase. $2 = the deny reason.
    [ "$_hit" = "$1" ] || return 0
    _cg_what="$1"
    deny "$2"
}

if [ -n "$fp" ]; then
    # The ambient locale, back, for as long as route 1 runs — see the note at
    # the top. Everything below this line compares PATHS, and their case folding
    # has to be the filesystem's, not ASCII's. Restored to C at the end of the
    # branch so route 2 gets bytes; route 1 exits through `deny` on a match, so
    # the only path that reaches route 2 is the one that falls out of the `fi`.
    LC_ALL="$_LC_AMBIENT"

    # Forks git, so it stays out of route 2 — the hot path. `cwd` is already in
    # hand from the forkless read above; there is no jq fallback for it, because
    # the only host that read is unavailable on is one where the library did not
    # source, and route 1 is off there anyway.
    root="${CLAUDE_PROJECT_DIR:-}"
    if [ -z "$root" ]; then
        root=$(cd "${cwd:-$PWD}" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null)
    fi
    [ -n "$root" ] || root="${cwd:-$PWD}"

    # A real payload carries the native spelling (`C:\proj\.loci\build.yaml`) —
    # `post-edit-hook.sh` says so twice. Git Bash resolves a backslash in a FILE
    # TEST but not in a parameter expansion, so without this the suffix fallback
    # cannot match one and the deny rests entirely on MSYS realpath's implicit
    # drive-path conversion. Forkless. (A backslash is a legal filename character
    # on POSIX, so this over-denies a file literally named `a\.loci\build.yaml`
    # there — the same direction this guard errs in everywhere else.)
    fp="${fp//\\//}"
    root="${root//\\//}"

    # A LEADING `//` IS A NETWORK LOOKUP, and both things route 1 does with a
    # path — one `-ef`, one `cd -P` — take it. Measured here: 2.83 s of a 5 s
    # budget for a `file_path` naming a server that is simply not there, and a
    # server that answers slowly rather than not at all can block far longer.
    # That is the fail-open this whole task is about, so collapse it — on BOTH
    # sides, because the comparison only works while the two are spelled by the
    # same rules. A project that really is on a share is then compared without
    # the share being physically resolved, which is symmetric and therefore
    # still decides correctly; `realpath` never resolved it either, it just
    # rewrote the text.
    while :; do case $fp in //*) fp="${fp#/}" ;; *) break ;; esac; done
    while :; do case $root in //*) root="${root#/}" ;; *) break ;; esac; done

    # Three spellings Win32 opens as the guarded file and a byte comparison reads
    # as a different one: a trailing dot or space (stripped by the filesystem),
    # and the `::$DATA` alternate-data-stream suffix. All builtin, no fork.
    #
    # ⚠ THE ADS SUFFIX IS CUT FROM THE LAST COMPONENT ONLY. `${fp%%::*}` cut the
    # path at the FIRST `::` and threw away everything after it, guarded name
    # included, and Win32 then pops the invalid component TEXTUALLY with the
    # `..` that follows it and opens what is left:
    #
    #     <root>/a::b/../.loci/contract.yaml   writes the Contract Envelope
    #
    # — measured, ALLOWED on `main` and by F18's first draft, with no escape, no
    # dotted component, no symlink and no long path. Same shape as F18 item 2
    # and found by its review round. `a::b` is not a name anything can open, but
    # it does not have to be: it only has to be popped.
    case $fp in
        *::*)
            case $fp in
                */*) _cg_head=${fp%/*}/ ;;
                *)   _cg_head="" ;;
            esac
            _cg_last=${fp##*/}
            fp=$_cg_head${_cg_last%%::*}
            ;;
    esac
    while :; do
        case "$fp" in
            *.|*" ") fp="${fp%?}" ;;
            *) break ;;
        esac
    done
    [ -n "$fp" ] || exit 0

    # From here to the end of route 1. Route 2's verb matching stays
    # case-SENSITIVE: those are shell commands, and a case-insensitive match
    # there would deny prose that merely mentions one.
    shopt -s nocasematch

    # WHICH guarded file this is, decided once. The suffix arms go first: they
    # are forkless and they answer every ordinary spelling (`…/.loci/build.yaml`
    # as written), so the common case never reaches the walk. They also
    # deliberately over-deny — a `.loci/contract.yaml` in ANOTHER checkout is
    # denied too, which is the direction this guard errs in everywhere else.
    #
    # ⚠ AND THEY ARE WHAT HID ALL FOUR OF F16. Every item above was invisible
    # because the arm answered first and every route-1 fixture wrote a path it
    # could answer; `./` x60 is enough to get past it, and then nothing but the
    # comparison is left. So read them as a FAST PATH and never as the decision:
    # the walk below has to be right on its own, and a test that does not reach
    # it is measuring this arm.
    #
    # `-ef` rides WITH them, and it has to be here rather than inside the walk.
    # It is the one test that follows a link in the LAST component — the walk
    # cannot, because reading a link's target needs `readlink` (a fork, and
    # BSD's has no `-f`) — and the basename gate below would hide it, because a
    # link named `notes.txt` carries no guarded basename to gate on. Putting it
    # behind that gate is a REGRESSION: `main` denies a name-hiding link and
    # this allowed it, for all three files, measured. Forkless (three stat
    # pairs), both operands as given before anything has `cd`-ed, and it only
    # ever widens the deny — a file that is not there yet answers false.
    #
    # ⚠ AND IT IS NOT BOUNDED BY LENGTH, which it was for one round. A `stat` is
    # Win32 canonicalising the whole string, so three of them on a 64 KB
    # `file_path` cost 0.71 s, and skipping them past 4 096 characters looked
    # free: the walk below refuses anything that long anyway. It is not free,
    # and it is this file's own trap with a different constant. `-ef` is the
    # ONLY thing route 1 has for a path whose last component is not a guarded
    # basename — the walk does not follow a link there and the gate below does
    # not even admit it — so a bound that skips `-ef` is a bound that ALLOWS
    # past itself. Measured: `<root>/` + `./`x2045 + `notes.txt` (a link to
    # contract.yaml) is 4 119 bytes, was ALLOWED, and the write that followed
    # landed in the Contract Envelope. The boundary was exactly the gate —
    # 4 095 denied, 4 097 allowed — and `<root>/.loci/CONTRA~1.YAM`, the NTFS
    # 8.3 short name, is a second spelling that needs no link at all.
    #
    # What removing it costs, measured end to end at the 64 KB field cap: the
    # worst shape 0.64 s → 1.29 s and the second 0.21 s → 0.95 s, everything
    # else unchanged, against a 5 s kill. That is the price of the only defence
    # there is, and route 1's ordinary traffic never sees it: an Edit that exits
    # at the prefilter is 0.042 s and a 64 KB Write that reaches route 1 is
    # 0.074 s here against 0.103 s on `main`.
    _hit=""
    root="${root%/}"
    _route1_hit "$fp" "$root"

    # AND AGAIN, ON THE SPELLING WIN32 OPENS. `_win32_dots` takes a trailing dot
    # off every interior component; where that changes nothing the path is
    # decided once, and where it changes something the two spellings name two
    # different files and route 1 denies on EITHER of them. Both the path and
    # the root go through it, and the pair is passed together: one reading on
    # one side and the other reading on the other is not a comparison.
    #
    # Both readings are kept rather than one replacing the other, because each
    # is right on one platform. On Windows the stripped one is what opens, and
    # the raw one cannot even be stat-ed. On POSIX a component ending in a dot
    # is an ordinary name and the RAW one is the file — so replacing it would
    # LOSE a deny `main` has: `<root>/L./.loci/contract.yaml`, where `L.` is a
    # real link to the root, resolves to the Contract Envelope there and to
    # nothing at all once the dot is gone.
    #
    # ⚠ IT WIDENS THE DENY, and this hook runs ahead of every Edit, Write and
    # Bash in every repo the plugin is installed for. What it costs is a POSIX
    # path whose directory really does end in a dot and whose basename is a
    # guarded one — `<root>/.loci./contract.yaml` on Linux is a different file
    # and is now denied. That is the same trade the backslash mask above makes
    # (a POSIX file named `a\.loci\build.yaml`) and the same one the trailing-dot
    # strip beside it has always made, in the direction this guard errs in
    # everywhere else. `test_a_dotted_component_is_read_the_way_win32_reads_it`
    # pins the deny and `test_an_ordinary_dotted_directory_is_still_allowed`
    # pins where it stops.
    if [ -z "$_hit" ]; then
        _win32_dots "$fp";   _cg_fpw=$_w_dots
        _win32_dots "$root"; _cg_rootw=$_w_dots
        { [ "$_cg_fpw" = "$fp" ] && [ "$_cg_rootw" = "$root" ]; } \
            || _route1_hit "$_cg_fpw" "$_cg_rootw"
    fi

    guard_path ".loci/contract.yaml"    "$REASON_FILE"
    guard_path ".loci/build.yaml"       "$REASON_RECIPE"
    guard_path ".loci/build/flags.json" "$REASON_FLAGS"

    # Only reached when nothing denied. Route 2's verbs are shell commands and
    # must stay case-SENSITIVE — `grep 'Contract Edit'` is not a contract write.
    # A payload carrying both a file_path and a command is not a shape the
    # harness sends today; this makes that an assumption the guard does not rest
    # on. The locale goes back to C with it, for the same reason and in the same
    # place: route 2 measures its own work in bytes.
    shopt -u nocasematch
    LC_ALL=C
fi

# ── route 2: a Bash command running a contract-writing verb ──────────────────
#
# Shell writes (`cat >`, `sed -i`, `cp`, `git restore`) are deliberately NOT
# matched — shape-matching produced every false positive this guard ever had and
# caught nothing; the commit diff is the backstop. Reads stay allowed: the agent
# must know the bounds, and the skill runs `git diff -- <path>` itself.
#
# WHAT ROUTE 2 IS. A speed bump over the verbs the tool's own `--help`
# advertises, not a sandbox — and no amount of lexing makes it one. These all
# RUN a contract-writing verb and all pass. They are FAMILIES, not spellings:
# writing them as three examples is how the list stopped matching the code, and
# two review rounds found members that were not on it.
#
#   R1 the verb, quoted in part. `loci "contract" accept`, `loci contract
#      "accept"`, `loci contract acc"ept"` — nothing here unquotes.
#   R2 the verb, expanded. `V=accept; loci contract $V` — nothing here expands.
#   R3 the BINARY hidden from a matcher that reads a segment's unquoted words:
#      through a variable (`$LOCI contract accept`), inside quotes
#      (`'/usr/bin/loci' …`, `"$HOME/bin/loci" …`), behind a nested shell
#      (`sh -c '…'`, `bash -lc '…'`, `eval "loci contract accept"`), behind a
#      backslash the mask keeps for Windows paths (`l\oci contract accept`),
#      under a name the patterns do not list (`loci.cmd`, `loci.bat`), or
#      PRODUCED by a substitution — `$(which loci) contract accept`,
#      `` `which loci` … ``, `"$(command -v loci)" …`. That last family used to
#      be filed under R5 because one cut severed both; they are different
#      residuals and F11 separated them. The substitution's body is a command
#      of its own and `which loci` is not an invocation, so what is left of the
#      outer word is `contract accept` with no binary in it. Closing it means
#      EXPANDING, which takes `loci "contract" accept` (R1) with it.
#   R4 the substitution the scan closes EARLY. Bash finds the `)` that ends
#      `$( … )` by parsing; a `case` arm, a `#` comment or a heredoc body line
#      can put an unmatched `)` inside one, and the scan reads the first as the
#      closer. Inside `"…"` that masks the rest of the substitution away:
#      `echo "$(case $x in *) loci contract accept;; esac)"`. See `_r2_mask`.
#   R7 a `#` inside a COMPOUND TOKEN. Bash has no comments inside `(( … ))`,
#      `$(( … ))`, `[[ … ]]` or an extglob `@(…)` — each is read as one token —
#      and it does have one after a function definition's `()`. The `#` arm
#      decides on the byte before, which cannot see any of that, so it invents
#      a comment in the first four and misses one in the last: `((#)) ; loci
#      contract accept` runs the verb and is allowed, and 20 more like it.
#      Six characters of prefix — recorded rather than chased because closing
#      it means teaching the scan four constructs, and this arm has produced a
#      critical in each of the last three review rounds. `bb4a547` denied them
#      all, by matching text. It is written here as a `#` family and the same
#      blindness is wider than that: a QUOTE is not a quote inside `$(( … ))`
#      either, so `$(('$(loci contract accept)'))` and its backtick twin run
#      the verb and are allowed. Same cause — the scan does not know it is
#      inside a compound token — and the same decision. (R10, below, is cheaper
#      than any of these.)
#   R6 an odd quote in a HEREDOC BODY. The body is not command text and the
#      scan cannot know where it ends, so a stray apostrophe in one opens a
#      region that hides every command after the heredoc:
#      `cat <<EOF > n.md` / `don't` / `EOF` / `loci contract accept`. The same
#      defect in a `#` COMMENT is fixed, but not because comments are easy —
#      round 4 found both ends of one wrong. A comment's start needs the word
#      tracking a parser needs, and its end is not simply a newline: a backtick
#      closes an open backtick region without honouring it. What makes those
#      tractable and a heredoc not is that both are single BYTES; a heredoc
#      ends at a line equal to a word chosen earlier in the command.
#      See the heredoc paragraph in the route 2 header.
#   R8 an ESCAPED SEPARATOR inside a word. `\(`, `\)`, `\;`, `\&` and `\|` are
#      ordinary characters to the shell and keep the word going, and the mask
#      emits them — so the split cuts a word bash does not cut, and
#      `loci --root a\;b contract accept` becomes `loci --root a` and
#      `b contract accept`, neither an invocation. All five RUN the verb; all
#      five are allowed at `43ed7a9` too, so this is inherited and not F11's.
#      It is R5's shape reached through an escape rather than a substitution,
#      and F11 closed the sixth member — `` \` `` — only because the mask
#      stopped writing a backtick for any other reason, which let the backtick
#      leave `_R2_SEPARATORS` entirely. The other five cannot go that way: the
#      mask still writes `(` and `)` for a real subshell, and `;`, `&` and `|`
#      are not scan characters at all, so the scan never sees one to mark. What
#      would close it is the split itself moving into the scan — the mask
#      emitting the boundaries it knows about instead of the separators being
#      re-found in its output afterwards — which is a change the size of F10.
#      `bb4a547` denied all five, by matching text. Filed as F25.
#   R9 an EXPANSION THE SHELL ERASES, glued to a token the guard has to
#      recognise. The mask writes `$` and lets the NAME through as ordinary
#      text, so `${X}loci contract accept` masks to the word `${X}loci` — which
#      matches none of the binary patterns — while bash removes the empty
#      expansion and runs `loci`. `${X:-}`, `${X#a}`, `$@`, `$*` and `$1` do it
#      too, and so does the same trick on the verb (`loci ${X}contract accept`,
#      which is R2 by another spelling). All of these RUN, and all of them are
#      allowed at `43ed7a9`: the family is inherited, not F11's.
#      **What F11 adds is three spellings, not a capability** — with the lift,
#      `$X$(true)loci contract accept` joins the erased `$X` to the binary where
#      the `$(` used to cut between them. The family's cheapest member needs no
#      substitution at all (`$@loci contract accept`, 22 bytes, allowed at
#      `43ed7a9`), so the three add no shorter route to it; a first draft of this
#      paragraph said they cost four characters more, and `` $X`true`loci … ``
#      is the same length as `${X}loci …`, which is why the claim is about
#      REACHABILITY and not about length. Closing it means the mask CONSUMING an
#      expansion rather than writing its text — bash's name rules, `${…}` with
#      its own operators inside, and the special parameters — which is the same
#      "teach the scan a construct" cost R7 was recorded rather than pay, on the
#      arm every command with a `$` goes through. `bb4a547` denied them all, by
#      matching text. Filed as F26.
#  R10 a REDIRECTION OPERATOR glued to the binary or to `contract`. Loop 1 and
#      loop 2 split a segment on SPACES (`IFS=" "; set -- $1`) and bash also
#      breaks a word at `<` and `>`, so `loci>x contract accept` arrives as the
#      single word `loci>x`, matches no binary pattern, and RUNS — with the file
#      `x` created as a side effect. `loci>>x`, `loci<&0` and `loci<$(pwd)` do
#      it too. `<` and `>` are deliberately NOT in `_R2_SEPARATORS` (they
#      separate WORDS, not commands) so nothing re-splits afterwards, and a byte
#      sweep of all 94 printable bytes in both positions found exactly two that
#      split for bash and not for the matcher: this and the `$` of R9.
#      **The cheapest residual in this file** — 22 bytes, no quoting, no `$`, no
#      substitution — and inherited: `43ed7a9` allows every spelling and
#      `bb4a547` denied them. Filed as F27.
#  R11 a TRAILING BACKSLASH. At end of input the `\` arm emits a literal
#      backslash, so the verb arrives as the token `accept\` and matches no
#      verb — while the shell that runs the command supplies the newline the
#      scan never saw, making it a line continuation, and the verb runs.
#      `loci contract accept\` is 21 bytes. Inherited, and found by the same
#      sweep as R10; `bb4a547` denied it. Filed as F27 with R10, because both
#      are the same sentence: the matcher's idea of a word is not bash's.
#
# **R5 is CLOSED** — F11, 2026-09-13. A substitution BETWEEN the binary and the
# verb (`loci --root $(pwd) contract accept`) used to cut one invocation into
# pieces that were each not an invocation, because `(`, `)` and the backtick
# were all segment separators. The scan now LIFTS a substitution out of the word
# around it instead of cutting the word at it — see *THE TWO STREAMS* in
# `_r2_mask`. The number is retired rather than reused: F10's evidence, three
# task files and two tests name these families by number.
#
# `bb4a547` denied most of these, by matching the text of every command at every
# size — which is what made it deny prose, which is what F07 replaced it for.
# All of it is the same class of thing as `cat > .loci/contract.yaml`, allowed
# for the same recorded reason, and three tests carry the list
# (`test_the_evasions_are_recorded_not_closed`,
# `test_a_binary_the_guard_cannot_see_is_the_price_of_reading_tokens` and
# `test_a_substitution_can_hide_an_invocation`) so the next reader finds them
# written down instead of believing this is airtight.
# ADR-0015's commit diff is what actually stands behind it.
#
# NOTHING is denied here for the recipe. `loci init`, `loci init set`,
# `loci init add-file` and `--refresh` are the sanctioned write path (§6.5) — the
# consent lives in the skill's own question before it invokes them — and a recipe
# nobody can write is a project nobody can measure. The consequence to state
# plainly, because the shipped prose must not claim otherwise: a SHELL write to
# the recipe or to flags.json is not blocked here, and for flags.json nothing
# catches it afterwards either.
#
# The matcher reads TOKENS, not text. It used to substring-match the
# whitespace-normalised command, which cannot tell a verb being run from a verb
# being written down: `git commit -m "… loci contract accept …"`, an `echo`
# appending a handoff note and a heredoc writing documentation were all denied.
# That is exactly the defect that got the write-SHAPE list deleted from this
# route — "it denied a `.c` edit whose comment mentioned the path" — reached
# through commands instead of paths.
#
# So the command is cut into SEGMENTS at the shell's own command separators and
# a segment is denied only when it is an INVOCATION. Quoting IS read now — one
# character-level scan (`_r2_mask`) removes quoted text before the split, so a
# separator inside a quoted argument no longer starts a segment and a quoted
# `loci` is no longer a word the split can hand back.
#
# The residual that remains is the HEREDOC BODY, and it is wider than this
# comment used to claim. A heredoc body is not quoted text — `cat <<'EOF'`
# quotes the DELIMITER, not the lines after it — and the guard is handed one
# flat command string with no way to know where the body ends. So every
# unquoted body line is read like any other command, and what is denied is not
# only "a line that begins with the invocation" but ANY unquoted line where a
# `loci` token is followed by `contract` and a writing verb, wherever in the
# line they sit. `then run loci contract accept yourself` is denied, and that is
# the most natural spelling of the very handoff note this route exists to
# permit. The quoted spellings all pass — `echo 'run: ! loci contract accept' >>
# n.md`, or a body line with the command in `'…'` — and so does writing the file
# with the Write tool. `test_an_unquoted_mention_is_the_stated_residual` pins
# both sides.
#
# It cuts the other way too, and that is the part this comment used to get
# wrong: a heredoc body is read as command text, so an ODD QUOTE in one opens a
# region the scan never closes, and every command AFTER the heredoc stops being
# visible. `cat <<EOF > n.md` / `don't` / `EOF` / `loci contract accept` runs
# the verb and is allowed. That is residual R6, and it is what makes the
# paragraph above conditional rather than absolute — whether an unquoted body
# line is denied depends on how many apostrophes precede it. The `#` COMMENT
# half of the same defect IS fixed (see the `'#'` arm in `_r2_mask`), because
# both ends of a comment are single bytes this scan already stops at; a heredoc
# body's end is a LINE equal to a word chosen earlier in the command, which is
# the thing that needs a parser. Do not chase it with a heredoc parser: parsing is where this
# guard's defects have always come from, and `_r2_mask` is already as much of
# one as this file should carry. It is still a far narrower surface than "the
# verb appears anywhere in the text".

# …and the same cap on SEGMENTS, because bytes do not bound the work. See the
# note on `_command_invokes_verb`'s rc 2. A command with more than this many
# separators is not a command anyone wrote.
_R2_MAX_SEGMENTS=512

# What answers over either cap. Whitespace before `contract` (so `subcontract`
# does not match) and one or more after it — the run the collapse loop used to
# buy. No trailing boundary, so `contract accepted` still matches, exactly as
# the glob it replaces did.
#
# `\\[ntr]` is a LITERAL backslash followed by n, t or r: above the cap the jq
# rung hands route 2 the raw payload, where a tab inside the command is still
# the two characters `\t`. Matching them here rather than rewriting the string
# first is what keeps this linear — four `${//}` normalising passes over 3 MB
# blew the whole budget on their own.
#
# A LONE backslash is in the separator run for a reason the caps made reachable:
# `loci contract \<newline>accept` is a line continuation, the tokenising path
# folds it away inside `_r2_mask`, and this path never sees that folding. So
# 9.8 KB of punctuation to trip the scan cap, then the verb spelled across a
# continuation, was an invocation that ran and was allowed — the same "choose
# punctuation to reach the coarse answer" move `_R2_MAX_SEGMENTS` exists to
# stop, and reachable through the segment cap on `bb4a547` too. It costs
# `contract\accept`, which is one word and runs nothing, being denied here; the
# coarse path over-denies by construction.
_R2_OVERSIZE_RE='(\\[ntr]|[[:space:]])contract(\\[ntr]|\\|[[:space:]])+(accept|init|edit|disable|enable)'

# A tab is NOT one of these: it separates words, not commands. A newline IS, and
# that is a change — it used to be flattened to a space, which is why
# `make build<newline>loci contract accept` had to be caught by substring.
#
# A BACKTICK is not one of them either, since F11. It used to be, because an
# opening backtick was written into the mask and something had to cut the
# command at it; the scan now lifts the region out instead, and the only
# backtick that can still reach a stream is an escaped one — `` echo a\`b `` —
# which the shell keeps inside the word. Leaving it in the set therefore only
# ever split a word bash does not split, and that is a HOLE and not an
# over-deny: `` loci --root a\`b contract accept `` runs the verb and was cut
# into `loci --root a` and `b contract accept`, neither an invocation.
# `(` and `)` stay: a plain paren really is a subshell, and the mask still
# writes both.
_R2_SEPARATORS=$'\n;&|()'

# The characters the quote scan below jumps between: `\` (as `\\`, because a
# bracket expression eats a lone one), `'`, `"`, a backtick, `$`, `(`, `)` and
# `#`. Built as a value rather than written into the pattern, so it carries a control character without one appearing in this
# source and without depending on how bash was started to parse it — the
# difference that cost an hour on 2026-09-09.
#
# `#` is in the set because a COMMENT is one of the two places in a flat command
# string where a quote character is not a quote, and reading one as a quote
# switched the whole route off: `make build  # don't forget<newline>loci
# contract accept` left a region open that nothing later closed, so the bare
# `loci` on the next line stopped being a command word — and it ran. `#'` is the
# whole evasion. A 372-candidate multi-line fuzz found 64 bypasses and every one
# was this. See the `'#'` arm below for where the word boundary is decided.
#
# `(` is in the set only so that `)` can be told apart from the `)` that closes
# a `$( … )`; a plain paren changes no quoting on its own. Leaving it out was a
# bypass — see the `'('` arm below.
_R2_SCAN_CHARS=$'\\\\\'"`$()#'

# How many CHARACTERS the quote scan below may walk, summed over its jumps. Not
# a count of interesting characters, and the difference is the whole point: each
# jump costs one pass over what is LEFT of the command, so the time is
# characters × length, not characters. A flat count would have to be set for the
# 64 KB case and would then throw away a 2 KB command with 2 000 quotes, which
# costs nothing. Summing the remaining length is one subtraction per jump and
# bounds the wall clock directly, which is what `_R2_MAX_SEGMENTS` says a byte
# cap fails to do.
#
# THREE SHAPES SET IT, not one, and the first cap this change carried was set
# from the easiest of them. Measured end to end, `LC_ALL=C` now making the
# locale irrelevant, all at or just under the 64 KB byte cap:
#
#                                  cap 6 M   cap 24 M   cap 48 M   addcd22
#   6 000 scan chars at the FRONT    0.22 s     0.56 s     0.84 s    0.15 s
#   5 600 scan chars at the END      0.33 s     0.95 s     0.80 s    0.16 s
#   4 500 x `😀(`  (4-byte fill)     0.21 s     0.60 s     1.00 s    0.51 s
#
# — against the 5 s in `hooks.json`, after which the hook is KILLED and
# PreToolUse FAILS OPEN, which allows the write. Before `LC_ALL=C` the same
# three read 3.17 s / 2.91 s / KILLED at 48 M, and the cap had been set from the
# front-loaded shape alone, which is the cheapest of the three: it trips after a
# few hundred jumps, while the end-loaded one buys tens of thousands of jumps
# whose remainder is short, and the multibyte
# one charged a quarter of what it spent.
#
# 24 M is ~6x of margin on the worst of the three, on a budget whose own tests
# record going red under load and green idle. Higher is better where it is free,
# because everything over the cap goes to a much blunter answer — so the cap is
# set by the wall clock, not by taste. The worst shape found since, 20 000 scan
# characters at the end of a 64 KB command, is 0.57 s. Worst overall: 0.95 s.
#
# One more thing the coarse answer costs, because every cap is a door to it and
# the doors are reachable by choosing the input: the mask's own catches go with
# it. `loci ''contract accept` runs the verb and the mask denies it; behind any
# cap the regex sees `''contract` and does not. `bb4a547` allowed it too, so it
# is not a regression — but it is why the caps are set high rather than tight.
#
# Over the cap the command goes to `_R2_OVERSIZE_RE`, the same coarse answer a
# command over the byte cap gets, and that answer is much blunter than it looks:
# the regex matches the PHRASE, with no binary token required. So what the cap
# costs is stated rather than hidden — a LARGE, scan-dense command that contains
# the words `contract accept` anywhere is denied whether or not anything runs.
# A 60 KB Solidity heredoc goes over the cap on its ~11 000 quotes and is still
# allowed, because `contract Vault` is not `contract accept`; a 60 KB document
# ABOUT this guard would not be. `bb4a547` denied every command of every size
# that said it. Commands where this bites are large: at 2 KB the budget allows
# ~2 800 scan characters, which no real command carries.
_R2_MAX_SCAN=24000000

# Where the mask stood when each open frame began, one entry per frame of
# `_r2_stack` and in the same order, so a close can cut its own body back out of
# `_r2_masked`. EVERY frame records one, including a plain `(` that lifts
# nothing: the two stacks have to stay index-aligned, because the backtick close
# pops SEVERAL frames at once and has to reach the offset of the FIRST of them.
#
# Six fixed digits per frame rather than a separator, so that reach is a slice
# and not a walk. `_R2_MAX_TOKENISE` caps the command at 65 536 bytes and the
# mask is never longer than the command, so six digits is one more than the
# value can need; `10#` on the way out, or bash reads `000012` as octal.
_r2_push_off() {
    printf -v _r2_o '%06d' "${#_r2_masked}"
    _r2_offs+="$_r2_o"
}
# …and the close cuts the body off the end of `_r2_masked` and appends it to
# `_r2_subs` on a line of its own. The newline goes IN FRONT: a body must not
# join the line before it, and an empty body must still not join the body after
# it.
#
# EVERY frame lifts, and the first version of this change lifted only the
# outermost — which was wrong, and wrong for a reason worth writing down because
# it is easy to talk yourself back into. The argument for outermost-only was
# that lifting a nested frame would cut its PARENT's body in two. That is true
# of writing a MARKER into the parent, which is what the design this task was
# filed with did; it is the opposite of what a LIFT does. A lift REMOVES the
# inner body from the parent, so the parent's word closes over the hole exactly
# as the outer command's word closes over the outermost substitution — the same
# operation, one level down.
#
# What outermost-only actually did was glue a nested body to whatever touched it
# inside the parent, and one byte of glue is enough to stop the binary being a
# word: `$(x$(loci contract accept))` masked its inner body to `xloci contract
# accept`, `xloci` matches none of the binary patterns, and it RAN. Six
# characters of prefix, found by review round 1.
#
# IT CHARGES ITSELF, and the first version of this change did not — which is the
# same mistake `_R2_MAX_SCAN`'s own comment records about counting characters
# while bash walks bytes. Both slices are O(the mask so far), so the cost is
# (closes x prefix) and the scan's per-jump charge cannot see it: the work cap
# bills the REMAINING length, and a substitution near the END of a long command
# is charged almost nothing while costing a full copy of everything before it.
# 3 464 empty backtick pairs after 56 KB of text — all under the byte cap, all
# under the work cap as it was — took 2.63 s where the same command before this
# change took 1.44 s, against the 5 s in `hooks.json` after which the hook is
# KILLED and PreToolUse fails OPEN. Charging the mask's length here puts that
# shape over the cap and onto the coarse path, which is FASTER than finishing
# the scan (measured: 1.2-1.7 s), so the cap is doing what it is for.
#
# Returns 1 over the cap, and every call site passes that up: `_r2_mask`'s
# contract is that rc 1 means "ask the coarse answer", and a helper that
# swallowed it would leave the budget unbounded in exactly the place this
# comment exists for.
_r2_sub_close() {
    _r2_sub0=$(( 10#${_r2_offs: -6} ))
    _r2_offs="${_r2_offs%??????}"
    _r2_lift
}
# A backtick region ends at the next unescaped backtick whatever is nested
# inside it, so its close pops the innermost `b`/`B` frame AND everything opened
# above it. That is ONE region and one body: the frames above were never
# terminated, and the shell discards whatever they held with them. `_r2_pre` is
# the stack below the frame, so the frame's own offset is the `${#_r2_pre}`-th
# entry — which is why the offsets are fixed-width.
_r2_bt_close() {
    _r2_n=$(( ${#_r2_pre} * 6 ))
    _r2_sub0=$(( 10#${_r2_offs:$_r2_n:6} ))
    _r2_offs="${_r2_offs:0:$_r2_n}"
    _r2_lift
}
_r2_lift() {
    _r2_subs+=$'\n'"${_r2_masked:$_r2_sub0}"
    _r2_masked="${_r2_masked:0:$_r2_sub0}"
    _r2_work=$(( _r2_work + ${#_r2_masked} ))
    [ "$_r2_work" -le "$_R2_MAX_SCAN" ] || return 1
}

# One left-to-right pass over the command, producing a copy with quoted TEXT
# removed. $1 = the command; sets `_r2_masked`. Returns 1 when the scan is over
# the cap above and the caller must fall back.
#
# WHY A SCAN AND NOT A COUNTER. This replaces per-token quote parity, which is
# not shell quoting and diverged from it three ways — each found by a review
# round after the previous round's fix, and each one a BYPASS: a token whose
# count came out odd pinned a region open, and every `loci` after it stopped
# being a command word. `echo "$(loci contract accept)"`, `echo 'a\'; loci
# contract accept` and `echo 'it'"'"'s'; loci contract accept` all RAN the verb
# and all were allowed. Brute-forced over every token of length ≤ 6 in
# {' " \ a}, the counter disagreed with the shell on 302 of them; this scan
# disagrees on none, in either direction (`test_the_scan_agrees_with_the_shell`).
#
# THE THREE RULES the counter could not express, which are just the shell's:
#
#   1. `$(` and a backtick open a COMMAND context, and they do it inside `"…"`
#      too. What is in there is a command whatever quoting surrounds it, so it
#      is KEPT — that is the whole of defect 1. Inside `'…'` they are literal.
#   2. Inside `'…'` a backslash is literal, so the `'` after it CLOSES the
#      region. Stripping `\'` unconditionally is defect 2.
#   3. Only the CURRENT quote character can close the region; the other kind is
#      ordinary text. Counting the first kind seen is defect 3.
#
# Quoted text is REMOVED rather than blanked to spaces, because a quoted region
# is part of the word it touches: `MSG="a b" loci …` is two words either way,
# but `x"y"z` is ONE word to the shell and blanking would hand the split three.
# What was INSIDE the quotes is gone either way, so a word that hides part of
# the binary or part of the verb behind them is not recognised — `loci contract
# acc"ept"` masks to `loci contract acc` and is allowed. That is the recorded
# quoted-token evasion, not a new one: `loci "contract" accept` masks to
# `loci  accept` and stays allowed for exactly the same reason it always did.
#
# A backslash before an ORDINARY character is kept, both characters, where the
# shell would drop it. That is deliberate and route 2 has always needed it: a
# real Windows payload carries `C:\Users\dev\.local\bin\loci.exe`, and the
# binary patterns below match it through the backslash. A backslash before a
# quote, a backtick, a `$` or another backslash is an escape and the escaped
# character is emitted alone, so `\'loci` stays the word `'loci` and is not the
# binary — which is what the shell does with it.
#
# `_r2_stack` remembers what to return to when a substitution closes, one
# character per open frame: `p`/`P` for `$(` opened outside quotes / inside
# `"…"`, `b`/`B` for a backtick, `s` for a plain `(`. Without it `echo "$(echo
# "hi") loci contract accept"` would come back to the wrong state and deny a
# sentence.
#
# WHERE THE STACK STOPS, AND WHY NOTHING HERE TRIES TO PATCH IT. Bash finds the
# `)` that closes a `$( … )` by PARSING the command inside it, not by counting,
# and three things put a `)` in there with no `(` to match: a `case` arm, a `#`
# comment, and a heredoc body line. The scan reads the first of those as the
# closer, restores the enclosing `"…"` early and masks the rest of the
# substitution — so `echo "$(case $x in *) loci contract accept;; esac)"` runs
# the verb and is allowed. It is written down as residual R4 in the list above
# rather than chased, and that is a decision with two rounds of evidence behind
# it:
#
#   * Counting cannot close it. A review round added "the parens must balance
#     or the mask is not trusted"; the next round defeated it with one `(`
#     anywhere in the command — in a comment, in `'…'`, in a neighbouring
#     payload field — because a command can supply its own balance. Both
#     reviewers found the same thing independently.
#   * The counting cost more than the hole. Falling back to `_R2_OVERSIZE_RE`
#     hands the verdict to a regex that matches the PHRASE with no binary token
#     at all, so one odd paren beside one `$( )` denied 22 ordinary commands,
#     none of which ran anything — led by
#     `echo "$(date +%F): 1) run loci contract accept" >> HANDOFF.md`, which is
#     the exact note F07 exists to permit.
#
# So: the scan restores the enclosing state on `)`, the way the shell does when
# the parens are honest, and the dishonest case is recorded. Not restoring at
# all was measured too and is worse: the following `"` then OPENS instead of
# closing and every quoted mention for the rest of the command is read as a
# command.
#
# THE TWO STREAMS, and why a substitution is LIFTED rather than cut (F11). A
# `$( … )` or a backtick region is a command inside a WORD of another command,
# and the mask has to be both things at once: the body is command text and must
# be segmented, while the outer word is one word and must not be split. Writing
# a `$(` marker into the mask and calling it a separator got the first half
# right and the second half wrong — `loci --root $(pwd) contract accept` became
# `loci --root $` / `pwd` / ` contract accept`, three segments of which none is
# an invocation, and it RAN. That was residual R5, inherited from F07.
#
# So the body goes to a second stream. `_r2_masked` is the outer command;
# `_r2_subs` collects the substitution bodies, each on its own line; the two are
# joined with a newline between them and segmented together
# (`_command_invokes_verb`), so the body is still read as commands and the outer
# word closes over the hole the substitution left.
#
# It is done by OFFSET — every frame records where the mask stood when it
# opened, and its close cuts that body back off the end — rather than by routing
# each emit to one of two buffers. Routing per emit would put a `case` on
# `_r2_stack` in front of every chunk, and `*[pPbB]*` is a pattern match over a
# stack a command can make thousands of frames deep, so that `case` is not even
# constant time. This way nothing at all changes on the hot path: every emit
# still appends to `_r2_masked`.
#
# EVERY frame lifts, and the recursion is the whole point: a nested `$( … )` is
# a command inside a word of ITS parent, which is the same sentence one level
# down. Its lift removes its body from the parent, so the parent's word closes
# over the hole exactly as the outer command's word closes over the outermost
# substitution. See the note on `_r2_sub_close` for the version of this that
# lifted only the outermost frame, why it looked right, and the six characters
# that got through it.
#
# What the outer stream loses is the substitution's text, and that is the point:
# a word is joined across it (`loci --root ` + ` contract accept`) rather than
# cut at it. What it does NOT lose is the body — it is in the other stream — so
# `echo "$(loci contract accept)"` is still denied, by a segment of `_r2_subs`.
#
# The join is the SHELL's word, not the shell's result, and the difference is a
# residual rather than a bug: the shell splits a substitution's OUTPUT on `IFS`,
# so `loci$(echo " ")contract accept` really is three words and really does run
# the verb. Nothing here expands, so nothing here can know that — it is the same
# class as R2, and it is allowed on every guard this file has had.
#
# WHAT IT COSTS, because every slice in this file has had to answer for itself:
# two `${var:off}` slices per substitution, each O(the mask so far), plus one
# `${#}` and one `printf -v` per frame OPENED. The shape that maximises it is a
# long prefix followed by many short substitutions — 58 KB of text and then
# 3 464 empty backtick pairs, the cheapest close there is — because the work cap
# charges the REMAINING length per jump and a jump near the end of the command
# is nearly free. That shape is why the lift charges itself: see
# `_r2_sub_close`. Measured: `runs/F11-2026-09-13/cap-calibration-f11.txt`.
#
# One boundary moved with the markers, and it is worth knowing about because it
# is a narrowing rather than a widening: a substitution used to cost TWO
# segments and now costs none, so commands that used to be pushed past
# `_R2_MAX_SEGMENTS` by their substitutions alone now get route 2's precise
# answer instead of `_R2_OVERSIZE_RE`'s coarse one. That is right in every case
# but the one it is not — a padded command carrying a recorded residual is now
# answered by the residual instead of by the regex — and it escalates nothing,
# because the same spelling unpadded was always allowed.
_r2_mask() {
    _r2_rest="$1"
    _r2_masked=""
    # The substitution bodies, and the offset of the one that is open. Both are
    # reset here rather than declared once: under `set -u` an unset `_r2_sub0`
    # kills the hook on the first `)`, and a `_r2_subs` left over from an
    # earlier call would hand the next command a segment from this one.
    _r2_subs=""
    _r2_sub0=0
    _r2_offs=""
    _r2_st=""            # "" outside | "'" single | '"' double | A = $'…'
    _r2_stack=""
    # Whether a WORD can begin at the byte about to be read — the only thing the
    # `#` arm needs, and the thing round 3 got wrong by deriving it from the
    # previous byte. Every arm below sets it for the next jump; a non-empty
    # chunk overrides it from its own last byte, in the `#` arm where it is
    # read. Start of string is a word boundary.
    _r2_wb=1
    _r2_len=${#1}
    _r2_work=0
    while [ -n "$_r2_rest" ]; do
        # Jump to the next character that can change the state. Everything
        # between here and there is one chunk: kept whole when outside quotes,
        # dropped whole when inside them.
        _r2_head="${_r2_rest%%[$_R2_SCAN_CHARS]*}"
        if [ "${#_r2_head}" -eq "${#_r2_rest}" ]; then
            [ -n "$_r2_st" ] || _r2_masked+="$_r2_rest"
            break
        fi
        [ -n "$_r2_st" ] || _r2_masked+="$_r2_head"
        # Ordinary text decides whether a word can begin after it, and this has
        # to run on every jump rather than only where it is read: with it inside
        # the `#` arm, `a\<newline>#x` kept the boundary the START of the string
        # set, so the `#` after a line continuation was read as a comment while
        # bash reads `a#x` as one word.
        if [ -n "$_r2_head" ]; then
            case "${_r2_head: -1}" in
                # Six, and every one has a row in the deny corpus. `<` and
                # `>` are NOT here: a `#` straight after one is a comment to
                # bash too, but it leaves the redirect without its target, so
                # the command is a syntax error and nothing runs either way.
                #
                # A byte sweep over all 100 printable bytes found no other
                # exception AT TOP LEVEL, and that qualifier is the whole of
                # residual R7: the exceptions are CONTEXTUAL, not byte-wise.
                # Inside `(( … ))`, `$(( … ))`, `[[ … ]]` or an extglob
                # `@(…)`, bash reads the whole thing as one token and there
                # are no comments in it at all — so `((#)) ; loci contract
                # accept` runs the verb and this set says otherwise. No choice
                # of bytes fixes that; it needs the scan to know those
                # constructs. See R7.
                #
                # `|` IS here, and round 4 took it out for the reason above,
                # which was simply wrong about the shell: `|` and `||` at end
                # of line are CONTINUATION operators, so bash keeps reading for
                # the right-hand command and a comment after one is legal.
                # `echo a |#don't<newline>loci contract accept` ran the verb and
                # was allowed for one round.
                " "|$'\t'|$'\n'|";"|"&"|"|") _r2_wb=1 ;;
                *) _r2_wb=0 ;;
            esac
        fi
        _r2_rest="${_r2_rest:${#_r2_head}}"
        _r2_c="${_r2_rest:0:1}"
        _r2_rest="${_r2_rest:1}"
        # What one more jump will cost, charged before it is taken. The arms
        # below sometimes consume a second byte without telling `_r2_len`, so
        # this runs slightly AHEAD of the real remaining length and the cap
        # bites a little early — the direction that keeps the budget, and it
        # cannot go negative because `_r2_rest` shrinks at least as fast.
        #
        # Only the remainder is charged, and a per-jump fixed term was tried and
        # dropped: the loop's own cost is real but never binds, because the jump
        # that pays it also pays a pass over what is left. Measured at 0 / 180 /
        # 2000 bytes-of-scanning per jump across five shapes built to favour it
        # — 20 000 scan characters at the end of 64 KB, 12 000 inside 2 KB — the
        # widest spread was 0.86 s vs 0.74 s. A knob that changes nothing is a
        # comment that misleads.
        _r2_len=$(( _r2_len - ${#_r2_head} - 1 ))
        _r2_work=$(( _r2_work + _r2_len ))
        [ "$_r2_work" -le "$_R2_MAX_SCAN" ] || return 1
        _r2_nx="${_r2_rest:0:1}"
        # A backtick CLOSES an open backtick region from any state, including
        # from inside `'…'`, and that has to be decided before the state
        # dispatch or the single-quote arm swallows it. Bash looks for the
        # closing backtick without regard to quoting — an unbalanced `'` inside
        # the region is an error INSIDE the substitution and nothing more — so
        # `` N=`wc -l < user's.txt`; loci contract accept `` runs the verb, and
        # a scan that let the apostrophe open a region masked everything after
        # it. 113 of 113 bypasses in a 52 000-command fuzz were this.
        case "$_r2_c$_r2_stack" in
            '`'*[bB]*)
                # ANY open backtick frame, not just one on top. Bash ends a
                # backtick region at the next unescaped backtick whatever is
                # nested inside it, so a `(` or `$(` opened in the region — and
                # never closed, because a `)` inside `'…'` is literal — used to
                # sit above the frame and the close was missed. `` echo `('` ``
                # then the invocation ran the verb and was allowed: round 1's
                # backtick critical, left half-fixed for four rounds. The
                # frames above it are discarded with it, which is what the
                # shell does to whatever was unterminated inside.
                _r2_pre="${_r2_stack%[bB]*}"
                case "${_r2_stack:${#_r2_pre}:1}" in
                    b) _r2_st="" ;;
                    B) _r2_st='"' ;;
                esac
                _r2_stack="$_r2_pre"
                _r2_wb=0
                # …and whatever those frames held is one backtick region, so
                # ONE body comes out of the mask here, not one per frame.
                _r2_bt_close || return 1
                continue ;;
        esac
        case "$_r2_st" in
        "'")
            # Inside '…' NOTHING is special but the closing quote — no escape,
            # no substitution, no other quote kind. Rules 2 and 3, together.
            if [ "$_r2_c" = "'" ]; then _r2_st=""; _r2_wb=0; fi
            ;;
        A)
            # $'…' is the one place a backslash DOES escape inside single
            # quotes, and `MSG=$'a\'b' loci contract accept` runs the verb.
            case "$_r2_c" in
                "'") _r2_st=""; _r2_wb=0 ;;
                \\)  _r2_rest="${_r2_rest:1}" ;;
            esac
            ;;
        '"')
            case "$_r2_c" in
                '"') _r2_st=""; _r2_wb=0 ;;
                \`)  _r2_push_off; _r2_stack+="B"; _r2_st=""; _r2_wb=1 ;;
                '$') if [ "$_r2_nx" = "(" ]; then
                         _r2_rest="${_r2_rest:1}"; _r2_push_off
                         _r2_stack+="P"; _r2_st=""; _r2_wb=1
                     fi ;;
                \\)  case "$_r2_nx" in
                        # The only five a backslash escapes inside "…"; before
                        # anything else it is an ordinary character.
                        '"'|\\|\`|'$'|$'\n') _r2_rest="${_r2_rest:1}" ;;
                     esac ;;
            esac
            ;;
        *)
            case "$_r2_c" in
                "'") _r2_st="'"; _r2_wb=0 ;;
                '"') _r2_st='"'; _r2_wb=0 ;;
                # A COMMENT, and only where a WORD CAN BEGIN. `a#b`,
                # `http://x#frag`, `--format=%h#%s`, `${x#y}` and `$#` are not
                # comments and must stay whole.
                #
                # Where a word can begin is TRACKED — `_r2_wb`, which the loop
                # sets from a chunk's last byte and every arm sets for the next
                # jump — and that is the whole of round 4. Deriving it from the
                # previous BYTE read the escaped byte of a `\ ` as a space and
                # commented out the rest of `echo a\ #x; loci contract accept`,
                # which ran; and where two scan characters are adjacent it fell
                # back to the scan character, so the `#` in `echo "$(#don't` was
                # read as text and the apostrophe hid everything after it, which
                # is the defect this arm exists to fix.
                #
                # The body is DROPPED, which is right twice over. It is not
                # command text, so an invocation written in a comment does not
                # run and must not be denied; and its quote characters are not
                # quotes — `make build  # don't forget` and then the invocation
                # on the next line ran the verb, on all three rungs.
                #
                # It ends at the first NEWLINE, or — only while a backtick
                # region is open — a backtick, because bash finds the closing
                # backquote without honouring comments. Ending only at the
                # newline let `` echo `make # x` ; loci contract accept `` eat
                # the command that followed.
                '#') if [ "$_r2_wb" = 1 ]; then
                         _r2_end=$'\n'
                         case "$_r2_stack" in *[bB]*) _r2_end=$'\n`' ;; esac
                         while :; do
                             _r2_head="${_r2_rest%%[$_r2_end]*}"
                             _r2_len=$(( _r2_len - ${#_r2_head} ))
                             if [ "${#_r2_head}" -eq "${#_r2_rest}" ]; then
                                 _r2_rest=""
                                 break
                             fi
                             _r2_rest="${_r2_rest:${#_r2_head}}"
                             # An ESCAPED backtick closes no region, so it ends
                             # no comment either. Counting the backslashes in
                             # front of it is the only way to tell, and the
                             # scan stopped at it regardless — so
                             # `` echo "`x # a\`don't`" `` ended the comment in
                             # the middle and the apostrophe after it opened a
                             # region that hid the next command.
                             case "${_r2_rest:0:1}" in
                                 # ONLY the last 64 bytes, and the bound is the
                                 # whole point. `${x##*[!\\]}` is O(n^2) in the
                                 # length of a trailing backslash run — measured
                                 # standalone at 25 ms for 1 k, 1.07 s for 16 k,
                                 # 5.2 s for 32 k — and the run costs one byte
                                 # each while the work cap is charged the
                                 # REMAINING LENGTH, which is the right model
                                 # for a linear pass and the wrong one for this.
                                 # 32 600 backslashes in a 65 KB payload sat
                                 # under every cap and took the hook past the
                                 # 5 s in `hooks.json`: rc 124, empty stdout,
                                 # PreToolUse fails OPEN and the write goes
                                 # through. Same class as the multibyte fill,
                                 # reached through a different quadratic.
                                 #
                                 # A run of 64 or more is not classified: the
                                 # parity is unknowable within the bound, so the
                                 # backtick is taken as UNESCAPED and the comment
                                 # ends there. That emits MORE text, which is the
                                 # deny direction.
                                 \`) if [ "${#_r2_head}" -gt 64 ]; then
                                         # `${v: -64}` is EMPTY when the string
                                         # is shorter than 64, not the whole
                                         # string — so the short case, which is
                                         # every real one, has to be spelled out.
                                         _r2_bs="${_r2_head: -64}"
                                     else
                                         _r2_bs="$_r2_head"
                                     fi
                                     _r2_bs="${_r2_bs##*[!\\]}"
                                     if [ "${#_r2_bs}" -lt 64 ] \
                                        && [ $(( ${#_r2_bs} % 2 )) -eq 1 ]; then
                                         _r2_rest="${_r2_rest:1}"
                                         _r2_len=$(( _r2_len - 1 ))
                                         # Charged like any other jump: this
                                         # loop is O(remaining) per escaped
                                         # backtick and must not be free.
                                         _r2_work=$(( _r2_work + _r2_len ))
                                         [ "$_r2_work" -le "$_R2_MAX_SCAN" ] \
                                             || return 1
                                         continue
                                     fi ;;
                             esac
                             break
                         done
                     else
                         _r2_masked+='#'
                     fi ;;
                \`)  _r2_push_off; _r2_stack+="b"; _r2_st=""; _r2_wb=1 ;;
                # A PLAIN `(` gets a frame of its own, and skipping that was a
                # bypass: with only `$(` pushing, the `)` of a subshell, an
                # arithmetic expansion, a function definition or a process
                # substitution popped the `$(` frame instead, restored `"…"`
                # early, and masked the rest of the substitution away.
                # `echo "$( (true) && loci contract accept )"` ran the verb.
                # A `(` in COMMAND position (`s`) ends a word when it
                # closes; one that continues a word (`S`) does not, and the
                # difference is visible: `(true)#x` is a comment to bash, while
                # `cat <(echo a)#x` is the word `/dev/fd/63#x` and runs. Saying
                # every `)` opened a word made the second a comment and dropped
                # the invocation after it — a regression against 0.2.3 as well,
                # out of round 1's `(` frame meeting round 4's boundary. A
                # command still begins INSIDE both, so the push sets `_r2_wb=1`
                # either way; it is only the pop that differs.
                # …and since F11's review round 2, the `s`/`S` distinction is
                # also the SUBSTITUTION distinction, which is what closes the
                # rest of R5. A `(` that continues a word is `<(`, `>(` or a
                # `=(` array assignment: its contents are not a command of the
                # outer shell's line, they are a word's contents, exactly like
                # `$( … )`. So `S` is LIFTED and writes no paren, while `s` — a
                # real subshell, a command boundary — goes on writing both and
                # being cut at. Without that, `loci --root <(pwd) contract
                # accept` was cut into `loci --root <` / `pwd` / ` contract
                # accept`, none an invocation, and it RAN: R5's own sentence
                # with a different substitution in it, and the corpus could not
                # spell it because every `<(` row in it put the verb after a
                # `;`.
                '(') _r2_push_off
                     if [ "$_r2_wb" = 1 ]; then _r2_stack+="s"; _r2_masked+='('
                     else _r2_stack+="S"; fi
                     _r2_wb=1 ;;
                # A `)` that ends a SUBSTITUTION writes nothing and lifts the
                # body out; one that ends a subshell, or that matches nothing,
                # is ordinary text and stays where it is. The difference is the
                # whole of R5: a subshell IS a command boundary and must go on
                # cutting, a substitution is a word's contents and must not.
                ')') case "${_r2_stack: -1}" in
                        s) _r2_stack="${_r2_stack%?}"; _r2_wb=1
                           _r2_offs="${_r2_offs%??????}"
                           _r2_masked+=')' ;;
                        S) _r2_stack="${_r2_stack%?}"; _r2_wb=0
                           _r2_sub_close || return 1 ;;
                        p) _r2_stack="${_r2_stack%?}"; _r2_st=""; _r2_wb=0
                           _r2_sub_close || return 1 ;;
                        P) _r2_stack="${_r2_stack%?}"; _r2_st='"'; _r2_wb=0
                           _r2_sub_close || return 1 ;;
                        *) _r2_wb=1; _r2_masked+=')' ;;
                     esac ;;
                '$') case "$_r2_nx" in
                        '(') _r2_rest="${_r2_rest:1}"; _r2_push_off
                             _r2_stack+="p"; _r2_wb=1 ;;
                        "'") _r2_rest="${_r2_rest:1}"; _r2_st="A"; _r2_wb=0 ;;
                        '"') _r2_rest="${_r2_rest:1}"; _r2_st='"'; _r2_wb=0 ;;
                        *)   _r2_wb=0; _r2_masked+='$' ;;
                     esac ;;
                # The escaped byte is ALWAYS consumed here, even when the
                # backslash is kept. Leaving it in the input made it the next
                # chunk's last byte, so `\ ` read as a word boundary and
                # commented out the rest of the command.
                \\)  case "$_r2_nx" in
                        "'"|'"'|\\|\`|'$'|'('|')') _r2_rest="${_r2_rest:1}"
                            _r2_wb=0; _r2_masked+="$_r2_nx" ;;
                        # A backslash-newline is a line continuation, not two
                        # commands: without this `loci \<newline>contract
                        # accept` split into `loci \` and `contract accept` and
                        # neither half was an invocation. It joins the lines, so
                        # it leaves the word boundary exactly as it found it.
                        $'\n') _r2_rest="${_r2_rest:1}" ;;
                        "") _r2_wb=0; _r2_masked+='\' ;;
                        *)  _r2_rest="${_r2_rest:1}"
                            _r2_wb=0; _r2_masked+="\\$_r2_nx" ;;
                     esac ;;
            esac
            ;;
        esac
    done
    return 0
}

# $1 = one segment. Returns 0 when the segment invokes a contract-writing verb.
# Forkless, for the reason route 1 is: this is the VERDICT path. It used to be
# `printf | tr | sed`, and with either binary off a minimal PATH the normalised
# command collapsed to a single space and every verb was allowed — exit 0, no
# output. Word splitting with `IFS` and `set --` needs no binary at all.
#
# THE RULE, in one sentence: the guard looks for the binary in the parts of a
# segment that are OUTSIDE quotes. Everything below follows from it, in both
# directions.
#
#   * It is why a mention is allowed. In `git commit -m "…the user runs loci
#     contract accept"` the `loci` token sits inside the quoted region, so it is
#     not a binary; same for an `echo` appending a handoff note, and for a
#     heredoc line whose text is quoted.
#
#     There is no quote logic HERE any more, and that is the point: `_r2_mask`
#     has already removed the quoted text, so this loop sees only words the
#     shell would have run. Three rounds tried to decide it token by token —
#     "stop at the first token containing a quote" (a one-character bypass:
#     `loci -f "json" contract accept` and twenty-one more ran the verb in plain
#     sight), then per-token quote parity, then parity with the escapes stripped
#     — and each fix opened the next hole. A token is now just a word.
#   * It is why a WRAPPER is still denied, without a list of wrappers. Round 1
#     required the segment's FIRST word to be `loci` and skipped a hand-written
#     set of prefixes; a review corpus of 230 spellings found 109 invocations
#     that used to be denied and had become allowed — `uv run loci …`,
#     `timeout 30 loci …`, `sudo -u ci loci …`, `env -i loci …`, `nice -n 10
#     loci …`, `xargs loci …`, `docker run img loci …`, `>log.txt loci …`, `eval
#     loci …`. Not a hypothetical spelling: `hooks/turn-clean.sh` itself runs
#     `timeout 10 loci build clean`, and `lib/eval-graders.sh` records `uv run
#     loci init` as a residual of the same mistake. A list can only ever be as
#     long as the last review; scanning the unquoted head for the binary needs
#     no list at all.
_segment_invokes_verb() {
    _r2_ifs="$IFS"
    IFS=" "
    # `set -f` so a `*` in the command is not expanded against the CWD while the
    # guard is deciding — a filename can invent the word `loci` out of nothing.
    set -f
    # shellcheck disable=SC2086
    set -- $1
    set +f
    IFS="$_r2_ifs"

    # 1. the binary. An assignment, a wrapper, a flag, a redirection, an
    #    argument — anything that is not `loci` is stepped over, because none of
    #    them ends the command. There is nothing to say about quotes: the words
    #    that survived `_r2_mask` are the words the shell would have run.
    #
    #    Backslashes are not folded to `/` the way route 1 folds them: a Windows
    #    payload really does carry `C:\Users\…\loci.exe`. The backslash is QUOTED
    #    (`*'\'loci`) and that is not a style choice — written `*\\loci` it also
    #    matches `notloci`, i.e. any word ending in `loci`, under `bash -c`,
    #    because the second backslash escapes the `l`; run from a FILE the same
    #    source does not. A guard whose verdict depends on how bash was started
    #    is not a guard. Measured 2026-09-09.
    #
    #    Case-INSENSITIVE, and only here. On NTFS and APFS `LOCI`, `Loci` and
    #    `loci.EXE` all open the same binary and all three RAN the verb while
    #    this `case` was case-sensitive — the same reasoning route 1 spells out
    #    at the top of the file for `FLAGS.JSON`, and the same `nocasematch`
    #    dance it does at its own start and end. It is unset before loop 2
    #    because the VERBS must stay case-sensitive: `grep 'Contract Edit'` is
    #    not a contract write, and this is the one line that keeps the two apart.
    shopt -s nocasematch
    while [ "$#" -gt 0 ]; do
        case "$1" in
            loci|loci.exe|*/loci|*/loci.exe|*'\'loci|*'\'loci.exe) break ;;
        esac
        shift
    done
    shopt -u nocasematch
    [ "$#" -gt 0 ] || return 1
    shift

    # 2. `contract`, then the subcommand IMMEDIATELY after it — which is the
    #    whole of `contract draft edit` being allowed while `contract edit` is
    #    denied, the single most important pair in this guard. A global flag may
    #    sit between the binary and the verb (`loci -f json contract accept`), so
    #    the token is searched for rather than required next; and the search
    #    CONTINUES past a `contract` that turned out not to be the verb, because
    #    `loci --format contract contract accept` really does run it.
    #
    #    Once the binary is known the rest of the segment is that binary's argv,
    #    and the verb is read out of it. Round 2 stopped this loop at the first
    #    quoted token, which allowed `loci --project "$PWD" contract accept` and
    #    six more where the binary is in hand and only an ARGUMENT is quoted;
    #    there is no quote logic here now for the same reason there is none in
    #    loop 1 — `_r2_mask` ran first. What that costs, in the other direction:
    #    `loci --help "x contract accept y"` is ALLOWED, where `bb4a547` denied
    #    it. The masking is right about that one — the verb is inside a single
    #    argv element and nothing runs — and it is the same trade as every other
    #    quoted mention this route was fixed to permit.
    while [ "$#" -gt 0 ]; do
        if [ "$1" = contract ]; then
            shift
            case "${1-}" in
                accept|init|edit|disable|enable) return 0 ;;
            esac
        else
            shift
        fi
    done
    return 1
}

# $1 = the whole command. 0 = a segment of it invokes a verb; 1 = none does;
# 2 = there are too many segments to answer this way, ask the substring test.
#
# Rc 2 exists because the byte cap does not bound the WORK: a 64 KB command is
# under it, but 64 KB of nothing but separators is ~32 000 segments, and one
# function call plus one `set --` each took 3.0 s of the hook's 5 s budget where
# `bb4a547` took 0.35 s. Past the budget the hook is killed and fails open, so
# the byte cap alone left a way to switch the guard off by choosing punctuation.
# `$#` after the split is the exact count and costs nothing.
_command_invokes_verb() {
    # A carriage return is line-ending noise, and it is DELETED rather than
    # turned into a space: `accept\r` is not the token `accept` (the substring
    # match this replaced never noticed, because it read text), and a `\` before
    # a CRLF has to end up next to the newline for the continuation below to see
    # it. It goes first for that second reason.
    _r2_norm="${1//$'\r'/}"
    # Quoted text goes BEFORE the split, not after it. Doing it in this order is
    # what makes the split safe: a `;`, `|`, `&`, `(`, `)` or backtick inside a
    # quoted argument is gone by the time the separators are read, so
    # `env NOTE="fix(guard)" loci contract accept` is one segment instead of
    # three, and the fragment that used to open with a CLOSING quote does not
    # exist. Rc 2 when the scan is over its cap, same as too many segments.
    _r2_mask "$_r2_norm" || return 2
    # The outer command, then the substitution bodies the scan lifted out of it,
    # with a newline between so the last word of one cannot join the first word
    # of the other. Both are command text and both are segmented below: the
    # bodies are what keeps `echo "$(loci contract accept)"` denied now that the
    # `$(` no longer cuts the outer word. Every body already begins with its own
    # newline, so this one is for the join and costs one empty segment.
    _r2_norm="$_r2_masked"$'\n'"$_r2_subs"
    # Each separator becomes one newline; `&&` and `||` leave an empty segment
    # between them, which word splitting on IFS whitespace drops.
    _r2_norm="${_r2_norm//[$_R2_SEPARATORS]/$'\n'}"
    # A tab is whitespace, not a separator: it separates words, not commands.
    _r2_norm="${_r2_norm//$'\t'/ }"
    _r2_outer_ifs="$IFS"
    IFS=$'\n'
    set -f
    # shellcheck disable=SC2086
    set -- $_r2_norm
    set +f
    IFS="$_r2_outer_ifs"
    [ "$#" -le "$_R2_MAX_SEGMENTS" ] || return 2
    # No state is carried between segments. There used to be one — the quote
    # state, because the split cut inside quoted strings — and it was the third
    # thing this route got wrong: it went stale the moment a segment found its
    # binary and stopped scanning, and a `$( … )` inside `"…"` inherited "still
    # quoted" and hid the substituted command. Segments are independent now
    # because there is no quoted text left in them to span one.
    for _r2_seg in "$@"; do
        _segment_invokes_verb "$_r2_seg" && return 0
    done
    return 1
}

if [ -n "$cmd" ]; then
    # Only contract verbs are denied below, so a command that never says
    # `contract` cannot be one. Keeps the recipe tokens' cost at zero forks, and
    # keeps the splitting off every ordinary command.
    case "$cmd" in
        *contract*)
            _cg_what="bash verb"
            _r2_rc=2
            if [ "${#cmd}" -le "$_R2_MAX_TOKENISE" ]; then
                _command_invokes_verb "$cmd"
                _r2_rc=$?
            fi
            case "$_r2_rc" in
                0) deny "$REASON_VERB" ;;
            esac
            if [ "$_r2_rc" -eq 2 ]; then
                # The coarse answer, for a command too big or too fragmented to
                # tokenise. It is a REGEX, not the glob-plus-collapse-loop this
                # change first inherited from `bb4a547`, and the reason is that
                # the loop and the glob each got one half of this wrong.
                #
                # The loop rewrites the whole string and compares two copies of
                # it, once per iteration: 39.8 s on a 1.1 MB indented heredoc,
                # against a 5 s budget, so the hook was killed and nothing was
                # denied. Dropping it was right. But dropping it and keeping the
                # glob lost `loci   contract   accept` — runs of spaces — which
                # `bb4a547` denied in 0.28 s at the cap, on the flat command an
                # evader would actually send. A pad is easy to add and the size
                # is the caller's to choose, so that was a way to walk the verb
                # through by typing two spaces.
                #
                # `[[ =~ ]]` is a builtin, needs no collapsing, and is linear:
                # 45 ms at 66 KB, 0.44 s at 1.1 MB, 1.2 s at 3 MB, measured.
                #
                # Nothing is rewritten first, and that is the point: four
                # normalising `${//}` passes over 3 MB overran the budget on
                # their own, which is the same trap the collapse loop was. The
                # only work here is one concatenation for the leading boundary.
                _r2_flat=" $cmd "
                if [[ "$_r2_flat" =~ $_R2_OVERSIZE_RE ]]; then
                    deny "$REASON_VERB"
                fi
            fi
            ;;
    esac
fi

exit 0
