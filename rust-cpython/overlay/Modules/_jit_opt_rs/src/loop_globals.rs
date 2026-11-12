use crate::Inconsistent;

/// Opcode identities are supplied by the interpreter's generated table.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct LoopOpcodes {
    pub start: u16,
    pub warm: u16,
    pub globals: u16,
    pub entry_globals: u16,
    pub validity: u16,
    pub jump: u16,
}

/// Boolean fields are 0 or 1. `version` is meaningful for a globals guard only.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct LoopUop {
    pub opcode: u16,
    pub effectful: u8,
    pub changes_frame: u8,
    pub version: u64,
}

/// Emit `opcode` with `source`'s metadata, using `target`'s exit state for a moved guard.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct LoopStep {
    pub opcode: u16,
    pub source: u32,
    pub target: u32,
}

/// Peels a globals guard into an entry prologue. The loop must keep one frame,
/// and a validity check must separate the original guard from prior effects.
/// The caller must retain a live globals watcher and add that dictionary to
/// the executor's dependencies. Warm marking remains at the backedge target.
/// `entry_globals` must use a side exit so failure at ENTER_EXECUTOR can detach
/// the self-target executor and make progress.
pub fn plan_loop_globals(uops: &[LoopUop], ops: &LoopOpcodes, version: u32,
                         resumable: u8) -> Result<Option<Vec<LoopStep>>, Inconsistent> {
    if uops.len() > u32::MAX as usize || resumable > 1
        || uops.iter().any(|uop| uop.effectful > 1 || uop.changes_frame > 1)
    {
        return Err(Inconsistent);
    }
    if resumable != 0 || version == 0 || uops.len() < 4
        || uops[0].opcode != ops.start || uops[1].opcode != ops.warm
        || uops.last().unwrap().opcode != ops.jump
        || uops.iter().any(|uop| uop.changes_frame != 0)
    {
        return Ok(None);
    }
    let mut valid = false;
    let mut guard = None;
    for (index, uop) in uops.iter().enumerate().skip(2) {
        if uop.opcode == ops.globals {
            if valid && uop.version == u64::from(version) {
                guard = Some(index);
            }
            break;
        }
        if uop.effectful != 0 {
            valid = false;
        }
        if uop.opcode == ops.validity {
            valid = true;
        }
    }
    let Some(guard) = guard else { return Ok(None) };
    let mut plan = Vec::new();
    if plan.try_reserve_exact(uops.len()).is_err() {
        return Ok(None);
    }
    plan.push(LoopStep { opcode: ops.start, source: 0, target: 0 });
    plan.push(LoopStep { opcode: ops.entry_globals, source: guard as u32, target: 0 });
    for index in 1..uops.len() {
        if index != guard {
            plan.push(LoopStep { opcode: uops[index].opcode, source: index as u32, target: index as u32 });
        }
    }
    Ok(Some(plan))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ops() -> LoopOpcodes {
        LoopOpcodes { start: 1, warm: 2, globals: 3, entry_globals: 8, validity: 4, jump: 5 }
    }

    fn trace() -> Vec<LoopUop> {
        [1, 2, 6, 4, 3, 7, 5].into_iter().map(|opcode| LoopUop {
            opcode, effectful: u8::from(opcode == 6), changes_frame: 0, version: 42,
        }).collect()
    }

    #[test]
    fn prologue_uses_entry_state_and_keeps_the_steady_body_order() {
        let steps = plan_loop_globals(&trace(), &ops(), 42, 0).unwrap().unwrap();
        assert_eq!(steps, vec![LoopStep { opcode: 1, source: 0, target: 0 },
            LoopStep { opcode: 8, source: 4, target: 0 }, LoopStep { opcode: 2, source: 1, target: 1 },
            LoopStep { opcode: 6, source: 2, target: 2 }, LoopStep { opcode: 4, source: 3, target: 3 },
            LoopStep { opcode: 7, source: 5, target: 5 }, LoopStep { opcode: 5, source: 6, target: 6 }]);
    }

    #[test]
    fn mutation_barriers_frame_changes_and_resumable_roots_keep_the_guard() {
        let mut dirty = trace();
        dirty.insert(4, LoopUop { opcode: 6, effectful: 1, changes_frame: 0, version: 0 });
        assert!(plan_loop_globals(&dirty, &ops(), 42, 0).unwrap().is_none());
        dirty.insert(5, LoopUop { opcode: ops().validity, effectful: 0,
                                 changes_frame: 0, version: 0 });
        assert!(plan_loop_globals(&dirty, &ops(), 42, 0).unwrap().is_some());
        let mut frames = trace();
        frames[5].changes_frame = 1;
        assert!(plan_loop_globals(&frames, &ops(), 42, 0).unwrap().is_none());
        assert!(plan_loop_globals(&trace(), &ops(), 42, 1).unwrap().is_none());
        assert!(plan_loop_globals(&trace(), &ops(), 43, 0).unwrap().is_none());
        let mut open = trace();
        open.last_mut().unwrap().opcode = 7;
        assert!(plan_loop_globals(&open, &ops(), 42, 0).unwrap().is_none());
    }

    #[test]
    fn ffi_errors_and_short_output_do_not_write_partial_plans() {
        let mut uops = trace();
        let untouched = LoopStep { opcode: u16::MAX, source: u32::MAX, target: u32::MAX };
        let mut output = vec![untouched; uops.len()];
        // SAFETY: separate initialized inputs and writable output; capacity is deliberately short.
        let status = unsafe { crate::_PyJitOpt_PlanLoopGlobals(uops.as_ptr(), uops.len(),
            &ops(), 42, 0, output.as_mut_ptr(), 1) };
        assert_eq!(status, 0);
        assert!(output.iter().all(|step| *step == untouched));
        uops[2].changes_frame = 2;
        // SAFETY: separate live arrays and a matching complete opcode map.
        let status = unsafe { crate::_PyJitOpt_PlanLoopGlobals(uops.as_ptr(), uops.len(),
            &ops(), 42, 0, output.as_mut_ptr(), output.len()) };
        assert_eq!(status, -1);
        assert!(output.iter().all(|step| *step == untouched));
        assert_eq!(std::mem::size_of::<LoopOpcodes>(), 12);
        assert_eq!(std::mem::size_of::<LoopUop>(), 16);
        assert_eq!(std::mem::size_of::<LoopStep>(), 12);
    }
}
