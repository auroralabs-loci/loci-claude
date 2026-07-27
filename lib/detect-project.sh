#!/bin/bash
# Detect C++ project context: compiler, build system, binaries, ASM files.
# Outputs JSON for session initialization.

# NOT pipefail: `find | head | jq` stages get SIGPIPE (141) on large trees when
# head closes early; pipefail would abort the script despite jq's valid output.
set -eu

CWD="${1:-.}"
IS_WINDOWS=false
[[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]] && IS_WINDOWS=true

# shellcheck source=loci_log.sh
. "$(dirname "$0")/loci_log.sh" 2>/dev/null || true
loci_log INFO detect-project "start: detect-project cwd=$CWD"

# Windows: search well-known install directories for vendor compilers not on PATH.
# Returns the full path to the compiler binary, or fails.
_find_windows_compiler() {
  $IS_WINDOWS || return 1
  local name="$1"
  local candidates=()
  case "$name" in
    tiarmclang)
      candidates=(
        /c/ti/ticlang/bin/tiarmclang.exe
        /c/ti/ccs*/tools/compiler/ti-cgt-armllvm_*/bin/tiarmclang.exe
        /c/ti/ti-cgt-armllvm_*/bin/tiarmclang.exe
      ) ;;
    armcl)
      candidates=(
        /c/ti/ccs*/tools/compiler/ti-cgt-arm_*/bin/armcl.exe
        /c/ti/ti-cgt-arm_*/bin/armcl.exe
      ) ;;
    iccarm)
      candidates=(
        "/c/Program Files/IAR Systems/Embedded Workbench"*/arm/bin/iccarm.exe
        "/c/Program Files (x86)/IAR Systems/Embedded Workbench"*/arm/bin/iccarm.exe
      ) ;;
    armcc)
      candidates=(
        "/c/Keil_v5/ARM/ARMCC/bin/armcc.exe"
        "/c/Keil_v5/ARM/ARMCLANG/bin/armclang.exe"
        "/c/Program Files/Keil_v5/ARM/ARMCC/bin/armcc.exe"
      ) ;;
    arm-none-eabi-gcc)
      candidates=(
        /c/ti/gcc-arm-none-eabi/bin/arm-none-eabi-gcc.exe
        "/c/Program Files/GNU Arm Embedded Toolchain"*/bin/arm-none-eabi-gcc.exe
        "/c/Program Files (x86)/GNU Arm Embedded Toolchain"*/bin/arm-none-eabi-gcc.exe
      ) ;;
  esac
  # Guard empty array: under `set -u`, "${arr[@]}" of an empty array trips
  # "unbound variable" on bash <= 4.3 (incl. macOS /bin/bash, our shebang).
  [ "${#candidates[@]}" -eq 0 ] && return 1
  for candidate in "${candidates[@]}"; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

# Detect C++ compiler (including vendor/embedded toolchains).
detect_compiler() {
  command -v g++ >/dev/null 2>&1 && echo "g++" && return
  command -v clang++ >/dev/null 2>&1 && echo "clang++" && return
  # Vendor / embedded compilers
  command -v tiarmclang >/dev/null 2>&1 && echo "tiarmclang" && return
  command -v armcl >/dev/null 2>&1 && echo "armcl" && return
  command -v iccarm >/dev/null 2>&1 && echo "iccarm" && return
  command -v armcc >/dev/null 2>&1 && echo "armcc" && return
  command -v arm-none-eabi-g++ >/dev/null 2>&1 && echo "arm-none-eabi-g++" && return
  command -v arm-none-eabi-gcc >/dev/null 2>&1 && echo "arm-none-eabi-gcc" && return
  command -v aarch64-linux-gnu-g++ >/dev/null 2>&1 && echo "aarch64-linux-gnu-g++" && return
  command -v aarch64-linux-gnu-gcc >/dev/null 2>&1 && echo "aarch64-linux-gnu-gcc" && return
  command -v aarch64-unknown-linux-gnu-g++ >/dev/null 2>&1 && echo "aarch64-unknown-linux-gnu-g++" && return
  command -v aarch64-unknown-linux-gnu-gcc >/dev/null 2>&1 && echo "aarch64-unknown-linux-gnu-gcc" && return
  command -v tricore-elf-g++ >/dev/null 2>&1 && echo "tricore-elf-g++" && return
  command -v tricore-elf-gcc >/dev/null 2>&1 && echo "tricore-elf-gcc" && return
  # Windows: check well-known install directories
  if $IS_WINDOWS; then
    for comp in tiarmclang armcl iccarm armcc arm-none-eabi-gcc; do
      if _find_windows_compiler "$comp" >/dev/null 2>&1; then
        echo "$comp"
        return
      fi
    done
  fi
  echo "unknown"
}

# Detect build system (including vendor IDEs). Emits "ccs+make" when a
# projectspec and a makefile coexist in the same tree — common for TI
# SimpleLink gmake builds that also ship CCS IDE metadata.
# Run a command under `timeout N` when the timeout binary exists (absent on
# stock macOS) and bare otherwise — a missing timeout must degrade to "run
# it", never to "silently skip the probe".
_maybe_timeout() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$secs" "$@"
  else
    "$@"
  fi
}

