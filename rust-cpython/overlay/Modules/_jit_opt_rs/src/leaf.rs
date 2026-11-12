use crate::Inconsistent;

#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct OpcodeRange {
    pub first: u16,
    pub last: u16,
}

impl OpcodeRange {
    fn contains(self, opcode: u16) -> bool {
        (self.first..=self.last).contains(&opcode)
    }
}

/// Opcode identities come from the interpreter's generated uop table.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct LeafOpcodes {
    pub init: OpcodeRange,
    pub load_fast: OpcodeRange,
    pub load_borrow: OpcodeRange,
    pub save_return: u16,
    pub push_frame: u16,
    pub return_value: u16,
    pub function_version: u16,
    pub function_version_inline: u16,
    pub function_args: u16,
    pub recursion: u16,
    pub stack_space: u16,
    pub pep523: u16,
    pub resume: u16,
    pub validity: u16,
    pub set_ip: u16,
    pub load_const: u16,
    pub load_const_borrow: u16,
    pub heap_safe: u16,
    pub nop: u16,
    pub leaf_call: u16,
    pub load_slot: u16,
    pub pop_borrow: u16,
    pub guard_globals: u16,
    pub leaf_globals: u16,
    pub build_tuple: u16,
    pub unpack_two: u16,
    pub unpack_two_unique: u16,
    pub pair_call: u16,
    pub build_list: u16,
    pub unpack_list: u16,
}

/// `effectful` is 0 or 1. Operand bits are opaque; Rust never dereferences them.
#[repr(C)]
#[derive(Clone, Copy, Debug)]
pub struct LeafUop {
    pub opcode: u16,
    pub oparg: u16,
    pub effectful: u8,
    pub operand: u64,
}

/// A replacement copies target metadata from `source`. The low two result bits
/// select a constant address (0), argument (1), or argument slot (2). Argument
/// indices occupy bits 2..18; slot byte offsets occupy bits 18..34.
/// For a leaf-globals guard, `result` holds the expected dictionary keys version.
/// For a pair call it holds 0 for argument order or 1 for reversed order.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct LeafStep {
    pub opcode: u16,
    pub oparg: u16,
    pub source: u32,
    pub result: u64,
}

#[derive(Clone, Copy)]
enum ResultSource {
    Argument(u16),
    Constant(u64),
    Slot { argument: u16, offset: u16 },
}

impl ResultSource {
    fn encode(self) -> Result<u64, Inconsistent> {
        match self {
            Self::Argument(index) => Ok((u64::from(index) << 2) | 1),
            Self::Constant(address) if address != 0 && address & 3 == 0 => Ok(address),
            Self::Constant(_) => Err(Inconsistent),
            Self::Slot { argument, offset } if offset != 0 && offset & 7 == 0 => {
                Ok((u64::from(offset) << 18) | (u64::from(argument) << 2) | 2)
            }
            Self::Slot { .. } => Err(Inconsistent),
        }
    }
}

enum BodyState {
    Entry { resumed: bool },
    Value { result: ResultSource, borrowed: bool },
    SlotOwner(ResultSource),
}

struct LeafBody {
    end: usize,
    result: ResultSource,
    globals_version: Option<u32>,
}

#[derive(Clone, Copy)]
enum PairOrder {
    Forward,
    Reverse,
}

#[derive(Clone, Copy)]
enum PairContainer {
    Tuple,
    List,
}

impl PairContainer {
    fn unpacks(self, opcode: u16, ops: &LeafOpcodes) -> bool {
        match self {
            Self::Tuple => opcode == ops.unpack_two || opcode == ops.unpack_two_unique,
            Self::List => opcode == ops.unpack_list,
        }
    }
}

enum PairState {
    Entry,
    Resumed,
    First(u16),
    Arguments(PairOrder),
    Built(PairOrder, PairContainer),
    Returned(PairOrder, PairContainer),
}

