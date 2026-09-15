#!/usr/bin/env bash
# The project gate: is this directory a project LOCI may arm in? Outputs JSON
# for session initialization — the gate's verdict and, for a container, the
# sub-project roots.
#
# That is ALL this script does since Phase 4 (T14). The scan it used to run
# after the gate — compilers on PATH, build systems, ELFs, LOCI's own
# artifacts, build directories, cross-compilers, a guessed target — is deleted:
# every fact about how a project builds is the recipe's (`loci init`), and the
# cascade those fields were hints for is gone from the CLI. The gate is what
# decides arming, and a recipe is what decides everything else.

# NOT pipefail: a `find | head` stage gets SIGPIPE (141) on large trees when head
# closes early; pipefail would abort the script despite the valid output.
set -eu

# One directory, optional, defaulting to `.`. `--force-scan` is gone with the
# scan: its one caller was the eval harness, which stages a recipe now.
_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --) ;;
    -*) printf 'detect-project.sh: unknown option %s\n' "$1" >&2; exit 2 ;;
    *)  [ -n "$_ARG" ] && { printf 'detect-project.sh: one directory, not two\n' >&2; exit 2; }
        _ARG="$1" ;;
  esac
  shift
done

CWD="${_ARG:-.}"
[ -d "$CWD" ] || { printf 'detect-project.sh: not a directory: %s\n' "$CWD" >&2; exit 2; }

# shellcheck source=loci_log.sh
. "$(dirname "$0")/loci_log.sh" 2>/dev/null || true
# shellcheck source=loci_json.sh
. "$(dirname "$0")/loci_json.sh" 2>/dev/null || true
loci_log INFO detect-project "start: detect-project cwd=$CWD"


_has_root_build_file() {
  local d="$1"
  # `go.mod` is a root build declaration exactly as `Cargo.toml` is. Without it
  # here a Go-only repository failed this gate, `detect_full` never ran, and
  # session-init printed the disarm block — so every Go project was inert no
  # matter what the rest of the Go support did.
  [ -f "$d/go.mod" ] ||
  [ -f "$d/Cargo.toml" ] || [ -f "$d/CMakeLists.txt" ] ||
  [ -f "$d/Makefile" ] || [ -f "$d/makefile" ] || [ -f "$d/GNUmakefile" ] ||
  [ -f "$d/meson.build" ] || [ -f "$d/BUILD" ] || [ -f "$d/WORKSPACE" ] ||
  [ -f "$d/conanfile.txt" ] || [ -f "$d/conanfile.py" ] || [ -f "$d/vcpkg.json" ] ||
  [ -f "$d/platformio.ini" ] || [ -f "$d/west.yml" ] || [ -f "$d/west.yaml" ] ||
  [ -f "$d/build.ninja" ]
}
# Every name above that the CLI's `init_evidence._ROOT_MARKERS` initializes on
# is here too, and `test_freshness_contract.py` checks that against the CLI's
# source: a marker `loci init` accepts and this gate does not arm on is a
# project the CLI would measure and the session disarms (`go.mod` was that
# once; `west.yaml` — the long spelling beside `west.yml` — was that until T14).
# `build.ninja` is the gate's own: a generated compile database is evidence of a
# build here even though init derives no recipe from it by itself.

