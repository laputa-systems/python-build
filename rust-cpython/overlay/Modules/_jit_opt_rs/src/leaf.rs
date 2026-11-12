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
    pub guard_tos_float: u16,
    pub guard_nos_float: u16,
    pub float_add: u16,
    pub float_subtract: u16,
    pub float_multiply: u16,
    pub float_divide: u16,
    pub float_call: u16,
    pub managed_values: u16,
    pub load_instance: u16,
    pub pop_float: u16,
    pub virtual_begin: u16,
    pub virtual_argument: u16,
    pub virtual_return: u16,
    pub float_inplace_left: [u16; 4],
    pub float_inplace_right: [u16; 4],
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
/// select a constant address (0), argument (1), argument slot (2), or guarded
/// inline instance value (3). Argument
/// indices occupy bits 2..18; slot byte offsets occupy bits 18..34.
/// For a leaf-globals guard, `result` holds the expected dictionary keys version.
/// For a pair call it holds 0 for argument order or 1 for reversed order.
/// For a float call, bits 0..16 hold the operation identity, bit 16 reverses
/// arguments, and bits 32..64 hold the callee's arithmetic error offset.
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
    InstanceValue { argument: u16, offset: u16 },
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
            Self::InstanceValue { argument, offset } if offset != 0 && offset & 7 == 0 => {
                Ok((u64::from(offset) << 18) | (u64::from(argument) << 2) | 3)
            }
            Self::Slot { .. } | Self::InstanceValue { .. } => Err(Inconsistent),
        }
    }
}

enum BodyState {
    Entry { resumed: bool },
    Value { result: ResultSource, borrowed: bool },
    SlotOwner(ResultSource),
    ManagedOwner(u16),
}

struct LeafBody {
    end: usize,
    result: ResultSource,
    globals_version: Option<u32>,
}

#[derive(Clone, Copy)]
enum ArgumentOrder {
    Forward,
    Reverse,
}

#[derive(Clone, Copy)]
struct FloatResult {
    operation: u16,
    order: ArgumentOrder,
    error_offset: u32,
}

impl FloatResult {
    fn encode(self) -> u64 {
        let reversed = match self.order { ArgumentOrder::Forward => 0, ArgumentOrder::Reverse => 1 };
        u64::from(self.operation) | (reversed << 16) | (u64::from(self.error_offset) << 32)
    }
}