detect_build_system() {
  # A root Cargo.toml is decisive: loci compiles these through cargo, so the
  # signal must win over an incidental Makefile at the root.
  [ -f "$CWD/Cargo.toml" ] && echo "cargo" && return
  [ -f "$CWD/CMakeLists.txt" ] && echo "cmake" && return
  [ -f "$CWD/Makefile" ] || [ -f "$CWD/makefile" ] && echo "make" && return
  [ -f "$CWD/meson.build" ] && echo "meson" && return
  [ -f "$CWD/BUILD" ] || [ -f "$CWD/WORKSPACE" ] && echo "bazel" && return
  [ -f "$CWD/conanfile.txt" ] || [ -f "$CWD/conanfile.py" ] && echo "conan" && return
  [ -f "$CWD/vcpkg.json" ] && echo "vcpkg" && return

  # Nested Cargo.toml (a rust/ subdir, workspace member layouts) counts only
  # when NO root-level build system claimed the tree above — a cargo-xtask
  # helper inside a CMake/Make project must not reclassify it as Rust.
  if _maybe_timeout 4 find "$CWD" -maxdepth 2 \
      -type d \( -name .git -o -name node_modules -o -name .venv \
      -o -name target -o -name vendor -o -name third_party \) -prune -o \
      -name "Cargo.toml" -type f -print -quit 2>/dev/null | grep -q .; then
    echo "cargo" && return
  fi

  # Subdir detection — deep scan, bounded by timeout.
  local has_projectspec=false has_makefile=false
  if timeout 4 find "$CWD" -maxdepth 10 \
      -type d \( -name .git -o -name node_modules -o -name .venv \
      -o -name target -o -name vendor -o -name third_party \) -prune -o \
      -name "*.projectspec" -type f -print -quit 2>/dev/null | grep -q .; then
    has_projectspec=true
  fi
  if timeout 4 find "$CWD" -maxdepth 10 \
      -type d \( -name .git -o -name node_modules -o -name .venv \
      -o -name target -o -name vendor -o -name third_party \) -prune -o \
      \( -name "Makefile" -o -name "makefile" -o -name "GNUmakefile" \) \
      -type f -print -quit 2>/dev/null | grep -q .; then
    has_makefile=true
  fi
  if $has_projectspec && $has_makefile; then
    echo "ccs+make" && return
  elif $has_projectspec; then
    echo "ccs" && return
  elif $has_makefile; then
    echo "make" && return
  fi

  find "$CWD" -maxdepth 2 -name "*.ccsproject" -print -quit 2>/dev/null | grep -q . && echo "ccs" && return
  find "$CWD" -maxdepth 2 -name ".cproject" -print -quit 2>/dev/null | grep -q . && echo "ccs" && return
  find "$CWD" -maxdepth 2 -name "*.ewp" -print -quit 2>/dev/null | grep -q . && echo "iar" && return
  find "$CWD" -maxdepth 2 -name "*.eww" -print -quit 2>/dev/null | grep -q . && echo "iar" && return
  find "$CWD" -maxdepth 2 -name "*.uvprojx" -print -quit 2>/dev/null | grep -q . && echo "keil" && return
  find "$CWD" -maxdepth 2 -name "*.uvproj" -print -quit 2>/dev/null | grep -q . && echo "keil" && return
  echo "direct"
}

find_sources() {
  # Two explicit branches, NOT an optional array spliced into one find:
  # "${arr[@]}" on an empty array is an "unbound variable" error under
  # `set -u` on bash <= 4.3 (macOS /bin/bash) — see the candidates/compilers
  # guards elsewhere in this file. Cargo projects also scan one level deeper
  # so workspace members (crates/<m>/src/*.rs) appear in source_files.
  if [ -f "$CWD/Cargo.toml" ]; then
    {
      find "$CWD" -maxdepth 2 \( -name "*.cpp" -o -name "*.cxx" -o -name "*.cc" -o -name "*.c" -o -name "*.h" -o -name "*.hpp" -o -name "*.rs" \) 2>/dev/null
      find "$CWD" -mindepth 3 -maxdepth 4 \
        -type d \( -name .git -o -name node_modules -o -name target \
        -o -name vendor -o -name third_party \) -prune -o \
        -name "*.rs" -type f -print 2>/dev/null
    } | sort -u | head -20 | jq -Rn '[inputs]'
  else
    find "$CWD" -maxdepth 2 \( -name "*.cpp" -o -name "*.cxx" -o -name "*.cc" -o -name "*.c" -o -name "*.h" -o -name "*.hpp" \) 2>/dev/null | head -20 | jq -Rn '[inputs]'
  fi
}

# Single maxdepth-10 walk for linked binaries (*.elf/*.out/*.axf), cached and
# shared by find_elf_files and find_build_dirs so the tree is walked once per
# SessionStart. `timeout`-wrapped so a giant tree can't stall session init.
# maxdepth 10 catches TI CCS / SimpleLink layouts where the ELF lives at
#   examples/rtos/<board>/<stack>/<sample>/<rtos>/<toolchain>/Release/<name>.out
# (depth 9).
_LINKED_BINS=""
_LINKED_BINS_DONE=false
_scan_linked_bins() {
  $_LINKED_BINS_DONE && return 0
  _LINKED_BINS_DONE=true
  # -prune must come BEFORE the match-type expression.
  local prune='-type d ( -name .git -o -name node_modules -o -name .venv -o -name target -o -name vendor -o -name third_party -o -name cmake-build-debug -o -name cmake-build-release -o -name __pycache__ -o -name .pytest_cache )'
  # shellcheck disable=SC2086
  _LINKED_BINS=$(
    timeout 6 find "$CWD" -maxdepth 10 $prune -prune -o \
      \( -name "*.elf" -o -name "*.out" -o -name "*.axf" \) -type f -print \
      2>/dev/null | head -60
  )
}

