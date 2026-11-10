mod unbox;

use std::slice;

pub use unbox::{
    ACTION_BOX, ACTION_COMPARE, ACTION_COPY, ACTION_UNBOX_NOS, ACTION_UNBOX_TOS, ARITH_ADD,
    ARITH_MULTIPLY, ARITH_SUBTRACT, ARITH_TRUE_DIVIDE, EFFECT_BARRIER, EFFECT_ERROR,
    EFFECT_ERROR_NO_POP, EFFECT_EXIT, Inconsistent, JitOptStep, JitOptUop, KIND_FLOAT_ARITH,
    KIND_FLOAT_COMPARE, KIND_NOP, KIND_OTHER, KIND_POP, MAX_UNBOXED_DEPTH, Plan, REUSE_LEFT,
    REUSE_NONE, REUSE_RIGHT, SYNTHETIC_ACTIONS, arith_action, plan_float_unboxing,
};

/// Plans float unboxing for one optimized tier-2 trace.
///
/// Returns the number of steps written to `steps` when the trace is rewritten,
/// 0 when it stays as it is, and -1 when the descriptors contradict the
/// unboxing contract.
///
/// # Safety
///
/// `uops` must point to `length` readable descriptors, `synthetic` to
/// `SYNTHETIC_ACTIONS` readable descriptors, and `steps` to `capacity`
/// writable steps. The regions must not overlap.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn _PyJitOpt_PlanFloatUnboxing(
    uops: *const JitOptUop,
    length: usize,
    synthetic: *const JitOptUop,
    steps: *mut JitOptStep,
    capacity: usize,
) -> isize {
    let uops = unsafe { slice::from_raw_parts(uops, length) };
    let synthetic = unsafe { slice::from_raw_parts(synthetic, SYNTHETIC_ACTIONS) };
    let steps = unsafe { slice::from_raw_parts_mut(steps, capacity) };
    match plan_float_unboxing(uops, synthetic) {
        Ok(Plan::Unchanged) => 0,
        Ok(Plan::Rewrite(plan)) if plan.len() <= steps.len() => {
            steps[..plan.len()].copy_from_slice(&plan);
            plan.len() as isize
        }
        Ok(Plan::Rewrite(_)) => 0,
        Err(Inconsistent) => -1,
    }
}
