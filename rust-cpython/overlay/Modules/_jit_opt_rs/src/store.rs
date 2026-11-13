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
    pub observed_float_store: u16,
    pub float_binary: [u16; 12],
    pub list_store: u16,
    pub list_float_store: u16,
    pub borrowed_pop: u16,
    pub swap_two: u16,
    pub swap_three: u16,
    pub set_ip: u16,
    pub borrow_first: u16,
    pub borrow_last: u16,
    pub small_first: u16,
    pub small_last: u16,
    pub constant_borrow: u16,
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

fn float_list_store(uops: &[StoreUop], index: usize, ops: &StoreOpcodes) -> bool {
    let mut previous = uops[..index].iter().rev().filter(|u| u.opcode != ops.nop);
    let mut tail = previous.next();
    if tail.is_some_and(|u| u.opcode == ops.set_ip) {
        tail = previous.next();
    }
    let Some(last) = tail else { return false; };
    let Some(prior) = previous.next() else { return false; };
    let rotated = last.opcode == ops.swap_two && last.oparg == 2 &&
        prior.opcode == ops.swap_three && prior.oparg == 3;
    let loaded = ((ops.small_first..=ops.small_last).contains(&last.opcode) ||
                  last.opcode == ops.constant_borrow) &&
        (ops.borrow_first..=ops.borrow_last).contains(&prior.opcode);
    if !rotated && !loaded { return false; }
    for _ in 0..2 {
        if !previous.next().is_some_and(|u|
            [ops.pop, ops.float_pop, ops.borrowed_pop].contains(&u.opcode)) {
            return false;
        }
    }
    previous.next().is_some_and(|u| ops.float_binary.contains(&u.opcode))
}

