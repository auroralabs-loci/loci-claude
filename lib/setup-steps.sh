#!/usr/bin/env bash
# LOCI plugin — shared setup/bootstrap primitives for session-init.sh,
# ensure-loci-cli.sh, and setup.sh. Sourcing only DEFINES functions (plus
# constants and the shared logger) — no installs, no PATH mutation, no writes.
#
# These live in shell, not a `loci` subcommand, because they bootstrap the very
# `loci` binary a subcommand would need (chicken-and-egg).
#
# Caller contract:
#   • PLUGIN_DIR set to the plugin root before sourcing.
#   • STATE_DIR / LOCI_STATE_DIR resolved before sourcing. Keep that resolution
#     identical across entry scripts (~/.loci/state, falling back to
#     $PLUGIN_DIR/state) or the detection guard checks a different dir than
#     session-init wrote to.
#   • JSON is read and written through `lib/loci_json.sh`, sourced below. No
#     host tool: `jq` was one, it is not bundled, and every function here that
#     needed it silently did nothing on a machine without it.

# Idempotent guard — safe to source repeatedly / from multiple entry scripts.
[ -n "${_LOCI_SETUP_STEPS_SOURCED:-}" ] && return 0
_LOCI_SETUP_STEPS_SOURCED=1

# shellcheck source=./loci_log.sh
. "${PLUGIN_DIR}/lib/loci_log.sh" 2>/dev/null || true
# shellcheck source=./loci_json.sh
. "${PLUGIN_DIR}/lib/loci_json.sh" 2>/dev/null || true

# Pinned loci CLI (prod), from the PyPI wheel. Dev installs float — see
# ensure_loci. This is the ONLY copy of these constants in the plugin.
LOCI_CLI_VERSION="0.2.20"
LOCI_CLI_PACKAGE="loci-tools"

loci_is_windows() {
    case "$(uname -s)" in MINGW*|MSYS*) return 0 ;; *) return 1 ;; esac
}

# Where `uv tool install` puts console-script shims, per uv's own resolution
# order. NOT probed with `uv tool dir --bin`: sourcing this file must never run
# uv (the pin-resolution tests assert that every uv invocation is an install).
loci_uv_bin_dir() {
    if [ -n "${UV_TOOL_BIN_DIR:-}" ]; then
        printf '%s' "$UV_TOOL_BIN_DIR"
    elif [ -n "${XDG_BIN_HOME:-}" ]; then
        printf '%s' "$XDG_BIN_HOME"
    elif loci_is_windows; then
        printf '%s' "${LOCALAPPDATA:-$HOME/AppData/Local}/uv/bin"
    else
        printf '%s' "$HOME/.local/bin"
    fi
}

# Move $1 to the front of PATH, dropping any existing occurrence.
_path_prepend_unique() {
    local _d="$1" _out="" _p
    local IFS=:
    for _p in $PATH; do
        [ "$_p" = "$_d" ] || _out="${_out:+$_out:}$_p"
    done
    PATH="$_d${_out:+:$_out}"
}

# Hook subprocesses don't inherit the login-shell PATH; prepend the common
# locations user-installed tools live in.
augment_path() {
    local _d
    for _d in \
        "$HOME/.local/bin" \
        "$HOME/.cargo/bin" \
        "/usr/local/bin" \
        "/opt/homebrew/bin" \
        "/opt/homebrew/opt/binutils/bin"; do
        [ -d "$_d" ] && case ":$PATH:" in *":$_d:"*) ;; *) PATH="$_d:$PATH" ;; esac
    done
    if loci_is_windows; then
        for _d in \
            "${LOCALAPPDATA:-$HOME/AppData/Local}/uv/bin" \
            "/mingw64/bin" "/ucrt64/bin" "/usr/bin"; do
            [ -d "$_d" ] && case ":$PATH:" in *":$_d:"*) ;; *) PATH="$_d:$PATH" ;; esac
        done
    fi
    # The uv shim dir outranks everything, unconditionally: the loop above
    # prepends in ascending order, so /opt/homebrew/bin and /usr/local/bin ended
    # up ahead of it, and a dir already in the inherited PATH was never moved.
    # Any other `loci` winning the lookup makes the pin check read a binary the
    # installer never writes — it reinstalls every session and nothing changes.
    _d=$(loci_uv_bin_dir)
    [ -n "$_d" ] && [ -d "$_d" ] && _path_prepend_unique "$_d"
    export PATH
}

# The FIRST dotted version out of `loci --version`, and nothing else.
#
# Two wrong spellings preceded this one, and both suppress the stale-CLI advisory
# on a CLI that is behind the pin — which is the one failure this function must
# not have, because everything downstream reads the advisory's absence as "at or
# above the pin":
#
#   * `head -1 | tr -cd '0-9.'` deleted the separators inside a prerelease tag,
#     so `loci 0.1.97-rc1` came back as `0.1.971` — which `_semver_gt` ranks
#     ABOVE 0.1.126.
#   * `s/.*[^0-9.]\(…\)/\1/` looks like it takes the first match and takes the
#     LAST: `.*` is greedy, so it backtracks to the rightmost one.
#     `loci 0.1.126 (Python 3.11.5)` came back as `3.11.5`, which is both
#     non-empty (so the "not asserted" marker does not fire) and newer than the
#     pin (so the advisory does not fire either).
#
# `^[^0-9]*` is anchored and cannot backtrack past a digit, so it takes the
# first. An unparseable line yields the empty string, which every caller already
# treats as "unknown".
loci_cli_version() {
    command -v loci >/dev/null 2>&1 || return 1
    loci --version 2>/dev/null \
        | sed -n 's/^[^0-9]*\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' \
        | sed -n '1{p;q;}'
}

have_uv() { command -v uv >/dev/null 2>&1; }

# Return 0 if dotted-numeric version $1 is strictly greater than $2. Pure bash
# so we don't depend on sort -V (BSD sort before macOS 10.13 lacks it).
_semver_gt() {
    local a b IFS=.
    # shellcheck disable=SC2206
    a=($1)
    # shellcheck disable=SC2206
    b=($2)
    local n=${#a[@]} m=${#b[@]} i
    [ "$m" -gt "$n" ] && n=$m
    for (( i=0; i<n; i++ )); do
        local x=${a[i]:-0} y=${b[i]:-0}
        case "$x$y" in *[!0-9]*) return 1;; esac
        if   [ "$x" -gt "$y" ]; then return 0
        elif [ "$x" -lt "$y" ]; then return 1
        fi
    done
    return 1
}