fn pair_body(uops: &[LeafUop], at: usize, ops: &LeafOpcodes) -> Option<(usize, PairOrder)> {
    if uops[at].oparg != 2 {
        return None;
    }
    let mut state = PairState::Entry;
    for (index, uop) in uops.iter().enumerate().skip(at + 3) {
        let opcode = uop.opcode;
        if (opcode == ops.nop || opcode == ops.set_ip) && uop.effectful == 0 {
            continue;
        }
        state = match state {
            PairState::Entry if opcode == ops.validity && uop.effectful == 0 => PairState::Entry,
            PairState::Entry if opcode == ops.resume => PairState::Resumed,
            PairState::Resumed
                if ops.load_borrow.contains(opcode) && uop.oparg < 2 && uop.effectful == 0 =>
            {
                PairState::First(uop.oparg)
            }
            PairState::First(first)
                if ops.load_borrow.contains(opcode) && uop.oparg == 1 - first && uop.effectful == 0 =>
            {
                PairState::Arguments(if first == 0 { PairOrder::Forward } else { PairOrder::Reverse })
            }
            PairState::Arguments(order) if opcode == ops.build_tuple && uop.oparg == 2 => {
                PairState::Built(order, PairContainer::Tuple)
            }
            PairState::Arguments(order) if opcode == ops.build_list && uop.oparg == 2 => {
                PairState::Built(order, PairContainer::List)
            }
            PairState::Built(order, container) if opcode == ops.heap_safe => PairState::Built(order, container),
            PairState::Built(order, container) if opcode == ops.validity && uop.effectful == 0 => {
                PairState::Built(order, container)
            }
            PairState::Built(order, container) if opcode == ops.return_value => PairState::Returned(order, container),
            PairState::Returned(order, container) if opcode == ops.validity && uop.effectful == 0 => {
                PairState::Returned(order, container)
            }
            PairState::Returned(order, container) if container.unpacks(opcode, ops) && uop.oparg == 2 =>
            {
                return Some((index, order));
            }
            _ => return None,
        };
    }
    None
}

fn header_is_guarded(uops: &[LeafUop], at: usize, ops: &LeafOpcodes) -> bool {
    if at == 0 || uops[at - 1].opcode != ops.recursion {
        return false;
    }
    for previous in uops[at.saturating_sub(16)..at].iter().rev() {
        if previous.effectful != 0 {
            return false;
        }
        let opcode = previous.opcode;
        if opcode == ops.function_version || opcode == ops.function_version_inline {
            return true;
        }
        if ![
            ops.nop, ops.recursion, ops.stack_space, ops.function_args, ops.pep523,
        ].contains(&opcode) {
            break;
        }
    }
    false
}

fn leaf_body(uops: &[LeafUop], at: usize, ops: &LeafOpcodes)
    -> Result<Option<LeafBody>, Inconsistent>
{
    let argc = uops[at].oparg;
    let mut state = BodyState::Entry { resumed: false };
    let mut globals_version = None;
    for (index, uop) in uops.iter().enumerate().skip(at + 3) {
        let opcode = uop.opcode;
        if opcode == ops.nop || opcode == ops.set_ip {
            if uop.effectful != 0 {
                return Err(Inconsistent);
            }
            continue;
        }
        state = match state {
            BodyState::Entry { resumed } if opcode == ops.validity && uop.effectful == 0 => {
                BodyState::Entry { resumed }
            }
            BodyState::Entry { resumed: false } if opcode == ops.resume => {
                BodyState::Entry { resumed: true }
            }
            BodyState::Entry { resumed: true }
                if opcode == ops.guard_globals && uop.effectful == 0
                    && globals_version.is_none() =>
            {
                globals_version = Some(u32::try_from(uop.operand).map_err(|_| Inconsistent)?);
                BodyState::Entry { resumed: true }
            }
            BodyState::Entry { resumed: true }
                if (ops.load_fast.contains(opcode) || ops.load_borrow.contains(opcode))
                    && uop.oparg <= argc && uop.effectful == 0 =>
            {
                BodyState::Value { result: ResultSource::Argument(uop.oparg),
                                   borrowed: ops.load_borrow.contains(opcode) }
            }
            BodyState::Entry { resumed: true }
                if (opcode == ops.load_const || opcode == ops.load_const_borrow)
                    && uop.effectful == 0 =>
            {
                let result = ResultSource::Constant(uop.operand);
                result.encode()?;
                BodyState::Value { result, borrowed: opcode == ops.load_const_borrow }
            }
            BodyState::Value { result: ResultSource::Argument(argument), borrowed: true }
                if opcode == ops.load_slot && uop.effectful == 0 =>
            {
                let offset = u16::try_from(uop.operand).map_err(|_| Inconsistent)?;
                let result = ResultSource::Slot { argument, offset };
                result.encode()?;
                BodyState::SlotOwner(result)
            }
            BodyState::SlotOwner(result) if opcode == ops.pop_borrow && uop.effectful == 0 => {
                BodyState::Value { result, borrowed: false }
            }
            BodyState::Value { result, .. } if opcode == ops.heap_safe => {
                BodyState::Value { result, borrowed: false }
            }
            BodyState::Value { result, .. } if opcode == ops.return_value => {
                return Ok(Some(LeafBody { end: index, result, globals_version }));
            }
            _ => return Ok(None),
        };
    }
    Ok(None)
}

