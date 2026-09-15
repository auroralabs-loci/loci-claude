# Getting a usable `loci` on PATH

Reference for `/loci:init` Step 0. Read this when `loci` or `uv` is missing, when
`loci` is present but does not know the `init` verb, or to compare the `loci` that is
installed against the version this plugin pins.

## `uv` missing

Give the single install command for *this* platform — work the platform out; do not
ask, and do not list commands for other OSes — then stop. The plugin never installs
host tools: they need root and an interactive password prompt, which no agent has.
The user runs it in their own terminal and re-invokes `/loci:init`.

## `loci` missing

**Invoke `/loci:setup` and let it finish before Step 1.** Installing is what that
skill owns: the `uv` prerequisite with the command for this platform, the installer
below, and a `loci doctor` pass proving the new binary runs — doing it here instead
reproduces a third of that and reports none of it. Re-probe `command -v loci` when it
returns: present → Step 1; still missing → **stop** and relay what it reported (a
`uv` the user must install, a proxy blocking PyPI). Do not invoke it twice, and do
not initialize without a CLI.

Run the installer directly only when `/loci:setup` is unavailable in this session:

```bash
bash "<plugin-dir>/hooks/ensure-loci-cli.sh"
```

`<plugin-dir>` comes from the `plugin dir:` line of the LOCI session context, or
`$CLAUDE_PLUGIN_ROOT`. With neither, stop and say the plugin has not loaded — do
not guess a path, or the `bash` call resolves against the user's project and fails
confusingly.

**Run it in the foreground and wait for it.** It is self-locking: a session-start
install may already be running, and this call waits for that one rather than
starting a second. Never background it, never run two, never install the wheel
yourself.

It is **silent and always exits 0**, and it can exit 0 without installing anything
— including when it gave up waiting on another install that is still running. So
its exit status tells you nothing: re-probe `command -v loci` when it returns. Still
missing → stop and report that, and point at `/loci:setup`, which reads the install
log and says what failed.

## `loci` present but too old to have `init`

The one Step 0 misses if you only check presence. A published CLI from before the
recipe shipped answers:

```
loci: error: argument <command>: invalid choice: 'init' (choose from login, …)
```

on **stderr**, exit 2, with **no envelope on stdout** — so there is no `.ok` to
branch on and no `.error.code` to match. Detect it by the shape: a `loci init …`
call that produces no JSON on stdout and says `invalid choice` is a stale CLI, not
a failed init.

This is a real window, not a hypothetical: session start spawns the CLI upgrade
**detached**, so on the first session after a plugin update the old binary can still
answer `command -v` while the new wheel is installing. It is a version state, so the
recovery is the version route: invoke **`/loci:setup`**, let it finish, then re-probe
— and this time check the **verb**, not mere presence, since the binary that
answered was already there:

```bash
loci init probe >/dev/null 2>&1 || echo "init verb absent"
```

Still absent after `/loci:setup` returns → stop and report it; the CLI did not
upgrade, and `loci doctor`'s output from that run says why.

## `loci` behind the plugin's CLI pin

Present, knows `init`, and still an **older build than this plugin version was
written against**: its envelopes can lack fields the steps below read and its
refusals can carry codes `recovery.md` has no row for, so the gate runs before
Step 1 rather than after a confusing failure.

The pin is `LOCI_CLI_VERSION` in `<plugin-dir>/lib/setup-steps.sh`, the one place the
plugin declares it (resolve `<plugin-dir>` exactly as above). Read it and the
installed build together:

```bash
have=$(loci --version 2>/dev/null |
       sed -n 's/^[^0-9]*\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | head -1)
pin=$(sed -n 's/^LOCI_CLI_VERSION="\([0-9.]*\)".*/\1/p' \
      "<plugin-dir>/lib/setup-steps.sh" | head -1)
printf 'have=%s pin=%s\n' "${have:-unknown}" "${pin:-unknown}"
```

Compare them **numerically, field by field** — `0.2.10` is ahead of `0.2.9`, and
string order says the opposite.

- `have` **older** than `pin` → invoke **`/loci:setup`**, let it finish, then re-read
  `have` once. Still behind → stop and report both numbers rather than initializing
  anyway; the usual cause is another `loci` winning the PATH lookup ahead of the one
  the installer writes, which is what that skill diagnoses.
- **equal to or newer** → carry on. The pin is a floor, not an equality: the CLI only
  moves forward, a `LOCI_DEV_CLI_PATH` checkout floats ahead by design, and after a
  CLI release every up-to-date machine reads ahead of an older plugin's pin.
- either number **unreadable** (`unknown`) → carry on, and say nothing about the
  version. No plugin dir, no pin line, or a `--version` that prints nothing parseable
  is an unknown, not a stale CLI — reinstalling on it fires on every invocation.

## Sign-in

`loci init` needs a signed-in session. `loci init probe` does not — it is a read-only
local diagnostic — so evidence collection works before sign-in and only the write needs
a session.

On `error.code == "auth_required"`, tell the user to type **`! loci login`**. Keep the
`!` prefix: it is a browser OAuth flow that has to run in their own terminal, and
**never run `loci login` yourself** — from here it blocks on a browser you cannot
reach. Then stop cleanly.

Say it as the one-time step it is, not as a failure: what you found so far, that
signing in is what lets LOCI record it, and that `/loci:init` picks up from there.
Promise no resume you do not perform — the next invocation re-derives the evidence,
which costs them nothing to re-run but is not a session left open.

## A note on timeouts

The installer above waits up to **300 s** for an in-flight install before giving up and
exiting 0. That is longer than the default command timeout, so give the call a timeout
of at least 360 s. A timed-out call is not a failed install — re-probe `command -v loci`
before concluding anything.