# Highest-semver plugin version in the cache root. PLUGIN_DIR (from $0) is stale
# after an in-flight upgrade — the session keeps running the old version's files.
# Cannot detect a deliberate plugin downgrade (0.1.105 still cached wins).
_resolve_authoritative_plugin_dir() {
    local cache_root="${PLUGIN_DIR%/*}"
    [ -n "$cache_root" ] && [ -d "$cache_root" ] \
        || { printf '%s' "$PLUGIN_DIR"; return; }
    local d ver best_dir="" best_ver=""
    for d in "$cache_root"/*/; do
        ver="${d%/}"; ver="${ver##*/}"
        case "$ver" in ''|*[!0-9.]*) continue;; esac # reject anything that isn't dotted-numeric
        [ -n "$best_ver" ] && ! _semver_gt "$ver" "$best_ver" && continue
        [ -f "${d}.claude-plugin/plugin.json" ] && [ -d "${d}lib" ] || continue
        best_ver="$ver"; best_dir="${d%/}"
    done
    if [ -n "$best_dir" ]; then printf '%s' "$best_dir"
    else printf '%s' "$PLUGIN_DIR"
    fi
}

# Executable bits on the shell entry points; guards checkouts that lose +x.
fix_exec_bits() {
    chmod +x "${PLUGIN_DIR}/hooks/"*.sh 2>/dev/null || true
    chmod +x "${PLUGIN_DIR}/lib/"*.sh   2>/dev/null || true
}

# Installer for the loci CLI (a uv tool on PATH). Install-source resolution is
# INDEPENDENT of LOCI_ENV: set LOCI_DEV_CLI_PATH=<checkout> for an editable
# install; otherwise the pinned build. Idempotent — reinstalls only when
# loci is missing, the pin drifted, or the recorded install spec changed.
# ALWAYS returns 0.
_loci_install_spec=""      # resolved by _loci_resolve_install_spec
_loci_cli_pinned=""        # set only when the spec is the version-tag pin

_loci_resolve_install_spec() {
    _loci_install_spec=""
    _loci_cli_pinned=""

    # Editable install is an explicit opt-in via LOCI_DEV_CLI_PATH.
    if [ -n "${LOCI_DEV_CLI_PATH:-}" ]; then
        local _abs; _abs="$(cd "$LOCI_DEV_CLI_PATH" 2>/dev/null && pwd)"
        if [ -n "$_abs" ] && [ -f "$_abs/pyproject.toml" ]; then
            _loci_install_spec="--editable $_abs"
            return 0
        fi
        # No valid checkout there — fall back to the pinned build rather than
        # fail (onboarding must never break).
        loci_log WARN setup-steps "LOCI_DEV_CLI_PATH set but no valid loci-cli checkout at '${LOCI_DEV_CLI_PATH}' — falling back to the pinned build"
    fi

    # A stale hook file's pin is behind (see _resolve_authoritative_plugin_dir);
    # taking the newer version's pin lands the upgrade in THIS session.
    local _auth; _auth=$(_resolve_authoritative_plugin_dir)
    if [ "$_auth" != "$PLUGIN_DIR" ]; then
        local _p
        _p=$(sed -n 's/^LOCI_CLI_VERSION="\([0-9.]*\)".*/\1/p' \
             "${_auth}/lib/setup-steps.sh" 2>/dev/null | head -1)
        if [ -n "$_p" ] && _semver_gt "$_p" "$LOCI_CLI_VERSION"; then
            loci_log INFO setup-steps \
                "newer pin from authoritative plugin dir ${_auth}: ${LOCI_CLI_VERSION} -> ${_p}"
            LOCI_CLI_VERSION="$_p"
        fi
    fi

    _loci_install_spec="${LOCI_CLI_PACKAGE}==${LOCI_CLI_VERSION}"
    _loci_cli_pinned=1
}

# The CLI only ever moves FORWARD: the pin is a floor, so no plugin version —
# stale or current — can roll a user back. Fix a bad CLI release by publishing a
# fix and bumping the pin.
_loci_cli_ready() {
    command -v loci >/dev/null 2>&1 || return 1
    if [ -f "${STATE_DIR}/loci-cli-status.json" ]; then
        local _recorded _want="$_loci_install_spec"
        loci_json_load "$(<"${STATE_DIR}/loci-cli-status.json")"
        _recorded=$(loci_json_get spec)
        [ -n "$_loci_cli_pinned" ] && { _recorded="${_recorded%%==*}"; _want="${_want%%==*}"; }
        [ -n "$_recorded" ] && [ "$_recorded" != "$_want" ] && return 1
    fi
    # Editable/floating installs float — accept presence.
    [ -n "$_loci_cli_pinned" ] || return 0
    local v; v=$(loci_cli_version)
    [ "$v" = "$LOCI_CLI_VERSION" ] || _semver_gt "$v" "$LOCI_CLI_VERSION"
}

_loci_write_status() {
    local st="$1" f="${STATE_DIR}/loci-cli-status.json" tmp
    tmp="${STATE_DIR}/loci-cli-status.json.tmp.$$"
    local ver=""
    ver=$(loci_cli_version) || ver=""
    declare -F loci_json_object >/dev/null 2>&1 || return 0
    # `path` is here for diagnosis: a version that never moves while the install
    # keeps succeeding means a different `loci` is winning the PATH lookup.
    loci_json_object status "$st" spec "$_loci_install_spec" version "$ver" \
        path "$(command -v loci 2>/dev/null)" \
        log "${STATE_DIR}/loci-cli-install.log" \
        ts "$(date -u +%FT%TZ 2>/dev/null)" > "$tmp" 2>/dev/null \
        && mv -f "$tmp" "$f" 2>/dev/null || rm -f "$tmp" 2>/dev/null
}

