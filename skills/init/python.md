# Python (Cython) projects

Reference for `/loci:init`, read when Step 2 recognises a Cython project. LOCI measures
the modules the project's own build cythonizes, with that build's directives and C
flags, and never cythonizes on its own: a plain Python project is refused.

Init reads the modules from the project's declaration, by backend: `setup.py`
(`ext_modules`, `cythonize(...)`), poetry's `build.py` and pdm's `pdm_build.py` by
running them once with the build stubbed; `[tool.setuptools.ext-modules]` and
hatch-cython's table in `pyproject.toml` as TOML, running nothing; scikit-build-core's
`CMakeLists.txt` and meson-python's `meson.build` through a cross configure into
`.loci/build/`, which compiles nothing. It writes nothing into the project tree.

## Running it

`loci init --python --project-root "<root>"`, with `--target=<isa>` only when the user
names a core. Without it init takes `aarch64`, or `neoverse-v2` when the project's
build flags name that core, and a note says which. Carry every answer below through
later calls, as Step 2 says.

`init_needs_user`, by `.error.question`:

- `python_run`: init has to run the declaration or the configure, then compile every
  module once for its Before. Relay `run_summary`, `before_summary` and `run_steps` in
  one line and ask; yes → re-run with `--allow-run`, no → stop, nothing was written.
  `--confirmed` is not this answer.
- `python_include`, `cython`, `python_interpreter`, `compiler`: one option per
  `.error.candidates[]` (`include` with `version` and `proof`; `cython` with `version`;
  `python` or `compiler`, likeliest first), then re-run with
  `--python-include=<include>`, `--cython=<cython>` or `--python-interpreter=<python>`.
  `compiler` has no flag: prefix the same command with `CC=<compiler>`.
- `python_version`: ask which CPython the device runs, offering `.error.candidates[]`
  (`version`; any `3.X` is an answer), then re-run with `--python-version=<3.X>`.

## What init shows

Step 4 confirms the module list, not a target: each of `.data.python.records[]` with its
`name`, `source` and `cflags`, and the `cython` and `include` init took (a no-change
`loci init` carries none of these; `--refresh` does).
Yes → `loci init --project-root "<root>" --refresh --confirmed`, and only then: a
target or `python_run` answer is not this confirmation. No → ask which modules are
missing or wrong. The fix is the declaration, then `--refresh`; `--python-modules` is
only for a backend init cannot read.

Tell the user, beside the report:

- each module left out, with its first error (the report's `!` lines,
  `.data.validation.failures`): it has no `.so`, so nothing in it is measured;
- calls into the CPython API are not measured, because the link leaves them
  unresolved; at `-O3` a function's body may be compiled out of line and left out of
  its number;
- init compiled every module once for its Before (a note says how long); an edit
  compiles its module again, which on a large module takes minutes.

`.data.python.full_build` is the project's own build. `offered`: ask only when the user
wants the ELF verbs (stack, memory, control flow) on the `.so` files the project builds;
relay `run_summary` and `run_steps`, and on a yes re-run with `--refresh --full-build`. A
no changes nothing. `failed`: relay `first_error` and `log`; the recipe is kept.
`unsupported`: relay `reason`; LOCI's module links still measure every module.

## Refusals

| `.error.code` | what you do |
|---|---|
| `python_not_cythonized` | relay the remedy, then stop: adopting Cython is the user's call |
| `python_backend_unread` | ask the user for the module list, then run the remedy's line with it |
| `python_modules_declared` | drop `--python-modules`: init reads this declaration (the no answer above) |
| `python_module_missing`, `python_module_not_source`, `python_module_outside_root` | `.error.module` is not a `.py`/`.pyx` under the root: ask for the right path |
| `python_target_unsupported` | relay it: a Linux CPython runs on `aarch64` and `neoverse-v2` only |
| `python_headers_missing`, `python_headers_foreign`, `cython_missing` | hand the user the remedy line whole (`--no-bin` included), for them to run from the project root, then stop; once they have run it, re-run the same command. `--python-include`/`--cython` only for headers or a Cython elsewhere |
| `python_declaration_failed` | relay its message (the first error) and remedy; re-run once the user has fixed it |
| `python_recipe_conflict` | a `.error.system` recipe governs the tree: ask before replacing it; yes → the same command with `--refresh` |
| `python_cflags_invalid`, `python_module_unknown`, `python_key_retired` | fix the call: flags `a,b` or JSON; a module from `.error.modules` |
| `python_link_unsupported`, `python_libpython_missing` | `loci init set python.link=unresolved` |
| `python_nothing_to_link`, `python_module_failed`, `cython_timeout` | relay each module's first error; a module that fails is left out |
| `python_full_build_failed`, `python_full_build_unsupported` | as `full_build` above |

`compiler_missing` on a first init: relay the message and the install line, then stop;
a `--refresh` refuses the same way. `recipe_stale`: [recovery.md](recovery.md)'s
`--refresh` row.