# The gate's only tree walk, so it is the gate's only real cost — ONE walk for
# both kinds of declaration, and `-quit` stops it at the first hit.
#
# Vendor project files were always looked for two levels down (`firmware/
# app.uvprojx` repos keep them there). Ordinary build files are looked for at
# the same depth for the same reason, and because restricting them to depth 0
# broke the commonest firmware-monorepo layout there is: a repo whose root
# holds `docs/` and `firmware/CMakeLists.txt` went inactive under a sentence
# claiming no build file declares a build here, which was false.
#
# The prune list is doing two jobs. `.git` is pruned because a build file in it
# would be nothing to do with this project — NOT for speed: at `-maxdepth 2`
# find never descends into `.git/objects/xx`, and measured on the docs repo the
# prune is worth nothing (0.32 s with, 0.31 s without). An earlier version of
# this comment credited it with the 3.5 s → 0.4 s the whole gate rework bought.
# And a build file inside
# `node_modules`/`.venv`/`target`/`vendor`/`third_party` is a DEPENDENCY's
# declaration, while one inside `docs`/`tests`/`examples`/`scripts`/`www` builds
# something that is not the project — `sphinx-quickstart` writes `docs/Makefile`
# into every Python repo it touches, and arming a pure-Python project off it is
# the same false-positive class this gate exists to close, re-opened one
# directory over. A project whose real build lives in one of those directories
# still has `/loci:init`, which records the recipe and never consults the gate
# again.
_has_nested_build_declaration() {
  # `-mindepth 1` is load-bearing, not tidiness: without it the prune is applied
  # to the START directory too, so a checkout whose own basename happens to be
  # `tests` or `docs` or `web` had its entire depth-2 walk pruned at the root —
  # the same repo armed or not depending on what its top folder was called, and
  # the inactive sentence ("no build file declares a build in this directory")
  # was flatly false with a `firmware/CMakeLists.txt` sitting in it.
  find "$CWD" -mindepth 1 -maxdepth 2 \
    -type d \( -name .git -o -name node_modules -o -name .venv \
    -o -name target -o -name vendor -o -name third_party \
    -o -name .loci -o -name .loci-build \
    -o -name docs -o -name doc -o -name Documentation \
    -o -name test -o -name tests -o -name example -o -name examples \
    -o -name sample -o -name samples -o -name bench -o -name benchmarks \
    -o -name contrib -o -name scripts -o -name www -o -name web -o -name site \) -prune -o \
    \( -name "*.projectspec" -o -name "*.ccsproject" \
    -o -name ".cproject" -o -name "*.ewp" -o -name "*.eww" \
    -o -name "*.uvprojx" -o -name "*.uvproj" -o -name "*.csolution.yml" \
    -o -name Cargo.toml -o -name go.mod -o -name CMakeLists.txt -o -name Makefile \
    -o -name makefile -o -name GNUmakefile -o -name meson.build \
    -o -name BUILD -o -name WORKSPACE -o -name conanfile.txt \
    -o -name conanfile.py -o -name vcpkg.json -o -name platformio.ini \
    -o -name west.yml -o -name west.yaml -o -name build.ninja \) -type f -print -quit \
    2>/dev/null | grep -q .
}

# A directory's device:inode, into `$_DEV_INODE`. Returns 1 when there isn't a
# usable one: a ":0" inode (FAT/exFAT and some SMB mounts on Windows report 0
# for every file) would make an inode compare call every directory the same one.
#
# It SETS a global rather than printing, so the `stat`-flavour cache survives:
# every caller was a command substitution, and a `$( )` runs the body in a
# subshell whose assignment dies with it — the same bug this file fixed for
# `_subproject_count`, repeated one function over. Cached, it is one `stat` per
# call on either platform instead of two on whichever one is not GNU.
_STAT_FLAVOUR=""
_DEV_INODE=""
_dev_inode() {
  _DEV_INODE=""
  local k
  case "$_STAT_FLAVOUR" in
    gnu) k=$(stat -c '%d:%i' "$1" 2>/dev/null) || k="" ;;
    bsd) k=$(stat -f '%d:%i' "$1" 2>/dev/null) || k="" ;;
    *)
      if k=$(stat -c '%d:%i' "$1" 2>/dev/null) && [ -n "$k" ]; then _STAT_FLAVOUR=gnu
      elif k=$(stat -f '%d:%i' "$1" 2>/dev/null) && [ -n "$k" ]; then _STAT_FLAVOUR=bsd
      else k=""; fi ;;
  esac
  case "$k" in ''|*:0) return 1 ;; esac
  _DEV_INODE="$k"
}

# Immediate subdirs that are project roots in their own right (own repo or
# own root-level build file). `-e` catches `.git` files too (worktrees,
# submodules).
_list_subproject_roots() {
  local d
  for d in "$CWD"/*/; do
    d="${d%/}"
    [ -d "$d" ] || continue
    if [ -e "$d/.git" ] || _has_root_build_file "$d"; then
      echo "$d"
    fi
  done
}

# The parent of an absolute path, without a subprocess. Every upward walk here
# runs one of these per level, and `dirname` is an external command.
_parent_of() {
  case "$1" in
    */?*) printf '%s' "${1%/*}" ;;
    *)    printf '/' ;;
  esac
}