ensure_loci() {
    [ -n "${_LOCI_BOOTSTRAP:-}" ] && { _loci_write_status skipped; return 0; }
    _loci_resolve_install_spec
    _loci_cli_ready && { _loci_write_status ready; return 0; }
    if ! have_uv; then
        _loci_write_status failed
        loci_log WARN setup-steps "cannot install loci CLI: uv not installed (prerequisite — the skill must give the user the install command)"
        return 0
    fi
    local _install_log="${STATE_DIR}/loci-cli-install.log"
    # Make uv use the OS trust store (schannel), not its bundled webpki roots:
    # corporate Windows usually MITMs HTTPS with a CA that's in the Windows cert
    # store but not uv's bundled roots, so the pypi fetch fails with "invalid
    # peer certificate: UnknownIssuer". Harmless where no proxy exists.
    loci_is_windows && export UV_NATIVE_TLS=1
    # -p 3.12 pins the interpreter (CLI + asmslicer need it). --force replaces an
    # existing install on a bump. Word-split $_loci_install_spec so a dev
    # editable spec ("--editable /path") passes as two args.
    # --refresh-package: a plugin release pins a CLI published minutes earlier;
    # uv's cached simple index predates the upload and resolution fails with
    # "no version of loci-tools==X" even though PyPI has it. Pinned installs
    # only run on pin drift, so the revalidation cost is one-off.
    local _refresh=""
    [ -n "$_loci_cli_pinned" ] && _refresh="--refresh-package ${LOCI_CLI_PACKAGE}"
    # shellcheck disable=SC2086
    if uv tool install --force -p 3.12 $_refresh $_loci_install_spec >"$_install_log" 2>&1; then
        _loci_write_status installed
        loci_log INFO setup-steps "loci CLI installed ($_loci_install_spec)"
    else
        _loci_write_status failed
        loci_log WARN setup-steps "loci CLI install failed ($_loci_install_spec) — see $_install_log"
    fi
    return 0
}

# Canonical per-directory key for state files. device:inode collapses
# case-variant paths and symlinks to one key on case-insensitive filesystems —
# without it, state splits and skills like /loci:trends miss prior measurements.
#
# FAT/exFAT and some SMB/virtual mounts on Windows report inode 0 for every
# file, which would collapse ALL projects on that volume to one hash (and read a
# sibling's state). So a ":0" inode is rejected and we fall back to a path key,
# lowercased on Windows to keep the case-insensitive collapsing. NTFS inodes are
# non-zero, so the healthy case is unaffected.
#
# Takes an optional directory, defaulting to the process's own. Every writer here
# means "this session's project" and passes nothing; `hooks/pre-edit-hook.sh` passes
# the hook payload's `cwd`, which is the root it is snapshotting FOR rather than the
# directory it happens to be running in. Both spellings reach the same key because
# `stat` resolves them to one inode — which is the point of keying on the inode, and
# is what lets a reader name the file this function named without reimplementing any
# part of the rule. A second implementation, anywhere, is a state directory that two
# components disagree about.
_canonical_cwd_key() {
    local dir="${1:-.}"
    local key
    # macOS/BSD: -f is the format flag (errors out on GNU, so falls through).
    # GNU/MSYS: -c is the format flag.
    if key=$(stat -f '%d:%i' "$dir" 2>/dev/null) && [ -n "$key" ]; then
        case "$key" in *:0) ;; *) printf '%s' "$key"; return 0 ;; esac
    fi
    if key=$(stat -c '%d:%i' "$dir" 2>/dev/null) && [ -n "$key" ]; then
        case "$key" in *:0) ;; *) printf '%s' "$key"; return 0 ;; esac
    fi
    # `pwd` is the fallback's fallback and is only right for the default case; a
    # named directory that cannot be resolved has no key rather than the caller's.
    local path
    if ! path=$(realpath "$dir" 2>/dev/null); then
        [ "$dir" = "." ] || return 1
        path=$(pwd)
    fi
    if loci_is_windows; then
        printf '%s' "$path" | tr '[:upper:]' '[:lower:]'
    else
        printf '%s' "$path"
    fi
}

hash_cwd() {
    local key h
    key=$(_canonical_cwd_key "${1:-.}") || return 1
    [ -n "$key" ] || return 1
    h=$(printf '%s' "$key" | sha256sum 2>/dev/null | cut -c1-12)
    [ -n "$h" ] && { echo "$h"; return 0; }
    h=$(printf '%s' "$key" | shasum -a 256 2>/dev/null | cut -c1-12)
    [ -n "$h" ] && { echo "$h"; return 0; }
    printf '%s' "$key" | cksum | awk '{print $1}'
}

# device:inode of an arbitrary path, for legacy-state migration. Same ":0"
# rejection as _canonical_cwd_key so migration bails rather than matching every
# project on a volume with no stable index.
# Which `stat` this machine has, decided ONCE. Both spellings were tried on
# every call, so on GNU the BSD probe failed first and every call cost two
# processes — and `_loci_find_recipe` makes one per level of the walk.
# Sets `$_LOCI_INODE_KEY`; returns 1 when there is no usable one. It SETS
# rather than prints so the flavour cache survives: `$(_inode_key …)` runs the
# body in a subshell and the assignment dies with it, so every call re-probed
# both spellings and paid two processes on whichever platform is not GNU.
# `printf` is kept for the callers that want it inline.
_LOCI_STAT_FLAVOUR=""
_LOCI_INODE_KEY=""
_inode_key() {
    _LOCI_INODE_KEY=""
    local k
    case "$_LOCI_STAT_FLAVOUR" in
        gnu) k=$(stat -c '%d:%i' "$1" 2>/dev/null) || k="" ;;
        bsd) k=$(stat -f '%d:%i' "$1" 2>/dev/null) || k="" ;;
        *)
            if k=$(stat -c '%d:%i' "$1" 2>/dev/null) && [ -n "$k" ]; then
                _LOCI_STAT_FLAVOUR=gnu
            elif k=$(stat -f '%d:%i' "$1" 2>/dev/null) && [ -n "$k" ]; then
                _LOCI_STAT_FLAVOUR=bsd
            else
                k=""
            fi ;;
    esac
    case "$k" in ''|*:0) return 1 ;; esac
    _LOCI_INODE_KEY="$k"
    printf '%s' "$k"
}

# ONE line, always. In a repo with no commits `git rev-parse --abbrev-ref HEAD`
# prints `HEAD` on stdout AND exits 128, so the bare `|| echo "unknown"` emitted
# BOTH — a two-line branch name that slugged to `HEADunknown` (naming the
# measurement JSONL after a branch that does not exist) and, since every context
# block prints `Branch: <b>`, put a stray `unknown` line into the model's
# context. `head -1` also makes the exit status the pipe's, which is why the
# fallback now tests the value rather than git's status.
# The parent of an absolute path, without a subprocess. Every upward walk here
# runs one of these per level and there are two walks per session — twelve
# `dirname` spawns on a six-deep path, ~0.1 s each under Git Bash on Windows,
# for a string operation the shell can do itself.
_parent_dir() {
    case "$1" in
        */?*) printf '%s' "${1%/*}" ;;
        *)    printf '/' ;;
    esac
}

