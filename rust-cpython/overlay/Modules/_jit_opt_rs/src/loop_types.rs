use crate::{Inconsistent, OpcodeRange};

/// Opcode identities are supplied by the interpreter's generated tables.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct LocalOpcodes {
    pub start: u16,
    pub warm: u16,
    pub jump: u16,
    pub nop: u16,
    pub set_ip: u16,
    pub validity: u16,
    pub periodic: u16,
    pub resume: u16,
    pub load: OpcodeRange,
    pub borrow: OpcodeRange,
    pub store: OpcodeRange,
    pub small_int: OpcodeRange,
    pub copy: OpcodeRange,
    pub swap: OpcodeRange,
    pub bit_set: OpcodeRange,
    pub bit_unset: OpcodeRange,
    pub constant: u16,
    pub constant_borrow: u16,
    pub pop: u16,
    pub pop_borrow: u16,
    pub pop_int: u16,
    pub pop_float: u16,
    pub pop_unicode: u16,
    pub tos_float: u16,
    pub nos_float: u16,
    pub tos_int: u16,
    pub nos_int: u16,
    pub tos_exact_int: u16,
    pub tos_overflow: u16,
    pub nos_overflow: u16,
    pub float_binary: [u16; 4],
    pub int_binary: [u16; 3],
    pub float_compare: u16,
    pub int_compare: u16,
    pub bool_bool: u16,
    pub bool_int: u16,
    pub iter_check: u16,
    pub iter_exhaustion: u16,
    pub iter_next: u16,
    pub globals: u16,
    pub builtins: u16,
    pub entry_globals: u16,
    pub local_int: u16,
    pub local_float: u16,
    pub push_null: u16,
}

/// A kind is 0 (unknown) or the corresponding local-guard opcode. Constant kinds
/// require a live owning reference; cached addresses alone are insufficient.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct LocalUop {
    pub opcode: u16,
    pub oparg: u16,
    pub constant_kind: u16,
}

/// Metadata is copied from `source`. Inserted local guards use source 0's entry
/// exit state; they must side-exit without consuming any stack reference.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct LocalStep {
    pub opcode: u16,
    pub oparg: u16,
    pub source: u32,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Kind {
    Unknown,
    Int,
    Float,
    Bool,
    RangeIterator,
}

fn contains(range: OpcodeRange, opcode: u16) -> bool {
    (range.first..=range.last).contains(&opcode)
}

fn decode(kind: u16, ops: &LocalOpcodes) -> Result<Kind, Inconsistent> {
    if kind == 0 { Ok(Kind::Unknown) }
    else if kind == ops.local_int { Ok(Kind::Int) }
    else if kind == ops.local_float { Ok(Kind::Float) }
    else { Err(Inconsistent) }
}

fn narrow(stack: &mut [Kind], depth: usize, kind: Kind) -> Result<bool, Inconsistent> {
    let index = stack.len().checked_sub(depth).ok_or(Inconsistent)?;
    let value = &mut stack[index];
    if *value == Kind::Unknown || *value == kind {
        *value = kind;
        Ok(true)
    }
    else { Ok(false) }
}

fn at(stack: &[Kind], depth: usize) -> Result<Kind, Inconsistent> {
    Ok(stack[stack.len().checked_sub(depth).ok_or(Inconsistent)?])
}

