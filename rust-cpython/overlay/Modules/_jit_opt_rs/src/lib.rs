mod exit_stubs;
mod unbox;
mod leaf;
mod cache;
mod loop_globals;
mod store;
mod loop_types;

use std::mem::MaybeUninit;
use std::slice;

pub use leaf::{LeafOpcodes, LeafStep, LeafUop, OpcodeRange, plan_leaf_calls};
pub use cache::{CacheUop, plan_cache};
pub use loop_globals::{LoopOpcodes, LoopStep, LoopUop, plan_loop_globals};
pub use store::{StoreOpcodes, StoreStep, StoreUop, plan_stores};
pub use loop_types::{LocalOpcodes, LocalStep, LocalUop, plan_loop_types};

/// Plans inductive root-local type guards for a closed numeric loop. Only a
/// positive result writes output; zero preserves the trace and -1 rejects
/// inconsistent descriptors. Allocation failure and short capacity preserve output.
///
/// # Safety
///
/// Inputs must point to initialized live descriptors and a generated opcode map.
/// `local_kinds` describes `local_count` fast locals; `initial_depth` is the
/// logical entry operand-stack depth. Output is separate aligned writable storage
/// for `capacity` steps. Runtime guards must use exact non-null type checks and
/// side exits; pending handlers must not return into the assumed warm cycle.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn _PyJitOpt_PlanLoopTypes(
    uops: *const LocalUop,
    length: usize,
    opcodes: *const LocalOpcodes,
    local_kinds: *const u16,
    local_count: usize,
    initial_depth: usize,
    output: *mut LocalStep,
    capacity: usize,
) -> isize {
    // SAFETY: the caller supplies initialized live input arrays and matching metadata.
    let (uops, opcodes, local_kinds) = unsafe {
        (slice::from_raw_parts(uops, length), &*opcodes, slice::from_raw_parts(local_kinds, local_count))
    };
    match plan_loop_types(uops, opcodes, local_kinds, initial_depth) {
        Ok(Some(plan)) if plan.len() <= capacity && plan.len() <= isize::MAX as usize => {
            // SAFETY: the caller supplies separate aligned writable output storage.
            let output = unsafe { slice::from_raw_parts_mut(output.cast::<MaybeUninit<LocalStep>>(), capacity) };
            for (slot, step) in output.iter_mut().zip(&plan) { slot.write(*step); }
            plan.len() as isize
        }
        Ok(_) => 0,
        Err(Inconsistent) => -1,
    }
}

/// Plans local-store stencil fusion. Positive results write a complete plan;
/// zero preserves the trace, and -1 rejects inconsistent descriptors. Allocation
/// failure and insufficient output capacity leave output unchanged.
///
/// # Safety
///
/// Inputs must point to `length` initialized descriptors and one generated opcode
/// map. Output must be separate, aligned writable storage for `capacity` steps.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn _PyJitOpt_PlanStores(
    uops: *const StoreUop,
    length: usize,
    opcodes: *const StoreOpcodes,
    output: *mut StoreStep,
    capacity: usize,
) -> isize {
    // SAFETY: the caller supplies live initialized inputs and a matching opcode map.
    let (uops, opcodes) = unsafe { (slice::from_raw_parts(uops, length), &*opcodes) };
    match plan_stores(uops, opcodes) {
        Ok(Some(plan)) if plan.len() <= capacity && plan.len() <= isize::MAX as usize => {
            // SAFETY: the caller supplies separate aligned writable output storage.
            let output = unsafe { slice::from_raw_parts_mut(output.cast::<MaybeUninit<StoreStep>>(), capacity) };
            for (slot, step) in output.iter_mut().zip(&plan) {
                slot.write(*step);
            }
            plan.len() as isize
        }
        Ok(_) => 0,
        Err(Inconsistent) => -1,
    }
}

