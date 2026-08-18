# Turn-scoped baseline — pointer

The plan for the post-edit measurement pipeline rework lives in the **`loci-cli`** repo:

    C:\Projects\loci-cli\docs\design\turn-scoped-baseline.md

It is kept there because the CLI half must ship to PyPI before this plugin can bump
`LOCI_CLI_VERSION` to pin it, so the CLI repo leads the sequence.

Branch in both repos: `fix/turn-scoped-baseline`.

## What lands in this repo

| Phase | Change |
| --- | --- |
| 01 | `hooks/pre-edit-hook.sh` reads `prompt_id` and captures the pre-edit **source** into the turn tree, first-write-wins. Must degrade to `loci build snapshot` when the installed CLI lacks the new verb. |
| 02 | **02b done.** `lib/compile-and-read-back.sh` is the one place any change-measuring skill learns an artifact path; five `SKILL.md` files call it instead of spelling `.loci-build/<target>/<basename>.o`, and `hooks/post-edit-hook.sh` carries `prompt_id` so the skill can pass `--turn`. **02c done** on this side: `lib/detect-project.sh` prunes `.loci-build/{cargo,elf,turns}` and walks deep enough to see the mirrored object layout, so a nested object is still advertised and the cargo cache cannot evict it. |
| 03 | **done.** `hooks/turn-reap.sh` calls `loci build reap` on **both** `Stop` (retention, protecting this turn via `--turn`) and `SessionStart` (the same plus `--reclaim-objects`). One script on two events because `session-init.sh` is registered `"matcher": "startup"` and so never runs on `resume`/`clear`/`compact`. The project root comes from the payload's `cwd` — the directory `build snapshot` actually writes into — not from the git top level, which is a different directory whenever a session runs in a subdirectory of a repo. The **`.gitignore` offer is designed out**: `.loci-build/.gitignore` (`*`) is measurably sufficient on its own, and phase 03 makes writing it a guarantee rather than a side effect of a turn capture. |
| 04 | `hooks/post-edit-hook.sh` — decide measurability from `tool_response.structuredPatch`, not from brace-spotting in an Edit fragment. |
| 06 | Header edits route to their dependent translation units in `loci-post-edit` / `loci-preflight`; reconcile the frontmatter's MANDATORY-for-`.h` claim with the hook and the CLI. |
| 08 | **done.** They stopped hand-rolling `.o.prev` in 01a and stopped invoking a compiler directly in 02b — the raw line wrote no `.meta.json` sidecar, so the next `build snapshot` refused and the turn lost its baseline. |
| 09 | `SKILL.md` files stop reading `data.modified` / `data.added` from `loci elf diff` — those fields do not exist; the counts are in `data.summary` and the per-symbol lists in the file at `data.diff_file`. `loci-post-edit`'s false claim was corrected in 02b because it sat in the block being rewritten; `control-flow`, `exec-trace` and the rest are still open. |
| 10 | `PostToolUseFailure` hook; plan/settings skips in the pre-edit hook; the subagent-reporting decision. |

## Evidence and fixtures

Findings report: `C:\Playground\loci-claude-tests\FINDINGS-AND-PLAN.html`
Reproductions and the hook-payload probe: `C:\Playground\loci-claude-tests\`

The probe harness there is what established the `prompt_id` and `tool_response` semantics this
design depends on — both were verified by instrumenting real sessions, because the
`PostToolUse` payload shape is undocumented.
