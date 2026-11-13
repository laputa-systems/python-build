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
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct StoreUop {
    pub opcode: u16,
    pub oparg: u16,
}

/// Target metadata is copied from `source`, including the original decref site.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct StoreStep {
    pub opcode: u16,
    pub oparg: u16,
    pub source: u32,
}

/// Fuse local swaps with generic cleanup and virtual argument swaps with typed
/// numeric cleanup. Only NOPs may separate the swap from its cleanup.
pub fn plan_stores(uops: &[StoreUop], ops: &StoreOpcodes)
    -> Result<Option<Vec<StoreStep>>, Inconsistent>
{
    if ops.swap_first > ops.swap_last || uops.len() > u32::MAX as usize {
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
    for (index, uop) in uops.iter().enumerate() {
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
        let opcode = match (local, uops[pop].opcode) {
            (true, cleanup) if cleanup == ops.pop => ops.store,
            (false, cleanup) if cleanup == ops.float_pop => ops.argument_float_store,
            (false, cleanup) if cleanup == ops.int_pop => ops.argument_int_store,
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
            argument_float_store: 16, argument_int_store: 17 }
    }

    fn uop(opcode: u16, oparg: u16) -> StoreUop {
        StoreUop { opcode, oparg }
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
                             (ops.int_pop, ops.argument_int_store)] {
            for argument in [0, 1, 7, u16::MAX] {
                let input = [uop(ops.argument_swap, argument), uop(ops.nop, 0), uop(pop, 0)];
                let steps = plan_stores(&input, &ops).unwrap().unwrap();
                assert_eq!(steps[0], StoreStep { opcode: store, oparg: argument, source: 2 });
                assert_eq!(steps[2].opcode, ops.nop);
                for boundary in [ops.pop, 99] {
                    let mut stopped = input;
                    stopped[1].opcode = boundary;
                    assert!(plan_stores(&stopped, &ops).unwrap().is_none());
                }
                assert!(plan_stores(&[uop(ops.swap_first, argument), uop(pop, 0)], &ops)
                    .unwrap().is_none());
            }
        }
        assert!(plan_stores(&[uop(ops.argument_swap, 0), uop(ops.pop, 0)], &ops)
            .unwrap().is_none());
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
        assert_eq!(std::mem::size_of::<StoreOpcodes>(), 20);
        assert_eq!(std::mem::size_of::<StoreUop>(), 4);
        assert_eq!(std::mem::size_of::<StoreStep>(), 8);
    }
}