/// Plans an entry prologue for a watched loop-global guard. A positive result
/// writes that many steps; 0 preserves the trace, and -1 rejects inconsistent
/// descriptors. Allocation failure and short capacity leave output unchanged.
///
/// # Safety
///
/// Inputs must point to `length` initialized descriptors and one matching opcode
/// map. `output` must be separate writable storage for `capacity` steps. The
/// interpreter must retain the dictionary watcher/dependency until invalidation.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn _PyJitOpt_PlanLoopGlobals(
    uops: *const LoopUop,
    length: usize,
    opcodes: *const LoopOpcodes,
    version: u32,
    resumable: u8,
    output: *mut LoopStep,
    capacity: usize,
) -> isize {
    // SAFETY: the caller supplies initialized live inputs and a complete opcode map.
    let (uops, opcodes) = unsafe { (slice::from_raw_parts(uops, length), &*opcodes) };
    match plan_loop_globals(uops, opcodes, version, resumable) {
        Ok(Some(plan)) if plan.len() <= capacity => {
            // SAFETY: the caller supplies separate writable storage with sufficient capacity.
            unsafe { std::ptr::copy_nonoverlapping(plan.as_ptr(), output, plan.len()) };
            plan.len() as isize
        }
        Ok(_) => 0,
        Err(Inconsistent) => -1,
    }
}

/// Plans whole-trace input cache depths. A positive result writes that many
/// depths, 0 preserves the current allocator, and -1 rejects inconsistent input.
/// Allocation failure and insufficient output capacity leave output unchanged.
///
/// # Safety
///
/// `uops` must point to `length` initialized descriptors from the matching generated
/// cache table. `output` must be aligned and writable for `capacity` bytes and must
/// not overlap input. Initial cache depth is zero; skipped uops have no effects.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn _PyJitOpt_PlanCache(
    uops: *const CacheUop,
    length: usize,
    output: *mut u8,
    capacity: usize,
) -> isize {
    // SAFETY: the caller supplies initialized live input descriptors.
    let uops = unsafe { slice::from_raw_parts(uops, length) };
    match plan_cache(uops) {
        Ok(Some(plan)) if plan.len() <= capacity && plan.len() <= isize::MAX as usize => {
            // SAFETY: the caller supplies disjoint writable storage; u8 permits every bit pattern.
            unsafe { std::ptr::copy_nonoverlapping(plan.as_ptr(), output, plan.len()) };
            plan.len() as isize
        }
        Ok(_) => 0,
        Err(Inconsistent) => -1,
    }
}

/// Plans frame-free exact-argument leaf calls, including preguarded slot getters.
/// A positive result writes that
/// many steps; 0 leaves the trace and output unchanged, and -1 rejects inconsistent
/// descriptors. Allocation failure also leaves the trace unchanged.
///
/// # Safety
///
/// `uops` and `opcodes` must point to `length` initialized descriptors and one
/// matching generated opcode map. `steps` must be aligned and writable for
/// `capacity` steps, and must not overlap either input. Output may be uninitialized.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn _PyJitOpt_PlanLeafCalls(
    uops: *const LeafUop,
    length: usize,
    opcodes: *const LeafOpcodes,
    steps: *mut LeafStep,
    capacity: usize,
) -> isize {
    // SAFETY: the caller supplies initialized, live input arrays and a matching map.
    let (uops, opcodes) = unsafe { (slice::from_raw_parts(uops, length), &*opcodes) };
    match plan_leaf_calls(uops, opcodes) {
        Ok(Some(plan)) if plan.len() <= capacity && plan.len() <= isize::MAX as usize => {
            // SAFETY: the caller supplies a separate writable output array.
            let output = unsafe { slice::from_raw_parts_mut(steps.cast::<MaybeUninit<LeafStep>>(), capacity) };
            for (slot, step) in output.iter_mut().zip(&plan) {
                slot.write(*step);
            }
            plan.len() as isize
        }
        Ok(_) => 0,
        Err(Inconsistent) => -1,
    }
}

pub use unbox::{
    ACTION_COPY, ARITH_ADD, ARITH_MULTIPLY, ARITH_SUBTRACT, ARITH_TRUE_DIVIDE, EFFECT_BARRIER,
    EFFECT_ERROR, EFFECT_ERROR_NO_POP, EFFECT_EXIT, Inconsistent, JitOptStep, JitOptUop,
    KIND_FLOAT_ARITH, KIND_FLOAT_COMPARE, KIND_INT_ARITH, KIND_INT_COMPARE, KIND_NOP, KIND_OTHER,
    KIND_POP, MAX_UNBOXED_DEPTH, Plan, REUSE_LEFT, REUSE_NONE, REUSE_RIGHT, SYNTHETIC_ACTIONS,
    plan_unboxing,
};