# Is this directory $HOME? Resolved once: $HOME's physical path and its
# device:inode, both taken through `cd … && pwd -P` so a symlinked home
# (ordinary on Linux) is compared by what it points at.
#
# There is NO basename short-circuit here, and there was one for exactly one
# commit. "Two paths naming one directory share a last component" is false in
# both of the cases the inode compare exists for: Git Bash mounts %TEMP% at
# `/tmp`, so `/tmp` and `…/Local/Temp` are one directory with different last
# components, and Windows filesystems are case-insensitive while `pwd -P` does
# not normalise case, so `…/Home` and `…/home` are too. It made the gate arm
# `~/Downloads` off a dotfiles repo again — the false positive two earlier
# rounds had closed — and it made `_loci_find_recipe` adopt `~/.loci/build.yaml`
# through the ceiling. One `stat` per level is what correctness costs here.
#
# What that costs, measured here: a deep NON-project directory under $HOME
# walks to the top and pays one `stat` per level — 0.77 s at depth 2, ~1.0 s
# at depth 6, against 0.65-0.68 s for the pre-T08 gate, which was cheaper
# because it compared the strings and got the answer wrong. Everywhere else
# the walk stops at the first `.git` and the difference does not arise: this
# docs repo is 0.36 s against 2.86 s before.
_HOME_KEY=""
_HOME_PATH=""
_HOME_KEY_DONE=false
_is_home_key() {
  if ! $_HOME_KEY_DONE; then
    _HOME_KEY_DONE=true
    _HOME_PATH=$(cd "${HOME:-/nonexistent}" 2>/dev/null && pwd -P) || _HOME_PATH=""
    if [ -n "$_HOME_PATH" ] && _dev_inode "$_HOME_PATH"; then
      _HOME_KEY="$_DEV_INODE"
    fi
  fi
  [ -n "$_HOME_PATH" ] && [ "$1" = "$_HOME_PATH" ] && return 0
  [ "$1" = "${HOME:-/}" ] && return 0
  [ -n "$_HOME_KEY" ] || return 1
  _dev_inode "$1" || return 1
  [ "$_DEV_INODE" = "$_HOME_KEY" ]
}

# The root of the checkout CWD is in — CWD itself, or an ancestor — or nothing.
# Cached, because three separate call sites used to make this climb and each
# one spawned two `stat`s per level.
#
# The $HOME stop comes FIRST and is exclusive, the same order as
# `_loci_find_recipe`'s ceiling in lib/setup-steps.sh: testing `.git` first let
# a dotfiles repo (`~/.git` beside `~/Makefile`) arm every directory under the
# home tree, while the recipe walk correctly refused the same tree.
_CHECKOUT_ROOT=""
_CHECKOUT_ROOT_DONE=false
_checkout_root() {
  if ! $_CHECKOUT_ROOT_DONE; then
    _CHECKOUT_ROOT_DONE=true
    local d i=0 up
    d=$(cd "$CWD" 2>/dev/null && pwd -P) || d=""
    while [ -n "$d" ] && [ $i -lt 40 ]; do
      _is_home_key "$d" && break
      [ -e "$d/.git" ] && { _CHECKOUT_ROOT="$d"; break; }
      [ "$d" = "/" ] && break
      up=$(_parent_of "$d")
      [ "$up" = "$d" ] && break
      d="$up"; i=$((i + 1))
    done
  fi
  [ -n "$_CHECKOUT_ROOT" ] || return 1
  printf '%s' "$_CHECKOUT_ROOT"
}

_is_home_dir() {
  local d
  d=$(cd "$CWD" 2>/dev/null && pwd -P) || return 1
  _is_home_key "$d"
}

# THE CHEAP GATE. Echoes the detection verdict:
#   ok            — a single analyzable project; run full detection
#   multi_project — a container of independent projects, not a project itself
#   no_project    — no declared build anchored to this tree
#
# From Phase 2 on this gate — and nothing below it — owns the arming decision
# (report §6.3). Evidence is a build the project DECLARES: a root build file, or
# a vendor project file within two levels. Three anchors were dropped because
# each produced an observed false positive:
#
#   `-d .loci-build`   LOCI's own cache anchoring the tree LOCI had scanned once,
#                      which armed this docs repo as an armv7e-m C++ project.
#   source sweeps      `_has_source_evidence`, which counted fixture `.c` files
#                      and armed the plugin's own repo as armv6-m.
#   ELF / PATH probes  compiled artifacts and cross-compilers on PATH, which
#                      prove the machine, never the directory. Those live in
#                      `detect_full` and only run AFTER this gate says "project".
#
# A project with sources and no declared build no longer arms. That is the
# deliberate trade: `/loci:init` (or auto-init on the first `not_initialized`)
# is what makes such a project measurable, and it records a recipe — after
# which session-init never consults this gate again.
# Is CWD one checkout — its root, or somewhere inside it? Then everything under
# it belongs to it, and nothing under it is an independent project.
_inside_one_checkout() {
  _checkout_root >/dev/null
}

