# The database has to be made in the container

Reference for `/loci:init` when `init_needs_user` carries `.error.question`
`compdb_regen`: this project has no compile database, and the build that would have
written one did not run on this machine — it ran inside a container, or during an
image build. Init can make one where that build ran. It never does so unasked.

## What it would run

`.error.regen_steps[]` is the command list, in order, and it is what the user is
being asked about: a `docker cp` per directory lifted out of the image, then one
`cmake -DCMAKE_EXPORT_COMPILE_COMMANDS=ON <build dir>` per configured tree. Relay
every line. It re-runs the generate step of a tree that is **already** configured —
same cache, same flags, nothing compiled, no object touched — and it writes into
their build directory.

Consent is `compdb.md`'s rule and is not waived here, and a no has somewhere to go.
Where `.error.artifact_only_elf` names a binary, offer as the second answer `loci init
--project-root "<root>" --no-exec-regen-compdb`: nothing is started, and an
artifact-only recipe over that binary is recorded (`compdb.md`'s last section). Where
it is `null`, say what LOCI cannot do without a database and stop. Headless: print the
steps and both `loci init` lines, then stop.

## The one question

`.error.exec_candidates[]` is `container-toolchain.md`'s list plus one source that
list does not have: `dockerfile` — a file in this repository that copies the project
in and builds it, always `blocked`, because nothing in a repository says which image
it was built as.

- **`.error.message` names `--exec-image`** → that is the missing fact. Ask for it
  once (the question tool, header `Toolchain`, free-form; the candidate's `why` names
  the Dockerfile, which is the context to show), then ask for consent, then one call:
  `loci init --project-root "<root>" --exec-kind <kind> --exec-image <image> --exec-regen-compdb`.
- **It does not** → the container is already known. Ask for consent alone, then
  `loci init --project-root "<root>" --exec-regen-compdb`.

## Afterwards

`.data.notes` say what was lifted out and what was regenerated; relay them in Step 4
beside the `toolchain:` line. The recipe records `build.compdb.kind: generated` and,
as `regen`, the project's **own** command — for colcon
`colcon build --cmake-args -DCMAKE_EXPORT_COMPILE_COMMANDS=ON` — not init's
reconfigure: that is the line to run when the database goes stale.

A step that fails is `init_failed` and transient, with `.error.message` naming the
step and what the engine said; nothing partial is recorded.