# Find ELF/object files: the shared linked-binary walk plus .o/.so files in
# common build directories.
find_elf_files() {
  local found=() f
  _scan_linked_bins
  while IFS= read -r f; do
    [ -n "$f" ] && found+=("$f")
  done <<< "$_LINKED_BINS"

  # .o/.so files only in build-like dirs — too many otherwise (a tree-wide *.so
  # glob would pull in prebuilt SDK/system libraries). Match root-level dirs
  # carrying a build token ([Bb]uild/[Dd]ebug/[Rr]elease) plus a few fixed names,
  # so cmake-build-debug, build-arm, Release-cortexm all qualify; unmatched globs
  # stay literal and fail the -d test. Separate finds so a flood of .o objects
  # can't starve .so libs under the shared head cap.
  for d in "$CWD"/*[Bb]uild* "$CWD"/*[Dd]ebug* "$CWD"/*[Rr]elease* \
           "$CWD"/out "$CWD"/output "$CWD"/bin "$CWD"/obj "$CWD"/artifacts; do
    [ -d "$d" ] || continue
    while IFS= read -r f; do
      [ -n "$f" ] && found+=("$f")
    done < <(find "$d" -maxdepth 3 -name "*.o" -type f 2>/dev/null | head -10)
    while IFS= read -r f; do
      [ -n "$f" ] && found+=("$f")
    done < <(find "$d" -maxdepth 3 \( -name "*.so" -o -name "*.so.*" \) -type f 2>/dev/null | head -10)
  done

  if [ ${#found[@]} -eq 0 ]; then
    echo '[]'
  else
    printf '%s\n' "${found[@]}" | sort -u | head -30 | jq -Rn '[inputs]'
  fi
}

# Find candidate build directories by locating dirs that contain either a
# linked ELF or a makefile that references $(CC). The Python cascade will
# score and pick one, but publishing the list here avoids re-walking the
# tree on every preflight invocation.
find_build_dirs() {
  local prune='-type d ( -name .git -o -name node_modules -o -name .venv -o -name target -o -name vendor -o -name third_party -o -name cmake-build-debug -o -name cmake-build-release -o -name __pycache__ -o -name .pytest_cache )'
  local dirs=() f
  # `${f%/*}` instead of `$(dirname "$f")` avoids ~100 subprocess spawns per
  # match on TI trees; find always emits CWD-prefixed paths (every path has a /).
  _scan_linked_bins
  while IFS= read -r f; do
    [ -n "$f" ] && dirs+=("${f%/*}")
  done <<< "$_LINKED_BINS"
  # Also: dirs containing makefile + projectspec together (strong TI signal)
  # shellcheck disable=SC2086
  while IFS= read -r f; do
    [ -n "$f" ] && dirs+=("${f%/*}")
  done < <(
    timeout 6 find "$CWD" -maxdepth 10 $prune -prune -o \
      -name "*.projectspec" -type f -print \
      2>/dev/null | head -40
  )
  if [ ${#dirs[@]} -eq 0 ]; then
    echo '[]'
    return
  fi
  printf '%s\n' "${dirs[@]}" | sort -u | head -40 | jq -Rn '[inputs]'
}

# Find compiled binaries in CWD root (legacy compat).
#
# Skip text/source extensions before spawning `file`: on MSYS2/Cygwin every
# regular file reports executable (NTFS has no x bit), so `[ -x ]` doesn't
# narrow anything and `file` would run on every README and Makefile. The filter
# cuts ~30 spawns to 0-3, saving ~1s per SessionStart on Windows.
find_binaries() {
  local bins=()
  for f in "$CWD"/*; do
    [ -f "$f" ] || continue
    case "$f" in
      *.md|*.txt|*.rst|*.json|*.jsonc|*.yml|*.yaml|*.toml|*.ini|*.cfg|*.conf\
      |*.xml|*.html|*.css|*.csv|*.log|*.lock\
      |*.py|*.pyc|*.pyi|*.sh|*.bash|*.zsh|*.ps1|*.bat|*.cmd\
      |*.js|*.ts|*.tsx|*.jsx|*.mjs|*.cjs\
      |*.c|*.cc|*.cpp|*.cxx|*.h|*.hh|*.hpp|*.hxx|*.rs|*.go|*.java|*.kt\
      |*.gitignore|*.gitattributes|*.editorconfig\
      |*Makefile*|*makefile*|README|README.*|LICENSE|LICENSE.*|CHANGELOG|CHANGELOG.*)
        continue ;;
    esac
    if [ -x "$f" ] && file "$f" 2>/dev/null | grep -qiE '(ELF|Mach-O|executable)'; then
      bins+=("$(basename "$f")")
    fi
  done
  if [ ${#bins[@]} -eq 0 ]; then
    echo '[]'
  else
    printf '%s\n' "${bins[@]}" | jq -Rn '[inputs]'
  fi
}

find_asm_files() {
  find "$CWD" -maxdepth 2 \( -name "*.asm" -o -name "*.s" -o -name "*.S" \) 2>/dev/null | head -20 | jq -Rn '[inputs]'
}

# Cargo artifact scan (Rust projects only). Cargo binaries are extensionless
# and live under target/<triple>/{debug,release} — a tree the generic linked-
# binary walk deliberately prunes — so scan those dirs directly, bounded, and
# confirm ELF via `file`. A session opened in a workspace *member* directory
# has its artifacts in the workspace root's target/, so climb (≤3 levels)
# while parents still carry a Cargo.toml until something is found.
find_cargo_elf_files() {
  local found=() f d base="$CWD" up=0 parent
  while :; do
    for d in "$base"/target/debug "$base"/target/release \
             "$base"/target/*/debug "$base"/target/*/release; do
      [ -d "$d" ] || continue
      while IFS= read -r f; do
        [ -n "$f" ] || continue
        if file "$f" 2>/dev/null | grep -q 'ELF'; then
          found+=("$f")
        fi
      done < <(find "$d" -maxdepth 1 -type f ! -name '*.*' 2>/dev/null | head -10)
    done
    [ ${#found[@]} -gt 0 ] && break
    up=$((up + 1))
    [ "$up" -gt 3 ] && break
    parent=$(dirname "$base")
    [ "$parent" = "$base" ] && break
    [ -f "$parent/Cargo.toml" ] || break
    base="$parent"
  done
  if [ ${#found[@]} -eq 0 ]; then
    echo '[]'
  else
    printf '%s\n' "${found[@]}" | sort -u | head -20 | jq -Rn '[inputs]'
  fi
}

# Rust cross-target detection (Rust projects only): installed rustup stds
# count as cross-compilers — no external cross-toolchain is needed, loci
# builds objects via `cargo rustc --emit=obj` without linking. Emits final
# LOCI target names, mapping ONLY the stds loci-cli itself resolves
# (cargo.py RUST_TARGETS): thumbv6m is armv6-m in its own right (never
# armv7e-m), and non-hf thumbv7em is deliberately absent — advertising it
# would steer users into a `rust_target_missing` for the hf std.
detect_rust_targets() {
  command -v rustup >/dev/null 2>&1 || { echo '[]'; return; }
  local installed compilers=()
  installed=$(_maybe_timeout 6 rustup target list --installed 2>/dev/null) || installed=""
  echo "$installed" | grep -q '^aarch64-unknown-linux-gnu$' && compilers+=("aarch64")
  echo "$installed" | grep -q '^thumbv7em-none-eabihf$'     && compilers+=("armv7e-m")
  echo "$installed" | grep -q '^thumbv6m-none-eabi$'        && compilers+=("armv6-m")
  if [ ${#compilers[@]} -eq 0 ]; then
    echo '[]'
  else
    printf '%s\n' "${compilers[@]}" | sort -u | jq -Rn '[inputs]'
  fi
}

# Locate a working readelf (system, cross-toolchain, or vendor).
_find_readelf() {
  local candidates=(readelf arm-none-eabi-readelf aarch64-linux-gnu-readelf tricore-elf-readelf tiarmreadelf)
  for re in "${candidates[@]}"; do
    if command -v "$re" >/dev/null 2>&1; then
      echo "$re"
      return
    fi
  done
  # Not on PATH — search well-known vendor install directories
  local search_dirs=()
  if $IS_WINDOWS; then
    search_dirs=(
      /c/ti/gcc-arm-none-eabi/bin/arm-none-eabi-readelf.exe
      "/c/Program Files/GNU Arm Embedded Toolchain"*/bin/arm-none-eabi-readelf.exe
      "/c/Program Files (x86)/GNU Arm Embedded Toolchain"*/bin/arm-none-eabi-readelf.exe
      /c/ti/ticlang/bin/tiarmreadelf.exe
      /c/ti/ccs*/tools/compiler/ti-cgt-armllvm_*/bin/tiarmreadelf.exe
      /c/ti/ti-cgt-armllvm_*/bin/tiarmreadelf.exe
    )
  else
    search_dirs=(
      /opt/ti/clang/ti-cgt-armllvm_*/bin/tiarmreadelf
      "$HOME/ti/ti-cgt-armllvm_"*/bin/tiarmreadelf
    )
  fi
  for candidate in "${search_dirs[@]}"; do
    if [ -x "$candidate" ]; then
      echo "$candidate"
      return
    fi
  done
  return 1
}