/// Fuses guarded exact-argument calls whose optimized body only returns an argument
/// or constant, loads one slot from a borrowed argument, or returns both arguments
/// in a tuple or list that is immediately unpacked. Slot guards must
/// already be discharged by the interpreter optimizer. An unsupported body or
/// allocation failure leaves the trace unchanged. A globals-version guard is
/// preserved before the call, checking the callee's dictionary.
pub fn plan_leaf_calls(uops: &[LeafUop], ops: &LeafOpcodes)
    -> Result<Option<Vec<LeafStep>>, Inconsistent>
{
    if uops.len() > u32::MAX as usize
        || [ops.init, ops.load_fast, ops.load_borrow].iter().any(|range| range.first > range.last)
        || uops.iter().any(|uop| uop.effectful > 1)
    {
        return Err(Inconsistent);
    }
    let mut steps = Vec::new();
    if steps.try_reserve_exact(uops.len()).is_err() {
        return Ok(None);
    }
    for (index, uop) in uops.iter().enumerate() {
        steps.push(LeafStep { opcode: uop.opcode, oparg: uop.oparg,
                              source: index as u32, result: 0 });
    }
    let mut changed = false;
    let mut at = 0;
    while at + 2 < uops.len() {
        if !ops.init.contains(uops[at].opcode)
            || uops[at + 1].opcode != ops.save_return
            || uops[at + 2].opcode != ops.push_frame
            || !header_is_guarded(uops, at, ops)
        {
            at += 1;
            continue;
        }
        if let Some((end, order)) = pair_body(uops, at, ops) {
            steps[at].opcode = ops.nop;
            steps[at + 2] = LeafStep {
                opcode: ops.pair_call, oparg: 2, source: (at - 1) as u32,
                result: match order { PairOrder::Forward => 0, PairOrder::Reverse => 1 },
            };
            for step in &mut steps[at + 3..=end] {
                step.opcode = ops.nop;
            }
            changed = true;
            at = end + 1;
            continue;
        }
        let Some(body) = leaf_body(uops, at, ops)? else {
            at += 1;
            continue;
        };
        if let Some(version) = body.globals_version {
            steps[at] = LeafStep { opcode: ops.leaf_globals, oparg: uops[at].oparg,
                                  source: (at - 1) as u32, result: u64::from(version) };
        }
        else {
            steps[at].opcode = ops.nop;
        }
        steps[at + 2] = LeafStep {
            opcode: ops.leaf_call, oparg: uops[at].oparg,
            source: (at - 1) as u32, result: body.result.encode()?,
        };
        for step in &mut steps[at + 3..=body.end] {
            step.opcode = ops.nop;
        }
        changed = true;
        at = body.end + 1;
    }
    Ok(changed.then_some(steps))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn opcodes() -> LeafOpcodes {
        LeafOpcodes {
            init: OpcodeRange { first: 1, last: 5 },
            load_fast: OpcodeRange { first: 6, last: 14 },
            load_borrow: OpcodeRange { first: 15, last: 23 },
            save_return: 24, push_frame: 25, return_value: 26,
            function_version: 27, function_version_inline: 28, function_args: 29,
            recursion: 30, stack_space: 31, pep523: 32, resume: 33, validity: 34,
            set_ip: 35, load_const: 36, load_const_borrow: 37, heap_safe: 38,
            nop: 39, leaf_call: 40, load_slot: 41, pop_borrow: 42,
            guard_globals: 43, leaf_globals: 44,
            build_tuple: 45, unpack_two: 46, unpack_two_unique: 47, pair_call: 48,
            build_list: 49, unpack_list: 50,
        }
    }

    fn uop(opcode: u16, oparg: u16) -> LeafUop {
        LeafUop { opcode, oparg, effectful: 0, operand: 0 }
    }

    fn identity() -> Vec<LeafUop> {
        let ops = opcodes();
        vec![uop(ops.function_version, 1), uop(ops.recursion, 1),
             uop(ops.init.first + 1, 1), uop(ops.save_return, 4), uop(ops.push_frame, 1),
             uop(ops.resume, 0), uop(ops.load_borrow.first, 0),
             uop(ops.set_ip, 0), uop(ops.heap_safe, 0), uop(ops.return_value, 0)]
    }

    #[test]
    fn a_leaf_reuses_the_callers_stack_and_deopt_target() {
        let uops = identity();
        let steps = plan_leaf_calls(&uops, &opcodes()).unwrap().unwrap();
        assert_eq!(steps[2].opcode, opcodes().nop);
        assert_eq!(steps[3].opcode, opcodes().save_return);
        assert_eq!(steps[4], LeafStep { opcode: opcodes().leaf_call, oparg: 1,
                                       source: 1, result: 1 });
        assert!(steps[5..].iter().all(|step| step.opcode == opcodes().nop));
        assert_eq!(steps[..2].iter().map(|step| step.opcode).collect::<Vec<_>>(),
                   vec![opcodes().function_version, opcodes().recursion]);
    }

    #[test]
    fn constants_and_bound_arguments_keep_distinct_results() {
        let mut uops = identity();
        uops[6] = LeafUop { opcode: opcodes().load_const_borrow, oparg: 0,
                            effectful: 0, operand: 0x1230 };
        assert_eq!(plan_leaf_calls(&uops, &opcodes()).unwrap().unwrap()[4].result, 0x1230);
        uops[6] = uop(opcodes().load_fast.first, 1);
        assert_eq!(plan_leaf_calls(&uops, &opcodes()).unwrap().unwrap()[4].result, 5);
        uops[6].oparg = 2;
        assert!(plan_leaf_calls(&uops, &opcodes()).unwrap().is_none());
    }

    #[test]
    fn observers_errors_nested_calls_and_entry_guards_keep_real_frames() {
        for replacement in [uop(99, 0), uop(opcodes().init.first, 0),
                            uop(opcodes().validity, 0)] {
            let mut uops = identity();
            uops.insert(7, replacement);
            assert!(plan_leaf_calls(&uops, &opcodes()).unwrap().is_none());
        }
        for index in [0, 1, 5] {
            let mut uops = identity();
            uops[index].opcode = opcodes().nop;
            assert!(plan_leaf_calls(&uops, &opcodes()).unwrap().is_none());
        }
        let mut uops = identity();
        uops[6].effectful = 1;
        assert!(plan_leaf_calls(&uops, &opcodes()).unwrap().is_none());
    }

    #[test]
    fn malformed_constants_and_descriptors_are_distinguishable() {
        let mut uops = identity();
        uops[6] = uop(opcodes().load_const, 0);
        assert!(matches!(plan_leaf_calls(&uops, &opcodes()), Err(Inconsistent)));
        uops[6].operand = 3;
        assert!(matches!(plan_leaf_calls(&uops, &opcodes()), Err(Inconsistent)));
        uops[6].effectful = 2;
        assert!(matches!(plan_leaf_calls(&uops, &opcodes()), Err(Inconsistent)));
    }

    #[test]
    fn slot_results_require_a_borrowed_owner_and_complete_cleanup() {
        let mut uops = identity();
        uops.insert(7, LeafUop { opcode: opcodes().load_slot, oparg: 0,
                                effectful: 0, operand: 16 });
        uops.insert(8, uop(opcodes().pop_borrow, 0));
        assert_eq!(plan_leaf_calls(&uops, &opcodes()).unwrap().unwrap()[4].result,
                   (16 << 18) | 2);
        for replacement in [uop(opcodes().nop, 0), uop(99, 0)] {
            let mut incomplete = uops.clone();
            incomplete[8] = replacement;
            assert!(plan_leaf_calls(&incomplete, &opcodes()).unwrap().is_none());
        }
        let mut owned = uops.clone();
        owned[6].opcode = opcodes().load_fast.first;
        assert!(plan_leaf_calls(&owned, &opcodes()).unwrap().is_none());
        for offset in [0, 3, 1 << 16] {
            uops[7].operand = offset;
            assert!(matches!(plan_leaf_calls(&uops, &opcodes()), Err(Inconsistent)));
        }
    }

    #[test]
    fn global_guards_keep_callee_context_and_precall_deopt() {
        let mut uops = identity();
        uops.insert(6, LeafUop { opcode: opcodes().guard_globals, oparg: 0,
                                effectful: 0, operand: 42 });
        let steps = plan_leaf_calls(&uops, &opcodes()).unwrap().unwrap();
        assert_eq!(steps[2], LeafStep { opcode: opcodes().leaf_globals, oparg: 1,
                                       source: 1, result: 42 });
        assert_eq!(steps[4].opcode, opcodes().leaf_call);
        assert!(steps[5..].iter().all(|step| step.opcode == opcodes().nop));
        uops.insert(6, uops[6]);
        assert!(plan_leaf_calls(&uops, &opcodes()).unwrap().is_none());
        uops.remove(6);
        uops[6].operand = 1 << 32;
        assert!(matches!(plan_leaf_calls(&uops, &opcodes()), Err(Inconsistent)));
    }

    #[test]
    fn ffi_rejects_without_partially_writing_a_plan() {
        let mut uops = identity();
        let mut bad = identity();
        bad[6] = uop(opcodes().load_const, 0);
        uops.extend(bad);
        let untouched = LeafStep { opcode: 0xbeef, oparg: 0xcafe, source: 0xdeadbeef,
                                   result: u64::MAX };
        let mut output = vec![untouched; uops.len()];
        // SAFETY: separate live input/output arrays and a complete opcode map.
        let status = unsafe { crate::_PyJitOpt_PlanLeafCalls(uops.as_ptr(), uops.len(),
            &opcodes(), output.as_mut_ptr(), output.len()) };
        assert_eq!(status, -1);
        assert!(output.iter().all(|step| *step == untouched));
        let valid = identity();
        // SAFETY: the one-step output array is valid but deliberately too short.
        let status = unsafe { crate::_PyJitOpt_PlanLeafCalls(valid.as_ptr(), valid.len(),
            &opcodes(), output.as_mut_ptr(), 1) };
        assert_eq!(status, 0);
        assert!(output.iter().all(|step| *step == untouched));
        assert_eq!(std::mem::size_of::<LeafUop>(), 16);
        assert_eq!(std::mem::size_of::<LeafStep>(), 16);
        assert_eq!(std::mem::size_of::<LeafOpcodes>(), 66);
    }

    fn pair() -> Vec<LeafUop> {
        let ops = opcodes();
        vec![uop(ops.function_version, 2), uop(ops.recursion, 2),
             uop(ops.init.first + 2, 2), uop(ops.save_return, 4), uop(ops.push_frame, 2),
             uop(ops.resume, 0), uop(ops.load_borrow.first, 0), uop(ops.load_borrow.first + 1, 1),
             LeafUop { effectful: 1, ..uop(ops.build_tuple, 2) },
             uop(ops.set_ip, 0), uop(ops.heap_safe, 0), uop(ops.return_value, 0),
             uop(ops.validity, 0), uop(ops.unpack_two_unique, 2)]
    }

    #[test]
    fn pair_calls_preserve_order_targets_and_return_offsets() {
        for unpack in [opcodes().unpack_two, opcodes().unpack_two_unique] {
            for order in [0, 1] {
                let mut uops = pair();
                uops[6].oparg = order;
                uops[7].oparg = 1 - order;
                uops[13].opcode = unpack;
                let steps = plan_leaf_calls(&uops, &opcodes()).unwrap().unwrap();
                assert_eq!(steps[4], LeafStep { opcode: opcodes().pair_call, oparg: 2,
                                               source: 1, result: u64::from(order) });
                assert_eq!(steps[3].opcode, opcodes().save_return);
                assert!(steps[5..].iter().all(|step| step.opcode == opcodes().nop));
            }
        }
    }

    #[test]
    fn list_pairs_require_a_matching_two_item_unpack() {
        for order in [0, 1] {
            let mut uops = pair();
            uops[6].oparg = order;
            uops[7].oparg = 1 - order;
            uops[8].opcode = opcodes().build_list;
            uops[13].opcode = opcodes().unpack_list;
            let steps = plan_leaf_calls(&uops, &opcodes()).unwrap().unwrap();
            assert_eq!(steps[4].opcode, opcodes().pair_call);
            assert_eq!(steps[4].result, u64::from(order));
            for unpack in [opcodes().unpack_two, opcodes().unpack_two_unique] {
                uops[13].opcode = unpack;
                assert!(plan_leaf_calls(&uops, &opcodes()).unwrap().is_none());
            }
            uops[13] = uop(opcodes().unpack_list, 3);
            assert!(plan_leaf_calls(&uops, &opcodes()).unwrap().is_none());
            uops[8].opcode = opcodes().build_tuple;
            uops[13].oparg = 2;
            assert!(plan_leaf_calls(&uops, &opcodes()).unwrap().is_none());
        }
    }

    #[test]
    fn list_construction_validity_is_consumed_with_the_allocation() {
        let mut uops = pair();
        uops[8].opcode = opcodes().build_list;
        uops[13].opcode = opcodes().unpack_list;
        uops.insert(9, uop(opcodes().validity, 0));
        let steps = plan_leaf_calls(&uops, &opcodes()).unwrap().unwrap();
        assert_eq!(steps[4].opcode, opcodes().pair_call);
        assert!(steps[5..].iter().all(|step| step.opcode == opcodes().nop));
        uops[9].effectful = 1;
        assert!(plan_leaf_calls(&uops, &opcodes()).unwrap().is_none());
    }

    #[test]
    fn escaping_observed_partial_and_repeated_pairs_remain_concrete() {
        for index in [0, 1, 5, 6, 7, 8, 10, 11, 13] {
            let mut uops = pair();
            uops[index].opcode = 99;
            assert!(plan_leaf_calls(&uops, &opcodes()).unwrap().is_none(), "index {index}");
        }
        for index in [6, 7, 12] {
            let mut uops = pair();
            uops[index].effectful = 1;
            assert!(plan_leaf_calls(&uops, &opcodes()).unwrap().is_none());
        }
        let mut repeated = pair();
        repeated[7].oparg = 0;
        assert!(plan_leaf_calls(&repeated, &opcodes()).unwrap().is_none());
        for end in 0..pair().len() {
            assert!(plan_leaf_calls(&pair()[..end], &opcodes()).unwrap().is_none());
        }
        for index in [6, 12] {
            let mut observed = pair();
            observed.insert(index, uop(99, 0));
            assert!(plan_leaf_calls(&observed, &opcodes()).unwrap().is_none());
        }
    }
}
