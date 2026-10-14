# Releasing the LOCI plugin

What a plugin release is, what has to be proven before it, and where the proof runs. The
`loci-tools` wheel has its own page (`loci-cli/RELEASING.md`); its step 7 hands over to this
one. Commands are bash — Git Bash on Windows, where releases are made from.

## 1. The version — four files, one commit

A plugin change ships under a new version, as does a pin bump (step 2):

1. `.claude-plugin/plugin.json` → `"version"`.
2. `agents.txt` → the `LOCI version:` line (the one number the agent reports as LOCI's).
3. `pyproject.toml` → `version`.
4. `uv.lock` → `uv lock`, never by hand.

`tests/unit/test_version_sync.py` pins 1, 3 and 4 to each other and
`tests/unit/test_agents_txt_contract.py` pins 2 to 1, so a forgotten file announces itself.
(The freshness registry no longer re-hashes `agents.txt` on a version move — its digest
ignores the stamp since PR #355 — so there is no fifth thing to redo.)

## 2. The CLI pin

`LOCI_CLI_VERSION` in `lib/setup-steps.sh` names the `loci-tools` wheel every install resolves.
It moves only after the wheel resolves on PyPI (`loci-cli/RELEASING.md` step 6);
`tests/unit/test_cli_pin_published.py` proves it, and **skips silently unless
`LOCI_TEST_NETWORK=1`** — run it explicitly. The manual install in `agents.txt`
(`uv tool install -p 3.12 loci-tools==<pin>`) moves with it;
`tests/unit/test_agents_txt_contract.py` pins the two to each other.

## 3. The proof runs — three hosts, one commit

There is no hosted CI (AAD-7772 ran the unit suite as a GitHub Actions matrix once and withdrew
it over cost), so the commit about to be released is proven by hand on **Windows (Git Bash),
Linux (WSL) and macOS (the mac-mini, stock bash 3.2)** — the same commit on all three. Each
host needs `uv`, `arm-none-eabi-gcc`, `make`, `git`, a signed-in `loci`, a signed-in `claude`
and a signed-in `copilot`; an installed `loci` plugin must be disabled
(`copilot plugin disable loci@loci`), or it loads beside the checkout.

| Run | Command | Green means |
| --- | --- | --- |
| Unit suite | `bash run_tests.sh tests/unit` | `0 failed` on every host — the mac-mini included since AAD-7806 cleared its three standing reds — and the passed/skipped counts match the host's `main` baseline (each host has its own skips; a new red is a blocker) |
| Claude Code parity | `bash tools/claude-parity.sh` | `Claude Code parity: all 10 checks passed` — the session context, turn stamp, pre-edit scan, post-edit reminder, recipe deny and stop nudge still reach a Claude Code session, with no hook error |
| Copilot smoke | `bash tests/acceptance/copilot_smoke.sh` | `Copilot smoke: all 12 checks passed` — the same deliverables under GitHub Copilot CLI: every hook ran, context injected, one turn id across stamp / reminder / turn tree / adapter record, the recipe deny and nothing else denied, the Stop clean |
| Copilot negative control | `bash tests/acceptance/copilot_smoke.sh --negative-control` | `the gate catches a broken launcher` — a copy with the pre-Epic `hooks.json` launcher FAILS the smoke, naming the denial; proves the smoke is not vacuous |

Both agent gates exit 3 (`SKIP: …`) when a tool is missing or `loci` is not signed in, and the
smoke also when `copilot` is not; a `claude` that is not signed in surfaces as failed parity
checks, so sign in first. A skip is not a pass. A failed run keeps its work dir and names it;
attach the three hosts' smoke and parity logs to the release ticket (the first set is on
AAD-7793). The smoke reads the hooks' own log through `LOCI_HOOK_LOG=1` — the log switch
alone — never `LOCI_ENV=dev`, which also moves the CLI to the dev backend and the dev
session store and so turns a production sign-in into `auth_required`.

Skill prose that the model reads (`skills/*/SKILL.md`, the hooks' context lines) also takes a
run of the skill evals, `bash run_evals.sh --ble-root <BLE>` (Claude host) and
`--host copilot`, compared with the latest report under `eval-results/` — the evals are
model-driven and noisy, so a red is re-run once before it counts.

**The mac-mini.** Reached as `ssh mac-mini`, or through the hop when the route has flipped
(`ssh -J vladimir@10.200.200.1 mac-mini`); try both. `copilot`, `claude`, `loci`, `uv` and the
Arm toolchain live in `~/.local/bin` (a login shell: `zsh -lc`). Run from a clean clone, not a
working checkout with local edits. **Keep the Mac awake for the run**: it sleeps after one
minute idle (`pmset -g custom`: `sleep 1`), and a non-interactive `ssh mac-mini '<command>'`
is not tty activity, so a suite started that way advances only while something wakes the
machine — pytest's own clock (which stops in sleep) said 5 min 41 s for a run that took
1 h 54 min by the wall clock (AAD-7806). The "30–40 minutes" this page used to quote was that.
Start it as `caffeinate -i bash run_tests.sh tests/unit > ~/suite.txt 2>&1` (detached with
`nohup … </dev/null &` if the ssh session may drop) and poll its log; it takes about six
minutes awake. Keep the log outside the clone: a `.txt` at its root is read as model-facing
prose, and seven prose-contract tests fail on it. **Sign `loci` in with the file backend**
(`LOCI_NO_KEYRING=1 ~/.local/bin/loci login --device`, then export `LOCI_NO_KEYRING=1` for
the gates): a plain `loci login` stores the session in the login keychain, which an ssh
session cannot read (the keychain is locked without a GUI login), so `loci init` answers
`auth_required` there and both gates skip. `--device` signs in from the ssh session
itself; the code is approved in any browser.

## 4. Merge, re-map, promote

- Merge the PR to `main`; then re-map the docs in `C:\Playground\LOCI` (`projects/loci-claude.md`
  header and `projects/README.md` row).
- `main` is the development repository. Users install from the public
  `auroralabs-loci/loci-claude`, which `.github/workflows/sync.yml` publishes on pushes to
  **`production`** only: promote with a PR `main` → `production` after QA. Until then the
  marketplace serves the previous promoted version.
