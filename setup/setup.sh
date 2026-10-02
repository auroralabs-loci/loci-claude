#!/usr/bin/env bash
# LOCI Plugin — setup / repair entry point. Orders the steps and reports; the
# step logic lives in lib/setup-steps.sh and hooks/ensure-loci-cli.sh.
#
# Nothing here detects a project or writes project context (AAD-7531): step 7
# asks `loci project` about the directory setup was run in, and the keyed
# context is `loci init`'s to write.

set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

# MUST match session-init.sh / ensure-loci-cli.sh so the install status read
# below is the `loci-cli-status.json` those scripts write.
STATE_DIR="${LOCI_STATE_DIR:-${HOME:-}/.loci/state}"
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

# 1. Prerequisites (uv). `jq` is NOT one any more — the hooks and the shared
# libraries read and write JSON through `lib/loci_json.sh`, which forks nothing.
# What still wants a JSON tool is steps 6 and 7 below, which run on a fresh
# machine possibly BEFORE the CLI exists, so they take whatever the host happens
# to have and say so when it has nothing. Nothing here fails for want of it.
echo -n "Checking prerequisites... "
if ! have_uv; then
  echo -e "${RED}missing: uv${NC}"
  echo "PREREQ_MISSING: uv is required to install the loci CLI but is not installed."
  exit 1
fi
echo -e "${GREEN}OK${NC}"

# 2. Install the loci CLI via the single installer (self-locking).
echo -n "Installing loci CLI... "
bash "${PLUGIN_DIR}/hooks/ensure-loci-cli.sh" >/dev/null 2>&1 || true
_cli_status="unknown"
if [ -f "${STATE_DIR}/loci-cli-status.json" ]; then
  loci_json_load "$(<"${STATE_DIR}/loci-cli-status.json")"
  _cli_status=$(loci_json_get status) || _cli_status="unknown"
  [ -n "$_cli_status" ] || _cli_status="unknown"
fi
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

# 5. Validate hooks.json — with whatever parser the host has.
#
# This step runs before the CLI is guaranteed to exist, so it cannot ask `loci`,
# and `jq` is no longer a prerequisite. So: jq if the host has one, else a
# python, else say plainly that nothing validated it. A missing parser is not a
# broken install and must not fail the setup — Claude Code parses hooks.json
# itself and reports its own error — but an INVALID file with a parser present
# still stops here, which is the whole point of the step.
JQ=$(command -v jq 2>/dev/null || true)
PY_BIN=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
echo -n "Validating hooks... "
if [ -n "$JQ" ]; then
  if "$JQ" empty "${PLUGIN_DIR}/hooks/hooks.json" 2>/dev/null; then
    echo -e "${GREEN}OK${NC}"
  else
    echo -e "${RED}INVALID hooks/hooks.json${NC}"
    exit 1
  fi
elif [ -n "$PY_BIN" ]; then
  if "$PY_BIN" -c 'import json,sys; json.load(open(sys.argv[1], encoding="utf-8"))' \
       "${PLUGIN_DIR}/hooks/hooks.json" 2>/dev/null; then
    echo -e "${GREEN}OK${NC}"
  else
    echo -e "${RED}INVALID hooks/hooks.json${NC}"
    exit 1
  fi
else
  echo -e "${YELLOW}skipped (no jq or python on this host)${NC}"
fi

# 6. Register hooks with Claude Code. As a plugin, Claude Code reads hooks.json
# directly — skip when running from the plugin cache (the ../../.. heuristic
# would resolve to a wrong path there).
echo -n "Registering hooks... "
if echo "${PLUGIN_DIR}" | grep -q '\.claude/plugins'; then
  echo -e "${GREEN}plugin mode — hooks.json used directly${NC}"
elif [ -z "$JQ" ]; then
  # Only the non-plugin (checkout) install reaches here, and the rewrite below
  # is a JSON transform this script cannot do on its own. Named, not silent: the
  # user is told which file to edit rather than left with hooks that never fire.
  echo -e "${YELLOW}skipped (needs jq; add hooks/hooks.json to .claude/settings.json by hand)${NC}"
elif [[ "${PLUGIN_DIR}" == *"'"* ]]; then
  # The rewrite below puts this path inside the launcher's single quotes
  # (AAD-7780), where a quote would end the literal early. Named, not silent.
  echo -e "${YELLOW}skipped (plugin dir contains a single quote; add hooks/hooks.json to .claude/settings.json by hand)${NC}"
else
  PROJECT_ROOT="$(cd "${PLUGIN_DIR}/../../.." 2>/dev/null && pwd || echo "")"
  # Skip if PROJECT_ROOT is empty, a filesystem root, or not writable
  if [ -z "$PROJECT_ROOT" ] || [ "$PROJECT_ROOT" = "/" ] || [[ "$PROJECT_ROOT" =~ ^/[a-zA-Z]/?$ ]] || ! [ -w "$PROJECT_ROOT" ]; then
    echo -e "${YELLOW}skipped (project root not detected)${NC}"
  else
    SETTINGS_FILE="${PROJECT_ROOT}/.claude/settings.json"
    mkdir -p "${PROJECT_ROOT}/.claude"

    if [ -f "$SETTINGS_FILE" ] && grep -q "exec bash.*hooks/session-init.sh" "$SETTINGS_FILE" 2>/dev/null; then
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

# 7. The project setup was run in, if any — asked of the CLI, not detected.
if command -v loci >/dev/null 2>&1; then
  loci_json_load "$(loci project 2>/dev/null)"
  _proj_root=$(loci_json_get project_root) || _proj_root=""
  if [ -n "$_proj_root" ]; then
    if [ "$(loci_json_get init_status)" = unsupported ]; then
      echo "  Project:    ${_proj_root} — its target is not one LOCI supports"
    elif [ "$(loci_json_get code)" = not_initialized ]; then
      echo "  Project:    ${_proj_root} — not initialized; run /loci:init ${_proj_root}"
    elif [ -z "$(loci_json_get code)" ]; then
      echo "  Project:    ${_proj_root} — ready (target $(loci_json_get loci_target))"
    fi
  fi
fi

echo ""
echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "In a project /loci:init has set up, the plugin will automatically:"
echo "  - Pre-scan C/C++/Rust/Go edits and prompt execution-aware analysis"
echo "  - Analyze ELF binaries locally via the loci CLI (timing, energy,"
echo "    stack depth, memory, symbols, assembly, diff)"
echo "  - Inject performance/regression findings into Claude's context"
echo ""
echo "Skills: /loci:exec-trace, /loci:stack-depth, /loci:memory-report, /loci:control-flow"
echo "Auto-runs: loci-preflight (in /plan), loci-post-edit (after edits)"
echo ""
echo "Run 'loci doctor' to verify your toolchain, and sign in once with"
echo "'loci login' when timing/energy analysis is first requested."
echo ""