_git_branch() {
    local b
    # `--show-current` FIRST, because it is the one that answers correctly on an
    # unborn HEAD: `rev-parse --abbrev-ref HEAD` prints the literal `HEAD` there
    # (and exits 128), while `git init`'s branch really is `main` — which is what
    # `_git.current_branch` in the CLI reports, from `.git/HEAD`'s symref. The
    # two writers of this file were naming two different histories for one
    # checkout, so `loci-measurements-<h>-{main,HEAD}.jsonl` flipped with
    # whoever wrote last. Falls back for git < 2.22.
    b=$(git -C "$(pwd)" branch --show-current 2>/dev/null | head -1)
    [ -n "$b" ] || b=$(git -C "$(pwd)" rev-parse --abbrev-ref HEAD 2>/dev/null | head -1)
    [ -n "$b" ] && printf '%s' "$b" || printf 'unknown'
}

_branch_slug() {
    printf '%s' "$1" | tr '/' '_' | tr -cd 'A-Za-z0-9_-' | cut -c1-64
}

# One-shot migration: rename state files keyed by any older hash to the
# canonical (device:inode) hash. Discovers candidates by resolving each
# project-context-*.json's project_root to an inode; same inode as the current
# cwd means it's ours, whatever path-spelling produced its old hash. Idempotent.
_migrate_legacy_state() {
    local new_hash="$1" slug="$2"
    # This is a ONE-SHOT that used to run every session for ever: it renames the
    # legacy files, so every later run spends a read and a `stat` per file in the
    # state directory to discover there is nothing left to rename — ~13 s of a
    # ~17 s SessionStart against a 45-project state directory, when the read was
    # a `jq` spawn.
    #
    # The guard is a marker, NOT the presence of `project-context-<new>.json`.
    # That test looked equivalent and was not: the loop migrates THREE files, and
    # `loci init` writes the context under the new key by itself — so a project
    # initialized before its first session-init would have had its measurement
    # and stats history orphaned under the legacy key, silently, with `/loci:trends`
    # showing nothing. Keyed by slug as well as hash because the measurement and
    # stats files are named per branch, and a branch first seen later still has
    # its own legacy pair to move.
    #
    # Written only when the pass completed with no failed `mv`, so a rename that
    # lost a race with a reader (Windows, file held open) is retried next session
    # — the retry the docstring's "idempotent" was promising.
    local marker="${STATE_DIR}/.migrated-${new_hash}-${slug}"
    [ -f "$marker" ] && return 0
    local current_inode; current_inode=$(_inode_key .)
    [ -z "$current_inode" ] && return 0
    local ctx_file legacy_hash old_root old_inode f new_f
    local _migrate_failed=""
    for ctx_file in "${STATE_DIR}"/project-context-*.json; do
        [ -f "$ctx_file" ] || continue
        legacy_hash=$(basename "$ctx_file" .json)
        legacy_hash="${legacy_hash#project-context-}"
        case "$legacy_hash" in *[!a-f0-9]*) continue;; esac
        [ "$legacy_hash" = "$new_hash" ] && continue
        loci_json_load "$(<"$ctx_file")"
        old_root=$(loci_json_get project_root)
        [ -z "$old_root" ] && continue
        old_inode=$(_inode_key "$old_root")
        [ "$old_inode" = "$current_inode" ] || continue
        # `init-nudge-` rides with them. It is keyed the same way and it means
        # "this project has already been told", so a key change that left it
        # behind gave the same project a second first-edit nudge — and left an
        # orphan under a key nothing will ever look up again. Reachable whenever
        # the key moves: a `:0` inode falling back to a path key and back, or the
        # legacy path keying this whole function exists to retire.
        for f in \
            "${STATE_DIR}/project-context-${legacy_hash}.json" \
            "${STATE_DIR}/init-nudge-${legacy_hash}" \
            "${STATE_DIR}/loci-measurements-${legacy_hash}-${slug}.jsonl" \
            "${STATE_DIR}/loci-stats-${legacy_hash}-${slug}.json"
        do
            new_f="${f//${legacy_hash}/${new_hash}}"
            [ -f "$f" ] && [ ! -e "$new_f" ] || continue
            mv -f "$f" "$new_f" 2>/dev/null || _migrate_failed=1
        done
    done
    [ -n "${_migrate_failed:-}" ] || : > "$marker" 2>/dev/null || true
}

# The recipe governing a directory: `<root>/.loci/build.yaml`, or nothing.
#
# A FILE TEST and an upward walk — no YAML is parsed here, ever. Hooks are
# bash; the recipe's contents reach them only through the keyed context JSON
# that `loci init` writes (report §6.3: "the keyed context IS the recipe's
# mirror"). This function answers one question: does a recipe govern this cwd.
#
# Three stops, the same three as the CLI's `recipe.discover`, so the two sides
# agree about which recipe owns a directory:
#   • the git toplevel — a recipe belongs to a checkout, and walking past its
#     root would let a parent directory's recipe govern an unrelated repo that
#     happens to sit inside it;
#   • $HOME, EXCLUSIVE — `~/.loci` is the state directory, so a
#     `~/.loci/build.yaml` is far likelier to be stray state than a deliberate
#     recipe for every project under the home directory;
#   • the filesystem root.
_loci_find_recipe() {
    local start home_p home_key ceiling d up i
    start=$(cd "${1:-.}" 2>/dev/null && pwd -P) || return 1
    home_p=$(cd "${HOME:-/nonexistent}" 2>/dev/null && pwd -P) || home_p=""
    # Git Bash mounts %TEMP% at /tmp, so walking up from the session cwd can
    # yield `/tmp/x/home` for the directory `cd "$HOME"` calls
    # `/c/Users/.../Temp/x/home`. A string compare misses that, and the walk
    # then sails past $HOME and adopts `~/.loci/build.yaml` for every project
    # under the home directory — which is the one outcome this ceiling exists
    # to prevent. `_inode_key` is the same device:inode rule the state files
    # are named by, and it already refuses a `:0` inode, so the string compare
    # below stays the answer on a filesystem with no usable index.
    home_key=""
    [ -n "$home_p" ] && home_key=$(_inode_key "$home_p" 2>/dev/null) || home_key=""

    # The ceiling is the NEAREST ancestor holding a `.git` (a directory for a
    # clone, a `gitdir:` file for a worktree or submodule — `-e` catches both).
    ceiling=""; d="$start"; i=0
    while [ $i -lt 64 ]; do
        [ -e "$d/.git" ] && { ceiling="$d"; break; }
        up=$(_parent_dir "$d"); [ "$up" = "$d" ] && break
        d="$up"; i=$((i + 1))
    done

    d="$start"; i=0
    while [ $i -lt 64 ]; do
        # $HOME is checked BEFORE the candidate, which is what makes it
        # exclusive — same order as the CLI's loop.
        [ -n "$home_p" ] && [ "$d" = "$home_p" ] && return 1
        # By inode, and with no basename short-circuit: `/tmp` and
        # `…/Local/Temp` are one directory under Git Bash with different last
        # components, and a case-variant spelling is another. Filtering on the
        # basename first made this ceiling adopt `~/.loci/build.yaml`.
        if [ -n "$home_key" ] && _inode_key "$d" >/dev/null 2>&1 \
           && [ "$_LOCI_INODE_KEY" = "$home_key" ]; then
            return 1
        fi
        [ -f "$d/.loci/build.yaml" ] && { printf '%s' "$d/.loci/build.yaml"; return 0; }
        [ -n "$ceiling" ] && [ "$d" = "$ceiling" ] && return 1
        up=$(_parent_dir "$d"); [ "$up" = "$d" ] && return 1
        d="$up"; i=$((i + 1))
    done
    return 1
}