# Probe ARM ELF build attributes to determine specific ISA (armv6-m vs armv7e-m).
# Returns the ISA string or fails if readelf unavailable or attributes unreadable.
_arm_isa_from_elf() {
  local elf_path="$1"
  local re
  re=$(_find_readelf) || return 1
  local attrs
  attrs=$("$re" -A "$elf_path" 2>/dev/null) || return 1
  # CPU_arch from either format (no grep -P, for macOS compat):
  #   standard readelf:   Tag_CPU_arch: v6S-M
  #   tiarmreadelf:       Description: ARM v6S-M
  local cpu_arch
  cpu_arch=$(echo "$attrs" | sed -n 's/.*Tag_CPU_arch:[[:space:]]*\([^ ]*\).*/\1/p' | head -1)
  [ -z "$cpu_arch" ] && \
    cpu_arch=$(echo "$attrs" | grep -A1 'TagName: CPU_arch' | sed -n 's/.*Description:[[:space:]]*ARM[[:space:]]*\([^ ]*\).*/\1/p' | head -1)
  case "$cpu_arch" in
    v6-M|v6S-M)      echo "armv6-m" ;;
    v7E-M)           echo "armv7e-m" ;;
    v7-M)            echo "armv7-m" ;;
    v8-M.main|v81-M) echo "armv8-m.main" ;;
    v8-M.base)       echo "armv8-m.base" ;;
    *)               return 1 ;;  # unknown or A-class — let caller handle
  esac
}