enum FloatState {
    Entry,
    Resumed,
    First(u16),
    Arguments(ArgumentOrder),
    Both(FloatResult),
    Left(FloatResult),
    Result(FloatResult),
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum VirtualValue {
    BorrowedFloat,
    UniqueFloat,
    SharedFloat,
    ReallocatedOperand,
}

struct VirtualChange {
    index: usize,
    opcode: u16,
    oparg: u16,
}

struct VirtualBody {
    end: usize,
    changes: Vec<VirtualChange>,
}

fn virtual_body(uops: &[LeafUop], at: usize, ops: &LeafOpcodes)
    -> Result<Option<VirtualBody>, Inconsistent>
{
    let argc = uops[at].oparg;
    let mut values = Vec::new();
    let mut changes = Vec::new();
    if values.try_reserve_exact(uops.len()).is_err()
        || changes.try_reserve_exact(uops.len()).is_err()
    {
        return Ok(None);
    }
    let mut resumed = false;
    let mut operations = 0;
    for (index, uop) in uops.iter().enumerate().skip(at + 3) {
        let opcode = uop.opcode;
        let replacement;
        if [ops.nop, ops.set_ip, ops.validity].contains(&opcode) && uop.effectful == 0 {
            replacement = ops.nop;
        }
        else if opcode == ops.resume && !resumed {
            resumed = true;
            replacement = ops.nop;
        }
        else if !resumed { return Ok(None) }
        else if ops.load_borrow.contains(opcode) && uop.oparg < argc && uop.effectful == 0 {
            values.push(VirtualValue::BorrowedFloat);
            replacement = ops.virtual_argument;
        }
        else if [ops.guard_tos_float, ops.guard_nos_float].contains(&opcode) && uop.effectful == 0 {
            let depth = if opcode == ops.guard_tos_float { 1 } else { 2 };
            if values.len() < depth { return Err(Inconsistent) }
            if values[values.len() - depth] == VirtualValue::ReallocatedOperand { return Ok(None) }
            replacement = ops.nop;
        }
        else if [ops.float_add, ops.float_subtract, ops.float_multiply, ops.float_divide].contains(&opcode)
            || ops.float_inplace_left.contains(&opcode) || ops.float_inplace_right.contains(&opcode)
        {
            if values.len() < 2 || u32::try_from(uop.operand).ok().filter(|offset| *offset != 0).is_none() {
                return Err(Inconsistent);
            }
            let mut right = values.pop().unwrap();
            let mut left = values.pop().unwrap();
            if left == VirtualValue::ReallocatedOperand || right == VirtualValue::ReallocatedOperand {
                return Ok(None);
            }
            let normal = [ops.float_add, ops.float_subtract, ops.float_multiply, ops.float_divide];
            if let Some(operation) = ops.float_inplace_left.iter().position(|op| *op == opcode) {
                if left != VirtualValue::UniqueFloat { return Ok(None) }
                left = VirtualValue::ReallocatedOperand;
                replacement = normal[operation];
            }
            else if let Some(operation) = ops.float_inplace_right.iter().position(|op| *op == opcode) {
                if right != VirtualValue::UniqueFloat { return Ok(None) }
                right = VirtualValue::ReallocatedOperand;
                replacement = normal[operation];
            }
            else { replacement = opcode }
            values.extend([VirtualValue::UniqueFloat, left, right]);
            operations += 1;
        }
        else if opcode == ops.pop_borrow || opcode == ops.pop_float {
            let Some(value) = values.pop() else { return Err(Inconsistent) };
            replacement = match (opcode, value) {
                (op, VirtualValue::BorrowedFloat) if op == ops.pop_borrow => ops.pop_borrow,
                (op, VirtualValue::UniqueFloat | VirtualValue::SharedFloat) if op == ops.pop_float => ops.pop_float,
                (op, VirtualValue::ReallocatedOperand) if op == ops.pop_borrow => ops.pop_float,
                _ => return Ok(None),
            };
        }
        else if opcode == ops.heap_safe {
            let Some(value) = values.last_mut() else { return Err(Inconsistent) };
            if *value == VirtualValue::ReallocatedOperand { return Ok(None) }
            if *value == VirtualValue::BorrowedFloat { *value = VirtualValue::SharedFloat }
            replacement = opcode;
        }
        else if opcode == ops.return_value {
            if operations < 2 || values.len() != 1
                || !matches!(values[0], VirtualValue::UniqueFloat | VirtualValue::SharedFloat) {
                return Ok(None);
            }
            changes.push(VirtualChange { index, opcode: ops.virtual_return, oparg: 0 });
            return Ok(Some(VirtualBody { end: index, changes }));
        }
        else { return Ok(None) }
        changes.push(VirtualChange { index, opcode: replacement, oparg: uop.oparg });
    }
    Ok(None)
}

fn float_body(uops: &[LeafUop], at: usize, ops: &LeafOpcodes)
    -> Result<Option<(usize, FloatResult)>, Inconsistent>
{
    if uops[at].oparg != 2 {
        return Ok(None);
    }
    let mut state = FloatState::Entry;
    for (index, uop) in uops.iter().enumerate().skip(at + 3) {
        let opcode = uop.opcode;
        if (opcode == ops.nop || opcode == ops.set_ip) && uop.effectful == 0 {
            continue;
        }
        state = match state {
            FloatState::Entry if opcode == ops.validity && uop.effectful == 0 => FloatState::Entry,
            FloatState::Entry if opcode == ops.resume => FloatState::Resumed,
            FloatState::Resumed
                if ops.load_borrow.contains(opcode) && uop.oparg < 2 && uop.effectful == 0 =>
            {
                FloatState::First(uop.oparg)
            }
            FloatState::First(first)
                if ops.load_borrow.contains(opcode) && uop.oparg == 1 - first && uop.effectful == 0 =>
            {
                FloatState::Arguments(if first == 0 { ArgumentOrder::Forward } else { ArgumentOrder::Reverse })
            }
            FloatState::Arguments(order)
                if (opcode == ops.guard_tos_float || opcode == ops.guard_nos_float) && uop.effectful == 0 =>
            {
                FloatState::Arguments(order)
            }
            FloatState::Arguments(order)
                if [ops.float_add, ops.float_subtract, ops.float_multiply, ops.float_divide].contains(&opcode) =>
            {
                let error_offset = u32::try_from(uop.operand).map_err(|_| Inconsistent)?;
                if error_offset == 0 {
                    return Err(Inconsistent);
                }
                FloatState::Both(FloatResult { operation: opcode, order, error_offset })
            }
            FloatState::Both(result) if opcode == ops.pop_borrow && uop.effectful == 0 => FloatState::Left(result),
            FloatState::Left(result) if opcode == ops.pop_borrow && uop.effectful == 0 => FloatState::Result(result),
            FloatState::Result(result) if opcode == ops.validity && uop.effectful == 0 => FloatState::Result(result),
            FloatState::Result(result) if opcode == ops.heap_safe => FloatState::Result(result),
            FloatState::Result(result) if opcode == ops.return_value => return Ok(Some((index, result))),
            _ => return Ok(None),
        };
    }
    Ok(None)
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
    Arguments(ArgumentOrder),
    Built(ArgumentOrder, PairContainer),
    Returned(ArgumentOrder, PairContainer),
}

fn pair_body(uops: &[LeafUop], at: usize, ops: &LeafOpcodes) -> Option<(usize, ArgumentOrder)> {
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
                PairState::Arguments(if first == 0 { ArgumentOrder::Forward } else { ArgumentOrder::Reverse })
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
                if opcode == ops.managed_values && uop.effectful == 0 =>
            {
                BodyState::ManagedOwner(argument)
            }
            BodyState::ManagedOwner(argument) if opcode == ops.load_instance && uop.effectful == 0 => {
                let offset = u16::try_from(uop.operand).map_err(|_| Inconsistent)?;
                let result = ResultSource::InstanceValue { argument, offset };
                result.encode()?;
                BodyState::SlotOwner(result)
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
/// in a tuple or list that is immediately unpacked, or computes one exact-float
/// operation with an error-only frame reconstruction recipe. Longer callback-free
/// float bodies use a reserved operand stack and borrowed argument loads, preserving
/// original arithmetic targets. In-place arithmetic and its NULL cleanup are
/// normalized together to boxed arithmetic and owning cleanup. Slot guards must
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
                result: match order { ArgumentOrder::Forward => 0, ArgumentOrder::Reverse => 1 },
            };
            for step in &mut steps[at + 3..=end] {
                step.opcode = ops.nop;
            }
            changed = true;
            at = end + 1;
            continue;
        }
        if let Some((end, result)) = float_body(uops, at, ops)? {
            steps[at].opcode = ops.nop;
            steps[at + 2] = LeafStep {
                opcode: ops.float_call, oparg: 2, source: (at - 1) as u32,
                result: result.encode(),
            };
            for step in &mut steps[at + 3..=end] {
                step.opcode = ops.nop;
            }
            changed = true;
            at = end + 1;
            continue;
        }
        if let Some(body) = virtual_body(uops, at, ops)? {
            steps[at].opcode = ops.nop;
            steps[at + 2] = LeafStep { opcode: ops.virtual_begin, oparg: uops[at].oparg,
                source: (at - 1) as u32, result: 0 };
            for change in body.changes {
                steps[change.index].opcode = change.opcode;
                steps[change.index].oparg = change.oparg;
            }
            changed = true;
            at = body.end + 1;
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
            guard_tos_float: 51, guard_nos_float: 52, float_add: 53,
            float_subtract: 54, float_multiply: 55, float_divide: 56, float_call: 57,
            managed_values: 58, load_instance: 59,
            pop_float: 60, virtual_begin: 61, virtual_argument: 62, virtual_return: 63,
            float_inplace_left: [64, 65, 66, 67], float_inplace_right: [68, 69, 70, 71],
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
    fn instance_values_require_valid_inline_storage_and_complete_cleanup() {
        let ops = opcodes();
        let mut input = identity();
        input.splice(7..7, [uop(ops.managed_values, 0),
            LeafUop { opcode: ops.load_instance, oparg: 0, effectful: 0, operand: 24 },
            uop(ops.pop_borrow, 0)]);
        let plan = plan_leaf_calls(&input, &ops).unwrap().unwrap();
        assert_eq!(plan[4].result, (24 << 18) | 3);
        for index in [7, 8, 9] {
            let mut partial = input.clone();
            partial[index].opcode = ops.nop;
            assert!(plan_leaf_calls(&partial, &ops).unwrap().is_none());
            let mut effectful = input.clone();
            effectful[index].effectful = 1;
            assert!(plan_leaf_calls(&effectful, &ops).unwrap().is_none());
        }
        for offset in [0, 3, 1 << 16] {
            let mut invalid = input.clone();
            invalid[8].operand = offset;
            assert!(matches!(plan_leaf_calls(&invalid, &ops), Err(Inconsistent)));
        }
        input[6].opcode = ops.load_fast.first;
        assert!(plan_leaf_calls(&input, &ops).unwrap().is_none());
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
        assert_eq!(std::mem::size_of::<LeafOpcodes>(), 108);
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

    fn float_call() -> Vec<LeafUop> {
        let ops = opcodes();
        vec![uop(ops.function_version, 2), uop(ops.recursion, 2),
             uop(ops.init.first + 2, 2), uop(ops.save_return, 4), uop(ops.push_frame, 2),
             uop(ops.resume, 0), uop(ops.load_borrow.first, 0), uop(ops.load_borrow.first + 1, 1),
             uop(ops.guard_tos_float, 0), uop(ops.guard_nos_float, 0),
             LeafUop { opcode: ops.float_add, oparg: 0, effectful: 1, operand: 3 },
             uop(ops.pop_borrow, 0), uop(ops.pop_borrow, 0), uop(ops.validity, 0),
             uop(ops.set_ip, 0), uop(ops.heap_safe, 0), uop(ops.return_value, 0)]
    }

    #[test]
    fn floats_keep_distinct_call_exit_and_callee_error_recipes() {
        let ops = opcodes();
        for operation in [ops.float_add, ops.float_subtract, ops.float_multiply, ops.float_divide] {
            for order in [0, 1] {
                let mut uops = float_call();
                uops[6].oparg = order;
                uops[7].oparg = 1 - order;
                uops[10].opcode = operation;
                let steps = plan_leaf_calls(&uops, &ops).unwrap().unwrap();
                assert_eq!(steps[4], LeafStep { opcode: ops.float_call, oparg: 2, source: 1,
                    result: u64::from(operation) | (u64::from(order) << 16) | (3 << 32) });
                assert_eq!(steps[3].opcode, ops.save_return);
                assert!(steps[5..].iter().all(|step| step.opcode == ops.nop));
            }
        }
    }

    #[test]
    fn virtual_calls_preserve_arithmetic_and_per_operation_error_targets() {
        let ops = opcodes();
        let mut input = float_call();
        input.truncate(14);
        input.extend([uop(ops.load_borrow.first, 0), uop(ops.guard_tos_float, 0),
            uop(ops.guard_nos_float, 0),
            LeafUop { opcode: ops.float_multiply, oparg: 0, effectful: 1, operand: 7 },
            uop(ops.pop_borrow, 0), uop(ops.pop_float, 0),
            uop(ops.heap_safe, 0), uop(ops.return_value, 0)]);
        let plan = plan_leaf_calls(&input, &ops).unwrap().unwrap();
        assert_eq!(plan[4], LeafStep { opcode: ops.virtual_begin, oparg: 2, source: 1, result: 0 });
        assert_eq!(plan[3].opcode, ops.save_return);
        assert_eq!(plan[6].opcode, ops.virtual_argument);
        assert_eq!(plan[7].opcode, ops.virtual_argument);
        assert_eq!(plan[14].opcode, ops.virtual_argument);
        assert_eq!(plan[10], LeafStep { opcode: ops.float_add, oparg: 0, source: 10, result: 0 });
        assert_eq!(plan[17], LeafStep { opcode: ops.float_multiply, oparg: 0, source: 17, result: 0 });
        assert_eq!(input[10].operand, 3);
        assert_eq!(input[17].operand, 7);
        assert_eq!(plan[21].opcode, ops.virtual_return);
        assert_eq!(plan[5].opcode, ops.nop);
        assert_eq!(plan[13].opcode, ops.nop);
        for index in [6, 7, 14] {
            let mut owned_argument = input.clone();
            owned_argument[index].opcode = ops.load_fast.first;
            assert!(plan_leaf_calls(&owned_argument, &ops).unwrap().is_none());
        }
        for index in [10, 17] {
            let mut bad_offset = input.clone();
            bad_offset[index].operand = 0;
            assert!(matches!(plan_leaf_calls(&bad_offset, &ops), Err(Inconsistent)));
        }
        for index in [5, 11, 12, 18, 19, 20, 21] {
            let mut callback = input.clone();
            callback[index].opcode = 99;
            assert!(plan_leaf_calls(&callback, &ops).unwrap().is_none(), "index {index}");
        }
        let mut wrong_cleanup = input.clone();
        wrong_cleanup[19].opcode = ops.pop_borrow;
        assert!(plan_leaf_calls(&wrong_cleanup, &ops).unwrap().is_none());
    }

    #[test]
    fn virtual_inplace_shapes_restore_allocation_and_the_reused_operand_cleanup() {
        let ops = opcodes();
        let normal = [ops.float_add, ops.float_subtract, ops.float_multiply, ops.float_divide];
        for right in [false, true] {
            for (operation, replacement) in normal.iter().enumerate() {
                let mut input = float_call()[..6].to_vec();
                if right { input.push(uop(ops.load_borrow.first, 0)) }
                input.extend_from_slice(&float_call()[6..14]);
                if !right { input.push(uop(ops.load_borrow.first, 0)) }
                input.extend([uop(ops.guard_tos_float, 0), uop(ops.guard_nos_float, 0)]);
                let arithmetic = input.len();
                input.push(LeafUop { opcode: if right { ops.float_inplace_right[operation] }
                    else { ops.float_inplace_left[operation] }, oparg: 0, effectful: 0, operand: 7 });
                input.extend([uop(ops.pop_borrow, 0), uop(ops.pop_borrow, 0),
                    uop(ops.heap_safe, 0), uop(ops.return_value, 0)]);
                let plan = plan_leaf_calls(&input, &ops).unwrap().unwrap();
                assert_eq!(plan[arithmetic].opcode, *replacement);
                assert_eq!(plan[arithmetic].source as usize, arithmetic);
                assert_eq!(plan[arithmetic + 1].opcode, if right { ops.pop_float } else { ops.pop_borrow });
                assert_eq!(plan[arithmetic + 2].opcode, if right { ops.pop_borrow } else { ops.pop_float });
                assert_eq!(plan[4].opcode, ops.virtual_begin);
            }
        }
        let mut shared = float_call();
        shared.insert(7, uop(ops.heap_safe, 0));
        shared[11].opcode = ops.float_inplace_left[0];
        assert!(plan_leaf_calls(&shared, &ops).unwrap().is_none());
    }

    #[test]
    fn float_callbacks_partial_cleanup_and_extra_state_keep_real_frames() {
        let ops = opcodes();
        for index in [0, 1, 5, 6, 7, 10, 11, 12, 16] {
            let mut uops = float_call();
            uops[index].opcode = 99;
            assert!(plan_leaf_calls(&uops, &ops).unwrap().is_none(), "index {index}");
        }
        for index in [6, 7, 8, 9, 11, 12, 13] {
            let mut uops = float_call();
            uops[index].effectful = 1;
            assert!(plan_leaf_calls(&uops, &ops).unwrap().is_none());
        }
        let mut observed = float_call();
        observed.insert(13, uop(99, 0));
        assert!(plan_leaf_calls(&observed, &ops).unwrap().is_none());
        let mut repeated = float_call();
        repeated[7].oparg = 0;
        assert!(plan_leaf_calls(&repeated, &ops).unwrap().is_none());
        for end in 0..float_call().len() {
            assert!(plan_leaf_calls(&float_call()[..end], &ops).unwrap().is_none());
        }
        for offset in [0, 1 << 32] {
            let mut malformed = float_call();
            malformed[10].operand = offset;
            assert!(matches!(plan_leaf_calls(&malformed, &ops), Err(Inconsistent)));
        }
    }
}