# The three key sets this file reasons about.
#
#   DEAD   the fields no consumer was ever found for (report §6.3's audit, and
#          todo 027 for `architecture`, `elf_files` and `build_dirs`). Deleted in
#          EVERY branch, always — a machine upgrading into this version must not
#          keep them for ever.
#   SCAN   what `detect-project.sh` emits: the gate's verdict and, for a
#          container, the sub-project roots. The eight fields the scan used to
#          fill beside them (compiler, build system, ELFs, LOCI artifacts, build
#          dirs, a guessed target) are gone with the scan (T14) — a recipe owns
#          every fact about a project's build. Replaced when the gate runs;
#          deleted when the session is inactive; left ALONE when a recipe
#          governs, because there the CLI's mirror owns the context.
#   CLI    what `loci init` records. session-init never writes these — it only
#          copies them between two keyed files for one project (see the mirror
#          below) — which is what makes §6.2's "carried forward by session-init"
#          true. A blanket overwrite loses them; a blanket merge keeps DEAD for
#          ever; both are wrong, which is why there are three sets and not one.
_LOCI_DEAD_KEYS='["architecture","asm_files","binaries","build_compiler","build_dirs","cross_compilers","detected_at","elf_files","language_stack","loci_artifacts","loci_compatible","project_type","scan_depth","source_files"]'
_LOCI_SCAN_KEYS='["detection_status","subproject_roots"]'
_LOCI_CLI_KEYS='["artifact","artifact_only","build_system","compiler","compiler_path","confirmed_by_user","detection_status","init_at","init_candidates","init_reason","init_recipe","init_status","loci_target","validated"]'
# THE RECIPE MIRROR: the build facts `loci init` records beside `init_status`,
# valid only while a recipe governs. Before T14 the session scan wrote the same
# keys from PATH and the tree, so a file a scanning session left behind
# still holds a compiler nothing vouches for and a target guessed off a
# cross-gcc — and `pre-edit-hook.sh` reads `loci_target` by name and ships it
# to `loci build snapshot` (a host-x86 Makefile project was getting
# `--loci-target=armv7e-m` that way). Deleted on every branch where no recipe
# governs — no recipe, or a recorded refusal — because there a value can only
# be that leftover: `loci init` writes these as null/empty on every non-`ok`
# status, so nothing of the CLI's is lost.
#
# `artifact_only` (AAD-7607) is the first of these a hook ACTS on rather than
# prints: under that recipe the compiling verbs can only answer `compdb_absent`,
# so the post-edit reminder is suppressed and the auto-run rules are narrowed.
# That is exactly why it belongs in BOTH sets — a stale `true` left behind by a
# recipe that no longer governs would disarm the reminder for a project that has
# a compile database.
_LOCI_RECIPE_KEYS='["artifact","artifact_only","build_system","compiler","compiler_path","loci_target"]'

