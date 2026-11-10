pub const KIND_OTHER: u8 = 0;
pub const KIND_NOP: u8 = 1;
pub const KIND_POP: u8 = 2;
pub const KIND_FLOAT_ARITH: u8 = 3;
pub const KIND_FLOAT_COMPARE: u8 = 4;

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
pub const ACTION_UNBOX_TOS: u8 = 1;
pub const ACTION_UNBOX_NOS: u8 = 2;
const ACTION_ARITH_BASE: u8 = 3;
pub const ACTION_COMPARE: u8 = 7;
pub const ACTION_BOX: u8 = 8;
pub const SYNTHETIC_ACTIONS: usize = 8;

pub const MAX_UNBOXED_DEPTH: usize = 32;

pub const fn arith_action(op: u8) -> u8 {
    ACTION_ARITH_BASE + op
}

#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
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
    pub exit_unboxed: u32,
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
enum ArithOp {
    Add,
    Subtract,
    Multiply,
    TrueDivide,
}

impl ArithOp {
    fn code(self) -> u8 {
        match self {
            ArithOp::Add => ARITH_ADD,
            ArithOp::Subtract => ARITH_SUBTRACT,
            ArithOp::Multiply => ARITH_MULTIPLY,
            ArithOp::TrueDivide => ARITH_TRUE_DIVIDE,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Side {
    Left,
    Right,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum NodeKind {
    Arith { op: ArithOp, reuses: Option<Side> },
    Compare,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Kind {
    Other,
    Nop,
    Pop,
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

fn decode(raw: &JitOptUop) -> Result<Uop, Inconsistent> {
    let kind = match (raw.kind, raw.detail) {
        (KIND_OTHER, 0) => Kind::Other,
        (KIND_NOP, 0) => Kind::Nop,
        (KIND_POP, 0) => Kind::Pop,
        (KIND_FLOAT_COMPARE, 0) => Kind::Node(NodeKind::Compare),
        (KIND_FLOAT_ARITH, detail) => {
            let op = match detail & 0b11 {
                ARITH_ADD => ArithOp::Add,
                ARITH_SUBTRACT => ArithOp::Subtract,
                ARITH_MULTIPLY => ArithOp::Multiply,
                _ => ArithOp::TrueDivide,
            };
            let reuses = match detail & !0b11 {
                REUSE_NONE => None,
                REUSE_LEFT => Some(Side::Left),
                REUSE_RIGHT => Some(Side::Right),
                _ => return Err(Inconsistent),
            };
            Kind::Node(NodeKind::Arith { op, reuses })
        }
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
        Kind::Pop => Some((1, 1, 0)),
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
    UnboxTos,
    UnboxNos,
    Arith(ArithOp),
    Compare,
    Box,
}

impl Action {
    const SYNTHETIC: [Action; SYNTHETIC_ACTIONS] = [
        Action::UnboxTos,
        Action::UnboxNos,
        Action::Arith(ArithOp::Add),
        Action::Arith(ArithOp::Subtract),
        Action::Arith(ArithOp::Multiply),
        Action::Arith(ArithOp::TrueDivide),
        Action::Compare,
        Action::Box,
    ];

    fn code(self) -> u8 {
        match self {
            Action::Copy => ACTION_COPY,
            Action::UnboxTos => ACTION_UNBOX_TOS,
            Action::UnboxNos => ACTION_UNBOX_NOS,
            Action::Arith(op) => arith_action(op.code()),
            Action::Compare => ACTION_COMPARE,
            Action::Box => ACTION_BOX,
        }
    }

    fn from_code(code: u8) -> Option<Action> {
        if code == ACTION_COPY {
            return Some(Action::Copy);
        }
        Action::SYNTHETIC.get(usize::from(code).checked_sub(1)?).copied()
    }

    fn contract(self) -> (usize, usize, usize, bool, Option<ErrorStack>) {
        match self {
            Action::Copy => unreachable!("copies keep the traced uop's own shape"),
            Action::UnboxTos => (1, 1, 1, false, None),
            Action::UnboxNos => (2, 2, 2, false, None),
            Action::Arith(ArithOp::TrueDivide) => (2, 2, 1, true, None),
            Action::Arith(_) | Action::Compare => (2, 2, 1, false, None),
            Action::Box => (1, 1, 1, false, Some(ErrorStack::PopsInputs)),
        }
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
        let (inputs, popped, pushed, exits, error) = action.contract();
        let shape = uop.shape.ok_or(Inconsistent)?;
        if uop.kind != Kind::Other
            || (shape.inputs, shape.popped, shape.pushed) != (inputs, popped, pushed)
            || uop.barrier
            || uop.exits != exits
            || uop.exit_at_end
            || uop.error != error
        {
            return Err(Inconsistent);
        }
        *slot = uop;
    }
    Ok(uops)
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
        Kind::Node(_) => depth < 2,
        Kind::Pop => matches!(producer, Producer::Operand(_)),
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
                    NodeKind::Arith { .. } => fresh(&mut values, Producer::Result(node)),
                    NodeKind::Compare => fresh(&mut values, Producer::Other),
                };
                let left = fresh(&mut values, Producer::Operand(node));
                let right = fresh(&mut values, Producer::Operand(node));
                nodes.push(Node {
                    uop: index,
                    kind,
                    left: window[1],
                    right: window[0],
                    result: matches!(kind, NodeKind::Arith { .. }).then_some(result),
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
    if uops[right_pop].kind != Kind::Pop || uops[left_pop].kind != Kind::Pop || right_pop >= left_pop {
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

pub fn plan_float_unboxing(raw: &[JitOptUop], synthetic: &[JitOptUop]) -> Result<Plan, Inconsistent> {
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
        let usable = !flow.values[value].blocked && pops[producer].is_some() && pops[consumer].is_some();
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
        if let NodeKind::Arith { reuses: None, .. } = node.kind {
            boxed_cost[root] += 1;
        }
        if node.result.is_some_and(|result| flow.values[result].consumer.and_then(|(uop, _)| flow.node_at[uop]).and_then(|consumer| eligible(Some(result), consumer)).is_none()) {
            unboxed_cost[root] += 1;
        }
        for side in [Side::Left, Side::Right] {
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
        steps.push(JitOptStep { source: source as u16, action: action.code(), exit_unboxed: 0, error_unboxed: 0 });
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
        if !raw_edge(node.left, node_id) {
            emit(index, Action::UnboxNos);
        }
        if !raw_edge(node.right, node_id) {
            emit(index, Action::UnboxTos);
        }
        match node.kind {
            NodeKind::Arith { op, .. } => {
                emit(index, Action::Arith(op));
                let result = node.result.expect("arithmetic nodes produce a float");
                let feeds_unboxed = flow.values[result]
                    .consumer
                    .and_then(|(uop, _)| flow.node_at[uop])
                    .is_some_and(|consumer| raw_edge(Some(result), consumer));
                if !feeds_unboxed {
                    emit(index, Action::Box);
                }
            }
            NodeKind::Compare => emit(index, Action::Compare),
        }
    }
    verify(&uops, &synthetic, &mut steps)?;
    Ok(Plan::Rewrite(steps))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Slot {
    Boxed,
    Unboxed,
}

fn unboxed_mask(stack: &[Slot]) -> Result<u32, Inconsistent> {
    let mut mask = 0u32;
    for (depth, slot) in stack.iter().rev().enumerate() {
        if *slot == Slot::Unboxed {
            if depth >= MAX_UNBOXED_DEPTH {
                return Err(Inconsistent);
            }
            mask |= 1 << depth;
        }
    }
    Ok(mask)
}

fn verify(uops: &[Uop], synthetic: &[Uop; SYNTHETIC_ACTIONS], steps: &mut [JitOptStep]) -> Result<(), Inconsistent> {
    let mut stack: Vec<Slot> = Vec::new();
    for step in steps.iter_mut() {
        let action = Action::from_code(step.action).ok_or(Inconsistent)?;
        let uop = match action {
            Action::Copy => *uops.get(usize::from(step.source)).ok_or(Inconsistent)?,
            _ => synthetic[usize::from(step.action) - 1],
        };
        let has_unboxed = stack.contains(&Slot::Unboxed);
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
            Action::UnboxTos => {
                expect(0, Slot::Boxed)?;
                vec![Slot::Unboxed]
            }
            Action::UnboxNos => {
                expect(1, Slot::Boxed)?;
                vec![Slot::Unboxed, at(0)]
            }
            Action::Arith(_) => {
                expect(0, Slot::Unboxed)?;
                expect(1, Slot::Unboxed)?;
                vec![Slot::Unboxed]
            }
            Action::Compare => {
                expect(0, Slot::Unboxed)?;
                expect(1, Slot::Unboxed)?;
                vec![Slot::Boxed]
            }
            Action::Box => {
                expect(0, Slot::Unboxed)?;
                vec![Slot::Boxed]
            }
        };
        if outputs.len() != shape.pushed {
            return Err(Inconsistent);
        }
        if uop.exits && !uop.exit_at_end {
            step.exit_unboxed = unboxed_mask(&stack)?;
        }
        match uop.error {
            Some(ErrorStack::KeepsInputs) => step.error_unboxed = unboxed_mask(&stack)?,
            Some(ErrorStack::PopsInputs) => {
                let kept = stack.len().saturating_sub(shape.popped);
                step.error_unboxed = unboxed_mask(&stack[..kept])?;
            }
            None => {}
        }
        stack.truncate(stack.len().saturating_sub(shape.popped));
        stack.extend(outputs);
        if uop.exits && uop.exit_at_end {
            step.exit_unboxed = unboxed_mask(&stack)?;
        }
    }
    if stack.contains(&Slot::Unboxed) {
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

    fn compare() -> JitOptUop {
        uop(KIND_FLOAT_COMPARE, 0, (2, 2, 3), 0b11, 0)
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
        let binary = uop(KIND_OTHER, 0, (2, 2, 1), 0b11, 0);
        [
            uop(KIND_OTHER, 0, (1, 1, 1), 1, 0),
            uop(KIND_OTHER, 0, (2, 2, 2), 0b11, 0),
            binary,
            binary,
            binary,
            uop(KIND_OTHER, 0, (2, 2, 1), 0b11, EFFECT_EXIT),
            binary,
            uop(KIND_OTHER, 0, (1, 1, 1), 1, EFFECT_ERROR),
        ]
    }

    fn steps(trace: &[JitOptUop]) -> Vec<(u16, u8)> {
        match plan_float_unboxing(trace, &synthetic()).expect("consistent trace") {
            Plan::Unchanged => Vec::new(),
            Plan::Rewrite(steps) => steps.iter().map(|step| (step.source, step.action)).collect(),
        }
    }

    fn rewrite(trace: &[JitOptUop]) -> Vec<JitOptStep> {
        match plan_float_unboxing(trace, &synthetic()).expect("consistent trace") {
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

    const MUL: u8 = arith_action(ARITH_MULTIPLY);
    const ADD: u8 = arith_action(ARITH_ADD);

    #[test]
    fn merging_two_temporaries_boxes_only_the_sum() {
        assert_eq!(
            steps(&sum_of_products()),
            vec![
                (0, ACTION_COPY),
                (1, ACTION_COPY),
                (2, ACTION_UNBOX_NOS),
                (2, ACTION_UNBOX_TOS),
                (2, MUL),
                (5, ACTION_COPY),
                (6, ACTION_COPY),
                (7, ACTION_UNBOX_NOS),
                (7, ACTION_UNBOX_TOS),
                (7, MUL),
                (10, ADD),
                (10, ACTION_BOX),
                (13, ACTION_COPY),
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
                (0, ACTION_COPY),
                (1, ACTION_COPY),
                (2, ACTION_UNBOX_NOS),
                (2, ACTION_UNBOX_TOS),
                (2, MUL),
                (5, ACTION_COPY),
                (6, ACTION_UNBOX_TOS),
                (6, ACTION_COMPARE),
                (9, ACTION_COPY),
            ]
        );
    }

    #[test]
    fn a_temporary_live_across_an_escaping_uop_stays_boxed() {
        let mut trace = sum_of_products();
        trace.insert(6, escaping_call());
        assert_eq!(steps(&trace), Vec::new());
    }

    #[test]
    fn a_temporary_read_by_another_uop_stays_boxed() {
        let mut trace = sum_of_products();
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
        assert_eq!(guard.exit_unboxed, 0b100);
        assert!(plan.iter().filter(|step| step.source != 7).all(|step| step.exit_unboxed == 0));
    }

    #[test]
    fn an_exit_after_its_pops_counts_slots_from_the_new_top() {
        let mut trace = sum_of_products();
        let mut branch = uop(KIND_OTHER, 0, (1, 1, 0), 1, EFFECT_EXIT);
        branch.exit_at_end = 1;
        trace.splice(5..5, [load(), branch]);
        let plan = rewrite(&trace);
        let branch = plan.iter().find(|step| step.source == 6).expect("branch is copied");
        assert_eq!(branch.exit_unboxed, 0b1);
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
        let boxes: Vec<_> = plan.iter().filter(|step| step.action == ACTION_BOX).collect();
        assert_eq!(boxes.len(), 1);
        assert_eq!(boxes[0].source, 15);
        assert_eq!(boxes[0].error_unboxed, 0b1);
        assert!(plan.iter().any(|step| step.source == 19 && step.action == ACTION_COMPARE));
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
            .find(|step| step.action == arith_action(ARITH_TRUE_DIVIDE))
            .expect("division is unboxed");
        assert_eq!(divide.exit_unboxed, 0b11);
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
            steps(&trace).iter().filter(|(_, action)| *action == MUL).count(),
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
    fn contradictory_descriptors_are_rejected() {
        let mut trace = sum_of_products();
        trace[2] = uop(KIND_FLOAT_ARITH, ARITH_MULTIPLY, (2, 2, 1), 0b11, 0);
        assert_eq!(plan_float_unboxing(&trace, &synthetic()), Err(Inconsistent));
        let mut wrong = synthetic();
        wrong[7].effects = EFFECT_ERROR | EFFECT_ERROR_NO_POP;
        assert_eq!(plan_float_unboxing(&sum_of_products(), &wrong), Err(Inconsistent));
    }

    #[test]
    fn ffi_layouts_match_the_c_glue() {
        assert_eq!(std::mem::size_of::<JitOptUop>(), 8);
        assert_eq!(std::mem::size_of::<JitOptStep>(), 12);
        assert_eq!(std::mem::offset_of!(JitOptStep, exit_unboxed), 4);
        assert_eq!(std::mem::offset_of!(JitOptStep, error_unboxed), 8);
    }
}
