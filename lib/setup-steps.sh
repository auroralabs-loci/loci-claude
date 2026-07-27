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
#   • jq-dependent functions read $JQ if set, else fall back to `jq` on PATH.

# Idempotent guard — safe to source repeatedly / from multiple entry scripts.
[ -n "${_LOCI_SETUP_STEPS_SOURCED:-}" ] && return 0
_LOCI_SETUP_STEPS_SOURCED=1

# shellcheck source=./loci_log.sh
. "${PLUGIN_DIR}/lib/loci_log.sh" 2>/dev/null || true

# Pinned loci CLI (prod), from the PyPI wheel. Dev installs float — see
# ensure_loci. This is the ONLY copy of these constants in the plugin.
# 0.1.102 carries the Rust/Cargo build path this plugin version documents —
# do NOT release this plugin before the loci-tools 0.1.102 wheel ships.
LOCI_CLI_VERSION="0.1.102"
LOCI_CLI_PACKAGE="loci-tools"

loci_is_windows() {
    case "$(uname -s)" in MINGW*|MSYS*) return 0 ;; *) return 1 ;; esac
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
    export PATH
}

find_jq() {
    local _c
    for _c in jq /usr/bin/jq /usr/local/bin/jq /opt/homebrew/bin/jq \
              "$HOME/.local/bin/jq"; do
        if command -v "$_c" >/dev/null 2>&1; then echo "$_c"; return 0; fi
        [ "$_c" != jq ] && [ -x "$_c" ] && { echo "$_c"; return 0; }
    done
    return 1
}

have_uv() { command -v uv >/dev/null 2>&1; }

# Executable bits on the shell entry points; guards checkouts that lose +x.
fix_exec_bits() {
    chmod +x "${PLUGIN_DIR}/hooks/"*.sh 2>/dev/null || true
    chmod +x "${PLUGIN_DIR}/lib/"*.sh   2>/dev/null || true
}

# Installer for the loci CLI (a uv tool on PATH). Install-source resolution is
# INDEPENDENT of LOCI_ENV: set LOCI_DEV_CLI_PATH=<checkout> for an editable
# install; otherwise the pinned build. Backend URLs are chosen at runtime by the
# CLI, not by which build is installed, so a tester runs the dev backend
# (LOCI_ENV=dev) against the pinned build. Idempotent — reinstalls only when
# loci is missing, the pin drifted, or the recorded install spec changed. Never
# installs under _LOCI_BOOTSTRAP (set by tests; also an air-gapped opt-out).
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

    _loci_install_spec="${LOCI_CLI_PACKAGE}==${LOCI_CLI_VERSION}"
    _loci_cli_pinned=1
}

_loci_cli_ready() {
    command -v loci >/dev/null 2>&1 || return 1
    # A prior install recording a different spec means the source changed
    # (pinned↔editable, or a different editable path) — force reinstall so the
    # opt-in takes. No record (externally installed loci) → fall through.
    if command -v jq >/dev/null 2>&1 && [ -f "${STATE_DIR}/loci-cli-status.json" ]; then
        local _recorded
        _recorded=$(jq -r '.spec // ""' "${STATE_DIR}/loci-cli-status.json" 2>/dev/null)
        [ -n "$_recorded" ] && [ "$_recorded" != "$_loci_install_spec" ] && return 1
    fi
    # Editable/floating installs float — accept presence. Only the pin is
    # version-gated (a bump forces reinstall).
    [ -n "$_loci_cli_pinned" ] || return 0
    local v; v=$(loci --version 2>/dev/null | tr -cd '0-9.')
    [ "$v" = "$LOCI_CLI_VERSION" ]
}

_loci_write_status() {
    local st="$1" f="${STATE_DIR}/loci-cli-status.json" tmp
    tmp="${STATE_DIR}/loci-cli-status.json.tmp.$$"
    local ver=""
    command -v loci >/dev/null 2>&1 && ver=$(loci --version 2>/dev/null | tr -cd '0-9.')
    command -v jq >/dev/null 2>&1 || return 0
    jq -n --arg s "$st" --arg spec "$_loci_install_spec" --arg ver "$ver" \
          --arg log "${STATE_DIR}/loci-cli-install.log" --arg ts "$(date -u +%FT%TZ 2>/dev/null)" \
          '{status:$s, spec:$spec, version:$ver, log:$log, ts:$ts}' > "$tmp" 2>/dev/null \
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
    # shellcheck disable=SC2086
    if uv tool install --force -p 3.12 $_loci_install_spec >"$_install_log" 2>&1; then
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
_canonical_cwd_key() {
    local key
    # macOS/BSD: -f is the format flag (errors out on GNU, so falls through).
    # GNU/MSYS: -c is the format flag.
    if key=$(stat -f '%d:%i' . 2>/dev/null) && [ -n "$key" ]; then
        case "$key" in *:0) ;; *) printf '%s' "$key"; return 0 ;; esac
    fi
    if key=$(stat -c '%d:%i' . 2>/dev/null) && [ -n "$key" ]; then
        case "$key" in *:0) ;; *) printf '%s' "$key"; return 0 ;; esac
    fi
    local path; path=$(realpath . 2>/dev/null || pwd)
    if loci_is_windows; then
        printf '%s' "$path" | tr '[:upper:]' '[:lower:]'
    else
        printf '%s' "$path"
    fi
}