# `loci scan` IS UNTOUCHED BY ALL OF THIS, deliberately. Its answer — can LOCI
# measure this binary — never depended on detection state and must not start
# depending on it: a user pointing `loci scan` at an ELF in an uninitialized
# project is asking about the ELF, not about the project. The armed/disarmed
# decision is this function's and the context block's, and nothing below the
# gate should be "fixed" to make scan agree with it.
#
# Decide what this session is (report §6.3's four branches), refresh the keyed
# state file, and export the _CTX_* the caller renders. session-init runs it
# every session; setup runs it as a guarded fallback.
#
# Gate-first, and that ordering is the point: the GATE never decides anything a
# recipe has already answered. A recipe (or a recorded `init_status`) answers
# first, from file tests and one context read; only a project with no recipe is asked
# the gate's question — is there a declared build here? — and nothing is scanned
# in either case (T14 deleted the scan). A docs repo is not armed.
detect_and_write_context() {
    local HASH; HASH=$(hash_cwd)
    local GIT_BRANCH; GIT_BRANCH=$(_git_branch)
    local BRANCH_SLUG; BRANCH_SLUG=$(_branch_slug "$GIT_BRANCH")
    _migrate_legacy_state "$HASH" "$BRANCH_SLUG"
    local KEYED="${STATE_DIR}/project-context-${HASH}.json"

    local RECIPE RECIPE_ROOT=""
    RECIPE=$(_loci_find_recipe "$(pwd)") || RECIPE=""
    # `<root>/.loci/build.yaml` → `<root>`.
    [ -n "$RECIPE" ] && RECIPE_ROOT=$(_parent_dir "$(_parent_dir "$RECIPE")")

    # THE MIRROR. `loci init` keys the context file by the RECIPE ROOT
    # (`recipe.context_path` resolves `rec.root`), while this function keys it by
    # the SESSION CWD. For a session opened in a subdirectory those are two
    # different files, and without this the subdirectory's file never receives a
    # single fact: the block reported "a recipe but no recorded state" for ever,
    # `pre-edit-hook.sh` — which keys by the same cwd — sent no `--loci-target`,
    # and running `loci init` from the subdirectory healed the ROOT file, so
    # nothing could ever fix it. So the CLI's keys are copied across, and the
    # identity keys (which name this session's measurement files) are not.
    local MIRROR=""
    if [ -n "$RECIPE_ROOT" ]; then
        local ROOT_HASH; ROOT_HASH=$(hash_cwd "$RECIPE_ROOT" 2>/dev/null) || ROOT_HASH=""
        if [ -n "$ROOT_HASH" ] && [ "$ROOT_HASH" != "$HASH" ]; then
            MIRROR="${STATE_DIR}/project-context-${ROOT_HASH}.json"
            [ -f "$MIRROR" ] || MIRROR=""
        fi
    fi

    # The state we decide from: this project's own file, plus whatever the CLI
    # recorded for it under the recipe root. Each file is read ONCE, here, and
    # every field below is answered out of the text — the alternative spends a
    # subshell per field per file on every session start.
    #
    # `ADOPT` says whether the mirror's keys are in play at all, and the read
    # order below is what applies them: a CLI key is the MIRROR's answer where
    # there is one, because that is the file `loci init` wrote. It is a name
    # rather than a merged document because the merge itself is the CLI's now
    # (`loci hook project-context --adopt`), and doing it twice in two languages
    # is how the two halves come to disagree.
    local KEYED_DOC="" MIRROR_DOC="" ADOPT=1
    _loci_ctx_read "$KEYED"  && KEYED_DOC="$_LOCI_CTX_DOC"
    _loci_ctx_read "$MIRROR" && MIRROR_DOC="$_LOCI_CTX_DOC"

    local INIT_STATUS
    INIT_STATUS=$(_loci_ctx_field init_status "$MIRROR_DOC" "$KEYED_DOC")

    # PROJECT_INFO stays empty unless the scan actually runs.
    # DROP is a LIST of key sets, one `--drop` flag each; the CLI unions them.
    # It used to be a single array built by a `jq` whose only job was `unique`.
    local PROJECT_INFO="" DEFAULT_STATUS="" GATE=""
    local DROP=( "$_LOCI_DEAD_KEYS" )
    _CTX_STATE=""
    _CTX_STATUS=""
    _CTX_SUBPROJECT_COUNT=0
    _CTX_TARGET="unknown"
    _CTX_COMPILER="unknown"
    _CTX_BUILD="unknown"
    _CTX_ARTIFACT=""
    _CTX_ARTIFACT_ONLY=""
    _CTX_RECIPE="$RECIPE"
    _CTX_INIT_STATUS="$INIT_STATUS"
    _CTX_DEGRADED=""

    case "$INIT_STATUS" in
        unsupported|needs_user)
            # Branch (3). Sticky: `unsupported` is permanent until the user runs
            # `/loci:init`, and a `needs_user` recorded by a headless run is
            # asked at the next analysis the user requests — neither is a reason
            # to arm, and neither is a reason to scan. Checked FIRST so a stray
            # recipe file cannot silently re-arm a project the CLI disarmed.
            #
            # The gate's keys and the recipe mirror go with it: a project that
            # WAS armed keeps a `loci_target` the pre-edit hook would go on
            # sending long after the CLI declared the project unsupportable.
            _CTX_STATE="inactive_status"
            _CTX_STATUS="$INIT_STATUS"
            DROP+=( "$_LOCI_SCAN_KEYS" "$_LOCI_RECIPE_KEYS" )
            # …and the mirror does not put them straight back: on a subdirectory
            # session the copy would undo the delete this branch exists to make.
            ADOPT=""
            ;;
        ok)
            # Branch (1). The recipe's mirror answers; no scan, ever — one would
            # overwrite a recorded target with a guess.
            if [ -n "$RECIPE" ]; then
                _CTX_STATE="initialized"
            else
                # The mirror says initialized and no recipe was found from here.
                # The CLI's own discovery is the authority (it is the one that
                # refuses to compile), so nothing is cleared and the first
                # analysis settles it.
                _CTX_STATE="initialized_degraded"
                _CTX_DEGRADED="recipe"
            fi
            _loci_read_mirror_facts "$MIRROR_DOC" "$KEYED_DOC"
            # …and then does not name it. `init_recipe` records where the CLI
            # last wrote one; printing that path in the very block that says no
            # recipe was found from here is a contradiction the model has to
            # resolve, and the file may not exist at all on this machine.
            [ "$_CTX_DEGRADED" = recipe ] && { _CTX_RECIPE=""; _CTX_ARTIFACT=""; }
            ;;
        *)
            # `failed` is transient (§6.2): re-armed at the next session start,
            # exactly as if nothing had been recorded — INCLUDING when a recipe
            # is still on disk, which happens whenever init refuses a stale,
            # invalid or tampered one (`_existing` clears `rec`, so `_record_status`
            # writes `failed` beside a `.loci/build.yaml`). Reporting that as
            # "a recipe but no recorded state" was false twice over.
            # THE RECIPE DECIDES THE BRANCH; the status only decides the
            # wording. Ordering it the other way round — testing the status
            # first — is what let an unrecognised value reach the "no recipe
            # yet" block with a `.loci/build.yaml` sitting on disk, telling the
            # model to wait for a `not_initialized` the CLI will never answer.
            if [ -n "$RECIPE" ]; then
                case "$INIT_STATUS" in
                    ""|uninitialized)
                        # A recipe on disk with nothing recorded: the state dir
                        # was wiped, or init has not seen this checkout. NO
                        # facts are read out of the file — with no `init_status`
                        # nothing in it came from the CLI, and the compiler and
                        # build system a previous scan left behind would be
                        # printed as fact directly above a line saying there is
                        # no recorded state (a host `clang++` for a project
                        # whose real toolchain is a cross-compiler).
                        _CTX_STATE="initialized_degraded"
                        _CTX_DEGRADED="state"
                        ;;
                    failed)
                        # `failed` beside a recipe — which is reachable: init
                        # clears `rec` for a stale, invalid or tampered recipe
                        # and for a compiler that has gone, so `_record_status`
                        # writes `failed` with the file still on disk. §6.2
                        # makes that transient, so the session arms, and the
                        # init skill routes whichever coded error the next
                        # compile answers with — which will not be
                        # `not_initialized`.
                        _CTX_STATE="armed"
                        _CTX_STATUS="ok"
                        _CTX_DEGRADED="failed"
                        ;;
                    *)
                        # Transient is the safe reading, but it is not a
                        # failure: saying "the last initialization FAILED"
                        # about a value this version merely does not recognise
                        # is a claim nothing supports.
                        loci_log WARN setup-steps "unrecognised init_status '${INIT_STATUS}' beside a recipe — treating it as transient"
                        _CTX_STATE="armed"
                        _CTX_STATUS="ok"
                        _CTX_DEGRADED="unknown"
                        ;;
                esac
            else
                # No recipe. `uninitialized` is THIS function's own default,
                # written below when the key is absent, so it means exactly "no
                # CLI status recorded" and takes the path an absent key takes.
                case "$INIT_STATUS" in
                    ""|uninitialized|failed) ;;
                    *) loci_log WARN setup-steps "unrecognised init_status '${INIT_STATUS}' — treating it as transient" ;;
                esac
                # Branch (2)/(4). `detect-project.sh` IS the cheap gate since
                # T14 — it answers "is a build declared here?" and probes
                # nothing: no compiler, no ELF, no PATH. Its verdict and the
                # sub-project count are all it emits.
                PROJECT_INFO=$("${PLUGIN_DIR}/lib/detect-project.sh" "$(pwd)" 2>/dev/null) \
                    || PROJECT_INFO=""
                loci_json_load "$PROJECT_INFO"
                if loci_json_is_object "$PROJECT_INFO" \
                   && loci_json_has detection_status; then
                    GATE=1
                    _CTX_STATUS=$(loci_json_get detection_status)
                    _CTX_SUBPROJECT_COUNT=$(loci_json_array_len subproject_roots)
                fi
                [ -n "$_CTX_STATUS" ] || _CTX_STATUS="failed"
                case "$_CTX_STATUS" in
                    ok)
                        _CTX_STATE="armed"
                        # The only branch that writes `init_status`, and only
                        # when the key is absent: the CLI owns this field, and
                        # session-init must never reset what it recorded.
                        DEFAULT_STATUS="uninitialized"
                        ;;
                    multi_project) _CTX_STATE="inactive_multi" ;;
                    # A detector that could not run has not said "project", so
                    # it does not arm — but it gets its own sentence rather than
                    # the `no_project` one, which would be a false claim about a
                    # directory that may well hold a Makefile.
                    failed)        _CTX_STATE="inactive_failed" ;;
                    *)             _CTX_STATE="inactive_none" ;;
                esac
                # No recipe governs, so nothing in the file about this
                # project's build can be current: DROP clears the mirror keys a
                # scanning session (or an init since undone) left behind. The
                # PATCH assembly strips the same keys out of the gate's emit —
                # which carries none today; the strip is what keeps that true.
                DROP+=( "$_LOCI_RECIPE_KEYS" )
            fi
            ;;
    esac

    # Set BEFORE the write: these two are what every context block prints, and
    # they do not depend on the write succeeding.
    _CTX_BRANCH="$GIT_BRANCH"
    _CTX_PROJECT_CONTEXT="$KEYED"

    # THE WRITE IS THE CLI'S. This is read-modify-write over a document
    # `loci init` also writes: keys the CLI owns have to survive untouched while
    # the ones a stale session left behind are deleted, and there is no way to do
    # that in shell — the file is pretty-printed, so a nested value spans lines,
    # and bash has no parser. It used to be five `jq` calls, which is five
    # processes on every session start plus a host tool the plugin does not ship.
    #
    # The DECISION is still this function's: which key sets to drop, whether the
    # mirror is in play, whether the gate ran. The verb applies it, against the
    # file re-read at the last possible moment — `loci init` finishing inside
    # this window used to be reverted wholesale, because the write replayed a
    # copy read before the detector ran. The residual window is one process and
    # one rename; bash has no compare-and-swap and this is as narrow as it gets.
    #
    # THE MERGE NEEDS THE CLI, and where there is none the fallback below writes
    # only what this function knows. That is a real degradation, stated rather
    # than hidden — but a mild one: every consumer of this file
    # (`pre-edit-hook.sh`'s target hint, `post-edit-hook.sh`'s nudge, the CLI
    # itself) needs `loci` to be there anyway, `loci init` writes the file too,
    # and the next session start refreshes it once the background install lands.
    local _ctx_args=( "--context=$KEYED" "--project-root=$(pwd)"
                      "--branch=$GIT_BRANCH" "--slug=$BRANCH_SLUG"
                      "--hash=$HASH" )
    local _d
    for _d in "${DROP[@]}"; do _ctx_args+=( "--drop=$_d" ); done
    if [ -n "$ADOPT" ] && [ -n "$MIRROR" ]; then
        _ctx_args+=( "--mirror=$MIRROR" "--adopt=$_LOCI_CLI_KEYS" )
    fi
    # The gate's emit carries no build fact today; the strip is what keeps that
    # true when someone adds one.
    [ -n "$GATE" ] && _ctx_args+=( --gate "--strip=$_LOCI_RECIPE_KEYS" )
    [ -n "$DEFAULT_STATUS" ] && _ctx_args+=( "--default-status=$DEFAULT_STATUS" )

    # A target that is not a regular file is never written through: the verb
    # refuses it too, and saying so here names the file the model was told is its
    # context.
    if [ -e "$KEYED" ] && [ ! -f "$KEYED" ]; then
        loci_log ERROR setup-steps "keyed context path is not a regular file: $KEYED"
        return 1
    fi

    # Captured rather than discarded, so fast-fail has something to quote. The
    # verb's answer is the FILE either way, and both destinations are non-tty, so
    # the capture is not a behaviour change.
    local _ctx_rc=1 _ctx_out=""
    if command -v loci >/dev/null 2>&1; then
        if [ -n "$GATE" ]; then
            _ctx_out=$(printf '%s' "$PROJECT_INFO" \
                | loci hook project-context "${_ctx_args[@]}" 2>&1)
        else
            _ctx_out=$(loci hook project-context "${_ctx_args[@]}" </dev/null 2>&1)
        fi
        _ctx_rc=$?
    fi
    # The FILE is the answer, not the exit status. A `loci` on PATH that exits 0
    # and writes nothing — a shim, a wrapper, a build that lost the verb — would
    # otherwise leave the project with no keyed context at all while this
    # function reported success.
    [ "$_ctx_rc" -eq 0 ] && [ -f "$KEYED" ] && return 0

    # No CLI, or one too old to know the verb. The merge cannot happen, and a
    # BLIND OVERWRITE is the one thing that must not: a file already holding the
    # CLI's facts — `init_status`, `loci_target`, the recipe mirror — is left
    # exactly as it is, because losing those is worse than not refreshing them.
    loci_log WARN setup-steps "keyed context not refreshed: loci hook project-context rc=$_ctx_rc ($KEYED)"
    # Fast-fail says it, and this is the one place in the mode that still takes
    # its fallback: what follows writes the four identity keys the SHELL already
    # knows and asserts no LOCI fact, while a session with no keyed context at
    # all makes every later hook misreport for reasons that have nothing to do
    # with this failure — noise over the signal QA is reading. `session-init.sh`
    # puts the note in the session's own context.
    if [ "$_ctx_rc" -ne 0 ] && declare -F loci_fail_fast >/dev/null 2>&1 \
       && loci_fail_fast && command -v loci >/dev/null 2>&1; then
        loci_log ERROR setup-steps "fail-fast: \`loci hook project-context\` exit $_ctx_rc"
        _LOCI_FF_SESSION_NOTE="LOCI fast-fail is on (LOCI_FAIL_FAST). \`loci hook project-context\` failed with exit $_ctx_rc at session start, so this project's recorded context is whatever was already on disk. Report that to the user before any LOCI analysis, and do not try to repair it. Output:
${_ctx_out:-(no output)}"
    fi
    [ -f "$KEYED" ] && return 0

    # Nothing to preserve, so what this function KNOWS is written directly. The
    # four identity keys are not optional: without `cwd_hash`
    # `stats._measurements_path` falls back to `default` and the project's
    # measurement history silently splits into a second JSONL. Nothing else is
    # asserted — a `detection_status` is written only where the gate actually
    # ran, and an `init_status` only where this function owns the default.
    local _ctx_fresh=( project_root "$(pwd)" git_branch "$GIT_BRANCH"
                       branch_slug "$BRANCH_SLUG" cwd_hash "$HASH" )
    [ -n "$GATE" ] && _ctx_fresh+=( detection_status "$_CTX_STATUS" )
    [ -n "$DEFAULT_STATUS" ] && _ctx_fresh+=( init_status "$DEFAULT_STATUS" )
    loci_json_object "${_ctx_fresh[@]}" > "$KEYED" 2>/dev/null || return 1
    return 0
}

# One context file's text, or nothing. `$(<file)` is a subshell and no process.
_LOCI_CTX_DOC=""
_loci_ctx_read() {
    _LOCI_CTX_DOC=""
    [ -n "${1:-}" ] && [ -f "$1" ] || return 1
    _LOCI_CTX_DOC="$(<"$1")"
    # A file holding anything but one object has nothing to preserve, so start
    # clean — the same answer the CLI's `write_project_context` gives it. The
    # read this replaced merged the caller's fields into BOTH of two concatenated
    # objects and wrote both back, so a malformed file that used to heal itself
    # every session propagated instead, and every later read of `loci_target`
    # answered with two lines.
    loci_json_is_object "$_LOCI_CTX_DOC" || { _LOCI_CTX_DOC=""; return 1; }
}

