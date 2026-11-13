use crate::Inconsistent;

/// Identities come from the interpreter's generated uop table.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct StoreOpcodes {
    pub swap_first: u16,
    pub swap_last: u16,
    pub pop: u16,
    pub store: u16,
    pub nop: u16,
    pub argument_swap: u16,
    pub float_pop: u16,
    pub int_pop: u16,
    pub argument_float_store: u16,
    pub argument_int_store: u16,
    pub argument_numeric_store: u16,
    pub local_float_store: u16,
    pub local_int_store: u16,
    pub local_float_guard: u16,
    pub push_frame: u16,
    pub return_value: u16,
    pub virtual_begin: u16,
    pub virtual_return: u16,
}

#[repr(C)]
#[derive(Clone, Copy)]
/// `observed_kind` is zero or `local_float_guard` on a local swap. The
/// observation selects a runtime guard; it never proves the cleanup type.
pub struct StoreUop {
    pub opcode: u16,
    pub oparg: u16,
    pub observed_kind: u16,
}

/// Target metadata is copied from `source`, including the original decref site.
/// Sources never precede their output index, so C may emit in place.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct StoreStep {
    pub opcode: u16,
    pub oparg: u16,
    pub source: u32,
}

/// Fuse local swaps with generic or typed numeric cleanup and virtual argument
/// swaps with typed or entry-guarded numeric cleanup. Only NOPs may separate the
/// swap from its cleanup. Generic virtual cleanup requires a numeric entry guard.
/// Observed float locals use a guard before the store, only in the initial frame.
pub fn plan_stores(uops: &[StoreUop], ops: &StoreOpcodes)
    -> Result<Option<Vec<StoreStep>>, Inconsistent>
{
    if ops.swap_first > ops.swap_last || uops.len() > u32::MAX as usize ||
        ops.local_float_guard == 0 ||
        uops.iter().any(|u| u.observed_kind != 0 &&
            (u.observed_kind != ops.local_float_guard || !(ops.swap_first..=ops.swap_last).contains(&u.opcode))) {
        return Err(Inconsistent);
    }
    let mut steps = Vec::new();
    if steps.try_reserve_exact(uops.len()).is_err() {
        return Ok(None);
    }
    for (index, uop) in uops.iter().enumerate() {
        steps.push(StoreStep { opcode: uop.opcode, oparg: uop.oparg, source: index as u32 });
    }
    let mut changed = false;
    let mut root_interval = true;
    for (index, uop) in uops.iter().enumerate() {
        if [ops.push_frame, ops.return_value, ops.virtual_begin, ops.virtual_return].contains(&uop.opcode) {
            root_interval = false;
        }
        let local = (ops.swap_first..=ops.swap_last).contains(&uop.opcode);
        if !local && uop.opcode != ops.argument_swap {
            continue;
        }
        let mut pop = index + 1;
        while pop < uops.len() && uops[pop].opcode == ops.nop {
            pop += 1;
        }
        if pop == uops.len() {
            continue;
        }
        if local && root_interval && uop.observed_kind == ops.local_float_guard && uops[pop].opcode == ops.pop {
            steps[index] = StoreStep { opcode: ops.local_float_guard, oparg: uop.oparg, source: pop as u32 };
            steps[pop] = StoreStep { opcode: ops.local_float_store, oparg: uop.oparg, source: pop as u32 };
            changed = true;
            continue;
        }
        let opcode = match (local, uops[pop].opcode) {
            (true, cleanup) if cleanup == ops.pop => ops.store,
            (true, cleanup) if cleanup == ops.float_pop => ops.local_float_store,
            (true, cleanup) if cleanup == ops.int_pop => ops.local_int_store,
            (false, cleanup) if cleanup == ops.float_pop => ops.argument_float_store,
            (false, cleanup) if cleanup == ops.int_pop => ops.argument_int_store,
            (false, cleanup) if cleanup == ops.pop => ops.argument_numeric_store,
            _ => continue,
        };
        steps[index] = StoreStep { opcode, oparg: uop.oparg, source: pop as u32 };
        steps[pop].opcode = ops.nop;
        changed = true;
    }
    Ok(changed.then_some(steps))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn opcodes() -> StoreOpcodes {
        StoreOpcodes { swap_first: 1, swap_last: 9, pop: 10, store: 11, nop: 12,
            argument_swap: 13, float_pop: 14, int_pop: 15,
            argument_float_store: 16, argument_int_store: 17, argument_numeric_store: 18,
            local_float_store: 19, local_int_store: 20, local_float_guard: 21,
            push_frame: 22, return_value: 23, virtual_begin: 24, virtual_return: 25 }
    }

    fn uop(opcode: u16, oparg: u16) -> StoreUop {
        StoreUop { opcode, oparg, observed_kind: 0 }
    }

    #[test]
    fn replicas_keep_local_indices_and_decref_metadata() {
        for opcode in 1..=9 {
            let steps = plan_stores(&[uop(opcode, 123), uop(12, 0), uop(10, 0)], &opcodes())
                .unwrap().unwrap();
            assert_eq!(steps[0], StoreStep { opcode: 11, oparg: 123, source: 2 });
            assert_eq!(steps[2].opcode, 12);
        }
        let steps = plan_stores(&[uop(1, 0), uop(10, 0), uop(2, 1), uop(10, 0)], &opcodes())
            .unwrap().unwrap();
        assert_eq!(steps[2], StoreStep { opcode: 11, oparg: 1, source: 3 });
    }

    #[test]
    fn interstitial_operations_and_partial_pairs_remain_unchanged() {
        for uops in [vec![], vec![uop(1, 0)], vec![uop(10, 0)],
                     vec![uop(1, 0), uop(99, 0), uop(10, 0)],
                     vec![uop(1, 0), uop(12, 0)], vec![uop(1, 0), uop(99, 0)]] {
            assert!(plan_stores(&uops, &opcodes()).unwrap().is_none());
        }
    }

    #[test]
    fn virtual_argument_stores_preserve_numeric_cleanup_and_its_source() {
        let ops = opcodes();
        for (pop, store) in [(ops.float_pop, ops.argument_float_store),
                             (ops.int_pop, ops.argument_int_store), (ops.pop, ops.argument_numeric_store)] {
            for argument in [0, 1, 7, u16::MAX] {
                let input = [uop(ops.argument_swap, argument), uop(ops.nop, 0), uop(pop, 0)];
                let steps = plan_stores(&input, &ops).unwrap().unwrap();
                assert_eq!(steps[0], StoreStep { opcode: store, oparg: argument, source: 2 });
                assert_eq!(steps[2].opcode, ops.nop);
                let mut stopped = input;
                stopped[1].opcode = 99;
                assert!(plan_stores(&stopped, &ops).unwrap().is_none());
            }
        }
    }

    #[test]
    fn typed_local_stores_preserve_replicas_indices_and_cleanup_metadata() {
        let ops = opcodes();
        for swap in ops.swap_first..=ops.swap_last {
            for (pop, store) in [(ops.float_pop, ops.local_float_store),
                                 (ops.int_pop, ops.local_int_store)] {
                for local in [0, 1, 7, u16::MAX] {
                    let input = [uop(swap, local), uop(ops.nop, 0), uop(pop, 0)];
                    let steps = plan_stores(&input, &ops).unwrap().unwrap();
                    assert_eq!(steps[0], StoreStep { opcode: store, oparg: local, source: 2 });
                    assert_eq!(steps[2].opcode, ops.nop);
                    let mut stopped = input;
                    stopped[1].opcode = 99;
                    assert!(plan_stores(&stopped, &ops).unwrap().is_none());
                }
            }
        }
    }

    #[test]
    fn hinted_stores_guard_before_replacement_only_in_root_interval() {
        let ops = opcodes();
        let hint = StoreUop { observed_kind: ops.local_float_guard, ..uop(1, 7) };
        let steps = plan_stores(&[hint, uop(ops.nop, 0), uop(ops.pop, 0)], &ops).unwrap().unwrap();
        assert_eq!(steps[0], StoreStep { opcode: ops.local_float_guard, oparg: 7, source: 2 });
        assert_eq!(steps[1], StoreStep { opcode: ops.nop, oparg: 0, source: 1 });
        assert_eq!(steps[2], StoreStep { opcode: ops.local_float_store, oparg: 7, source: 2 });
        for boundary in [ops.push_frame, ops.return_value, ops.virtual_begin, ops.virtual_return] {
            let steps = plan_stores(&[uop(boundary, 0), hint, uop(ops.pop, 0)], &ops).unwrap().unwrap();
            assert_eq!(steps[1], StoreStep { opcode: ops.store, oparg: 7, source: 2 });
            assert_eq!(steps[2].opcode, ops.nop);
        }
    }

    #[test]
    fn ffi_rejects_bad_hint_without_writes() {
        let ops = opcodes();
        let input = [uop(1, 3), uop(ops.pop, 0), StoreUop { observed_kind: 99, ..uop(ops.nop, 0) }];
        let sentinel = StoreStep { opcode: 0xffff, oparg: 0xffff, source: u32::MAX };
        let mut output = [sentinel; 3];
        // SAFETY: initialized independent input and writable output.
        let status = unsafe { crate::_PyJitOpt_PlanStores(input.as_ptr(), input.len(), &ops, output.as_mut_ptr(), output.len()) };
        assert_eq!(status, -1);
        assert_eq!(output, [sentinel; 3]);
        let invalid = [StoreUop { observed_kind: ops.local_float_guard, ..uop(ops.nop, 0) }];
        // SAFETY: initialized independent input and writable output.
        let status = unsafe { crate::_PyJitOpt_PlanStores(invalid.as_ptr(), invalid.len(), &ops, output.as_mut_ptr(), output.len()) };
        assert_eq!(status, -1);
        assert_eq!(output, [sentinel; 3]);
    }

    #[test]
    fn ffi_initializes_only_complete_output() {
        let ops = opcodes();
        let input = [StoreUop { observed_kind: ops.local_float_guard, ..uop(1, 5) }, uop(ops.pop, 0)];
        let sentinel = StoreStep { opcode: 0xffff, oparg: 0xffff, source: u32::MAX };
        let mut refused = [sentinel; 2];
        for capacity in [0, 1] {
            // SAFETY: initialized independent arrays; the output capacity is deliberately short.
            let status = unsafe { crate::_PyJitOpt_PlanStores(input.as_ptr(), input.len(), &ops, refused.as_mut_ptr(), capacity) };
            assert_eq!(status, 0);
            assert_eq!(refused, [sentinel; 2]);
        }
        let mut output = [std::mem::MaybeUninit::<StoreStep>::uninit(); 2];
        // SAFETY: inputs are initialized and output has space for both steps.
        let status = unsafe { crate::_PyJitOpt_PlanStores(input.as_ptr(), input.len(), &ops, output.as_mut_ptr().cast(), output.len()) };
        assert_eq!(status, 2);
        // SAFETY: a successful complete plan initialized both entries.
        let result = unsafe { [output[0].assume_init(), output[1].assume_init()] };
        assert_eq!(result[0], StoreStep { opcode: ops.local_float_guard, oparg: 5, source: 1 });
        assert_eq!(result[1], StoreStep { opcode: ops.local_float_store, oparg: 5, source: 1 });
    }

    #[test]
    fn ffi_rejects_short_output_without_partial_writes() {
        let uops = [uop(1, 0), uop(10, 0)];
        let untouched = StoreStep { opcode: 0xffff, oparg: 0xffff, source: u32::MAX };
        let mut output = [untouched; 2];
        // SAFETY: separate initialized inputs and live output; capacity is deliberately short.
        let status = unsafe { crate::_PyJitOpt_PlanStores(uops.as_ptr(), uops.len(),
            &opcodes(), output.as_mut_ptr(), 1) };
        assert_eq!(status, 0);
        assert_eq!(output, [untouched; 2]);
        let bad = StoreOpcodes { swap_first: 10, ..opcodes() };
        // SAFETY: valid allocations with a deliberately inconsistent opcode range.
        let status = unsafe { crate::_PyJitOpt_PlanStores(uops.as_ptr(), uops.len(),
            &bad, output.as_mut_ptr(), output.len()) };
        assert_eq!(status, -1);
        assert_eq!(output, [untouched; 2]);
        assert_eq!(std::mem::size_of::<StoreOpcodes>(), 36);
        assert_eq!(std::mem::offset_of!(StoreOpcodes, local_float_store), 22);
        assert_eq!(std::mem::offset_of!(StoreOpcodes, local_int_store), 24);
        assert_eq!(std::mem::offset_of!(StoreOpcodes, local_float_guard), 26);
        assert_eq!(std::mem::offset_of!(StoreOpcodes, virtual_return), 34);
        assert_eq!(std::mem::size_of::<StoreUop>(), 6);
        assert_eq!(std::mem::offset_of!(StoreUop, observed_kind), 4);
        assert_eq!(std::mem::size_of::<StoreStep>(), 8);
    }
}