arch_from_elf() {
  local elf_path="$1"
  local file_output
  file_output=$(file "$elf_path" 2>/dev/null) || return 1
  if echo "$file_output" | grep -qiE 'aarch64|ARM aarch64|ARM 64'; then
    echo "aarch64"
  elif echo "$file_output" | grep -qiE 'ARM,.*EABI|Thumb|Cortex|armv7|arm,'; then
    # ARM detected — refine to specific ISA via ELF attributes.
    local isa
    isa=$(_arm_isa_from_elf "$elf_path") && echo "$isa" || echo "arm"
  elif echo "$file_output" | grep -qiE 'TriCore|tricore'; then
    echo "tricore"
  elif echo "$file_output" | grep -qiE 'x86.64|x86-64|AMD64'; then
    echo "x86_64"
  elif echo "$file_output" | grep -qiE 'Intel 80386|i386|x86,'; then
    echo "i386"
  else
    return 1
  fi
}

# Detect architecture — prefer ELF analysis over uname. With multiple ELFs
# (vendor SDKs ship rom/driverlib binaries alongside the project output), the
# freshest wins, so a stale armv7e-m driverlib doesn't mask a fresh armv6-m build.
detect_architecture() {
  local elf_files="$1"
  local arch elf_path

  # 1. Freshest ELF wins.
  elf_path=$(_freshest_elf "$elf_files")
  if [ -n "$elf_path" ] && [ -f "$elf_path" ]; then
    arch=$(arch_from_elf "$elf_path")
    if [ -n "$arch" ]; then
      echo "$arch"
      return
    fi
  fi
  # 2. Fall back to the first ELF — useful when stat is unavailable.
  elf_path=$(echo "$elf_files" | jq -r '.[0] // empty' 2>/dev/null)
  if [ -n "$elf_path" ] && [ -f "$elf_path" ]; then
    arch=$(arch_from_elf "$elf_path")
    if [ -n "$arch" ]; then
      echo "$arch"
      return
    fi
  fi
  # 3. Fall back to executables in CWD. Extension filter before `file` — same
  # MSYS2 [-x]-is-always-true rationale as find_binaries().
  for f in "$CWD"/*; do
    [ -f "$f" ] || continue
    case "$f" in
      *.md|*.txt|*.rst|*.json|*.jsonc|*.yml|*.yaml|*.toml|*.ini|*.cfg|*.conf\
      |*.xml|*.html|*.css|*.csv|*.log|*.lock\
      |*.py|*.pyc|*.pyi|*.sh|*.bash|*.zsh|*.ps1|*.bat|*.cmd\
      |*.js|*.ts|*.tsx|*.jsx|*.mjs|*.cjs\
      |*.c|*.cc|*.cpp|*.cxx|*.h|*.hh|*.hpp|*.hxx|*.rs|*.go|*.java|*.kt\
      |*.gitignore|*.gitattributes|*.editorconfig\
      |*Makefile*|*makefile*|README|README.*|LICENSE|LICENSE.*|CHANGELOG|CHANGELOG.*)
        continue ;;
    esac
    if [ -x "$f" ] && file "$f" 2>/dev/null | grep -qiE '(ELF|Mach-O)'; then
      arch=$(arch_from_elf "$f")
      if [ -n "$arch" ]; then
        echo "$arch"
        return
      fi
    fi
  done
  uname -m
}

# Detect available LOCI-compatible cross-compilers.
detect_cross_compilers() {
  local compilers=()
  command -v aarch64-linux-gnu-g++ >/dev/null 2>&1 && compilers+=("aarch64")
  command -v aarch64-linux-gnu-gcc >/dev/null 2>&1 && compilers+=("aarch64")
  command -v aarch64-unknown-linux-gnu-g++ >/dev/null 2>&1 && compilers+=("aarch64")
  command -v aarch64-unknown-linux-gnu-gcc >/dev/null 2>&1 && compilers+=("aarch64")
  command -v arm-none-eabi-g++ >/dev/null 2>&1 && compilers+=("cortexm")
  command -v arm-none-eabi-gcc >/dev/null 2>&1 && compilers+=("cortexm")
  command -v tricore-elf-g++ >/dev/null 2>&1 && compilers+=("tricore")
  command -v tricore-elf-gcc >/dev/null 2>&1 && compilers+=("tricore")
  # Vendor compilers that target LOCI architectures
  command -v tiarmclang >/dev/null 2>&1 && compilers+=("cortexm")
  command -v armcl >/dev/null 2>&1 && compilers+=("cortexm")
  command -v iccarm >/dev/null 2>&1 && compilers+=("cortexm")
  command -v armcc >/dev/null 2>&1 && compilers+=("cortexm")
  # Windows: also check well-known install directories
  if $IS_WINDOWS; then
    _find_windows_compiler tiarmclang >/dev/null 2>&1 && compilers+=("cortexm")
    _find_windows_compiler armcl >/dev/null 2>&1 && compilers+=("cortexm")
    _find_windows_compiler iccarm >/dev/null 2>&1 && compilers+=("cortexm")
    _find_windows_compiler armcc >/dev/null 2>&1 && compilers+=("cortexm")
    _find_windows_compiler arm-none-eabi-gcc >/dev/null 2>&1 && compilers+=("cortexm")
  fi
  if [ ${#compilers[@]} -eq 0 ]; then
    echo '[]'
  else
    printf '%s\n' "${compilers[@]}" | sort -u | jq -Rn '[inputs]'
  fi
}

# Map generic arch name to LOCI timing-backend target.
_map_to_timing_target() {
  case "$1" in
    cortexm)  echo "armv7e-m" ;;
    tricore)  echo "tc399" ;;
    aarch64)  echo "aarch64" ;;
    *)        echo "$1" ;;
  esac
}

# Map detected architecture to LOCI target (aarch64, armv7e-m, armv6-m, tc399) or null
resolve_loci_target() {
  local arch="$1"
  local cross_compilers="$2"
  local lower_arch
  lower_arch=$(echo "$arch" | tr '[:upper:]' '[:lower:]')
  local generic
  case "$lower_arch" in
    aarch64|arm64)
      generic="aarch64" ;;
    armv6-m)
      echo "armv6-m" && return ;;
    armv7e-m|armv7-m)
      echo "armv7e-m" && return ;;
    armv8-m.main|armv8-m.base)
      echo "armv7e-m" && return ;;
    arm|armv7*|armv8-m*|cortex-m*|thumb)
      generic="cortexm" ;;
    tricore|tc3*|tc39*)
      generic="tricore" ;;
    *)
      # Host arch is not a LOCI target — check if any cross-compiler is available
      local first
      first=$(echo "$cross_compilers" | jq -r '.[0] // empty' 2>/dev/null)
      if [ -n "$first" ]; then
        generic="$first"
      else
        echo "null"
        return
      fi
      ;;
  esac
  _map_to_timing_target "$generic"
}

# Infer compiler from a path token. Most vendor SDKs use per-toolchain build
# dirs (`ticlang`, `iar`, `gcc`, `keil`, `armclang`) — far more reliable than
# grepping orchestration makefiles, which mention every toolchain.
_compiler_from_path() {
  local p="$1"
  case "$p" in
    */ticlang/*)                              echo "tiarmclang"; return 0 ;;
    */iar/*|*/ewarm/*)                        echo "iccarm"; return 0 ;;
    */gcc/*|*/arm-gcc/*)                      echo "arm-none-eabi-gcc"; return 0 ;;
    */keil/*|*/armcc/*)                       echo "armcc"; return 0 ;;
    */armclang/*)                             echo "armcc"; return 0 ;;
    */tricore/*)                              echo "tricore-elf-gcc"; return 0 ;;
    */aarch64/*|*/aarch64-linux-gnu/*|*/arm64/*) echo "aarch64-linux-gnu-gcc"; return 0 ;;
  esac
  return 1
}

# Pick the freshest linked binary from a JSON array. Skips .o files — they live
# in caches like .loci-build/ and don't represent the active toolchain; only
# .out/.elf/.axf linker outputs are meaningful for path-based inference.
_freshest_elf() {
  local elfs="$1"
  local best="" best_mt=0 mt
  while IFS= read -r elf; do
    [ -z "$elf" ] && continue
    [ -f "$elf" ] || continue
    case "$elf" in
      *.o) continue ;;
    esac
    # GNU coreutils use `stat -c %Y`; macOS/BSD use `stat -f %m`. Try GNU first:
    # on Linux `stat -f` means --file-system and succeeds with the wrong output,
    # so probing it first would mask the correct mtime.
    mt=$(stat -c %Y "$elf" 2>/dev/null || stat -f %m "$elf" 2>/dev/null)
    [ -z "$mt" ] && continue
    if [ "$mt" -gt "$best_mt" ]; then
      best_mt=$mt
      best=$elf
    fi
  done < <(echo "$elfs" | jq -r '.[]?' 2>/dev/null)
  [ -n "$best" ] && echo "$best"
}

# Count (into _TALLY_N) how many of the given files contain the ERE pattern.
# One grep spawn per call; the counter is a builtin while-read loop (bash 3.2
# safe, no `mapfile`) so grep is the only process spawned.
_count_files_matching() {
  local pat="$1"; shift
  local _line
  _TALLY_N=0
  while IFS= read -r _line; do
    [ -n "$_line" ] && _TALLY_N=$((_TALLY_N + 1))
  done < <(grep -lEi "$pat" "$@" 2>/dev/null)
}

# Tally compiler references across config files; pick the most-mentioned one.
# Fallback when no toolchain path token is found in a fresh ELF.
#
# One `grep -lEi` per compiler family across ALL files (7 spawns) instead of 7
# greps per file: the per-file loop was ~7s on TI SimpleLink trees on Git Bash
# (~19ms/spawn) and dominated SessionStart.
_tally_compiler_refs() {
  # Pre-filter to existing regular files (builtin test). Also guards `grep`
  # against an all-missing list, which would read stdin and block.
  local existing=() f
  for f in "$@"; do
    [ -f "$f" ] && existing+=("$f")
  done
  [ "${#existing[@]}" -eq 0 ] && { echo ""; return; }

  local tia armcl icc armcc gcc_eabi gcc_a64 tricore
  _count_files_matching 'tiarmclang|ti_arm_clang|TI_TOOLCHAIN' "${existing[@]}"; tia=$_TALLY_N
  _count_files_matching 'ti_arm_cgt|TI_CGT'                     "${existing[@]}"; armcl=$_TALLY_N
  _count_files_matching 'iccarm|IAR_ARMCOMPILER|ewarm'          "${existing[@]}"; icc=$_TALLY_N
  _count_files_matching 'armcc|ARMCC|armclang'                  "${existing[@]}"; armcc=$_TALLY_N
  _count_files_matching 'arm-none-eabi'                         "${existing[@]}"; gcc_eabi=$_TALLY_N
  _count_files_matching 'aarch64-linux-gnu'                     "${existing[@]}"; gcc_a64=$_TALLY_N
  _count_files_matching 'tricore-elf'                           "${existing[@]}"; tricore=$_TALLY_N

  local best="" best_n=0
  if [ "$tia"      -gt "$best_n" ]; then best="tiarmclang";            best_n=$tia;      fi
  if [ "$armcl"    -gt "$best_n" ]; then best="armcl";                 best_n=$armcl;    fi
  if [ "$icc"      -gt "$best_n" ]; then best="iccarm";                best_n=$icc;      fi
  if [ "$armcc"    -gt "$best_n" ]; then best="armcc";                 best_n=$armcc;    fi
  if [ "$gcc_eabi" -gt "$best_n" ]; then best="arm-none-eabi-gcc";     best_n=$gcc_eabi; fi
  if [ "$gcc_a64"  -gt "$best_n" ]; then best="aarch64-linux-gnu-gcc"; best_n=$gcc_a64;  fi
  if [ "$tricore"  -gt "$best_n" ]; then best="tricore-elf-gcc";       best_n=$tricore;  fi
  echo "$best"
}

# Detect compiler referenced in build configs (not necessarily in PATH).
#   1. Prefer the toolchain implied by the freshest ELF's build path (vendor
#      SDKs use per-toolchain build dirs like `ticlang/Release/`).
#   2. Else tally compiler references across matched config files, picking the
#      most-mentioned — avoids false positives from SDK orchestration makefiles
#      that mention every toolchain.
detect_build_compiler() {
  local build_sys="$1"
  local elfs="$2"

  # 1. Path-based detection from freshest ELF.
  local freshest
  freshest=$(_freshest_elf "$elfs")
  if [ -n "$freshest" ]; then
    local from_path
    if from_path=$(_compiler_from_path "$freshest"); then
      echo "$from_path"
      return
    fi
  fi

  # 2. Config-file tally fallback.
  local config_files=()
  case "$build_sys" in
    cmake) config_files=("$CWD/CMakeLists.txt" "$CWD/cmake"/*.cmake) ;;
    make) config_files=("$CWD/Makefile" "$CWD/makefile") ;;
    ccs) config_files=("$CWD"/*.projectspec "$CWD"/.cproject) ;;
    ccs+make)
      local prune='-type d ( -name .git -o -name node_modules -o -name .venv -o -name target -o -name vendor -o -name third_party -o -name __pycache__ -o -name .pytest_cache )'
      while IFS= read -r f; do
        [ -n "$f" ] && config_files+=("$f")
      done < <(
        # shellcheck disable=SC2086
        timeout 4 find "$CWD" -maxdepth 10 $prune -prune -o \
          \( -name "*.projectspec" -o -name "Makefile" -o -name "makefile" \) \
          -type f -print 2>/dev/null | head -50
      )
      ;;
    iar) config_files=("$CWD"/*.ewp) ;;
    keil) config_files=("$CWD"/*.uvprojx "$CWD"/*.uvproj) ;;
  esac

  # Guard empty array (bash <= 4.3 "unbound variable" under set -u). Also covers
  # build_system values with no config-file mapping yet (meson, bazel, conan,
  # vcpkg): degrade to empty BUILD_COMPILER instead of aborting detection.
  if [ "${#config_files[@]}" -eq 0 ]; then
    echo ""
    return
  fi

  _tally_compiler_refs "${config_files[@]}"
}

# Wrap each stage with start/end log lines so SessionStart slowness is traceable
# via loci.log. `|| rc=$?` keeps set -e happy when the command exits non-zero.
_stage() {
    local label="$1"; shift
    loci_log INFO detect-project "start: $label"
    local rc=0
    "$@" || rc=$?
    loci_log INFO detect-project "end: $label (rc=$rc)"
    return $rc
}

COMPILER=$(_stage detect_compiler        detect_compiler)
BUILD_SYSTEM=$(_stage detect_build_system detect_build_system)
SOURCES=$(_stage find_sources             find_sources)
# Prime the shared walk in the MAIN shell so the command-substitution subshells
# below (find_elf_files, find_build_dirs) inherit the cached result rather than
# each re-walking the tree (subshells share state only with their parent).
loci_log INFO detect-project "start: scan_linked_bins"
_scan_linked_bins
loci_log INFO detect-project "end: scan_linked_bins (rc=0)"
ELF_FILES=$(_stage find_elf_files         find_elf_files)
BUILD_DIRS=$(_stage find_build_dirs       find_build_dirs)
BINARIES=$(_stage find_binaries           find_binaries)
ASM_FILES=$(_stage find_asm_files         find_asm_files)

# Rust/Cargo augmentation — everything in this block is cargo-gated so
# non-Rust projects take the exact path they always did.
IS_CARGO=false
[ "$BUILD_SYSTEM" = "cargo" ] && IS_CARGO=true
if $IS_CARGO; then
  # Cargo bins live under the pruned target/ tree — merge a targeted scan
  # into ELF_FILES so the freshest-ELF arch detection sees them (e.g. a
  # CI-built target/aarch64-unknown-linux-gnu/release/<bin>).
  CARGO_ELF_FILES=$(_stage find_cargo_elf_files find_cargo_elf_files)
  ELF_FILES=$(printf '%s\n%s\n' "$ELF_FILES" "$CARGO_ELF_FILES" | jq -s 'add | unique')
  if command -v rustc >/dev/null 2>&1; then
    COMPILER="rustc"
  fi
fi

ARCH=$(_stage detect_architecture         detect_architecture "$ELF_FILES")
CROSS_COMPILERS=$(_stage detect_cross_compilers detect_cross_compilers)
if $IS_CARGO; then
  # Installed rustup stds count as cross-compilers: `cargo rustc --emit=obj`
  # produces target objects without any external cross-toolchain. For the
  # LOCI-target decision only they matter — a C cross-gcc on PATH proves
  # nothing about what cargo can build, so cargo projects resolve against
  # the rust list alone (the merged list is still reported for context).
  RUST_CROSS=$(_stage detect_rust_targets detect_rust_targets)
  LOCI_TARGET=$(_stage resolve_loci_target resolve_loci_target "$ARCH" "$RUST_CROSS")
  CROSS_COMPILERS=$(printf '%s\n%s\n' "$CROSS_COMPILERS" "$RUST_CROSS" | jq -s 'add | unique')
else
  LOCI_TARGET=$(_stage resolve_loci_target  resolve_loci_target "$ARCH" "$CROSS_COMPILERS")
fi

# Only compute BUILD_COMPILER when COMPILER is generic: when it's already
# vendor-specific the result would be discarded anyway, and the fallback
# grep-tally walks every Makefile + projectspec (~7s on TI trees on Windows).
BUILD_COMPILER=""
if $IS_CARGO; then
  loci_log INFO detect-project "skip: detect_build_compiler (cargo project)"
else
  case "$COMPILER" in
    g++|clang++|unknown)
      BUILD_COMPILER=$(_stage detect_build_compiler detect_build_compiler "$BUILD_SYSTEM" "$ELF_FILES")
      [ -n "$BUILD_COMPILER" ] && COMPILER="$BUILD_COMPILER"
      ;;
    *)
      loci_log INFO detect-project "skip: detect_build_compiler (COMPILER=$COMPILER is vendor-specific)"
      ;;
  esac
fi

loci_log INFO detect-project "result: compiler=$COMPILER build_system=$BUILD_SYSTEM arch=$ARCH loci_target=$LOCI_TARGET elfs=$(echo "$ELF_FILES" | jq 'length' 2>/dev/null || echo ?) build_dirs=$(echo "$BUILD_DIRS" | jq 'length' 2>/dev/null || echo ?)"

# Resolve full path for compilers discovered via Windows search (not on PATH).
COMPILER_PATH=""
if $IS_WINDOWS && [ "$COMPILER" != "unknown" ] && ! command -v "$COMPILER" >/dev/null 2>&1; then
  COMPILER_PATH=$(_find_windows_compiler "$COMPILER" 2>/dev/null || true)
fi

if [ "$LOCI_TARGET" != "null" ]; then
  LOCI_COMPATIBLE="true"
else
  LOCI_COMPATIBLE="false"
fi

jq -n \
  --arg compiler "$COMPILER" \
  --arg compiler_path "$COMPILER_PATH" \
  --arg build_compiler "$BUILD_COMPILER" \
  --arg build_system "$BUILD_SYSTEM" \
  --arg project_type "cpp" \
  --arg architecture "$ARCH" \
  --argjson source_files "$SOURCES" \
  --argjson binaries "$BINARIES" \
  --argjson elf_files "$ELF_FILES" \
  --argjson build_dirs "$BUILD_DIRS" \
  --argjson asm_files "$ASM_FILES" \
  --argjson cross_compilers "$CROSS_COMPILERS" \
  --argjson loci_compatible "$LOCI_COMPATIBLE" \
  --arg loci_target "$LOCI_TARGET" \
  --arg detected_at "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" \
  '{
    language_stack: (if $build_system == "cargo" then ["rust"] else ["cpp"] end),
    compiler: $compiler,
    compiler_path: (if $compiler_path == "" then null else $compiler_path end),
    build_compiler: (if $build_compiler == "" then null else $build_compiler end),
    build_system: $build_system,
    project_type: (if $build_system == "cargo" then "rust" else $project_type end),
    architecture: $architecture,
    source_files: $source_files,
    binaries: $binaries,
    elf_files: $elf_files,
    build_dirs: $build_dirs,
    asm_files: $asm_files,
    cross_compilers: $cross_compilers,
    loci_compatible: $loci_compatible,
    loci_target: (if $loci_target == "null" then null else $loci_target end),
    detected_at: $detected_at,
    scan_depth: 8,
    detection_status: "ok"
  }'
