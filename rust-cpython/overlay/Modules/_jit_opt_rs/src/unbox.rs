pub const KIND_OTHER: u8 = 0;
pub const KIND_NOP: u8 = 1;
pub const KIND_POP: u8 = 2;
pub const KIND_FLOAT_ARITH: u8 = 3;
pub const KIND_FLOAT_COMPARE: u8 = 4;
pub const KIND_INT_ARITH: u8 = 5;
pub const KIND_INT_COMPARE: u8 = 6;
pub const KIND_FLOAT_ARRAY_READ: u8 = 7;
pub const POP_OTHER: u8 = 0;
pub const POP_BORROWED: u8 = 1;
pub const POP_INT: u8 = 2;

pub const ARITH_ADD: u8 = 0;
pub const ARITH_SUBTRACT: u8 = 1;
pub const ARITH_MULTIPLY: u8 = 2;
pub const ARITH_TRUE_DIVIDE: u8 = 3;
pub const REUSE_NONE: u8 = 0;
pub const REUSE_LEFT: u8 = 1 << 2;
pub const REUSE_RIGHT: u8 = 2 << 2;

pub const EFFECT_BARRIER: u8 = 1;
pub const EFFECT_EXIT: u8 = 2;
pub const EFFECT_ERROR: u8 = 4;
pub const EFFECT_ERROR_NO_POP: u8 = 8;

pub const ACTION_COPY: u8 = 0;
pub const SYNTHETIC_ACTIONS: usize = 16;

pub const MAX_UNBOXED_DEPTH: usize = 32;

#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
/// `KIND_POP` uses `POP_OTHER`, `POP_BORROWED`, or `POP_INT` in `detail`.
/// Array-read descriptors retain boxed inputs and the original error stack.
pub struct JitOptUop {
    pub kind: u8,
    pub detail: u8,
    pub inputs: i8,
    pub popped: i8,
    pub pushed: i8,
    pub read: u8,
    pub effects: u8,
    pub exit_at_end: u8,
}

#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct JitOptStep {
    pub source: u16,
    pub action: u8,
    pub exit_floats: u32,
    pub exit_ints: u32,
    pub error_unboxed: u32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Inconsistent;