/// Plans float and compact-int unboxing for one optimized tier-2 trace.
///
/// Returns the number of steps written to `steps` when the trace is rewritten,
/// 0 when it stays as it is, and -1 when the descriptors or the planned
/// rewrite contradict the unboxing contract. Only a positive return writes to
/// `steps`, and then only its first that-many entries.
///
/// # Safety
///
/// `uops` must point to `length` initialized descriptors and `synthetic` to
/// `SYNTHETIC_ACTIONS` initialized descriptors. `steps` must be non-null,
/// aligned, and valid for writes of `capacity` steps; its contents may be
/// uninitialized. `steps` must not overlap either descriptor array.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn _PyJitOpt_PlanUnboxing(
    uops: *const JitOptUop,
    length: usize,
    synthetic: *const JitOptUop,
    steps: *mut JitOptStep,
    capacity: usize,
) -> isize {
    // SAFETY: the caller provides `length` initialized descriptors at `uops`.
    let uops = unsafe { slice::from_raw_parts(uops, length) };
    // SAFETY: the caller provides `SYNTHETIC_ACTIONS` initialized descriptors at `synthetic`.
    let synthetic = unsafe { slice::from_raw_parts(synthetic, SYNTHETIC_ACTIONS) };
    // SAFETY: the caller provides `capacity` writable, non-overlapping steps; viewing them as
    // `MaybeUninit` makes no claim that they are initialized.
    let steps = unsafe { slice::from_raw_parts_mut(steps.cast::<MaybeUninit<JitOptStep>>(), capacity) };
    match plan_unboxing(uops, synthetic) {
        Ok(Plan::Unchanged) => 0,
        Ok(Plan::Rewrite(plan)) if plan.len() <= steps.len() => {
            for (slot, step) in steps.iter_mut().zip(&plan) {
                slot.write(*step);
            }
            plan.len() as isize
        }
        Ok(Plan::Rewrite(_)) => 0,
        Err(Inconsistent) => -1,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn uop(kind: u8, detail: u8, shape: (i8, i8, i8), read: u8, effects: u8) -> JitOptUop {
        JitOptUop { kind, detail, inputs: shape.0, popped: shape.1, pushed: shape.2, read, effects, exit_at_end: 0 }
    }

    fn synthetic() -> [JitOptUop; SYNTHETIC_ACTIONS] {
        unbox::synthetic_descriptors()
    }

    fn sum_of_products() -> Vec<JitOptUop> {
        let load = uop(KIND_OTHER, 0, (0, 0, 1), 0, 0);
        let pop = uop(KIND_POP, 0, (1, 1, 0), 1, 0);
        let multiply = uop(KIND_FLOAT_ARITH, ARITH_MULTIPLY, (2, 2, 3), 0b11, EFFECT_ERROR | EFFECT_ERROR_NO_POP);
        let add = uop(KIND_FLOAT_ARITH, ARITH_ADD | REUSE_LEFT, (2, 2, 3), 0b11, EFFECT_ERROR | EFFECT_ERROR_NO_POP);
        vec![load, load, multiply, pop, pop, load, load, multiply, pop, pop, add, pop, pop, uop(KIND_OTHER, 0, (1, 1, 1), 1, 0)]
    }

    const UNTOUCHED: JitOptStep =
        JitOptStep { source: 0xbeef, action: 0xee, exit_floats: 0xdead, exit_ints: 0xf00d, error_unboxed: 0xface };

    fn plan(trace: &[JitOptUop], synthetic: &[JitOptUop; SYNTHETIC_ACTIONS], steps: &mut [JitOptStep]) -> isize {
        // SAFETY: every pointer comes from a live slice of the stated length, and `steps` is
        // a distinct mutable borrow.
        unsafe {
            _PyJitOpt_PlanUnboxing(trace.as_ptr(), trace.len(), synthetic.as_ptr(), steps.as_mut_ptr(), steps.len())
        }
    }

    #[test]
    fn a_rejected_plan_writes_no_steps() {
        let mut contradictory = sum_of_products();
        contradictory[2].pushed = 1;
        let mut steps = [UNTOUCHED; 32];
        assert_eq!(plan(&contradictory, &synthetic(), &mut steps), -1);
        assert!(steps.iter().all(|step| *step == UNTOUCHED));

        let mut wrong = synthetic();
        wrong[0].effects = EFFECT_BARRIER;
        assert_eq!(plan(&sum_of_products(), &wrong, &mut steps), -1);
        assert!(steps.iter().all(|step| *step == UNTOUCHED));
    }

    #[test]
    fn a_rewrite_writes_exactly_its_steps() {
        let mut steps = [UNTOUCHED; 32];
        let written = plan(&sum_of_products(), &synthetic(), &mut steps);
        let Ok(Plan::Rewrite(expected)) = plan_unboxing(&sum_of_products(), &synthetic()) else {
            panic!("the sum of two products is rewritten");
        };
        assert_eq!(written, expected.len() as isize);
        assert_eq!(&steps[..expected.len()], expected.as_slice());
        assert!(steps[expected.len()..].iter().all(|step| *step == UNTOUCHED));
    }

    #[test]
    fn a_rewrite_longer_than_the_buffer_leaves_the_trace_unchanged() {
        let mut steps = [UNTOUCHED; 4];
        assert_eq!(plan(&sum_of_products(), &synthetic(), &mut steps), 0);
        assert!(steps.iter().all(|step| *step == UNTOUCHED));
    }
}

/// Plans virtual resume exit reuse. Output is written only for a complete plan.
/// Resume descriptors with overlapping float and integer masks are invalid.
///
/// # Safety
/// Input is non-null and aligned for `length` initialized live descriptors.
/// Output is non-null, aligned writable storage for `capacity` u32 values,
/// possibly uninitialized, and does not overlap input. Both remain live for
/// the call. Zero preserves output on allocation failure or short capacity;
/// -1 preserves output on invalid descriptors.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn _PyJitOpt_PlanExitStubs(
    input: *const exit_stubs::ExitStubUop, length: usize,
    output: *mut u32, capacity: usize,
) -> isize {
    // SAFETY: the caller supplies a non-null aligned live descriptor array.
    let input = unsafe { slice::from_raw_parts(input, length) };
    match exit_stubs::plan(input) {
        Ok(Some(plan)) if plan.len() <= capacity && plan.len() <= isize::MAX as usize => {
            // SAFETY: separate live output storage may be uninitialized until written.
            let output = unsafe { slice::from_raw_parts_mut(output.cast::<MaybeUninit<u32>>(), capacity) };
            for (slot, value) in output.iter_mut().zip(plan) { slot.write(value); }
            length as isize
        }
        Ok(_) => 0,
        Err(Inconsistent) => -1,
    }
}

