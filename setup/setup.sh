#!/usr/bin/env bash
# LOCI Plugin — setup / repair entry point. Orders the steps and reports; the
# step logic lives in lib/setup-steps.sh and hooks/ensure-loci-cli.sh.
#
# The SessionStart hook owns project detection every session. Setup writes
# project-context state only as a guarded fallback (plugin installed mid-session,
# before any SessionStart ran the detector), and only after install.

set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# MUST match session-init.sh / ensure-loci-cli.sh so the detection guard below
# checks the same keyed file session-init writes.
STATE_DIR="${HOME}/.loci/state"
mkdir -p "$STATE_DIR" 2>/dev/null || STATE_DIR="${PLUGIN_DIR}/state"
mkdir -p "$STATE_DIR" 2>/dev/null || true
export LOCI_STATE_DIR="$STATE_DIR"

# shellcheck source=../lib/setup-steps.sh
. "${PLUGIN_DIR}/lib/setup-steps.sh"
augment_path

echo ""
echo -e "${BLUE}=========================================${NC}"
echo -e "${BLUE}  LOCI Plugin for Claude Code${NC}"
echo -e "${BLUE}  SW Execution-Aware Analysis${NC}"
echo -e "${BLUE}=========================================${NC}"
echo ""

# 1. Prerequisites (jq, uv)
echo -n "Checking prerequisites... "
if ! JQ=$(find_jq); then
  echo -e "${RED}missing: jq${NC}"
  echo "PREREQ_MISSING: jq is required but not installed."
  exit 1
fi
if ! have_uv; then
  echo -e "${RED}missing: uv${NC}"
  echo "PREREQ_MISSING: uv is required to install the loci CLI but is not installed."
  exit 1
fi
echo -e "${GREEN}OK${NC}"

# 2. Install the loci CLI via the single installer (self-locking).
echo -n "Installing loci CLI... "
bash "${PLUGIN_DIR}/hooks/ensure-loci-cli.sh" >/dev/null 2>&1 || true
_cli_status=$("$JQ" -r '.status // "unknown"' "${STATE_DIR}/loci-cli-status.json" 2>/dev/null || echo unknown)
case "$_cli_status" in
  ready|installed) echo -e "${GREEN}OK${NC}" ;;
  skipped)         echo -e "${YELLOW}skipped (bootstrap/test mode)${NC}" ;;
  *)               echo -e "${YELLOW}FAILED — see ${STATE_DIR}/loci-cli-install.log${NC}" ;;
esac

# 3. Toolchain verification is `loci doctor`'s job, not this script's — this
# installs LOCI's own dependencies, not a C/C++/Rust compiler.

# 4. Permissions.
echo -n "Setting permissions... "
fix_exec_bits
echo -e "${GREEN}OK${NC}"

# 5. Project detection. Skip only when a healthy context exists; re-detect when
# missing or a prior detection failed, so setup repairs instead of rubber-stamping.
echo -n "Detecting project... "
_hash=$(hash_cwd)
_ctx="${STATE_DIR}/project-context-${_hash}.json"
_status="missing"
[ -f "$_ctx" ] && _status=$("$JQ" -r '.detection_status // "ok"' "$_ctx" 2>/dev/null)
if [ "$_status" = "ok" ]; then
  echo -e "${GREEN}OK (already detected this session)${NC}"
elif detect_and_write_context; then
  echo -e "${GREEN}OK${NC}"
  echo "  Compiler:   ${_CTX_COMPILER:-unknown}"
  echo "  Build:      ${_CTX_BUILD:-unknown}"
  echo "  Target:     ${_CTX_TARGET:-unknown}"
else
  echo -e "${YELLOW}detection failed${NC}"
fi

# 6. Validate hooks.json.
echo -n "Validating hooks... "
if "$JQ" empty "${PLUGIN_DIR}/hooks/hooks.json" 2>/dev/null; then
  echo -e "${GREEN}OK${NC}"
else
  echo -e "${RED}INVALID hooks/hooks.json${NC}"
  exit 1
fi

# 7. Register hooks with Claude Code. As a plugin, Claude Code reads hooks.json
# directly — skip when running from the plugin cache (the ../../.. heuristic
# would resolve to a wrong path there).
echo -n "Registering hooks... "
if echo "${PLUGIN_DIR}" | grep -q '\.claude/plugins'; then
  echo -e "${GREEN}plugin mode — hooks.json used directly${NC}"
else
  PROJECT_ROOT="$(cd "${PLUGIN_DIR}/../../.." 2>/dev/null && pwd || echo "")"
  # Skip if PROJECT_ROOT is empty, a filesystem root, or not writable
  if [ -z "$PROJECT_ROOT" ] || [ "$PROJECT_ROOT" = "/" ] || [[ "$PROJECT_ROOT" =~ ^/[a-zA-Z]/?$ ]] || ! [ -w "$PROJECT_ROOT" ]; then
    echo -e "${YELLOW}skipped (project root not detected)${NC}"
  else
    SETTINGS_FILE="${PROJECT_ROOT}/.claude/settings.json"
    mkdir -p "${PROJECT_ROOT}/.claude"

    if [ -f "$SETTINGS_FILE" ] && grep -q "capture-action.sh" "$SETTINGS_FILE" 2>/dev/null; then
      echo -e "${GREEN}already registered${NC}"
    else
      # Expand ${CLAUDE_PLUGIN_ROOT} to the absolute plugin dir.
      HOOKS_CONFIG=$("$JQ" --arg pd "${PLUGIN_DIR}" '
        def replace_plugin_root:
          if type == "string" then
            gsub("\\$\\{CLAUDE_PLUGIN_ROOT\\}"; $pd) |
            gsub("\\$CLAUDE_PLUGIN_ROOT"; $pd)
          elif type == "array" then map(replace_plugin_root)
          elif type == "object" then to_entries | map(.value |= replace_plugin_root) | from_entries
          else .
          end;
        replace_plugin_root
      ' "${PLUGIN_DIR}/hooks/hooks.json")

      if [ -f "$SETTINGS_FILE" ]; then
        # Merge hooks into existing settings.json
        HOOKS_ONLY=$(echo "$HOOKS_CONFIG" | "$JQ" '.hooks')
        if "$JQ" --argjson hooks "$HOOKS_ONLY" '. + {hooks: $hooks}' "$SETTINGS_FILE" > "${SETTINGS_FILE}.tmp" 2>/dev/null; then
          mv "${SETTINGS_FILE}.tmp" "$SETTINGS_FILE"
          echo -e "${GREEN}OK (merged into existing settings.json)${NC}"
        else
          rm -f "${SETTINGS_FILE}.tmp"
          echo -e "${YELLOW}FAILED to merge — add hooks manually${NC}"
        fi
      else
        echo "$HOOKS_CONFIG" > "$SETTINGS_FILE"
        echo -e "${GREEN}OK${NC}"
      fi
    fi
  fi
fi

echo ""
echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "The plugin will automatically:"
echo "  - Detect your project's compiler, build system, and target arch"
echo "  - Pre-scan C/C++/Rust edits and prompt execution-aware analysis"
echo "  - Analyze ELF binaries locally via the loci CLI (timing, energy,"
echo "    stack depth, memory, symbols, assembly, diff)"
echo "  - Inject performance/regression findings into Claude's context"
echo ""
echo "Skills: /exec-trace, /stack-depth, /memory-report, /control-flow"
echo "Auto-runs: loci-preflight (in /plan), loci-post-edit (after edits)"
echo ""
echo "Run 'loci doctor' to verify your toolchain, and sign in once with"
echo "'loci login' when timing/energy analysis is first requested."
echo ""