# One field out of the context documents given, in the order given: the first
# recorded value wins. A `null` counts as unrecorded — it is the same claim as an
# absent key, and every caller here treats it that way.
_loci_ctx_field() {
    local _key="$1" _doc _v
    shift
    for _doc in "$@"; do
        [ -n "$_doc" ] || continue
        loci_json_load "$_doc"
        _v=$(loci_json_get "$_key") || _v=""
        case "$_v" in
            ""|null) continue ;;
        esac
        printf '%s' "$_v"
        return 0
    done
    return 1
}

# The facts the initialized blocks print, out of the recipe's mirror. The
# documents are passed in precedence order and read here, not re-read per field:
# this runs on every SessionStart of every initialized project.
#
# Five are plain strings in the file and `artifact_only` is a JSON boolean, which
# `_loci_ctx_field` renders as the word — hence the `= "true"` below and not a
# `-n` test, which every spelling including `false` would pass. The read it replaced went
# through a JSON array and one text line per field, because a `@tsv` read with
# `IFS=$'\t'` collapses runs of tabs — one empty middle field shifted every
# later field left, and that is how an initialized project came to be told its
# ELF was its recipe (`init_recipe` empty, so `artifact` landed in `recipe`).
# Asking for each field by name cannot shift anything, so the whole trap is gone
# rather than guarded, along with the CRLF strip a Windows `jq` made necessary.
_loci_read_mirror_facts() {
    _CTX_STATUS="ok"
    local _recipe _artifact
    _CTX_TARGET=$(_loci_ctx_field loci_target "$@")   || _CTX_TARGET="unknown"
    _CTX_COMPILER=$(_loci_ctx_field compiler "$@")    || _CTX_COMPILER="unknown"
    _CTX_BUILD=$(_loci_ctx_field build_system "$@")   || _CTX_BUILD="unknown"
    _recipe=$(_loci_ctx_field init_recipe "$@")       || _recipe=""
    _artifact=$(_loci_ctx_field artifact "$@")        || _artifact=""
    _CTX_ARTIFACT="$_artifact"
    # AAD-7607. `true` only — `false` and the absent key (an older `loci init`
    # wrote neither) are the same answer, and the one this must not get wrong is
    # the positive: a project WITH a database whose reminder got disarmed stops
    # measuring. Unlike the two paths below this is NOT `-f`-checked: it is a
    # statement about the recipe, not about a file, and the recipe's own
    # existence has already been established by the branch that calls this.
    [ "$(_loci_ctx_field artifact_only "$@")" = "true" ] && _CTX_ARTIFACT_ONLY=1
    # The CLI's own spelling of the recipe path wins: it is the one the CLI
    # prints in every error it raises about that file, and the walk's `pwd -P`
    # form is MSYS's (`/c/…`) where the CLI's is native (`C:/…`) — one file, two
    # spellings, in one context block.
    [ -n "$_recipe" ] && _CTX_RECIPE="$_recipe"
    # Neither is asserted to the model unless it is there to be measured: a
    # recipe recorded on another machine, or an artifact since deleted, was
    # printed as fact in the very block that says the recipe was not found.
    [ -n "$_CTX_RECIPE" ] && [ ! -f "$_CTX_RECIPE" ] && _CTX_RECIPE=""
    [ -n "$_CTX_ARTIFACT" ] && [ ! -f "$_CTX_ARTIFACT" ] && _CTX_ARTIFACT=""
    # `loci init` maps the recipe's `artifacts.elf` into `artifact`, and that is
    # what the `artifact:` line above is read from. Nothing here rewrites it —
    # bash does not read the recipe — so an initialized project recording none is
    # worth a line in the log and nothing more.
    if [ -z "$_artifact" ]; then
        loci_log INFO setup-steps "initialized project recorded no artifact (recipe: ${_CTX_RECIPE:-none})"
    fi
}