#[cfg(test)]
#[test]
fn exit_stubs_ffi_uninitialized_and_atomic() {
    use exit_stubs::ExitStubUop;
    let input = [ExitStubUop { kind: 1, exit_variant: 0, target: 0, exit_floats: 0, exit_ints: 0 }, ExitStubUop { kind: 3, exit_variant: 1, target: 4, exit_floats: 1, exit_ints: 2 }, ExitStubUop { kind: 3, exit_variant: 1, target: 4, exit_floats: 1, exit_ints: 2 }, ExitStubUop { kind: 2, exit_variant: 0, target: 0, exit_floats: 0, exit_ints: 0 }];
    let mut output = [MaybeUninit::<u32>::uninit(); 4];
    // SAFETY: initialized input and separate aligned writable uninitialized output.
    assert_eq!(unsafe { _PyJitOpt_PlanExitStubs(input.as_ptr(), 4, output.as_mut_ptr().cast(), 4) }, 4);
    // SAFETY: the positive count confirms every returned output entry was initialized.
    assert_eq!(output.map(|x| unsafe { x.assume_init() }), [0, 1, 1, 3]);
    let mut sentinel = [77u32; 4];
    // SAFETY: valid separate arrays with deliberately insufficient declared output capacity.
    assert_eq!(unsafe { _PyJitOpt_PlanExitStubs(input.as_ptr(), 4, sentinel.as_mut_ptr(), 3) }, 0);
    assert_eq!(sentinel, [77; 4]);
    let bad = [ExitStubUop { kind: 99, exit_variant: 0, target: 0, exit_floats: 0, exit_ints: 0 }];
    // SAFETY: the descriptor is initialized; its raw kind is deliberately invalid.
    assert_eq!(unsafe { _PyJitOpt_PlanExitStubs(bad.as_ptr(), 1, sentinel.as_mut_ptr(), 4) }, -1);
    assert_eq!(sentinel, [77; 4]);
    let bad = [ExitStubUop { kind: 3, exit_variant: 0, target: 0, exit_floats: 3, exit_ints: 2 }];
    // SAFETY: initialized descriptor and separate writable output.
    assert_eq!(unsafe { _PyJitOpt_PlanExitStubs(bad.as_ptr(), 1, sentinel.as_mut_ptr(), 4) }, -1);
    assert_eq!(sentinel, [77; 4]);
}