/// Fuse local swaps with generic or typed numeric cleanup and virtual argument
/// swaps with typed or entry-guarded numeric cleanup. Only NOPs may separate the
/// swap from its cleanup. Generic virtual cleanup requires a numeric entry guard.
/// Observed float locals select a store that guards an exact float or NULL before
/// effects, only in the initial frame. Tracing can observe a value after a store;
/// fresh-frame NULL locals must not repeatedly exit at that same store.
/// Numeric list-store sequences select a kernel that guards the current old item
/// before effects; the incoming float pattern never proves the old item's type.
pub fn plan_stores(uops: &[StoreUop], ops: &StoreOpcodes)
    -> Result<Option<Vec<StoreStep>>, Inconsistent>
{
    if ops.swap_first > ops.swap_last || ops.borrow_first > ops.borrow_last ||
        ops.small_first > ops.small_last || ops.list_float_store == 0 ||
        uops.len() > u32::MAX as usize ||
        ops.local_float_guard == 0 || ops.observed_float_store == 0 ||
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
        if uop.opcode == ops.list_store && float_list_store(uops, index, ops) {
            steps[index].opcode = ops.list_float_store;
            changed = true;
            continue;
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
            steps[index] = StoreStep { opcode: ops.observed_float_store, oparg: uop.oparg, source: pop as u32 };
            steps[pop].opcode = ops.nop;
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
            push_frame: 22, return_value: 23, virtual_begin: 24, virtual_return: 25, observed_float_store: 26,
            float_binary: [27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38],
            list_store: 39, list_float_store: 40, borrowed_pop: 41,
            swap_two: 42, swap_three: 43, set_ip: 44,
            borrow_first: 45, borrow_last: 52, small_first: 53, small_last: 56,
            constant_borrow: 57 }
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
        assert_eq!(steps[0], StoreStep { opcode: ops.observed_float_store, oparg: 7, source: 2 });
        assert_eq!(steps[1], StoreStep { opcode: ops.nop, oparg: 0, source: 1 });
        assert_eq!(steps[2], StoreStep { opcode: ops.nop, oparg: 0, source: 2 });
        for boundary in [ops.push_frame, ops.return_value, ops.virtual_begin, ops.virtual_return] {
            let steps = plan_stores(&[uop(boundary, 0), hint, uop(ops.pop, 0)], &ops).unwrap().unwrap();
            assert_eq!(steps[1], StoreStep { opcode: ops.store, oparg: 7, source: 2 });
            assert_eq!(steps[2].opcode, ops.nop);
        }
    }

    fn numeric_list_input(rotated: bool) -> Vec<StoreUop> {
        let ops = opcodes();
        let middle = if rotated { [uop(ops.swap_three, 3), uop(ops.swap_two, 2)] }
            else { [uop(ops.borrow_first, 5), uop(ops.small_first, 0)] };
        vec![uop(ops.float_binary[0], 0), uop(ops.borrowed_pop, 0),
             uop(ops.float_pop, 0), middle[0], middle[1],
             uop(ops.set_ip, 0), uop(ops.list_store, 0)]
    }

    #[test]
    fn numeric_list_stores_keep_original_stack_and_exit_source() {
        let ops = opcodes();
        for rotated in [false, true] {
            for arithmetic in ops.float_binary {
                let mut input = numeric_list_input(rotated);
                input[0].opcode = arithmetic;
                let plan = plan_stores(&input, &ops).unwrap().unwrap();
                for i in 0..input.len() - 1 {
                    assert_eq!(plan[i], StoreStep { opcode: input[i].opcode,
                        oparg: input[i].oparg, source: i as u32 });
                }
                assert_eq!(plan[6], StoreStep { opcode: ops.list_float_store, oparg: 0, source: 6 });
                input.insert(3, uop(ops.nop, 0));
                assert_eq!(plan_stores(&input, &ops).unwrap().unwrap()[7].source, 7);
            }
        }
    }

    #[test]
    fn list_store_selection_refuses_unknown_effects_and_wrong_stack_operations() {
        let ops = opcodes();
        for rotated in [false, true] {
            let input = numeric_list_input(rotated);
            for changed in 0..5 {
                let mut bad = input.clone();
                bad[changed].opcode = 99;
                assert!(plan_stores(&bad, &ops).unwrap().is_none());
            }
            for gap in 1..=5 {
                let mut bad = input.clone();
                bad.insert(gap, uop(99, 0));
                assert!(plan_stores(&bad, &ops).unwrap().is_none());
            }
        }
        for (index, wrong) in [(3, 2), (4, 3)] {
            let mut bad = numeric_list_input(true);
            bad[index].oparg = wrong;
            assert!(plan_stores(&bad, &ops).unwrap().is_none());
        }
    }

    #[test]
    fn list_store_ffi_is_atomic_for_uninitialized_and_short_output() {
        let ops = opcodes();
        let input = numeric_list_input(true);
        let sentinel = StoreStep { opcode: 0xffff, oparg: 0xffff, source: u32::MAX };
        let mut refused = [sentinel; 7];
        for capacity in 0..input.len() {
            // SAFETY: inputs and output are separate live allocations with the stated capacity.
            let status = unsafe { crate::_PyJitOpt_PlanStores(input.as_ptr(), input.len(),
                &ops, refused.as_mut_ptr(), capacity) };
            assert_eq!(status, 0);
            assert_eq!(refused, [sentinel; 7]);
        }
        let mut output = [std::mem::MaybeUninit::<StoreStep>::uninit(); 7];
        // SAFETY: inputs and output are separate live allocations with the stated capacity.
        let status = unsafe { crate::_PyJitOpt_PlanStores(input.as_ptr(), input.len(),
            &ops, output.as_mut_ptr().cast(), output.len()) };
        assert_eq!(status, 7);
        // SAFETY: a positive full-length result initialized every output entry.
        let result = output.map(|entry| unsafe { entry.assume_init() });
        assert_eq!(result[6].opcode, ops.list_float_store);
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
        assert_eq!(result[0], StoreStep { opcode: ops.observed_float_store, oparg: 5, source: 1 });
        assert_eq!(result[1], StoreStep { opcode: ops.nop, oparg: 0, source: 1 });
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
        assert_eq!(std::mem::size_of::<StoreOpcodes>(), 84);
        assert_eq!(std::mem::offset_of!(StoreOpcodes, float_binary), 38);
        assert_eq!(std::mem::offset_of!(StoreOpcodes, list_store), 62);
        assert_eq!(std::mem::offset_of!(StoreOpcodes, constant_borrow), 82);
        assert_eq!(std::mem::offset_of!(StoreOpcodes, local_float_store), 22);
        assert_eq!(std::mem::offset_of!(StoreOpcodes, local_int_store), 24);
        assert_eq!(std::mem::offset_of!(StoreOpcodes, local_float_guard), 26);
        assert_eq!(std::mem::offset_of!(StoreOpcodes, virtual_return), 34);
        assert_eq!(std::mem::offset_of!(StoreOpcodes, observed_float_store), 36);
        assert_eq!(std::mem::size_of::<StoreUop>(), 6);
        assert_eq!(std::mem::offset_of!(StoreUop, observed_kind), 4);
        assert_eq!(std::mem::size_of::<StoreStep>(), 8);
    }
}
