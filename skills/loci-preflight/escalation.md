# Escalating an execution risk

Reference for `/loci:loci-preflight`. Step 4's second half: which signals an object cannot answer, when an escalation is justified, and how the child's row is drawn.

---

**Escalate execution risks.** Use the heuristics below to identify plans that
need whole-binary Stack or Memory context. They decide which child skill runs,
not what any figure means: they apply no budget of their own, and a trigger
firing is not itself a finding. **With no contract nothing proposes an escalation
at all** — there are no entries for the CLI to derive one from, so the decision is
yours on the same heuristics. The child costs latency and context, not money, so
argue for it from what it would tell you. Pass `--parent-run` either way, and let the child
keep its own verdict — it gets its own row here, its `ENTRY` cell under a `└ `, and
its figures are reused rather than measured again. **Name the child in the
`Execution fit:` cause only where it moved the word** —
`Execution fit: **ADJUST PLAN** — stack-depth: 202% of the 2 KB budget on comms_task`.
A child that changed nothing is not mentioned there; its row already carries its
figures, and naming every escalation makes the one that decided the answer
indistinguishable from the ones that did not.

*Escalate to `stack-depth`* when — increment R by 1 at trigger:
- Execution context is ISR, HWI, or interrupt callback, AND call chain
  depth > 3 levels visible in the manifest's candidate blocks, OR
- The plan introduces a structural hazard — recursion, a call through a function
  pointer, or a callee this project does not define — in the plan, the source, or a
  measured path's `reasons` (`unresolved call site`, `recursive call`), OR
- Plan adds a new RTOS task (xTaskCreate, Task_construct, osThreadNew) that
  needs stack sizing, OR
- Plan introduces large local variables on stack (buffers, arrays, C++ objects
  with non-trivial constructors), OR
- Plan adds a known-deep callee (printf, snprintf, crypto, TLS functions).

After stack-depth returns, reason over its results — increment R by 1:
- What worst-case stack depth does the plan require?
- Are there large frames that could move to static or heap allocation?
- Could the call chain be flattened to reduce depth?
→ adjust plan based on conclusion before proceeding.

*Escalate to `memory-report`* when — increment R by 1 at trigger:
- The plan introduces significant new static allocations (large buffers,
  global arrays, static structs) visible from reading the source, OR
- a baseline exists and the plan grows or restructures existing data sections.

After memory-report returns, reason over its results — increment R by 1:
- Does the new allocation fit within available ROM/RAM headroom?
- Which region is under most pressure after the change?
- Does the plan need to reduce static footprint before proceeding?
→ adjust plan based on conclusion before proceeding.

**Reason before you report**, and know when a second metered call is justified —
the pass and the re-query loop, which `SKILL.md` also links directly.