hash_cwd() {
    local key h
    key=$(_canonical_cwd_key)
    h=$(printf '%s' "$key" | sha256sum 2>/dev/null | cut -c1-12)
    [ -n "$h" ] && { echo "$h"; return 0; }
    h=$(printf '%s' "$key" | shasum -a 256 2>/dev/null | cut -c1-12)
    [ -n "$h" ] && { echo "$h"; return 0; }
    printf '%s' "$key" | cksum | awk '{print $1}'
}

# device:inode of an arbitrary path, for legacy-state migration. Same ":0"
# rejection as _canonical_cwd_key so migration bails rather than matching every
# project on a volume with no stable index.
_inode_key() {
    local k
    k=$(stat -f '%d:%i' "$1" 2>/dev/null) || k=$(stat -c '%d:%i' "$1" 2>/dev/null)
    case "$k" in ''|*:0) return 1 ;; esac
    printf '%s' "$k"
}

_git_branch() {
    git -C "$(pwd)" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown"
}

_branch_slug() {
    printf '%s' "$1" | tr '/' '_' | tr -cd 'A-Za-z0-9_-' | cut -c1-64
}

# One-shot migration: rename state files keyed by any older hash to the
# canonical (device:inode) hash. Discovers candidates by resolving each
# project-context-*.json's project_root to an inode; same inode as the current
# cwd means it's ours, whatever path-spelling produced its old hash. Idempotent.
_migrate_legacy_state() {
    local JQ="${JQ:-jq}"
    local new_hash="$1" slug="$2"
    local current_inode; current_inode=$(_inode_key .)
    [ -z "$current_inode" ] && return 0
    local ctx_file legacy_hash old_root old_inode f new_f
    for ctx_file in "${STATE_DIR}"/project-context-*.json; do
        [ -f "$ctx_file" ] || continue
        legacy_hash=$(basename "$ctx_file" .json)
        legacy_hash="${legacy_hash#project-context-}"
        case "$legacy_hash" in *[!a-f0-9]*) continue;; esac
        [ "$legacy_hash" = "$new_hash" ] && continue
        old_root=$("$JQ" -r '.project_root // empty' "$ctx_file" 2>/dev/null)
        [ -z "$old_root" ] && continue
        old_inode=$(_inode_key "$old_root")
        [ "$old_inode" = "$current_inode" ] || continue
        for f in \
            "${STATE_DIR}/project-context-${legacy_hash}.json" \
            "${STATE_DIR}/loci-measurements-${legacy_hash}-${slug}.jsonl" \
            "${STATE_DIR}/loci-stats-${legacy_hash}-${slug}.json"
        do
            new_f="${f//${legacy_hash}/${new_hash}}"
            [ -f "$f" ] && [ ! -e "$new_f" ] && mv -f "$f" "$new_f" 2>/dev/null
        done
    done
}

# Detect the current project and atomically write its keyed state file. Exports
# _CTX_* for the caller. session-init runs it every session; setup runs it only
# as a guarded fallback when the keyed file is missing.
detect_and_write_context() {
    local JQ="${JQ:-jq}"
    local PROJECT_INFO
    PROJECT_INFO=$("${PLUGIN_DIR}/lib/detect-project.sh" "$(pwd)" 2>/dev/null) || PROJECT_INFO=""
    # detect-project.sh owns the schema and always emits detection_status:"ok".
    # No/invalid output means it couldn't run — record that, don't re-declare it.
    "$JQ" -e 'has("detection_status")' <<< "$PROJECT_INFO" >/dev/null 2>&1 \
        || PROJECT_INFO='{"detection_status":"failed"}'

    local COMPILER BUILD_SYS LOCI_TARGET
    COMPILER=$( "$JQ" -r '.compiler     // "unknown"' <<< "$PROJECT_INFO" 2>/dev/null || echo unknown)
    BUILD_SYS=$("$JQ" -r '.build_system // "unknown"' <<< "$PROJECT_INFO" 2>/dev/null || echo unknown)
    LOCI_TARGET=$("$JQ" -r '.loci_target // "unknown"' <<< "$PROJECT_INFO" 2>/dev/null || echo unknown)

    local HASH; HASH=$(hash_cwd)
    local GIT_BRANCH; GIT_BRANCH=$(_git_branch)
    local BRANCH_SLUG; BRANCH_SLUG=$(_branch_slug "$GIT_BRANCH")
    _migrate_legacy_state "$HASH" "$BRANCH_SLUG"
    local KEYED="${STATE_DIR}/project-context-${HASH}.json"
    local TMP="${KEYED}.tmp.$$"

    # Writer injects only identity fields (cwd_hash/branch) and persists; the
    # schema is detect-project.sh's. Always overwrites — no stale state left.
    "$JQ" --arg pwd "$(pwd)" --arg branch "$GIT_BRANCH" --arg slug "$BRANCH_SLUG" --arg hash "$HASH" \
        '. + {project_root: $pwd, git_branch: $branch, branch_slug: $slug, cwd_hash: $hash}' <<< "$PROJECT_INFO" \
        > "$TMP" 2>/dev/null \
        && mv -f "$TMP" "$KEYED" 2>/dev/null \
        || { rm -f "$TMP" 2>/dev/null; return 1; }

    _CTX_TARGET="$LOCI_TARGET"
    _CTX_COMPILER="$COMPILER"
    _CTX_BUILD="$BUILD_SYS"
    _CTX_BRANCH="$GIT_BRANCH"
    _CTX_PROJECT_CONTEXT="$KEYED"
}
