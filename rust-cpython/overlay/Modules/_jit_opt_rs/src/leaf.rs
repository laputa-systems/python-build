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
    pub store_fast: OpcodeRange,
    pub pop: u16,
    pub virtual_argument_copy: u16,
    pub float_power: u16,
    pub type_version: u16,
    pub type_version_locked: u16,
    pub int_arithmetic: [u16; 3],
    pub int_inplace_left: [u16; 3],
    pub int_inplace_right: [u16; 3],
    pub guard_tos_int: u16,
    pub guard_nos_int: u16,
    pub overflow_tos: u16,
    pub overflow_nos: u16,
    pub pop_int: u16,
    pub float_add_int: u16,
    pub guard_tos_exact_int: u16,
    pub unary_negative: u16,
    pub unary_negative_float_inplace: u16,
}

impl LeafOpcodes {
    fn virtual_arithmetic(&self, opcode: u16) -> bool {
        self.virtual_kind(opcode).is_some() || opcode == self.unary_negative
            || opcode == self.unary_negative_float_inplace
    }

    fn virtual_kind(&self, opcode: u16) -> Option<ScalarKind> {
        if opcode == self.float_add_int { return Some(ScalarKind::Float) }
        if self.int_arithmetic.contains(&opcode) || self.int_inplace_left.contains(&opcode)
            || self.int_inplace_right.contains(&opcode)
        {
            return Some(ScalarKind::CompactInt);
        }
        ([self.float_add, self.float_subtract, self.float_multiply, self.float_divide, self.float_power].contains(&opcode)
            || self.float_inplace_left.contains(&opcode) || self.float_inplace_right.contains(&opcode))
            .then_some(ScalarKind::Float)
    }

    fn virtual_guard(&self, opcode: u16) -> Option<(ScalarKind, usize)> {
        if opcode == self.guard_tos_exact_int { Some((ScalarKind::ExactInt, 1)) }
        else if opcode == self.guard_tos_float { Some((ScalarKind::Float, 1)) }
        else if opcode == self.guard_nos_float { Some((ScalarKind::Float, 2)) }
        else if opcode == self.guard_tos_int || opcode == self.overflow_tos { Some((ScalarKind::CompactInt, 1)) }
        else if opcode == self.guard_nos_int || opcode == self.overflow_nos { Some((ScalarKind::CompactInt, 2)) }
        else { None }
    }

    fn scalar_guard(&self, kind: ScalarKind, depth: usize) -> u16 {
        match (kind, depth) {
            (ScalarKind::Float, 1) => self.guard_tos_float,
            (ScalarKind::Float, _) => self.guard_nos_float,
            (ScalarKind::ExactInt, 1) => self.guard_tos_exact_int,
            (ScalarKind::ExactInt, _) => unreachable!(),
            (ScalarKind::CompactInt, 1) => self.guard_tos_int,
            (ScalarKind::CompactInt, _) => self.guard_nos_int,
        }
    }

