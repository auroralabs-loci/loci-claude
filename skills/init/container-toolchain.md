# The toolchain is in a container

Reference for `/loci:init` Step 3 when `init_needs_user` carries `.error.question`
`toolchain_unreachable`: the compile database describes a build this machine cannot
reproduce — its paths, or its compiler, exist only inside a container that
bind-mounts this project. Init has already inferred the host ↔ container map from
the database's own paths (`.error.inferred_mount`) and read the running containers,
`.devcontainer/devcontainer.json` and the root's compose file; what it could not
observe is what `.error.exec_candidates[]` is for.

## Candidates first, then at most one question

Each candidate carries `source` (`container`, `devcontainer`, `compose`,
`dockerfile`, `compdb`),
`why`, the `exec` section it can fill in, and `complete` — recordable as it stands.
A `blocked` one was found and cannot be used (a read-only mount, a container that
mounts the project somewhere else, a volume left to the environment): relay its
sentence, it is the real obstacle. Show them with `.error.exec_symptoms`, then:

- **Exactly one `complete`** → no question. Re-run init naming it:
  `--exec-kind <kind> --exec-container <name>` for a running container,
  `--exec-image <image>` for an image, `--exec-compose-service <name>` (plus the
  `--exec-compose-file`/`-project` its `exec` carries) for a Compose service — for
  one nothing runs for, offer the `docker compose … up -d` line `.error.message`
  spells first: a running service is recorded without asking. `<kind>` is the
  candidate's `exec.kind`.
- **Several `complete`** → one question-tool call, header `Toolchain`, one option per
  candidate with its `why`.
- **None** → the one missing fact is the image. Ask once, header `Toolchain`,
  free-form (a `devcontainer` candidate's `exec.image` leads) — then `loci init --project-root "<root>" --exec-kind <kind> --exec-image <image>`,
  `<kind>` being the engine `.error.message` spells. The map is already inferred; add
  `--exec-mount <host>:<container>` only when `.error.inferred_mount` is null.

A **fact-finding** question, not the recipe's decision: the target ISA is still the
one decision, asked or confirmed afterwards as on any project. Never invent an
image, and never pass `--confirmed` here. **Carry the `--exec-*` flags through every
later init call in this run** — init re-derives each time, like Step 2's flags. Headless: print the symptoms, the candidates and the
exact `loci init --exec-kind <kind> --exec-image <image>` line, then stop.

## The confirmation shows what will run in the user's name

`.data.report` carries a `toolchain:` line — engine, mode, image, container or
Compose service (with file and project), platform — and one `mount:` line per
bind mount. Relay every one in Step 4, on a recipe that asked and on one that did
not. **Recording a `build.exec` recipe is itself a consent.** From then on every
LOCI compile starts a container on this machine, as a user the recipe names, with
the project bind-mounted read-write — and by default LOCI keeps one warm between
compiles. What the user approves is exactly that: **that LOCI starts containers on
their machine and writes into those directories.** Say it in those terms; it is not
covered by the build-tree permission, which is about the tree, nor by the
validation tier, which is about evidence. Show them the lines; they are the consent.

## Codes only a container recipe raises

`exec_unavailable` and `recipe_foreign_host` are two of the eleven, and their
recoveries are written once in
[the coded errors](../_shared/house-rules.md#coded-errors) — read them
there. `loci doctor` reports the same `engine-*` check the first of them names.

| code | what it is | what you do |
|---|---|---|
| `init_unsupported` naming a triplet (`x86_64-linux-gnu`) | the container's compiler targets an ISA LOCI does not predict | Step 3's row |

A project with no compile database **at all** is a different question —
`compdb_regen`, answered in [`compdb-container.md`](compdb-container.md).

Knobs later: `loci init set build.exec.<key>=<value>` — [`recovery.md`](recovery.md)
lists the keys and carries the consent rule for `set`. They survive a `--refresh`.
Do not restate the list here; the copy that lived in this file had lost a key.