# Cached in a GLOBAL that callers read, not printed: the gate asks twice, and
# `$(_subproject_count)` ran the body in a command-substitution subshell whose
# assignment died with it — so the "cache" cached nothing and the walk ran twice
# anyway. Callers now invoke it and read `$_SUBPROJECT_COUNT`.
_SUBPROJECT_COUNT=""
_subproject_count() {
  [ -n "$_SUBPROJECT_COUNT" ] \
    || _SUBPROJECT_COUNT=$(_list_subproject_roots | grep -c . || true)
}

# May a build file two levels down be read as CWD's own?
#
# Inside a checkout, yes: `firmware/CMakeLists.txt` in a repo is that repo's
# build, and a repo subdir whose children are submodules is one project's
# interior. Outside one, only when nothing else claims the file — a directory
# holding somebody's repo is not a project because that repo has a Makefile,
# which is the whole reason `~/Projects` and `wrapper/repo/` must stay quiet.
_depth2_belongs_to_cwd() {
  _inside_one_checkout && return 0
  _subproject_count
  [ "$_SUBPROJECT_COUNT" -eq 0 ]
}

_project_gate() {
  # Declared in CWD itself. $HOME included: a Makefile a user put in their home
  # directory is still a declared build (pinned by
  # test_home_dir_with_own_build_file_is_still_ok).
  if _has_root_build_file "$CWD"; then
    echo "ok"; return
  fi
  # Declared within two levels. $HOME is excluded from this one because a
  # single `~/Downloads/demo.uvprojx` would otherwise claim the entire home
  # tree — the same shape of false positive the source sweeps produced.
  if ! _is_home_dir && _depth2_belongs_to_cwd && _has_nested_build_declaration; then
    echo "ok"; return
  fi
  # Declared at the root of the checkout CWD is INSIDE. `cd repo/firmware &&
  # claude` is an ordinary way to start work, and the repo's `Makefile` two
  # levels up is still what builds the file you are about to edit — the old gate
  # covered this with a maxdepth-6 source sweep, and deleting the sweep without
  # replacing it made every subdirectory session inactive under a sentence
  # saying no build file declares a build here.
  #
  # It is the same climb the recipe walk makes, and it stops in the same two
  # places, so the gate and the recipe cannot disagree about which directory is
  # the project.
  local _root
  if _root=$(_checkout_root) && [ "$_root" != "$CWD" ] \
     && _has_root_build_file "$_root"; then
    echo "ok"; return
  fi
  # No evidence. Say WHICH kind of non-project this is, because "a directory
  # holding your repos" earns a different sentence than "not a project".
  # Inside a checkout we never say it: a repo subdir whose children are
  # submodules is one project's interior, not a container.
  if ! _inside_one_checkout; then
    _subproject_count
    [ "$_SUBPROJECT_COUNT" -ge 2 ] && { echo "multi_project"; return; }
  fi
  echo "no_project"
}

_stage() {
    local label="$1"; shift
    loci_log INFO detect-project "start: $label"
    local rc=0
    "$@" || rc=$?
    loci_log INFO detect-project "end: $label (rc=$rc)"
    return $rc
}


# The printf at the bottom is the script's single emit point.
# `subproject_roots` is filled only for a container, so a consumer can say how
# many independent projects it found.
SUBPROJECT_ROOTS='[]'

GATE_STATUS=$(_stage project_gate _project_gate)
case "$GATE_STATUS" in
  multi_project)
    # A JSON array of paths, built forklessly out of the walk's own lines. A
    # directory name may hold a quote or a backslash, so every element goes
    # through `loci_json_escape` — this used to be `jq -Rn '[inputs]'`, and jq
    # is a host tool the plugin does not bundle.
    _sr_sep=""
    SUBPROJECT_ROOTS="["
    while IFS= read -r _sr_line; do
      [ -n "$_sr_line" ] || continue
      SUBPROJECT_ROOTS="${SUBPROJECT_ROOTS}${_sr_sep}\"$(loci_json_escape "$_sr_line")\""
      _sr_sep=", "
    done <<EOF
$(_list_subproject_roots | head -20)
EOF
    SUBPROJECT_ROOTS="${SUBPROJECT_ROOTS}]"
    ;;
esac
loci_log INFO detect-project "result: detection_status=$GATE_STATUS"

# THE EMIT, and the whole of it. `session-init.sh` treats this key set as the
# set it owns (`_LOCI_SCAN_KEYS`): a key that used to be here and is not any
# more is DELETED from the keyed context file, so a machine upgrading into this
# version does not keep the scan's eight fields for ever. Adding a key here
# claims it away from the CLI — check `init.context_fields` first.
printf '{\n  "subproject_roots": %s,\n  "detection_status": "%s"\n}\n' \
  "$SUBPROJECT_ROOTS" "$(loci_json_escape "$GATE_STATUS")"