/// Proves a callback-free numeric warm cycle under exact root-local guards.
/// Every seeded local must retain its type at the returning backedge. Stack
/// inputs begin unknown; no ownership, compact-int, or constant-identity fact is
/// inferred. Unsupported operations and allocation failure preserve the trace.
pub fn plan_loop_types(uops: &[LocalUop], ops: &LocalOpcodes, local_kinds: &[u16],
                       initial_depth: usize) -> Result<Option<Vec<LocalStep>>, Inconsistent>
{
    let ranges = [ops.load, ops.borrow, ops.store, ops.small_int, ops.copy, ops.swap,
                  ops.bit_set, ops.bit_unset];
    if ranges.iter().any(|range| range.first > range.last)
        || ops.local_int == 0 || ops.local_float == 0 || ops.local_int == ops.local_float
        || uops.len() > u32::MAX as usize || local_kinds.len() > u16::MAX as usize + 1
    {
        return Err(Inconsistent);
    }
    if uops.len() < 4 || uops[0].opcode != ops.start || uops.last().unwrap().opcode != ops.jump {
        return Ok(None);
    }
    let Some(warm) = uops.iter().position(|uop| uop.opcode == ops.warm) else { return Ok(None) };
    if warm == 0 || warm + 1 >= uops.len()
        || uops[1..warm].iter().any(|uop| ![ops.entry_globals, ops.nop].contains(&uop.opcode)) {
        return Ok(None);
    }
    let mut used = Vec::new();
    let mut locals = Vec::new();
    let mut stack = Vec::new();
    let mut steps = Vec::new();
    let capacity = uops.len().checked_add(local_kinds.len()).ok_or(Inconsistent)?;
    let stack_capacity = initial_depth.checked_add(uops.len().checked_mul(3).ok_or(Inconsistent)?)
        .ok_or(Inconsistent)?;
    if used.try_reserve_exact(local_kinds.len()).is_err()
        || locals.try_reserve_exact(local_kinds.len()).is_err()
        || stack.try_reserve_exact(stack_capacity).is_err()
        || steps.try_reserve_exact(capacity).is_err()
    {
        return Ok(None);
    }
    used.resize(local_kinds.len(), false);
    for uop in &uops[warm + 1..] {
        if contains(ops.load, uop.opcode) || contains(ops.borrow, uop.opcode) || contains(ops.store, uop.opcode) {
            *used.get_mut(usize::from(uop.oparg)).ok_or(Inconsistent)? = true;
        }
    }
    for (index, kind) in local_kinds.iter().enumerate() {
        let kind = decode(*kind, ops)?;
        locals.push(if used[index] { kind } else { Kind::Unknown });
    }
    stack.resize(initial_depth, Kind::Unknown);
    for (index, uop) in uops.iter().enumerate() {
        steps.push(LocalStep { opcode: uop.opcode, oparg: uop.oparg, source: index as u32 });
    }
    let mut changed = false;
    for (index, uop) in uops.iter().enumerate().skip(warm + 1).take(uops.len() - warm - 2) {
        let opcode = uop.opcode;
        if [ops.nop, ops.set_ip, ops.validity, ops.resume, ops.globals, ops.builtins].contains(&opcode) {
            continue;
        }
        if opcode == ops.periodic {
            steps[index].opcode = ops.resume;
        }
        else if contains(ops.load, opcode) || contains(ops.borrow, opcode) {
            stack.push(locals[usize::from(uop.oparg)]);
        }
        else if contains(ops.store, opcode) {
            let value = stack.pop().ok_or(Inconsistent)?;
            let local = &mut locals[usize::from(uop.oparg)];
            stack.push(*local);
            *local = value;
        }
        else if opcode == ops.constant || opcode == ops.constant_borrow {
            stack.push(decode(uop.constant_kind, ops)?);
        }
        else if contains(ops.small_int, opcode) {
            stack.push(Kind::Int);
        }
        else if opcode == ops.push_null {
            stack.push(Kind::Unknown);
        }
        else if contains(ops.copy, opcode) {
            if uop.oparg == 0 { return Err(Inconsistent) }
            stack.push(at(&stack, usize::from(uop.oparg))?);
        }
        else if contains(ops.swap, opcode) {
            let len = stack.len();
            if uop.oparg < 2 || usize::from(uop.oparg) > len { return Err(Inconsistent) }
            stack.swap(len - 1, len - usize::from(uop.oparg));
        }
        else if opcode == ops.pop {
            let kind = stack.pop().ok_or(Inconsistent)?;
            steps[index].opcode = match kind {
                Kind::Int => ops.pop_int,
                Kind::Float => ops.pop_float,
                Kind::Bool => ops.pop,
                _ => return Ok(None),
            };
            changed |= steps[index].opcode != opcode;
        }
        else if [ops.pop_borrow, ops.pop_int, ops.pop_float, ops.pop_unicode].contains(&opcode) {
            let kind = stack.pop().ok_or(Inconsistent)?;
            if (opcode == ops.pop_int && ![Kind::Unknown, Kind::Int].contains(&kind))
                || (opcode == ops.pop_float && ![Kind::Unknown, Kind::Float].contains(&kind))
            {
                return Ok(None);
            }
        }
        else if opcode == ops.tos_float || opcode == ops.nos_float {
            let depth = if opcode == ops.tos_float { 1 } else { 2 };
            if at(&stack, depth)? == Kind::Float {
                steps[index].opcode = ops.nop;
                changed = true;
            }
            else if !narrow(&mut stack, depth, Kind::Float)? { return Ok(None) }
        }
        else if [ops.tos_int, ops.nos_int, ops.tos_exact_int].contains(&opcode) {
            let depth = if opcode == ops.nos_int { 2 } else { 1 };
            if !narrow(&mut stack, depth, Kind::Int)? { return Ok(None) }
        }
        else if opcode == ops.tos_overflow || opcode == ops.nos_overflow {
            let depth = if opcode == ops.nos_overflow { 2 } else { 1 };
            if at(&stack, depth)? != Kind::Int { return Ok(None) }
        }
        else if ops.float_binary.contains(&opcode) || ops.int_binary.contains(&opcode)
            || opcode == ops.float_compare || opcode == ops.int_compare
        {
            let right = stack.pop().ok_or(Inconsistent)?;
            let left = stack.pop().ok_or(Inconsistent)?;
            let expected = if ops.float_binary.contains(&opcode) || opcode == ops.float_compare {
                Kind::Float
            } else { Kind::Int };
            if left != expected || right != expected { return Ok(None) }
            stack.push(if opcode == ops.float_compare || opcode == ops.int_compare { Kind::Bool } else { expected });
            stack.push(left);
            stack.push(right);
        }
        else if opcode == ops.bool_bool {
            if !narrow(&mut stack, 1, Kind::Bool)? { return Ok(None) }
        }
        else if opcode == ops.bool_int {
            let value = stack.pop().ok_or(Inconsistent)?;
            if value != Kind::Int { return Ok(None) }
            stack.push(Kind::Bool);
            stack.push(value);
        }
        else if contains(ops.bit_set, opcode) || contains(ops.bit_unset, opcode) {
            if stack.pop().ok_or(Inconsistent)? != Kind::Bool { return Ok(None) }
        }
        else if opcode == ops.iter_check {
            if !narrow(&mut stack, 2, Kind::RangeIterator)? { return Ok(None) }
        }
        else if opcode == ops.iter_exhaustion || opcode == ops.iter_next {
            if at(&stack, 2)? != Kind::RangeIterator { return Ok(None) }
            if opcode == ops.iter_next { stack.push(Kind::Int) }
        }
        else { return Ok(None) }
    }
    if !changed || stack.len() != initial_depth { return Ok(None) }
    for (index, kind) in local_kinds.iter().enumerate() {
        if used[index] && *kind != 0 && locals[index] != decode(*kind, ops)? {
            return Ok(None);
        }
    }
    let mut guards = 0;
    for (index, kind) in local_kinds.iter().enumerate() {
        if used[index] && *kind != 0 {
            steps.insert(warm + guards, LocalStep { opcode: *kind, oparg: index as u16, source: 0 });
            guards += 1;
        }
    }
    if guards == 0 { return Ok(None) }
    Ok(Some(steps))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn opcodes() -> LocalOpcodes {
        LocalOpcodes {
            start: 1, warm: 2, jump: 3, nop: 4, set_ip: 5, validity: 6, periodic: 7, resume: 8,
            load: OpcodeRange { first: 10, last: 18 }, borrow: OpcodeRange { first: 20, last: 28 },
            store: OpcodeRange { first: 30, last: 38 }, small_int: OpcodeRange { first: 40, last: 44 },
            copy: OpcodeRange { first: 50, last: 53 }, swap: OpcodeRange { first: 60, last: 62 },
            bit_set: OpcodeRange { first: 70, last: 74 }, bit_unset: OpcodeRange { first: 80, last: 84 },
            constant: 90, constant_borrow: 91, pop: 92, pop_borrow: 93,
            pop_int: 94, pop_float: 95, pop_unicode: 96,
            tos_float: 97, nos_float: 98, tos_int: 99, nos_int: 100, tos_exact_int: 101,
            tos_overflow: 102, nos_overflow: 103,
            float_binary: [104, 105, 106, 107], int_binary: [108, 109, 110],
            float_compare: 111, int_compare: 112, bool_bool: 113, bool_int: 114,
            iter_check: 115, iter_exhaustion: 116, iter_next: 117,
            globals: 118, builtins: 119, entry_globals: 120, local_int: 121, local_float: 122,
            push_null: 123,
        }
    }

    fn uop(opcode: u16, oparg: u16) -> LocalUop {
        LocalUop { opcode, oparg, constant_kind: 0 }
    }

    fn numeric_loop(integer: bool) -> Vec<LocalUop> {
        let o = opcodes();
        vec![uop(o.start, 0), uop(o.warm, 0), uop(o.set_ip, 0), uop(o.periodic, 0),
             uop(o.validity, 0), uop(o.iter_check, 0), uop(o.iter_exhaustion, 0),
             uop(o.iter_next, 0), uop(o.store.first, 2), uop(o.pop, 0),
             uop(o.borrow.first, 0), uop(o.borrow.first, 1),
             uop(if integer { o.tos_int } else { o.tos_float }, 0),
             uop(if integer { o.nos_int } else { o.nos_float }, 0),
             uop(if integer { o.int_binary[0] } else { o.float_binary[0] }, 0),
             uop(o.pop_borrow, 0), uop(o.pop_borrow, 0), uop(o.store.first, 0),
             uop(o.pop, 0), uop(o.jump, 0)]
    }

    #[test]
    fn exact_entry_guards_prove_backedges_and_preserve_checkpoint_metadata() {
        let ops = opcodes();
        let input = numeric_loop(false);
        let seeds = [ops.local_float, ops.local_float, ops.local_int, ops.local_float];
        let plan = plan_loop_types(&input, &ops, &seeds, 2).unwrap().unwrap();
        assert_eq!(plan.len(), input.len() + 3);
        assert_eq!(plan[1..4], [LocalStep { opcode: ops.local_float, oparg: 0, source: 0 },
            LocalStep { opcode: ops.local_float, oparg: 1, source: 0 },
            LocalStep { opcode: ops.local_int, oparg: 2, source: 0 }]);
        assert_eq!(plan[4].opcode, ops.warm);
        assert_eq!(plan[6], LocalStep { opcode: ops.resume, oparg: 0, source: 3 });
        assert_eq!(plan[12].opcode, ops.pop_int);
        assert_eq!(plan[15].opcode, ops.nop);
        assert_eq!(plan[16].opcode, ops.nop);
        assert_eq!(plan[21].opcode, ops.pop_float);
        assert_eq!(plan.last().unwrap().source, (input.len() - 1) as u32);
        let mut with_prologue = input;
        with_prologue.insert(1, uop(ops.entry_globals, 0));
        let plan = plan_loop_types(&with_prologue, &ops, &seeds, 2).unwrap().unwrap();
        assert_eq!(plan[1], LocalStep { opcode: ops.entry_globals, oparg: 0, source: 1 });
        assert_eq!(plan[5].opcode, ops.warm);
    }

    #[test]
    fn integer_seeds_never_erase_compactness_or_overflow_checks() {
        let ops = opcodes();
        let seeds = [ops.local_int; 3];
        let input = numeric_loop(true);
        let plan = plan_loop_types(&input, &ops, &seeds, 2).unwrap().unwrap();
        assert_eq!(plan[15].opcode, ops.tos_int);
        assert_eq!(plan[16].opcode, ops.nos_int);
        let mut overflow = input;
        overflow[12].opcode = ops.tos_overflow;
        overflow[13].opcode = ops.nos_overflow;
        let plan = plan_loop_types(&overflow, &ops, &seeds, 2).unwrap().unwrap();
        assert_eq!(plan[15].opcode, ops.tos_overflow);
        assert_eq!(plan[16].opcode, ops.nos_overflow);
    }

    #[test]
    fn callbacks_unproved_iterators_and_noninductive_assignments_keep_originals() {
        let ops = opcodes();
        let seeds = [ops.local_float, ops.local_float, ops.local_int];
        let mut callback = numeric_loop(false);
        callback.insert(19, uop(0xffff, 0));
        assert!(plan_loop_types(&callback, &ops, &seeds, 2).unwrap().is_none());
        let mut no_iterator = numeric_loop(false);
        no_iterator[5].opcode = ops.nop;
        assert!(plan_loop_types(&no_iterator, &ops, &seeds, 2).unwrap().is_none());
        let mut changed_type = numeric_loop(false);
        changed_type.splice(19..19, [LocalUop { constant_kind: ops.local_int,
            ..uop(ops.constant, 0) }, uop(ops.store.first, 0), uop(ops.pop_float, 0)]);
        assert!(plan_loop_types(&changed_type, &ops, &seeds, 2).unwrap().is_none());
        assert!(plan_loop_types(&numeric_loop(false), &ops,
            &[0, ops.local_float, ops.local_int], 2).unwrap().is_none());
        let plain = [uop(ops.start, 0), uop(ops.warm, 0), uop(ops.periodic, 0), uop(ops.jump, 0)];
        assert!(plan_loop_types(&plain, &ops, &seeds, 0).unwrap().is_none());
    }

    #[test]
    fn ffi_capacity_and_bad_descriptors_do_not_partially_write() {
        let ops = opcodes();
        let input = numeric_loop(false);
        let seeds = [ops.local_float, ops.local_float, ops.local_int];
        let untouched = LocalStep { opcode: u16::MAX, oparg: u16::MAX, source: u32::MAX };
        let mut output = vec![untouched; input.len()];
        // SAFETY: live initialized inputs and separate output, deliberately too short for guards.
        let status = unsafe { crate::_PyJitOpt_PlanLoopTypes(input.as_ptr(), input.len(), &ops,
            seeds.as_ptr(), seeds.len(), 2, output.as_mut_ptr(), output.len()) };
        assert_eq!(status, 0);
        assert!(output.iter().all(|step| *step == untouched));
        let bad = [u16::MAX, ops.local_float, ops.local_int];
        // SAFETY: valid allocations with a deliberately inconsistent scalar kind.
        let status = unsafe { crate::_PyJitOpt_PlanLoopTypes(input.as_ptr(), input.len(), &ops,
            bad.as_ptr(), bad.len(), 2, output.as_mut_ptr(), output.len()) };
        assert_eq!(status, -1);
        assert!(output.iter().all(|step| *step == untouched));
        assert!(matches!(plan_loop_types(&input, &ops, &seeds, 0), Err(Inconsistent)));
        assert_eq!(std::mem::size_of::<LocalOpcodes>(), 116);
        assert_eq!(std::mem::size_of::<LocalUop>(), 6);
        assert_eq!(std::mem::size_of::<LocalStep>(), 8);
    }
}