#[derive(Debug, PartialEq, Eq)]
pub enum Plan {
    Unchanged,
    Rewrite(Vec<JitOptStep>),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Domain {
    Float,
    Int,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ArithOp {
    Add,
    Subtract,
    Multiply,
    TrueDivide,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Side {
    Left,
    Right,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum NodeKind {
    Arith { domain: Domain, op: ArithOp, reuses: Option<Side> },
    Compare { domain: Domain },
    FloatArrayRead,
}

impl NodeKind {
    fn input_domain(self) -> Option<Domain> {
        match self {
            Self::FloatArrayRead => None,
            Self::Arith { domain, .. } | Self::Compare { domain } => Some(domain),
        }
    }

    fn domain(self) -> Domain {
        match self {
            NodeKind::Arith { domain, .. } | NodeKind::Compare { domain } => domain,
            NodeKind::FloatArrayRead => Domain::Float,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum PopKind { Other, Borrowed, Int }

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Kind {
    Other,
    Nop,
    Pop(PopKind),
    Node(NodeKind),
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Shape {
    inputs: usize,
    popped: usize,
    pushed: usize,
    read: u8,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ErrorStack {
    PopsInputs,
    KeepsInputs,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Uop {
    kind: Kind,
    shape: Option<Shape>,
    barrier: bool,
    exits: bool,
    exit_at_end: bool,
    error: Option<ErrorStack>,
}

fn decode_arith(domain: Domain, detail: u8) -> Result<NodeKind, Inconsistent> {
    let op = match (domain, detail & 0b11) {
        (_, ARITH_ADD) => ArithOp::Add,
        (_, ARITH_SUBTRACT) => ArithOp::Subtract,
        (_, ARITH_MULTIPLY) => ArithOp::Multiply,
        (Domain::Float, _) => ArithOp::TrueDivide,
        (Domain::Int, _) => return Err(Inconsistent),
    };
    let reuses = match detail & !0b11 {
        REUSE_NONE => None,
        REUSE_LEFT => Some(Side::Left),
        REUSE_RIGHT => Some(Side::Right),
        _ => return Err(Inconsistent),
    };
    Ok(NodeKind::Arith { domain, op, reuses })
}

fn decode(raw: &JitOptUop) -> Result<Uop, Inconsistent> {
    let kind = match (raw.kind, raw.detail) {
        (KIND_OTHER, 0) => Kind::Other,
        (KIND_NOP, 0) => Kind::Nop,
        (KIND_POP, POP_OTHER) => Kind::Pop(PopKind::Other),
        (KIND_POP, POP_BORROWED) => Kind::Pop(PopKind::Borrowed),
        (KIND_POP, POP_INT) => Kind::Pop(PopKind::Int),
        (KIND_FLOAT_ARRAY_READ, 0) => Kind::Node(NodeKind::FloatArrayRead),
        (KIND_FLOAT_COMPARE, 0) => Kind::Node(NodeKind::Compare { domain: Domain::Float }),
        (KIND_INT_COMPARE, 0) => Kind::Node(NodeKind::Compare { domain: Domain::Int }),
        (KIND_FLOAT_ARITH, detail) => Kind::Node(decode_arith(Domain::Float, detail)?),
        (KIND_INT_ARITH, detail) => Kind::Node(decode_arith(Domain::Int, detail)?),
        _ => return Err(Inconsistent),
    };
    let shape = match (raw.inputs, raw.popped, raw.pushed) {
        (-1, -1, -1) => None,
        (inputs, popped, pushed) => {
            let inputs = usize::try_from(inputs).map_err(|_| Inconsistent)?;
            let popped = usize::try_from(popped).map_err(|_| Inconsistent)?;
            let pushed = usize::try_from(pushed).map_err(|_| Inconsistent)?;
            if inputs > 8 || popped > inputs {
                return Err(Inconsistent);
            }
            Some(Shape { inputs, popped, pushed, read: raw.read })
        }
    };
    let expected = match kind {
        Kind::Nop => Some((0, 0, 0)),
        Kind::Pop(_) => Some((1, 1, 0)),
        Kind::Node(_) => Some((2, 2, 3)),
        Kind::Other => None,
    };
    if let Some((inputs, popped, pushed)) = expected {
        match shape {
            Some(found) if (found.inputs, found.popped, found.pushed) == (inputs, popped, pushed) => {}
            _ => return Err(Inconsistent),
        }
    }
    if raw.effects & !(EFFECT_BARRIER | EFFECT_EXIT | EFFECT_ERROR | EFFECT_ERROR_NO_POP) != 0
        || raw.exit_at_end > 1
    {
        return Err(Inconsistent);
    }
    let error = match (raw.effects & EFFECT_ERROR != 0, raw.effects & EFFECT_ERROR_NO_POP != 0) {
        (false, false) => None,
        (true, false) => Some(ErrorStack::PopsInputs),
        (true, true) => Some(ErrorStack::KeepsInputs),
        (false, true) => return Err(Inconsistent),
    };
    Ok(Uop {
        kind,
        shape,
        barrier: raw.effects & EFFECT_BARRIER != 0,
        exits: raw.effects & EFFECT_EXIT != 0,
        exit_at_end: raw.exit_at_end != 0,
        error,
    })
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Action {
    Copy,
    UnboxTos(Domain),
    UnboxNos(Domain),
    Arith(Domain, ArithOp),
    Compare(Domain),
    Box(Domain),
    FloatArrayRead,
}

struct Contract {
    shape: (usize, usize, usize),
    exits: bool,
    error: Option<ErrorStack>,
}

impl Action {
    const SYNTHETIC: [Action; SYNTHETIC_ACTIONS] = [
        Action::UnboxTos(Domain::Float),
        Action::UnboxNos(Domain::Float),
        Action::Arith(Domain::Float, ArithOp::Add),
        Action::Arith(Domain::Float, ArithOp::Subtract),
        Action::Arith(Domain::Float, ArithOp::Multiply),
        Action::Arith(Domain::Float, ArithOp::TrueDivide),
        Action::Compare(Domain::Float),
        Action::Box(Domain::Float),
        Action::UnboxTos(Domain::Int),
        Action::UnboxNos(Domain::Int),
        Action::Arith(Domain::Int, ArithOp::Add),
        Action::Arith(Domain::Int, ArithOp::Subtract),
        Action::Arith(Domain::Int, ArithOp::Multiply),
        Action::Compare(Domain::Int),
        Action::Box(Domain::Int),
        Action::FloatArrayRead,
    ];

    fn code(self) -> u8 {
        match self {
            Action::Copy => ACTION_COPY,
            synthetic => {
                let index = Action::SYNTHETIC
                    .iter()
                    .position(|&action| action == synthetic)
                    .expect("every non-copy action has a synthetic uop");
                index as u8 + 1
            }
        }
    }

    fn from_code(code: u8) -> Option<Action> {
        if code == ACTION_COPY {
            return Some(Action::Copy);
        }
        Action::SYNTHETIC.get(usize::from(code).checked_sub(1)?).copied()
    }

    fn contract(self) -> Contract {
        let (shape, exits, error) = match self {
            Action::Copy => unreachable!("copies keep the traced uop's own shape"),
            Action::UnboxTos(_) => ((1, 1, 1), false, None),
            Action::UnboxNos(_) => ((2, 2, 2), false, None),
            Action::Arith(Domain::Float, ArithOp::TrueDivide) | Action::Arith(Domain::Int, _) => ((2, 2, 1), true, None),
            Action::Arith(Domain::Float, _) | Action::Compare(_) => ((2, 2, 1), false, None),
            Action::Box(_) => ((1, 1, 1), false, Some(ErrorStack::PopsInputs)),
            Action::FloatArrayRead => ((2, 2, 1), true, None),
        };
        Contract { shape, exits, error }
    }
}

fn decode_synthetic(raw: &[JitOptUop]) -> Result<[Uop; SYNTHETIC_ACTIONS], Inconsistent> {
    if raw.len() != SYNTHETIC_ACTIONS {
        return Err(Inconsistent);
    }
    let mut uops = [Uop {
        kind: Kind::Other,
        shape: None,
        barrier: false,
        exits: false,
        exit_at_end: false,
        error: None,
    }; SYNTHETIC_ACTIONS];
    for (slot, (action, raw)) in uops.iter_mut().zip(Action::SYNTHETIC.iter().zip(raw)) {
        let uop = decode(raw)?;
        let contract = action.contract();
        let shape = uop.shape.ok_or(Inconsistent)?;
        if uop.kind != Kind::Other
            || (shape.inputs, shape.popped, shape.pushed) != contract.shape
            || uop.barrier
            || uop.exits != contract.exits
            || uop.exit_at_end
            || uop.error != contract.error
        {
            return Err(Inconsistent);
        }
        *slot = uop;
    }
    Ok(uops)
}

#[cfg(test)]
pub(crate) fn synthetic_descriptors() -> [JitOptUop; SYNTHETIC_ACTIONS] {
    Action::SYNTHETIC.map(|action| {
        let contract = action.contract();
        let (inputs, popped, pushed) = contract.shape;
        let effects = if contract.exits { EFFECT_EXIT } else { 0 } | if contract.error.is_some() { EFFECT_ERROR } else { 0 };
        JitOptUop {
            kind: KIND_OTHER,
            detail: 0,
            inputs: inputs as i8,
            popped: popped as i8,
            pushed: pushed as i8,
            read: (1 << inputs) - 1,
            effects,
            exit_at_end: 0,
        }
    })
}

type ValueId = usize;
type NodeId = usize;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Producer {
    Result(NodeId),
    Operand(NodeId),
    Other,
}

#[derive(Clone, Copy, Debug)]
struct Value {
    producer: Producer,
    consumer: Option<(usize, usize)>,
    blocked: bool,
}

#[derive(Clone, Copy, Debug)]
struct Node {
    uop: usize,
    kind: NodeKind,
    left: Option<ValueId>,
    right: Option<ValueId>,
    result: Option<ValueId>,
    operands: [ValueId; 2],
}

impl Node {
    fn input(&self, side: Side) -> Option<ValueId> {
        match side {
            Side::Left => self.left,
            Side::Right => self.right,
        }
    }

    fn reuses(&self, side: Side) -> bool {
        matches!(self.kind, NodeKind::Arith { reuses: Some(reused), .. } if reused == side)
    }
}

struct Dataflow {
    values: Vec<Value>,
    nodes: Vec<Node>,
    node_at: Vec<Option<NodeId>>,
}

fn consumes_in_place(kind: Kind, producer: Producer, depth: usize) -> bool {
    match kind {
        Kind::Node(kind) => kind.input_domain().is_some() && depth < 2,
        Kind::Pop(_) => matches!(producer, Producer::Operand(_)),
        Kind::Other | Kind::Nop => false,
    }
}

fn fresh(values: &mut Vec<Value>, producer: Producer) -> ValueId {
    values.push(Value { producer, consumer: None, blocked: false });
    values.len() - 1
}

fn dataflow(uops: &[Uop]) -> Dataflow {
    let mut values: Vec<Value> = Vec::new();
    let mut nodes: Vec<Node> = Vec::new();
    let mut node_at = vec![None; uops.len()];
    let mut stack: Vec<Option<ValueId>> = Vec::new();
    for (index, uop) in uops.iter().enumerate() {
        let Some(shape) = uop.shape else {
            for value in stack.drain(..).flatten() {
                values[value].blocked = true;
            }
            continue;
        };
        if uop.barrier {
            for &value in stack.iter().flatten() {
                values[value].blocked = true;
            }
        }
        let window: Vec<Option<ValueId>> = (0..shape.inputs)
            .map(|depth| stack.len().checked_sub(depth + 1).and_then(|slot| stack[slot]))
            .collect();
        for (depth, value) in window.iter().enumerate() {
            let Some(value) = *value else { continue };
            if depth < shape.popped {
                values[value].consumer = Some((index, depth));
                if !consumes_in_place(uop.kind, values[value].producer, depth) {
                    values[value].blocked = true;
                }
            } else if shape.read & (1 << depth) != 0 {
                values[value].blocked = true;
            }
        }
        stack.truncate(stack.len().saturating_sub(shape.popped));
        match uop.kind {
            Kind::Node(kind) => {
                let node = nodes.len();
                let result = match kind {
                    NodeKind::Arith { .. } | NodeKind::FloatArrayRead => fresh(&mut values, Producer::Result(node)),
                    NodeKind::Compare { .. } => fresh(&mut values, Producer::Other),
                };
                let left = fresh(&mut values, Producer::Operand(node));
                let right = fresh(&mut values, Producer::Operand(node));
                nodes.push(Node {
                    uop: index,
                    kind,
                    left: kind.input_domain().and(window[1]),
                    right: kind.input_domain().and(window[0]),
                    result: matches!(kind, NodeKind::Arith { .. } | NodeKind::FloatArrayRead).then_some(result),
                    operands: [left, right],
                });
                node_at[index] = Some(node);
                stack.extend([Some(result), Some(left), Some(right)]);
            }
            _ => {
                for _ in 0..shape.pushed {
                    let value = fresh(&mut values, Producer::Other);
                    stack.push(Some(value));
                }
            }
        }
        let deep = stack.len().saturating_sub(MAX_UNBOXED_DEPTH);
        for &value in stack[..deep].iter().flatten() {
            values[value].blocked = true;
        }
    }
    for &value in stack.iter().flatten() {
        values[value].blocked = true;
    }
    Dataflow { values, nodes, node_at }
}

fn operand_pops(uops: &[Uop], flow: &Dataflow, node: &Node) -> Option<[usize; 2]> {
    let [left, right] = node.operands;
    let (right_pop, 0) = flow.values[right].consumer? else { return None };
    let (left_pop, 0) = flow.values[left].consumer? else { return None };
    if !matches!(uops[right_pop].kind, Kind::Pop(_)) || !matches!(uops[left_pop].kind, Kind::Pop(_)) || right_pop >= left_pop {
        return None;
    }
    if matches!(node.kind, NodeKind::FloatArrayRead) &&
        (!matches!(uops[right_pop].kind, Kind::Pop(PopKind::Borrowed | PopKind::Int)) ||
         uops[left_pop].kind != Kind::Pop(PopKind::Borrowed) ||
         uops[right_pop].barrier || uops[left_pop].barrier) {
        return None;
    }
    let mut between = (node.uop + 1..left_pop).filter(|&index| index != right_pop);
    if between.any(|index| uops[index].kind != Kind::Nop) {
        return None;
    }
    Some([right_pop, left_pop])
}

fn find(parent: &mut [NodeId], node: NodeId) -> NodeId {
    let mut root = node;
    while parent[root] != root {
        root = parent[root];
    }
    let mut current = node;
    while parent[current] != root {
        let next = parent[current];
        parent[current] = root;
        current = next;
    }
    root
}

pub fn plan_unboxing(raw: &[JitOptUop], synthetic: &[JitOptUop]) -> Result<Plan, Inconsistent> {
    let uops = raw.iter().map(decode).collect::<Result<Vec<_>, _>>()?;
    let synthetic = decode_synthetic(synthetic)?;
    if uops.len() > usize::from(u16::MAX) {
        return Ok(Plan::Unchanged);
    }
    let flow = dataflow(&uops);
    let pops: Vec<Option<[usize; 2]>> = flow.nodes.iter().map(|node| operand_pops(&uops, &flow, node)).collect();

    let producer_node = |value: ValueId| match flow.values[value].producer {
        Producer::Result(node) => Some(node),
        Producer::Operand(_) | Producer::Other => None,
    };
    let eligible = |value: Option<ValueId>, consumer: NodeId| -> Option<NodeId> {
        let value = value?;
        let producer = producer_node(value)?;
        let usable = !flow.values[value].blocked
            && flow.nodes[consumer].kind.input_domain() == Some(flow.nodes[producer].kind.domain())
            && pops[producer].is_some()
            && pops[consumer].is_some();
        usable.then_some(producer)
    };

    let mut parent: Vec<NodeId> = (0..flow.nodes.len()).collect();
    let mut linked = vec![false; flow.nodes.len()];
    for (consumer, node) in flow.nodes.iter().enumerate() {
        for side in [Side::Left, Side::Right] {
            if let Some(producer) = eligible(node.input(side), consumer) {
                let (a, b) = (find(&mut parent, producer), find(&mut parent, consumer));
                parent[a] = b;
                linked[producer] = true;
                linked[consumer] = true;
            }
        }
    }

    let unique = |value: Option<ValueId>| value.is_some_and(|value| producer_node(value).is_some());
    let mut boxed_cost = vec![0usize; flow.nodes.len()];
    let mut unboxed_cost = vec![0usize; flow.nodes.len()];
    for (index, node) in flow.nodes.iter().enumerate() {
        if !linked[index] {
            continue;
        }
        let root = find(&mut parent, index);
        if matches!(node.kind, NodeKind::Arith { reuses: None, .. } | NodeKind::FloatArrayRead) {
            boxed_cost[root] += 1;
        }
        if node.result.is_some_and(|result| flow.values[result].consumer.and_then(|(uop, _)| flow.node_at[uop]).and_then(|consumer| eligible(Some(result), consumer)).is_none()) {
            unboxed_cost[root] += 1;
        }
        for side in [Side::Left, Side::Right] {
            if node.kind.input_domain().is_none() { continue; }
            let input = node.input(side);
            if !node.reuses(side) && unique(input) {
                boxed_cost[root] += 1;
            }
            if eligible(input, index).is_none() && (unique(input) || node.reuses(side)) {
                unboxed_cost[root] += 1;
            }
        }
    }
    let unboxed: Vec<bool> = (0..flow.nodes.len())
        .map(|index| {
            linked[index] && {
                let root = find(&mut parent, index);
                unboxed_cost[root] < boxed_cost[root]
            }
        })
        .collect();
    if !unboxed.iter().any(|&chosen| chosen) {
        return Ok(Plan::Unchanged);
    }
    let raw_edge = |value: Option<ValueId>, consumer: NodeId| {
        unboxed[consumer] && eligible(value, consumer).is_some_and(|producer| unboxed[producer])
    };

    let mut removed = vec![false; uops.len()];
    for (index, chosen) in unboxed.iter().enumerate() {
        if *chosen {
            for pop in pops[index].expect("an unboxed node has its operand pops") {
                removed[pop] = true;
            }
        }
    }
    let mut steps = Vec::with_capacity(uops.len() + flow.nodes.len());
    let mut emit = |source: usize, action: Action| {
        steps.push(JitOptStep { source: source as u16, action: action.code(), ..JitOptStep::default() });
    };
    for (index, uop) in uops.iter().enumerate() {
        if removed[index] {
            continue;
        }
        let Some(node_id) = flow.node_at[index].filter(|&node| unboxed[node]) else {
            emit(index, Action::Copy);
            continue;
        };
        let node = &flow.nodes[node_id];
        debug_assert!(matches!(uop.kind, Kind::Node(_)));
        let domain = node.kind.domain();
        if let Some(input_domain) = node.kind.input_domain() {
            if !raw_edge(node.left, node_id) {
                emit(index, Action::UnboxNos(input_domain));
            }
            if !raw_edge(node.right, node_id) {
                emit(index, Action::UnboxTos(input_domain));
            }
        }
        match node.kind {
            NodeKind::Arith { .. } | NodeKind::FloatArrayRead => {
                match node.kind {
                    NodeKind::Arith { op, .. } => emit(index, Action::Arith(domain, op)),
                    NodeKind::FloatArrayRead => emit(index, Action::FloatArrayRead),
                    _ => unreachable!(),
                }
                let result = node.result.expect("arithmetic nodes produce a number");
                let feeds_unboxed = flow.values[result]
                    .consumer
                    .and_then(|(uop, _)| flow.node_at[uop])
                    .is_some_and(|consumer| raw_edge(Some(result), consumer));
                if !feeds_unboxed {
                    emit(index, Action::Box(domain));
                }
            }
            NodeKind::Compare { .. } => emit(index, Action::Compare(domain)),
        }
    }
    verify(&uops, &synthetic, &mut steps)?;
    Ok(Plan::Rewrite(steps))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Slot {
    Boxed,
    Unboxed(Domain),
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
struct Masks {
    floats: u32,
    ints: u32,
}

impl Masks {
    fn union(self) -> u32 {
        self.floats | self.ints
    }
}

fn unboxed_masks(stack: &[Slot]) -> Result<Masks, Inconsistent> {
    let mut masks = Masks::default();
    for (depth, slot) in stack.iter().rev().enumerate() {
        let Slot::Unboxed(domain) = *slot else { continue };
        if depth >= MAX_UNBOXED_DEPTH {
            return Err(Inconsistent);
        }
        match domain {
            Domain::Float => masks.floats |= 1 << depth,
            Domain::Int => masks.ints |= 1 << depth,
        }
    }
    Ok(masks)
}

fn verify(uops: &[Uop], synthetic: &[Uop; SYNTHETIC_ACTIONS], steps: &mut [JitOptStep]) -> Result<(), Inconsistent> {
    let mut stack: Vec<Slot> = Vec::new();
    for step in steps.iter_mut() {
        let action = Action::from_code(step.action).ok_or(Inconsistent)?;
        let uop = match action {
            Action::Copy => *uops.get(usize::from(step.source)).ok_or(Inconsistent)?,
            _ => synthetic[usize::from(step.action) - 1],
        };
        let has_unboxed = stack.iter().any(|slot| *slot != Slot::Boxed);
        let Some(shape) = uop.shape else {
            if has_unboxed {
                return Err(Inconsistent);
            }
            stack.clear();
            continue;
        };
        if uop.barrier && has_unboxed {
            return Err(Inconsistent);
        }
        let at = |depth: usize| stack.len().checked_sub(depth + 1).map_or(Slot::Boxed, |slot| stack[slot]);
        let expect = |depth: usize, slot: Slot| if at(depth) == slot { Ok(()) } else { Err(Inconsistent) };
        let outputs: Vec<Slot> = match action {
            Action::Copy => {
                for depth in 0..shape.inputs {
                    if depth < shape.popped || shape.read & (1 << depth) != 0 {
                        expect(depth, Slot::Boxed)?;
                    }
                }
                vec![Slot::Boxed; shape.pushed]
            }
            Action::UnboxTos(domain) => {
                expect(0, Slot::Boxed)?;
                vec![Slot::Unboxed(domain)]
            }
            Action::UnboxNos(domain) => {
                expect(1, Slot::Boxed)?;
                vec![Slot::Unboxed(domain), at(0)]
            }
            Action::Arith(domain, _) => {
                expect(0, Slot::Unboxed(domain))?;
                expect(1, Slot::Unboxed(domain))?;
                vec![Slot::Unboxed(domain)]
            }
            Action::Compare(domain) => {
                expect(0, Slot::Unboxed(domain))?;
                expect(1, Slot::Unboxed(domain))?;
                vec![Slot::Boxed]
            }
            Action::FloatArrayRead => {
                expect(0, Slot::Boxed)?;
                expect(1, Slot::Boxed)?;
                vec![Slot::Unboxed(Domain::Float)]
            }
            Action::Box(domain) => {
                expect(0, Slot::Unboxed(domain))?;
                vec![Slot::Boxed]
            }
        };
        if outputs.len() != shape.pushed {
            return Err(Inconsistent);
        }
        if uop.exits && !uop.exit_at_end {
            let masks = unboxed_masks(&stack)?;
            (step.exit_floats, step.exit_ints) = (masks.floats, masks.ints);
        }
        match uop.error {
            Some(ErrorStack::KeepsInputs) => step.error_unboxed = unboxed_masks(&stack)?.union(),
            Some(ErrorStack::PopsInputs) => {
                let kept = stack.len().saturating_sub(shape.popped);
                step.error_unboxed = unboxed_masks(&stack[..kept])?.union();
            }
            None => {}
        }
        stack.truncate(stack.len().saturating_sub(shape.popped));
        stack.extend(outputs);
        if uop.exits && uop.exit_at_end {
            let masks = unboxed_masks(&stack)?;
            (step.exit_floats, step.exit_ints) = (masks.floats, masks.ints);
        }
    }
    if stack.iter().any(|slot| *slot != Slot::Boxed) {
        return Err(Inconsistent);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn uop(kind: u8, detail: u8, shape: (i8, i8, i8), read: u8, effects: u8) -> JitOptUop {
        JitOptUop { kind, detail, inputs: shape.0, popped: shape.1, pushed: shape.2, read, effects, exit_at_end: 0 }
    }

    fn load() -> JitOptUop {
        uop(KIND_OTHER, 0, (0, 0, 1), 0, 0)
    }

    fn arith(op: u8, reuse: u8) -> JitOptUop {
        uop(KIND_FLOAT_ARITH, op | reuse, (2, 2, 3), 0b11, EFFECT_ERROR | EFFECT_ERROR_NO_POP)
    }

    fn int_arith(op: u8, reuse: u8) -> JitOptUop {
        uop(KIND_INT_ARITH, op | reuse, (2, 2, 3), 0b11, EFFECT_EXIT)
    }

    fn compare() -> JitOptUop {
        uop(KIND_FLOAT_COMPARE, 0, (2, 2, 3), 0b11, 0)
    }

    fn int_compare() -> JitOptUop {
        uop(KIND_INT_COMPARE, 0, (2, 2, 3), 0b11, 0)
    }

    fn pop() -> JitOptUop {
        uop(KIND_POP, 0, (1, 1, 0), 1, 0)
    }

    fn guard_nos() -> JitOptUop {
        uop(KIND_OTHER, 0, (2, 0, 0), 0b10, EFFECT_EXIT)
    }

    fn consume(effects: u8) -> JitOptUop {
        uop(KIND_OTHER, 0, (1, 1, 1), 1, effects)
    }

    fn escaping_call() -> JitOptUop {
        uop(KIND_OTHER, 0, (1, 1, 1), 1, EFFECT_BARRIER | EFFECT_ERROR)
    }

    fn synthetic() -> [JitOptUop; SYNTHETIC_ACTIONS] {
        synthetic_descriptors()
    }

    fn steps(trace: &[JitOptUop]) -> Vec<(u16, Action)> {
        match plan_unboxing(trace, &synthetic()).expect("consistent trace") {
            Plan::Unchanged => Vec::new(),
            Plan::Rewrite(steps) => steps
                .iter()
                .map(|step| (step.source, Action::from_code(step.action).expect("planned actions decode")))
                .collect(),
        }
    }

    fn rewrite(trace: &[JitOptUop]) -> Vec<JitOptStep> {
        match plan_unboxing(trace, &synthetic()).expect("consistent trace") {
            Plan::Unchanged => panic!("expected a rewrite"),
            Plan::Rewrite(steps) => steps,
        }
    }

    fn sum_of_products() -> Vec<JitOptUop> {
        vec![
            load(),
            load(),
            arith(ARITH_MULTIPLY, REUSE_NONE),
            pop(),
            pop(),
            load(),
            load(),
            arith(ARITH_MULTIPLY, REUSE_NONE),
            pop(),
            pop(),
            arith(ARITH_ADD, REUSE_LEFT),
            pop(),
            pop(),
            consume(0),
        ]
    }

    fn int_sum_of_products() -> Vec<JitOptUop> {
        vec![
            load(),
            load(),
            int_arith(ARITH_ADD, REUSE_NONE),
            pop(),
            pop(),
            load(),
            load(),
            int_arith(ARITH_ADD, REUSE_NONE),
            pop(),
            pop(),
            int_arith(ARITH_MULTIPLY, REUSE_LEFT),
            pop(),
            pop(),
            consume(0),
        ]
    }

    use Action::{Arith, Box as BoxValue, Compare, Copy, UnboxNos, UnboxTos};
    use Domain::{Float, Int};

    fn array_products(index_pop: u8) -> Vec<JitOptUop> {
        let read = uop(KIND_FLOAT_ARRAY_READ, 0, (2, 2, 3), 3, EFFECT_EXIT | EFFECT_ERROR | EFFECT_ERROR_NO_POP);
        let cleanup = |detail| uop(KIND_POP, detail, (1, 1, 0), 1, 0);
        let mut trace = Vec::new();
        for pair in 0..2 {
            for _ in 0..2 {
                trace.extend([load(), load(), read, cleanup(index_pop), cleanup(POP_BORROWED)]);
            }
            trace.extend([arith(ARITH_MULTIPLY, REUSE_NONE), pop(), pop()]);
            if pair == 1 { trace.extend([arith(ARITH_ADD, REUSE_LEFT), pop(), pop(), consume(0)]); }
        }
        trace
    }

    #[test]
    fn borrowed_array_reads_feed_float_products_and_record_exit_prefixes() {
        for detail in [POP_BORROWED, POP_INT] {
            let plan = rewrite(&array_products(detail));
            let reads: Vec<_> = plan.iter().filter(|s| s.action == Action::FloatArrayRead.code()).collect();
            assert_eq!(reads.iter().map(|s| s.source).collect::<Vec<_>>(), vec![2, 7, 15, 20]);
            assert_eq!(reads.iter().map(|s| s.exit_floats).collect::<Vec<_>>(), vec![0, 4, 4, 12]);
            assert_eq!(Action::FloatArrayRead.code(), 16);
        }
    }

    #[test]
    fn array_reads_require_borrowed_cleanup_and_do_not_accept_raw_inputs() {
        let mut trace = array_products(POP_INT);
        trace[4].detail = POP_OTHER;
        trace[4].effects = EFFECT_BARRIER;
        let plan = rewrite(&trace);
        assert!(!plan.iter().any(|s| s.source == 2 && s.action == Action::FloatArrayRead.code()));
        let mut trace = array_products(POP_INT);
        trace[3].detail = POP_OTHER;
        let plan = rewrite(&trace);
        assert!(!plan.iter().any(|s| s.source == 2 && s.action == Action::FloatArrayRead.code()));
        let trace = vec![load(), load(), arith(ARITH_MULTIPLY, REUSE_NONE), pop(), pop(), load(),
                         uop(KIND_FLOAT_ARRAY_READ, 0, (2, 2, 3), 3, EFFECT_EXIT | EFFECT_ERROR | EFFECT_ERROR_NO_POP),
                         uop(KIND_POP, POP_INT, (1, 1, 0), 1, 0),
                         uop(KIND_POP, POP_BORROWED, (1, 1, 0), 1, 0), consume(0)];
        assert_eq!(steps(&trace), Vec::new());
        let single = &array_products(POP_INT)[..5];
        assert_eq!(steps(single), Vec::new());
    }

    #[test]
    fn malformed_array_descriptors_refuse_the_plan() {
        let mut trace = array_products(POP_INT);
        trace[2].detail = 1;
        assert_eq!(plan_unboxing(&trace, &synthetic()), Err(Inconsistent));
        trace[2].detail = 0;
        trace[3].detail = 3;
        assert_eq!(plan_unboxing(&trace, &synthetic()), Err(Inconsistent));
        let mut wrong = synthetic();
        wrong[15].effects = 0;
        assert_eq!(plan_unboxing(&array_products(POP_INT), &wrong), Err(Inconsistent));
    }

    #[test]
    fn merging_two_temporaries_boxes_only_the_sum() {
        assert_eq!(
            steps(&sum_of_products()),
            vec![
                (0, Copy),
                (1, Copy),
                (2, UnboxNos(Float)),
                (2, UnboxTos(Float)),
                (2, Arith(Float, ArithOp::Multiply)),
                (5, Copy),
                (6, Copy),
                (7, UnboxNos(Float)),
                (7, UnboxTos(Float)),
                (7, Arith(Float, ArithOp::Multiply)),
                (10, Arith(Float, ArithOp::Add)),
                (10, BoxValue(Float)),
                (13, Copy),
            ]
        );
    }

    #[test]
    fn integer_temporaries_merge_like_floats() {
        assert_eq!(
            steps(&int_sum_of_products()),
            vec![
                (0, Copy),
                (1, Copy),
                (2, UnboxNos(Int)),
                (2, UnboxTos(Int)),
                (2, Arith(Int, ArithOp::Add)),
                (5, Copy),
                (6, Copy),
                (7, UnboxNos(Int)),
                (7, UnboxTos(Int)),
                (7, Arith(Int, ArithOp::Add)),
                (10, Arith(Int, ArithOp::Multiply)),
                (10, BoxValue(Int)),
                (13, Copy),
            ]
        );
    }

    #[test]
    fn an_integer_overflow_exit_boxes_both_raw_operands() {
        let plan = rewrite(&int_sum_of_products());
        let multiply = plan
            .iter()
            .find(|step| step.action == Arith(Int, ArithOp::Multiply).code())
            .expect("the product is unboxed");
        assert_eq!((multiply.exit_floats, multiply.exit_ints), (0, 0b11));
        let first_add = plan
            .iter()
            .find(|step| step.action == Arith(Int, ArithOp::Add).code())
            .expect("the first sum is unboxed");
        assert_eq!((first_add.exit_floats, first_add.exit_ints), (0, 0b11));
    }

    #[test]
    fn an_integer_result_never_feeds_a_float_operation() {
        let trace = vec![
            load(),
            load(),
            int_arith(ARITH_ADD, REUSE_NONE),
            pop(),
            pop(),
            load(),
            arith(ARITH_MULTIPLY, REUSE_NONE),
            pop(),
            pop(),
            consume(0),
        ];
        assert_eq!(steps(&trace), Vec::new());
    }

    #[test]
    fn integer_division_is_contradictory() {
        let mut trace = int_sum_of_products();
        trace[2] = int_arith(ARITH_TRUE_DIVIDE, REUSE_NONE);
        assert_eq!(plan_unboxing(&trace, &synthetic()), Err(Inconsistent));
    }

    #[test]
    fn an_exit_records_floats_and_ints_in_their_own_masks() {
        let trace = vec![
            load(),
            load(),
            arith(ARITH_MULTIPLY, REUSE_NONE),
            pop(),
            pop(),
            load(),
            load(),
            int_arith(ARITH_ADD, REUSE_NONE),
            pop(),
            pop(),
            load(),
            load(),
            guard_nos(),
            int_arith(ARITH_ADD, REUSE_NONE),
            pop(),
            pop(),
            int_arith(ARITH_MULTIPLY, REUSE_LEFT),
            pop(),
            pop(),
            consume(0),
            arith(ARITH_ADD, REUSE_NONE),
            pop(),
            pop(),
            consume(0),
        ];
        let plan = rewrite(&trace);
        let guard = plan.iter().find(|step| step.source == 12).expect("guard is copied");
        assert_eq!((guard.exit_floats, guard.exit_ints), (0b1000, 0b100));
        let boxes: Vec<_> = plan
            .iter()
            .filter_map(|step| match Action::from_code(step.action) {
                Some(BoxValue(domain)) => Some((step.source, domain)),
                _ => None,
            })
            .collect();
        assert_eq!(boxes, vec![(16, Int), (20, Float)]);
    }

    #[test]
    fn a_compared_integer_temporary_is_never_boxed() {
        let trace = vec![
            load(),
            load(),
            int_arith(ARITH_ADD, REUSE_NONE),
            pop(),
            pop(),
            load(),
            int_compare(),
            pop(),
            pop(),
            consume(0),
        ];
        assert_eq!(
            steps(&trace),
            vec![
                (0, Copy),
                (1, Copy),
                (2, UnboxNos(Int)),
                (2, UnboxTos(Int)),
                (2, Arith(Int, ArithOp::Add)),
                (5, Copy),
                (6, UnboxTos(Int)),
                (6, Compare(Int)),
                (9, Copy),
            ]
        );
    }

    #[test]
    fn a_chain_that_reuses_its_temporary_stays_boxed() {
        let trace = vec![
            load(),
            load(),
            arith(ARITH_MULTIPLY, REUSE_NONE),
            pop(),
            pop(),
            load(),
            arith(ARITH_ADD, REUSE_LEFT),
            pop(),
            pop(),
            consume(0),
        ];
        assert_eq!(steps(&trace), Vec::new());
    }

    #[test]
    fn a_compared_temporary_is_never_boxed() {
        let trace = vec![
            load(),
            load(),
            arith(ARITH_MULTIPLY, REUSE_NONE),
            pop(),
            pop(),
            load(),
            compare(),
            pop(),
            pop(),
            consume(0),
        ];
        assert_eq!(
            steps(&trace),
            vec![
                (0, Copy),
                (1, Copy),
                (2, UnboxNos(Float)),
                (2, UnboxTos(Float)),
                (2, Arith(Float, ArithOp::Multiply)),
                (5, Copy),
                (6, UnboxTos(Float)),
                (6, Compare(Float)),
                (9, Copy),
            ]
        );
    }

    #[test]
    fn a_temporary_live_across_an_escaping_uop_stays_boxed() {
        let mut trace = sum_of_products();
        trace.insert(6, escaping_call());
        assert_eq!(steps(&trace), Vec::new());
        let mut trace = int_sum_of_products();
        trace.insert(6, escaping_call());
        assert_eq!(steps(&trace), Vec::new());
    }

    #[test]
    fn a_temporary_read_by_another_uop_stays_boxed() {
        let mut trace = sum_of_products();
        trace.insert(10, uop(KIND_OTHER, 0, (2, 0, 0), 0b11, EFFECT_EXIT));
        assert_eq!(steps(&trace), Vec::new());
        let mut trace = int_sum_of_products();
        trace.insert(10, uop(KIND_OTHER, 0, (2, 0, 0), 0b11, EFFECT_EXIT));
        assert_eq!(steps(&trace), Vec::new());
    }

    #[test]
    fn a_side_exit_over_a_live_temporary_records_its_stack_slot() {
        let mut trace = sum_of_products();
        trace.insert(7, guard_nos());
        let plan = rewrite(&trace);
        let guard = plan.iter().find(|step| step.source == 7).expect("guard is copied");
        assert_eq!(guard.action, ACTION_COPY);
        assert_eq!((guard.exit_floats, guard.exit_ints), (0b100, 0));
        assert!(plan.iter().filter(|step| step.source != 7).all(|step| step.exit_floats == 0));
    }

    #[test]
    fn an_exit_after_its_pops_counts_slots_from_the_new_top() {
        let mut trace = sum_of_products();
        let mut branch = uop(KIND_OTHER, 0, (1, 1, 0), 1, EFFECT_EXIT);
        branch.exit_at_end = 1;
        trace.splice(5..5, [load(), branch]);
        let plan = rewrite(&trace);
        let branch = plan.iter().find(|step| step.source == 6).expect("branch is copied");
        assert_eq!(branch.exit_floats, 0b1);
    }

    #[test]
    fn an_error_below_a_live_temporary_records_the_slots_left_after_its_pops() {
        let mut trace = sum_of_products();
        trace.insert(7, consume(EFFECT_ERROR));
        let plan = rewrite(&trace);
        let failing = plan.iter().find(|step| step.source == 7).expect("uop is copied");
        assert_eq!(failing.error_unboxed, 0b10);
        let mut kept = sum_of_products();
        kept.insert(7, consume(EFFECT_ERROR | EFFECT_ERROR_NO_POP));
        let plan = rewrite(&kept);
        let failing = plan.iter().find(|step| step.source == 7).expect("uop is copied");
        assert_eq!(failing.error_unboxed, 0b100);
    }

    #[test]
    fn boxing_a_result_records_unboxed_slots_below_it() {
        let trace = vec![
            load(),
            load(),
            arith(ARITH_MULTIPLY, REUSE_NONE),
            pop(),
            pop(),
            load(),
            load(),
            arith(ARITH_MULTIPLY, REUSE_NONE),
            pop(),
            pop(),
            load(),
            load(),
            arith(ARITH_MULTIPLY, REUSE_NONE),
            pop(),
            pop(),
            arith(ARITH_ADD, REUSE_LEFT),
            pop(),
            pop(),
            consume(0),
            compare(),
            pop(),
            pop(),
            consume(0),
        ];
        let plan = rewrite(&trace);
        let boxes: Vec<_> = plan.iter().filter(|step| step.action == BoxValue(Float).code()).collect();
        assert_eq!(boxes.len(), 1);
        assert_eq!(boxes[0].source, 15);
        assert_eq!(boxes[0].error_unboxed, 0b1);
        assert!(plan.iter().any(|step| step.source == 19 && step.action == Compare(Float).code()));
    }

    #[test]
    fn dividing_records_both_unboxed_operands_for_its_deopt() {
        let trace = vec![
            load(),
            load(),
            arith(ARITH_MULTIPLY, REUSE_NONE),
            pop(),
            pop(),
            load(),
            load(),
            arith(ARITH_MULTIPLY, REUSE_NONE),
            pop(),
            pop(),
            arith(ARITH_TRUE_DIVIDE, REUSE_LEFT),
            pop(),
            pop(),
            consume(0),
        ];
        let plan = rewrite(&trace);
        let divide = plan
            .iter()
            .find(|step| step.action == Arith(Float, ArithOp::TrueDivide).code())
            .expect("division is unboxed");
        assert_eq!(divide.exit_floats, 0b11);
    }

    #[test]
    fn temporaries_deeper_than_the_mask_stay_boxed() {
        let chain = |spacer: usize| {
            let mut trace = vec![load(), load(), arith(ARITH_MULTIPLY, REUSE_NONE), pop(), pop()];
            trace.extend(std::iter::repeat_n(load(), spacer));
            trace.extend(std::iter::repeat_n(pop(), spacer));
            trace.extend([load(), load(), arith(ARITH_MULTIPLY, REUSE_NONE), pop(), pop()]);
            trace.extend([arith(ARITH_ADD, REUSE_LEFT), pop(), pop(), consume(0)]);
            trace
        };
        assert!(!steps(&chain(MAX_UNBOXED_DEPTH - 1)).is_empty());
        assert_eq!(steps(&chain(MAX_UNBOXED_DEPTH)), Vec::new());
    }

    #[test]
    fn operand_pops_must_follow_the_node() {
        let mut trace = sum_of_products();
        trace.insert(9, load());
        trace.insert(10, pop());
        assert_eq!(
            steps(&trace).iter().filter(|(_, action)| *action == Arith(Float, ArithOp::Multiply)).count(),
            0
        );
    }

    #[test]
    fn variable_shaped_uops_end_tracking() {
        let mut trace = sum_of_products();
        trace.insert(6, uop(KIND_OTHER, 0, (-1, -1, -1), 0, 0));
        assert_eq!(steps(&trace), Vec::new());
    }

    #[test]
    fn opaque_regions_keep_live_prefixes_boxed_and_independent_chains_unboxed() {
        let mut trace = sum_of_products();
        trace.pop();
        let begin = trace.len();
        trace.extend([uop(KIND_OTHER, 0, (-1, -1, -1), 0, EFFECT_BARRIER); 5]);
        let after = trace.len();
        trace.extend(int_sum_of_products());
        let plan = rewrite(&trace);
        assert!(plan.iter().any(|step| usize::from(step.source) < begin
            && step.action == Action::Arith(Domain::Float, ArithOp::Add).code()));
        assert!(plan.iter().any(|step| usize::from(step.source) < begin
            && step.action == Action::Box(Domain::Float).code()));
        assert!(plan.iter().any(|step| usize::from(step.source) >= after
            && step.action == Action::Arith(Domain::Int, ArithOp::Multiply).code()));
        let opaque: Vec<_> = plan.iter().filter(|step| (begin..after).contains(&usize::from(step.source))).collect();
        assert_eq!(opaque.len(), 5);
        assert!(opaque.iter().all(|step| step.action == ACTION_COPY
            && (step.exit_floats | step.exit_ints | step.error_unboxed) == 0));
    }

    #[test]
    fn virtual_boundaries_box_float_body_and_track_resume_and_error_masks() {
        let barrier = uop(KIND_OTHER, 0, (-1, -1, -1), 0, EFFECT_BARRIER);
        let mut body = sum_of_products();
        body.insert(7, guard_nos());
        body.insert(8, consume(EFFECT_ERROR));
        let mut trace = vec![barrier];
        trace.extend(body);
        trace.push(barrier);
        let plan = rewrite(&trace);
        assert!(plan.iter().any(|s| s.action == Arith(Float, ArithOp::Multiply).code()));
        assert!(plan.iter().any(|s| s.exit_floats != 0));
        assert!(plan.iter().any(|s| s.error_unboxed != 0));
        for boundary in [0, trace.len() - 1] {
            let step = plan.iter().find(|s| usize::from(s.source) == boundary).unwrap();
            assert_eq!((step.action, step.exit_floats, step.exit_ints, step.error_unboxed), (ACTION_COPY, 0, 0, 0));
        }
    }

    #[test]
    fn virtual_boundaries_box_integer_body_and_track_overflow_masks() {
        let barrier = uop(KIND_OTHER, 0, (-1, -1, -1), 0, EFFECT_BARRIER);
        let mut trace = vec![barrier];
        trace.extend(int_sum_of_products());
        trace.push(barrier);
        let plan = rewrite(&trace);
        assert!(plan.iter().any(|s| s.action == Arith(Int, ArithOp::Multiply).code() && s.exit_ints != 0));
        assert!(plan.iter().any(|s| s.action == BoxValue(Int).code()));
        for boundary in [0, trace.len() - 1] {
            let step = plan.iter().find(|s| usize::from(s.source) == boundary).unwrap();
            assert_eq!((step.action, step.exit_floats, step.exit_ints, step.error_unboxed), (ACTION_COPY, 0, 0, 0));
        }
    }

    #[test]
    fn contradictory_descriptors_are_rejected() {
        let mut trace = sum_of_products();
        trace[2] = uop(KIND_FLOAT_ARITH, ARITH_MULTIPLY, (2, 2, 1), 0b11, 0);
        assert_eq!(plan_unboxing(&trace, &synthetic()), Err(Inconsistent));
        let mut wrong = synthetic();
        wrong[7].effects = EFFECT_ERROR | EFFECT_ERROR_NO_POP;
        assert_eq!(plan_unboxing(&sum_of_products(), &wrong), Err(Inconsistent));
        let mut wrong = synthetic();
        wrong[Action::SYNTHETIC.iter().position(|&a| a == Arith(Int, ArithOp::Add)).unwrap()].effects = 0;
        assert_eq!(plan_unboxing(&int_sum_of_products(), &wrong), Err(Inconsistent));
    }

    #[test]
    fn action_codes_round_trip() {
        for action in Action::SYNTHETIC {
            assert_eq!(Action::from_code(action.code()), Some(action));
        }
        assert_eq!(Action::from_code(ACTION_COPY), Some(Copy));
        assert_eq!(Action::from_code(SYNTHETIC_ACTIONS as u8 + 1), None);
    }

    #[test]
    fn ffi_layouts_match_the_c_glue() {
        assert_eq!(std::mem::size_of::<JitOptUop>(), 8);
        assert_eq!(std::mem::offset_of!(JitOptUop, kind), 0);
        assert_eq!(std::mem::offset_of!(JitOptUop, detail), 1);
        assert_eq!(std::mem::offset_of!(JitOptUop, inputs), 2);
        assert_eq!(std::mem::offset_of!(JitOptUop, popped), 3);
        assert_eq!(std::mem::offset_of!(JitOptUop, pushed), 4);
        assert_eq!(std::mem::offset_of!(JitOptUop, read), 5);
        assert_eq!(std::mem::offset_of!(JitOptUop, effects), 6);
        assert_eq!(std::mem::offset_of!(JitOptUop, exit_at_end), 7);
        assert_eq!(std::mem::size_of::<JitOptStep>(), 16);
        assert_eq!(std::mem::offset_of!(JitOptStep, source), 0);
        assert_eq!(std::mem::offset_of!(JitOptStep, action), 2);
        assert_eq!(std::mem::offset_of!(JitOptStep, exit_floats), 4);
        assert_eq!(std::mem::offset_of!(JitOptStep, exit_ints), 8);
        assert_eq!(std::mem::offset_of!(JitOptStep, error_unboxed), 12);
    }
}
