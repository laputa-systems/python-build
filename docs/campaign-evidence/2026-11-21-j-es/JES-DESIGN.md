# J-ES: escape-synced frame locals (owner opened 2026-11-21)

Goal: a float/int local whose value is loop-carried stays unboxed across `_JUMP_TO_TOP`;
the frame's boxed slot is made current only where something can observe it.

## Observers and how each is covered
| Observer | Reaches frame via | Coverage |
|---|---|---|
| Side exit / deopt | tier-1 resumes on frame | existing `_BOX_UNBOXED_SLOTS` exit fixups (optimizer.c ~1375) extended to unboxed locals |
| Error / traceback | `frame` in traceback, `f_locals` | error fixups (optimizer.c ~1577-1610) materialize before `ERROR_*` |
| Eval breaker: signals, pending calls, GIL drop (other threads), async exc, GC (`_Py_ScheduleGC` sets a breaker bit, gc.c:1986), instrumentation/`settrace` | only at `_CHECK_PERIODIC` | loop-types already rewrites it to `_TIER2_RESUME_CHECK` (deopt = exit) in proven warm cycles; exit materializes |
| Arbitrary code in a uop (`HAS_ESCAPES_FLAG`: calls, generic decref/`__del__`, generic binary op) | any | stays a barrier: materialize before, or refuse the trace shape |
| `f_locals`/`locals()`/`sys._getframe` | requires running Python code | only reachable through the rows above |

Ownership: the frame slot keeps owning its last boxed value (valid for GC); the unboxed
copy is authoritative between materializations. Materialization = box new value, store,
close old exact float/int (non-escaping dealloc). Allocation failure while materializing
on an exit/error path is an obligation to settle in stage 3 (keep old object, raise
MemoryError on the error path).

## Stages (each its own preregistered hypothesis; no timing until a qualified host)
1. **J-ES-1 barrier removal:** extend `plan_loop_types` to the array subscript uops, so
   scimark_sparse_mat_mult's inner loop becomes a proven warm cycle (`_CHECK_PERIODIC` ->
   `_TIER2_RESUME_CHECK`, generic replaced-local store -> typed). Plan: PLAN-JES1.md.
2. **J-ES-2 audit:** after 1, recount store-to-next-store barriers per kernel (prior method,
   doc 1969-1992) to get the achievable boxed-local share on Linux structure.
3. **J-ES-3 representation:** unboxed-local shadow slots carried across the backedge in
   `unbox.rs`, with materialization at every exit/error/barrier above. Only if 2 shows a
   non-zero barrier-free share.