    fn scalar_pop(&self, kind: ScalarKind) -> u16 {
        match kind {
            ScalarKind::Float => self.pop_float,
            ScalarKind::CompactInt | ScalarKind::ExactInt => self.pop_int,
        }
    }
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
/// For a virtual-call begin, the low 32 bits hold the minimum locals-plus
/// extent and the high 32 bits select a standalone (0) or bound (1) call.
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
enum ScalarKind {
    Float,
    CompactInt,
    ExactInt,
}

impl ScalarKind {
    fn implies(self, required: Self) -> bool {
        self == required || (self == Self::CompactInt && required == Self::ExactInt)
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum ScalarOwnership {
    Borrowed,
    BorrowedLocal(u16),
    ArgumentCopy,
    Fresh,
    Shared,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum VirtualValue {
    BorrowedReceiver,
    ManagedReceiver,
    OwnedUnknown,
    BorrowedArgument(u16),
    CopiedArgument(u16),
    BorrowedConstant,
    Scalar(ScalarKind, ScalarOwnership),
    ReallocatedOperand,
    Null,
}

impl VirtualValue {
    fn is_receiver(self) -> bool {
        matches!(self, Self::BorrowedReceiver | Self::ManagedReceiver)
    }

    fn scalar_is_proven(self, kind: ScalarKind, arguments: &[Option<ScalarKind>]) -> bool {
        match self {
            Self::BorrowedArgument(index) | Self::CopiedArgument(index) => arguments[usize::from(index)].is_some_and(|proven| proven.implies(kind)),
            Self::Scalar(proven, _) => proven.implies(kind),
            _ => false,
        }
    }

    fn narrow_scalar(self, kind: ScalarKind, arguments: &mut [Option<ScalarKind>]) -> Option<Self> {
        match self {
            Self::BorrowedArgument(index) | Self::CopiedArgument(index) => {
                let proof = &mut arguments[usize::from(index)];
                let narrowed = match *proof {
                    Some(proven) if proven.implies(kind) => proven,
                    Some(proven) if kind.implies(proven) => kind,
                    Some(_) => return None,
                    None => kind,
                };
                *proof = Some(narrowed);
                Some(Self::Scalar(narrowed, if matches!(self, Self::BorrowedArgument(_)) {
                    ScalarOwnership::Borrowed
                } else { ScalarOwnership::ArgumentCopy }))
            }
            Self::BorrowedConstant => Some(Self::Scalar(kind, ScalarOwnership::Borrowed)),
            Self::OwnedUnknown => Some(Self::Scalar(kind, ScalarOwnership::Shared)),
            Self::Scalar(proven, _) if proven.implies(kind) => Some(self),
            Self::Scalar(proven, ownership) if kind.implies(proven) => Some(Self::Scalar(kind, ownership)),
            Self::Scalar(_, _) => None,
            Self::BorrowedReceiver | Self::ManagedReceiver | Self::ReallocatedOperand | Self::Null => None,
        }
    }
}

struct VirtualChange {
    index: usize,
    opcode: u16,
    oparg: u16,
}

struct VirtualGuard {
    index: usize,
    opcode: u16,
}

struct VirtualBody {
    end: usize,
    changes: Vec<VirtualChange>,
    local_count: u32,
    bound: bool,
    guards: Vec<VirtualGuard>,
}

fn virtual_body(uops: &[LeafUop], at: usize, ops: &LeafOpcodes, bound: bool)
    -> Result<Option<VirtualBody>, Inconsistent>
{
    if !uops.iter().skip(at + 3).take_while(|uop| uop.opcode != ops.return_value)
        .any(|uop| ops.virtual_arithmetic(uop.opcode))
    {
        return Ok(None);
    }
    let argc = u32::from(uops[at].oparg) + u32::from(bound);
    let local_count = uops.iter().skip(at + 3).take_while(|uop| uop.opcode != ops.return_value)
        .filter(|uop| ops.load_fast.contains(uop.opcode) || ops.load_borrow.contains(uop.opcode)
            || ops.store_fast.contains(uop.opcode))
        .map(|uop| usize::from(uop.oparg) + 1).max().unwrap_or(0).max(argc as usize);
    let mut values = Vec::new();
    let mut changes = Vec::new();
    let mut locals = Vec::new();
    let mut arguments = Vec::new();
    let mut guards = Vec::new();
    if values.try_reserve_exact(uops.len()).is_err()
        || changes.try_reserve_exact(uops.len()).is_err()
        || locals.try_reserve_exact(local_count).is_err()
        || arguments.try_reserve_exact(argc as usize).is_err()
        || guards.try_reserve_exact(uops.len().saturating_mul(2)).is_err()
    {
        return Ok(None);
    }
    locals.resize(local_count, None);
    arguments.resize(argc as usize, None);
    let mut resumed = false;
    let mut operations = 0;
    let mut integer_cleanup: u8 = 0;
    for (index, uop) in uops.iter().enumerate().skip(at + 3) {
        let opcode = uop.opcode;
        if integer_cleanup != 0 && ![ops.nop, ops.set_ip, ops.validity,
            ops.pop_borrow, ops.pop_int, ops.pop].contains(&opcode)
        {
            return Ok(None);
        }
        let replacement;
        if [ops.nop, ops.set_ip, ops.validity].contains(&opcode) && uop.effectful == 0 {
            replacement = ops.nop;
        }
        else if opcode == ops.resume && !resumed {
            resumed = true;
            replacement = ops.nop;
        }
        else if !resumed { return Ok(None) }
        else if (ops.load_borrow.contains(opcode) || ops.load_fast.contains(opcode)) && uop.effectful == 0 {
            let borrowed = ops.load_borrow.contains(opcode);
            if u32::from(uop.oparg) < argc {
                if bound && uop.oparg == 0 {
                    if !borrowed { return Ok(None) }
                    values.push(VirtualValue::BorrowedReceiver);
                }
                else {
                    values.push(match (borrowed, arguments[usize::from(uop.oparg)]) {
                        (true, Some(kind)) => VirtualValue::Scalar(kind, ScalarOwnership::Borrowed),
                        (false, Some(kind)) => VirtualValue::Scalar(kind, ScalarOwnership::ArgumentCopy),
                        (true, None) => VirtualValue::BorrowedArgument(uop.oparg),
                        (false, None) => VirtualValue::CopiedArgument(uop.oparg),
                    });
                }
                replacement = if borrowed { ops.virtual_argument } else { ops.virtual_argument_copy };
            }
            else {
                let Some(kind) = locals[usize::from(uop.oparg)] else { return Ok(None) };
                values.push(VirtualValue::Scalar(kind, if borrowed {
                    ScalarOwnership::BorrowedLocal(uop.oparg)
                } else { ScalarOwnership::Shared }));
                replacement = opcode;
            }
        }
        else if [ops.load_const, ops.load_const_borrow].contains(&opcode) && uop.effectful == 0 {
            ResultSource::Constant(uop.operand).encode()?;
            values.push(if opcode == ops.load_const { VirtualValue::OwnedUnknown }
                        else { VirtualValue::BorrowedConstant });
            replacement = opcode;
        }
        else if ops.store_fast.contains(opcode) {
            if u32::from(uop.oparg) < argc { return Ok(None) }
            let Some(value) = values.pop() else { return Err(Inconsistent) };
            let VirtualValue::Scalar(kind, ScalarOwnership::Fresh | ScalarOwnership::Shared) = value else {
                return Ok(None);
            };
            if values.iter().any(|value| matches!(value,
                VirtualValue::Scalar(_, ScalarOwnership::BorrowedLocal(index)) if *index == uop.oparg))
            {
                return Ok(None);
            }
            let local = &mut locals[usize::from(uop.oparg)];
            values.push(local.map_or(VirtualValue::Null, |old_kind| VirtualValue::Scalar(old_kind, ScalarOwnership::Shared)));
            *local = Some(kind);
            replacement = opcode;
        }
        else if let Some((kind, depth)) = ops.virtual_guard(opcode) {
            if uop.effectful != 0 { return Ok(None) }
            if values.len() < depth { return Err(Inconsistent) }
            let position = values.len() - depth;
            let proven = values[position].scalar_is_proven(kind, &arguments);
            if [ops.overflow_tos, ops.overflow_nos].contains(&opcode) && !proven {
                guards.push(VirtualGuard { index, opcode: ops.scalar_guard(kind, depth) });
            }
            replacement = if kind == ScalarKind::Float && proven { ops.nop } else { opcode };
            let Some(narrowed) = values[position].narrow_scalar(kind, &mut arguments) else { return Ok(None) };
            values[position] = narrowed;
        }
        else if [ops.type_version, ops.type_version_locked, ops.managed_values].contains(&opcode)
            && uop.effectful == 0
        {
            let Some(owner) = values.last_mut() else { return Err(Inconsistent) };
            if !owner.is_receiver() { return Ok(None) }
            if opcode == ops.managed_values { *owner = VirtualValue::ManagedReceiver }
            replacement = opcode;
        }
        else if [ops.load_slot, ops.load_instance].contains(&opcode) && uop.effectful == 0 {
            let Some(owner) = values.pop() else { return Err(Inconsistent) };
            if !owner.is_receiver() || (opcode == ops.load_instance && owner != VirtualValue::ManagedReceiver) {
                return Ok(None);
            }
            values.extend([VirtualValue::OwnedUnknown, owner]);
            replacement = opcode;
        }
        else if opcode == ops.unary_negative || opcode == ops.unary_negative_float_inplace {
            let Some(value) = values.pop() else { return Err(Inconsistent) };
            let kind = match value {
                VirtualValue::Scalar(kind, _) => kind,
                VirtualValue::BorrowedArgument(index) | VirtualValue::CopiedArgument(index) => {
                    let Some(kind) = arguments[usize::from(index)] else { return Ok(None) };
                    kind
                }
                _ => return Ok(None),
            };
            let Some(value) = value.narrow_scalar(kind, &mut arguments) else { return Ok(None) };
            if opcode == ops.unary_negative_float_inplace {
                if value != VirtualValue::Scalar(ScalarKind::Float, ScalarOwnership::Fresh) {
                    return Ok(None);
                }
                values.extend([value, VirtualValue::Null]);
            }
            else {
                if u32::try_from(uop.operand).ok().filter(|offset| *offset != 0).is_none() {
                    return Err(Inconsistent);
                }
                values.extend([VirtualValue::Scalar(kind, ScalarOwnership::Fresh), value]);
            }
            replacement = opcode;
            operations += 1;
        }
        else if let Some(kind) = ops.virtual_kind(opcode) {
            if values.len() < 2 || u32::try_from(uop.operand).ok().filter(|offset| *offset != 0).is_none() {
                return Err(Inconsistent);
            }
            for depth in [2, 1] {
                let position = values.len() - depth;
                let required = if opcode == ops.float_add_int && depth == 1 { ScalarKind::ExactInt } else { kind };
                if !values[position].scalar_is_proven(required, &arguments) {
                    guards.push(VirtualGuard { index, opcode: ops.scalar_guard(required, depth) });
                }
                let Some(narrowed) = values[position].narrow_scalar(required, &mut arguments) else { return Ok(None) };
                values[position] = narrowed;
            }
            let mut right = values.pop().unwrap();
            let mut left = values.pop().unwrap();
            let normal = [ops.float_add, ops.float_subtract, ops.float_multiply, ops.float_divide];
            if let Some(operation) = ops.float_inplace_left.iter().position(|op| *op == opcode) {
                if left != VirtualValue::Scalar(kind, ScalarOwnership::Fresh) { return Ok(None) }
                left = VirtualValue::ReallocatedOperand;
                replacement = normal[operation];
            }
            else if let Some(operation) = ops.float_inplace_right.iter().position(|op| *op == opcode) {
                if right != VirtualValue::Scalar(kind, ScalarOwnership::Fresh) { return Ok(None) }
                right = VirtualValue::ReallocatedOperand;
                replacement = normal[operation];
            }
            else {
                if (ops.int_inplace_left.contains(&opcode) && left != VirtualValue::Scalar(kind, ScalarOwnership::Fresh))
                    || (ops.int_inplace_right.contains(&opcode) && right != VirtualValue::Scalar(kind, ScalarOwnership::Fresh))
                { return Ok(None) }
                if ops.int_inplace_left.contains(&opcode) || ops.int_inplace_right.contains(&opcode) {
                    integer_cleanup = 2;
                }
                replacement = opcode;
            }
            values.extend([VirtualValue::Scalar(kind, ScalarOwnership::Fresh), left, right]);
            operations += 1;
        }
        else if [ops.pop_borrow, ops.pop_float, ops.pop_int, ops.pop].contains(&opcode) {
            if opcode == ops.pop_borrow && uop.effectful != 0 { return Ok(None) }
            let Some(value) = values.pop() else { return Err(Inconsistent) };
            replacement = match (opcode, value) {
                (op, VirtualValue::BorrowedReceiver | VirtualValue::ManagedReceiver | VirtualValue::Null |
                    VirtualValue::Scalar(_, ScalarOwnership::Borrowed | ScalarOwnership::BorrowedLocal(_)))
                    if op == ops.pop_borrow || op == ops.pop => ops.pop_borrow,
                (op, VirtualValue::Scalar(kind, _)) if op == ops.scalar_pop(kind) || op == ops.pop => ops.scalar_pop(kind),
                (op, VirtualValue::ReallocatedOperand) if op == ops.pop_borrow || op == ops.pop => ops.pop_float,
                _ => return Ok(None),
            };
            integer_cleanup = integer_cleanup.saturating_sub(1);
        }
        else if opcode == ops.heap_safe {
            let Some(value) = values.last_mut() else { return Err(Inconsistent) };
            let VirtualValue::Scalar(_, ownership) = value else { return Ok(None) };
            if !matches!(ownership, ScalarOwnership::Fresh | ScalarOwnership::Shared) {
                *ownership = ScalarOwnership::Shared;
            }
            replacement = opcode;
        }
        else if opcode == ops.return_value {
            if operations == 0 || values.len() != 1
                || !matches!(values[0], VirtualValue::Scalar(_, ScalarOwnership::Fresh | ScalarOwnership::Shared)) {
                return Ok(None);
            }
            changes.push(VirtualChange { index, opcode: ops.virtual_return, oparg: 0 });
            return Ok(Some(VirtualBody { end: index, changes, local_count: local_count as u32, bound, guards }));
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
/// operation with an error-only frame reconstruction recipe. Other callback-free
/// float bodies use a reserved operand stack and borrowed argument loads, preserving
/// original arithmetic targets. In-place arithmetic and its NULL cleanup are
/// normalized together to boxed arithmetic and owning cleanup. Extra locals hold
/// heap-safe floats or NULL; argument writes and live local-borrow overwrites are
/// rejected. Slot guards must
/// already be discharged by the interpreter optimizer. An unsupported body or
/// allocation failure leaves the trace unchanged. A globals-version guard is
/// preserved before the call, checking the callee's dictionary.
pub fn plan_leaf_calls(uops: &[LeafUop], ops: &LeafOpcodes)
    -> Result<Option<Vec<LeafStep>>, Inconsistent>
{
    if uops.len() > u32::MAX as usize
        || [ops.init, ops.load_fast, ops.load_borrow, ops.store_fast].iter().any(|range| range.first > range.last)
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
    let mut guards = Vec::new();
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
        let virtual_call = match virtual_body(uops, at, ops, false)? {
            Some(body) => Some(body),
            None => virtual_body(uops, at, ops, true)?,
        };
        if let Some(body) = virtual_call {
            if guards.try_reserve_exact(body.guards.len()).is_err() { return Ok(None) }
            guards.extend(body.guards);
            steps[at].opcode = ops.nop;
            steps[at + 2] = LeafStep { opcode: ops.virtual_begin, oparg: uops[at].oparg,
                source: (at - 1) as u32,
                result: u64::from(body.local_count) | (u64::from(body.bound) << 32) };
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
    if !changed { return Ok(None) }
    if guards.is_empty() { return Ok(Some(steps)) }
    let mut expanded = Vec::new();
    if expanded.try_reserve_exact(steps.len().saturating_add(guards.len())).is_err() {
        return Ok(None);
    }
    let mut guards = guards.into_iter().peekable();
    for (index, step) in steps.into_iter().enumerate() {
        while guards.peek().is_some_and(|guard| guard.index == index) {
            let guard = guards.next().unwrap();
            expanded.push(LeafStep { opcode: guard.opcode, oparg: 0, source: index as u32, result: 0 });
        }
        expanded.push(step);
    }
    Ok(Some(expanded))
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
            store_fast: OpcodeRange { first: 72, last: 80 }, pop: 81, virtual_argument_copy: 82,
            float_power: 83, type_version: 84, type_version_locked: 85,
            int_arithmetic: [86, 87, 88], int_inplace_left: [89, 90, 91],
            int_inplace_right: [92, 93, 94], guard_tos_int: 95, guard_nos_int: 96,
            overflow_tos: 97, overflow_nos: 98, pop_int: 100,
            float_add_int: 101, guard_tos_exact_int: 102,
            unary_negative: 103, unary_negative_float_inplace: 104,
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
        assert_eq!(std::mem::size_of::<LeafOpcodes>(), 158);
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
        assert_eq!(plan[4], LeafStep { opcode: ops.virtual_begin, oparg: 2, source: 1, result: 2 });
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
    fn virtual_unary_preserves_proof_ownership_and_error_source() {
        let ops = opcodes();
        for (guard, pop) in [(ops.guard_tos_float, ops.pop_float),
                                  (ops.guard_tos_int, ops.pop_int),
                                  (ops.guard_tos_exact_int, ops.pop_int)] {
            for load in [ops.load_borrow.first, ops.load_fast.first] {
                let mut input = float_call()[..6].to_vec();
                input.extend([uop(load, 0), uop(guard, 0),
                    LeafUop { opcode: ops.unary_negative, oparg: 0, effectful: 1, operand: 9 },
                    uop(if load == ops.load_borrow.first { ops.pop_borrow } else { pop }, 0),
                    uop(ops.heap_safe, 0), uop(ops.return_value, 0)]);
                let body = virtual_body(&input, 2, &ops, false).unwrap().unwrap();
                assert_eq!(body.changes[3].opcode, ops.unary_negative);
                assert_eq!(body.changes[3].index, 8);
                assert_eq!(input[8].operand, 9);
                input[7].opcode = ops.nop;
                assert!(virtual_body(&input, 2, &ops, false).unwrap().is_none());
                input[7].opcode = guard;
                input[8].operand = 0;
                assert!(matches!(virtual_body(&input, 2, &ops, false), Err(Inconsistent)));
            }
        }
        let mut input = float_call()[..14].to_vec();
        input.extend([uop(ops.unary_negative_float_inplace, 0), uop(ops.pop_borrow, 0),
            uop(ops.heap_safe, 0), uop(ops.return_value, 0)]);
        let body = virtual_body(&input, 2, &ops, false).unwrap().unwrap();
        assert_eq!(body.changes[9].opcode, ops.unary_negative_float_inplace);
        assert_eq!(body.changes[10].opcode, ops.pop_borrow);
        input[14].opcode = ops.unary_negative;
        input[14].operand = 11;
        input[15].opcode = ops.pop_float;
        assert!(virtual_body(&input, 2, &ops, false).unwrap().is_some());
        input[14].opcode = ops.unary_negative_float_inplace;
        input[13].opcode = ops.store_fast.first;
        assert!(virtual_body(&input, 2, &ops, false).unwrap().is_none());
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
    fn virtual_locals_preserve_nullable_cleanup_aliases_and_required_extent() {
        let ops = opcodes();
        let input = virtual_locals();
        let plan = plan_leaf_calls(&input, &ops).unwrap().unwrap();
        assert_eq!(plan[4].result, 4);
        assert_eq!(plan[15].opcode, ops.pop_borrow);
        assert_eq!(plan[18].opcode, ops.pop_borrow);
        assert_eq!(plan[27].opcode, ops.pop_float);
        assert_eq!(plan[16].opcode, ops.load_fast.first);
        assert_eq!(plan[19].opcode, ops.load_borrow.first);
        assert_eq!(plan[20].opcode, ops.virtual_argument);
        assert_eq!(plan[28].opcode, ops.load_fast.first);
        assert_eq!(plan[30].opcode, ops.virtual_return);
        let mut large = input.clone();
        for item in &mut large {
            if (ops.load_fast.contains(item.opcode) || ops.load_borrow.contains(item.opcode)
                || ops.store_fast.contains(item.opcode)) && item.oparg >= 2 {
                item.oparg = u16::MAX - (item.oparg - 2);
            }
        }
        assert_eq!(plan_leaf_calls(&large, &ops).unwrap().unwrap()[4].result, 65_536);
    }

    fn virtual_locals() -> Vec<LeafUop> {
        let ops = opcodes();
        let mut input = float_call()[..14].to_vec();
        input.extend([uop(ops.store_fast.first, 2), uop(ops.pop, 0),
            uop(ops.load_fast.first, 2), uop(ops.store_fast.first, 3), uop(ops.pop, 0),
            uop(ops.load_borrow.first, 2), uop(ops.load_borrow.first, 0),
            uop(ops.guard_tos_float, 0), uop(ops.guard_nos_float, 0),
            LeafUop { opcode: ops.float_multiply, oparg: 0, effectful: 1, operand: 7 },
            uop(ops.pop_borrow, 0), uop(ops.pop_borrow, 0),
            uop(ops.store_fast.first, 2), uop(ops.pop, 0), uop(ops.load_fast.first, 3),
            uop(ops.heap_safe, 0), uop(ops.return_value, 0)]);
        input
    }

    #[test]
    fn local_borrows_and_argument_copies_cannot_outlive_their_anchors() {
        let ops = opcodes();
        let mut argument_write = virtual_locals();
        argument_write[14].oparg = 0;
        assert!(plan_leaf_calls(&argument_write, &ops).unwrap().is_none());
        let mut unassigned = virtual_locals();
        unassigned[16].oparg = 4;
        assert!(plan_leaf_calls(&unassigned, &ops).unwrap().is_none());
        let mut borrowed = virtual_locals();
        borrowed.insert(19, uop(ops.load_borrow.first, 2));
        assert!(plan_leaf_calls(&borrowed, &ops).unwrap().is_none());
        let mut shared = virtual_locals();
        shared[16].oparg = 0;
        assert!(plan_leaf_calls(&shared, &ops).unwrap().is_none());
        shared.insert(17, uop(ops.heap_safe, 0));
        let plan = plan_leaf_calls(&shared, &ops).unwrap().unwrap();
        assert_eq!(plan[16].opcode, ops.virtual_argument_copy);
        let mut not_unique = virtual_locals();
        not_unique[19].opcode = ops.load_fast.first;
        not_unique[23].opcode = ops.float_inplace_left[2];
        not_unique[25].opcode = ops.pop_borrow;
        assert!(plan_leaf_calls(&not_unique, &ops).unwrap().is_none());
    }

    #[test]
    fn power_and_repeated_arguments_keep_original_virtual_operand_order() {
        let ops = opcodes();
        let mut power = float_call();
        power[10].opcode = ops.float_power;
        let plan = plan_leaf_calls(&power, &ops).unwrap().unwrap();
        assert_eq!(plan[4].opcode, ops.virtual_begin);
        assert_eq!(plan[10], LeafStep { opcode: ops.float_power, oparg: 0, source: 10, result: 0 });
        assert_eq!(power[10].operand, 3);
        assert_eq!(plan[16].opcode, ops.virtual_return);
        let mut repeated = float_call();
        repeated[7].oparg = 0;
        let repeated_plan = plan_leaf_calls(&repeated, &ops).unwrap().unwrap();
        assert_eq!(repeated_plan[4].opcode, ops.virtual_begin);
        assert_eq!(repeated_plan[6].opcode, ops.virtual_argument);
        assert_eq!(repeated_plan[7].opcode, ops.virtual_argument);
        assert_eq!(repeated_plan[6].oparg, repeated_plan[7].oparg);
        repeated[2].opcode = ops.init.first + 1;
        repeated[2].oparg = 1;
        let single = plan_leaf_calls(&repeated, &ops).unwrap().unwrap();
        assert_eq!(single[4].oparg, 1);
        assert_eq!(single[4].result, 1);
    }

    fn bound_float_call(managed: bool) -> Vec<LeafUop> {
        let ops = opcodes();
        let mut input = float_call()[..6].to_vec();
        input[2].oparg = 1;
        input.push(uop(ops.load_borrow.first, 0));
        input.push(uop(ops.type_version, 0));
        if managed { input.push(uop(ops.managed_values, 0)) }
        input.extend([
            LeafUop { opcode: if managed { ops.load_instance } else { ops.load_slot },
                oparg: 0, effectful: 0, operand: 16 },
            uop(ops.pop_borrow, 0), uop(ops.load_borrow.first, 1),
            uop(ops.guard_nos_float, 0), uop(ops.guard_tos_float, 0),
            LeafUop { opcode: ops.float_add, oparg: 0, effectful: 1, operand: 13 },
            uop(ops.pop_borrow, 0), uop(ops.pop_float, 0),
            uop(ops.heap_safe, 0), uop(ops.return_value, 0)]);
        input
    }

    #[test]
    fn bound_calls_retain_storage_and_attribute_type_guards() {
        let ops = opcodes();
        for managed in [false, true] {
            let input = bound_float_call(managed);
            let plan = plan_leaf_calls(&input, &ops).unwrap().unwrap();
            assert_eq!(plan[4], LeafStep { opcode: ops.virtual_begin, oparg: 1,
                source: 1, result: (1 << 32) | 2 });
            assert_eq!(plan[6].opcode, ops.virtual_argument);
            assert_eq!(plan[7].opcode, ops.type_version);
            for (index, original) in input.iter().enumerate() {
                if [ops.managed_values, ops.load_instance, ops.load_slot, ops.guard_nos_float].contains(&original.opcode) {
                    assert_eq!(plan[index].opcode, original.opcode);
                    assert_eq!(plan[index].source as usize, index);
                }
            }
            let guard = input.iter().position(|uop| uop.opcode == ops.guard_nos_float).unwrap();
            assert_eq!(plan[guard + 1].opcode, ops.guard_tos_float);
            assert_eq!(plan.last().unwrap().opcode, ops.virtual_return);
            let mut unproved = input.clone();
            unproved[guard].opcode = ops.nop;
            let repaired = plan_leaf_calls(&unproved, &ops).unwrap().unwrap();
            assert_eq!(repaired.len(), unproved.len() + 1);
            assert_eq!(repaired[guard + 2], LeafStep { opcode: ops.guard_nos_float,
                oparg: 0, source: (guard + 2) as u32, result: 0 });
            let mut callback = input.clone();
            callback[7].effectful = 1;
            assert!(plan_leaf_calls(&callback, &ops).unwrap().is_none());
            let mut owned = input.clone();
            owned[6].opcode = ops.load_fast.first;
            assert!(plan_leaf_calls(&owned, &ops).unwrap().is_none());
            if managed {
                let mut missing_storage = input.clone();
                missing_storage[8].opcode = ops.nop;
                assert!(plan_leaf_calls(&missing_storage, &ops).unwrap().is_none());
            }
            let mut large = input.clone();
            large[2].oparg = u16::MAX;
            assert_eq!(plan_leaf_calls(&large, &ops).unwrap().unwrap()[4].result,
                       (1 << 32) | 65_536);
        }
    }

    #[test]
    fn constants_and_unproved_arguments_get_original_pc_guards_transactionally() {
        let ops = opcodes();
        for borrowed in [false, true] {
            let mut input = float_call();
            input[2].oparg = 3;
            input[7] = LeafUop { opcode: if borrowed { ops.load_const_borrow } else { ops.load_const },
                oparg: 0, effectful: 0, operand: 0x1000 };
            input[8].opcode = ops.nop;
            input[9].opcode = ops.nop;
            input[11].opcode = if borrowed { ops.pop_borrow } else { ops.pop_float };
            let plan = plan_leaf_calls(&input, &ops).unwrap().unwrap();
            assert_eq!(plan.len(), input.len() + 2);
            assert_eq!(plan[4].result, 3);
            assert_eq!(plan[7].opcode, input[7].opcode);
            assert_eq!(plan[10], LeafStep { opcode: ops.guard_nos_float, oparg: 0, source: 10, result: 0 });
            assert_eq!(plan[11], LeafStep { opcode: ops.guard_tos_float, oparg: 0, source: 10, result: 0 });
            assert_eq!(plan[12].opcode, ops.float_add);
            assert_eq!(plan[12].source, 10);
            assert_eq!(input[10].operand, 3);
            let untouched = LeafStep { opcode: 999, oparg: 99, source: 99, result: 99 };
            let mut output = vec![untouched; plan.len()];
            // SAFETY: separate initialized inputs and writable output match their lengths.
            let refused = unsafe { crate::_PyJitOpt_PlanLeafCalls(input.as_ptr(), input.len(),
                &ops, output.as_mut_ptr(), input.len()) };
            assert_eq!(refused, 0);
            assert!(output.iter().all(|step| *step == untouched));
            // SAFETY: separate initialized inputs and writable output match their lengths.
            let count = unsafe { crate::_PyJitOpt_PlanLeafCalls(input.as_ptr(), input.len(),
                &ops, output.as_mut_ptr(), output.len()) };
            assert_eq!(count as usize, plan.len());
            assert_eq!(output, plan);
            input[7].operand = 3;
            assert!(matches!(plan_leaf_calls(&input, &ops), Err(Inconsistent)));
        }
    }

    #[test]
    fn compact_integer_calls_preserve_range_guards_and_inplace_operand_ownership() {
        let ops = opcodes();
        for right in [false, true] {
            for operation in 0..3 {
                let mut input = float_call()[..14].to_vec();
                input[8].opcode = ops.guard_tos_int;
                input[9].opcode = ops.guard_nos_int;
                input[10].opcode = ops.int_arithmetic[0];
                if right { input.insert(6, uop(ops.load_borrow.first, 0)); }
                else { input.push(uop(ops.load_borrow.first, 0)); }
                input.extend([uop(ops.guard_nos_int, 0), uop(ops.guard_tos_int, 0),
                    uop(ops.overflow_nos, 0), uop(ops.overflow_tos, 0)]);
                let arithmetic = input.len();
                let opcode = if right { ops.int_inplace_right[operation] } else { ops.int_inplace_left[operation] };
                input.extend([LeafUop { opcode, oparg: 0, effectful: 0, operand: 7 },
                    uop(if right { ops.pop_int } else { ops.pop_borrow }, 0),
                    uop(if right { ops.pop_borrow } else { ops.pop_int }, 0),
                    uop(ops.heap_safe, 0), uop(ops.return_value, 0)]);
                let plan = plan_leaf_calls(&input, &ops).unwrap().unwrap();
                assert_eq!(plan.len(), input.len());
                assert_eq!(plan[4].opcode, ops.virtual_begin);
                assert_eq!(plan[arithmetic].opcode, opcode);
                assert_eq!(plan[arithmetic].source as usize, arithmetic);
                for index in arithmetic - 4..arithmetic + 3 {
                    assert_eq!(plan[index].opcode, input[index].opcode);
                }
                let mut shared = input.clone();
                shared[arithmetic].opcode = if right { ops.int_inplace_left[operation] } else { ops.int_inplace_right[operation] };
                assert!(plan_leaf_calls(&shared, &ops).unwrap().is_none());
                let mut interleaved = input.clone();
                interleaved.insert(arithmetic + 1, uop(ops.store_fast.first, 4));
                assert!(plan_leaf_calls(&interleaved, &ops).unwrap().is_none());
            }
        }
    }

    #[test]
    fn unknown_integer_overflow_inputs_get_exact_compact_guards_first() {
        let ops = opcodes();
        let mut input = float_call();
        input[8].opcode = ops.overflow_tos;
        input[9].opcode = ops.overflow_nos;
        input[10].opcode = ops.int_arithmetic[2];
        let plan = plan_leaf_calls(&input, &ops).unwrap().unwrap();
        assert_eq!(plan.len(), input.len() + 2);
        assert_eq!(plan[8], LeafStep { opcode: ops.guard_tos_int, oparg: 0, source: 8, result: 0 });
        assert_eq!(plan[9].opcode, ops.overflow_tos);
        assert_eq!(plan[10], LeafStep { opcode: ops.guard_nos_int, oparg: 0, source: 9, result: 0 });
        assert_eq!(plan[11].opcode, ops.overflow_nos);
        assert_eq!(plan[12].opcode, ops.int_arithmetic[2]);
    }

    #[test]
    fn scalar_local_overwrites_clean_the_old_kind_and_preserve_borrow_anchors() {
        let ops = opcodes();
        let mut input = virtual_locals();
        input[2].oparg = 4;
        input[8].opcode = ops.guard_tos_int;
        input[9].opcode = ops.guard_nos_int;
        input[10].opcode = ops.int_arithmetic[0];
        for index in [14, 16, 19, 26] { input[index].oparg = 4 }
        for index in [17, 28] { input[index].oparg = 5 }
        input[19].oparg = 2;
        input[20].oparg = 3;
        let plan = plan_leaf_calls(&input, &ops).unwrap().unwrap();
        assert_eq!(plan[27].opcode, ops.pop_int);
        assert_eq!(plan[30].opcode, ops.virtual_return);
        let mut dangling = input.clone();
        dangling.insert(19, uop(ops.load_borrow.first, 4));
        assert!(plan_leaf_calls(&dangling, &ops).unwrap().is_none());
        let mut contradictory = input.clone();
        contradictory[21].opcode = ops.guard_tos_int;
        assert!(plan_leaf_calls(&contradictory, &ops).unwrap().is_none());
    }

    #[test]
    fn mixed_add_preserves_integer_proofs_ownership_and_error_target() {
        let ops = opcodes();
        let mut input = float_call();
        input[8].opcode = ops.guard_tos_exact_int;
        input[10].opcode = ops.float_add_int;
        input[11].opcode = ops.pop_borrow;
        let plan = plan_leaf_calls(&input, &ops).unwrap().unwrap();
        assert_eq!(plan[4].opcode, ops.virtual_begin);
        assert_eq!(plan[8].opcode, ops.guard_tos_exact_int);
        assert_eq!(plan[10], LeafStep { opcode: ops.float_add_int, oparg: 0, source: 10, result: 0 });
        assert_eq!(input[10].operand, 3);
        assert_eq!(plan[11].opcode, ops.pop_borrow);
        let mut owned = input.clone();
        owned[7].opcode = ops.load_fast.first;
        owned[11].opcode = ops.pop_int;
        let plan = plan_leaf_calls(&owned, &ops).unwrap().unwrap();
        assert_eq!(plan[11].opcode, ops.pop_int);
        let mut compact = input.clone();
        compact[8].opcode = ops.guard_tos_int;
        let plan = plan_leaf_calls(&compact, &ops).unwrap().unwrap();
        assert_eq!(plan.len(), compact.len());
        assert_eq!(plan[10].opcode, ops.float_add_int);
        let mut chained = virtual_locals();
        chained[8].opcode = ops.guard_tos_int;
        chained[9].opcode = ops.guard_nos_int;
        chained[10].opcode = ops.int_arithmetic[0];
        chained[2].oparg = 3;
        chained[19].oparg = 2;
        chained[20].oparg = 3;
        for index in [14, 16, 26] { chained[index].oparg = 3 }
        for index in [17, 28] { chained[index].oparg = 4 }
        chained[21].opcode = ops.guard_tos_exact_int;
        chained[22].opcode = ops.guard_nos_float;
        chained[23].opcode = ops.float_add_int;
        let plan = plan_leaf_calls(&chained, &ops).unwrap().unwrap();
        assert_eq!(plan.len(), chained.len());
        assert_eq!(plan[23].opcode, ops.float_add_int);
        assert_eq!(plan[24].opcode, ops.pop_borrow);
        let mut unknown = input.clone();
        unknown[8].opcode = ops.nop;
        let plan = plan_leaf_calls(&unknown, &ops).unwrap().unwrap();
        assert_eq!(plan[10], LeafStep { opcode: ops.guard_tos_exact_int, oparg: 0, source: 10, result: 0 });
        assert_eq!(plan[11].source, 10);
        let mut incompatible = input.clone();
        incompatible[8].opcode = ops.guard_tos_float;
        assert!(plan_leaf_calls(&incompatible, &ops).unwrap().is_none());
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
