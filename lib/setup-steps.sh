#!/usr/bin/env bash
# LOCI plugin — shared setup/bootstrap primitives for session-init.sh,
# ensure-loci-cli.sh, setup.sh and post-edit-hook.sh (its first-edit nudge
# keys the project by `hash_cwd`). Sourcing only DEFINES functions (plus
# constants and the shared logger) — no installs, no PATH mutation, no writes.
#
# These live in shell, not a `loci` subcommand, because they bootstrap the very
# `loci` binary a subcommand would need (chicken-and-egg).
#
# Caller contract:
#   • PLUGIN_DIR set to the plugin root before sourcing.
#   • STATE_DIR / LOCI_STATE_DIR resolved before sourcing. Keep that resolution
#     identical across entry scripts (~/.loci/state, falling back to
#     $PLUGIN_DIR/state) or a hook reads a different dir than `loci init`
#     and the other hooks wrote to.
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
# The host adapter: where a host other than Claude Code keeps the plugin, and
# whether a SessionStart is a first start there (AAD-7784). Functions only.
# shellcheck source=./loci_host.sh
. "${PLUGIN_DIR}/lib/loci_host.sh" 2>/dev/null || true

# Pinned loci CLI (prod), from the PyPI wheel. Dev installs float — see
# ensure_loci. This is the ONLY copy of these constants in the plugin.
LOCI_CLI_VERSION="0.2.70"
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
    # Under GitHub Copilot CLI the plugin is where the host says — the root it
    # exports — and has no version siblings: the scan below would find nothing
    # under `~/.copilot/installed-plugins/<marketplace>/<name>/`, and could take
    # a dotted-numeric neighbour of a `--plugin-dir` checkout for a newer
    # install (AAD-7784). Outside Copilot the call answers nothing and the
    # scan decides, as it always has.
    local _exported
    if command -v loci_host_plugin_dir >/dev/null 2>&1 \
       && _exported=$(loci_host_plugin_dir) && [ -n "$_exported" ]; then
        printf '%s' "$_exported"
        return
    fi
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
# Takes an optional directory, defaulting to the process's own. `session-init.sh`
# passes nothing, for its debug session id; `hooks/post-edit-hook.sh` passes the
# edited file's project root, which is the project it is nudging FOR rather than
# the directory it happens to be running in. Both spellings reach the same key because
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
        if [ "$dir" = "." ]; then
            path=$(pwd)
        else
            # GNU realpath tolerates a missing FINAL component; BSD (macOS) refuses
            # it. Resolve the parent and re-attach the leaf, so the two agree and
            # the CLI's port (`recipe.context_key`) keys a not-yet-created leaf the
            # same way on every host. A missing parent still has no key.
            local parent
            parent=$(realpath "$(dirname "$dir")" 2>/dev/null) || return 1
            path="${parent%/}/$(basename "$dir")"
        fi
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

# device:inode of an arbitrary path, for `_loci_find_recipe`'s $HOME ceiling.
# Same ":0" rejection as _canonical_cwd_key, so on a volume with no stable
# index the ceiling falls back to its string compare rather than matching every
# directory on it.
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

# The parent of an absolute path, without a subprocess. `_loci_find_recipe`'s
# two upward walks run one of these per level — twelve `dirname` spawns on a
# six-deep path, ~0.1 s each under Git Bash on Windows, for a string operation
# the shell can do itself.
_parent_dir() {
    case "$1" in
        */?*) printf '%s' "${1%/*}" ;;
        *)    printf '/' ;;
    esac
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
