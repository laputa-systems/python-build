use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::mem::{offset_of, size_of};

use _jit_opt_rs::{Inconsistent, LocalOpcodes, LocalStep, LocalUop, OpcodeRange, plan_loop_types};

const CAPTURED_FIELDS: &[&str] = &[
    "start",
    "warm",
    "jump",
    "nop",
    "set_ip",
    "validity",
    "periodic",
    "resume",
    "load.first",
    "load.last",
    "borrow.first",
    "borrow.last",
    "store.first",
    "store.last",
    "small_int.first",
    "small_int.last",
    "copy.first",
    "copy.last",
    "swap.first",
    "swap.last",
    "bit_set.first",
    "bit_set.last",
    "bit_unset.first",
    "bit_unset.last",
    "constant",
    "constant_borrow",
    "pop",
    "pop_borrow",
    "pop_int",
    "pop_float",
    "pop_unicode",
    "tos_float",
    "nos_float",
    "tos_int",
    "nos_int",
    "tos_exact_int",
    "tos_overflow",
    "nos_overflow",
    "float_binary[0]",
    "float_binary[1]",
    "float_binary[2]",
    "float_binary[3]",
    "int_binary[0]",
    "int_binary[1]",
    "int_binary[2]",
    "float_compare",
    "int_compare",
    "bool_bool",
    "bool_int",
    "iter_check",
    "iter_exhaustion",
    "iter_next",
    "globals",
    "builtins",
    "entry_globals",
    "local_int",
    "local_float",
    "push_null",
];

const CANDIDATE_FIELDS: &[&str] = &[
    "start",
    "warm",
    "jump",
    "nop",
    "set_ip",
    "validity",
    "periodic",
    "resume",
    "load.first",
    "load.last",
    "borrow.first",
    "borrow.last",
    "store.first",
    "store.last",
    "small_int.first",
    "small_int.last",
    "copy.first",
    "copy.last",
    "swap.first",
    "swap.last",
    "bit_set.first",
    "bit_set.last",
    "bit_unset.first",
    "bit_unset.last",
    "constant",
    "constant_borrow",
    "pop",
    "pop_borrow",
    "pop_int",
    "pop_float",
    "pop_unicode",
    "tos_float",
    "nos_float",
    "tos_int",
    "nos_int",
    "tos_exact_int",
    "tos_overflow",
    "nos_overflow",
    "float_binary[0]",
    "float_binary[1]",
    "float_binary[2]",
    "float_binary[3]",
    "float_binary_inplace[0]",
    "float_binary_inplace[1]",
    "float_binary_inplace[2]",
    "float_binary_inplace[3]",
    "float_binary_inplace[4]",
    "float_binary_inplace[5]",
    "float_binary_inplace[6]",
    "float_binary_inplace[7]",
    "int_binary[0]",
    "int_binary[1]",
    "int_binary[2]",
    "float_compare",
    "int_compare",
    "bool_bool",
    "bool_int",
    "iter_check",
    "iter_exhaustion",
    "iter_next",
    "globals",
    "builtins",
    "entry_globals",
    "local_int",
    "local_float",
    "push_null",
    "guard_nos_object_type_version",
    "subscr_array_d",
    "subscr_array_i",
];

struct Trace {
    label: String,
    code: String,
    filename: String,
    co_firstlineno: usize,
    source_line: usize,
    length: usize,
    initial_depth: usize,
    locals: usize,
    planner_return: isize,
    uops: Vec<LocalUop>,
    uop_names: Vec<String>,
    local_kinds: Vec<u16>,
    opcode_fields: Vec<String>,
    opcode_words: Vec<u16>,
}

fn required<'a>(fields: &'a HashMap<String, String>, key: &str) -> &'a str {
    fields
        .get(key)
        .unwrap_or_else(|| panic!("missing fixture field {key}"))
}

fn parse_numbers(value: &str) -> Vec<u16> {
    value.split(',').map(|part| part.parse().unwrap()).collect()
}

fn parse_trace(label: String, fields: HashMap<String, String>) -> Trace {
    let uops = required(&fields, "uops")
        .split(';')
        .map(|entry| {
            let values = parse_numbers(entry);
            assert_eq!(values.len(), 3);
            LocalUop {
                opcode: values[0],
                oparg: values[1],
                constant_kind: values[2],
            }
        })
        .collect::<Vec<_>>();
    let trace = Trace {
        label,
        code: required(&fields, "code").to_owned(),
        filename: required(&fields, "filename").to_owned(),
        co_firstlineno: required(&fields, "co_firstlineno").parse().unwrap(),
        source_line: required(&fields, "source_line").parse().unwrap(),
        length: required(&fields, "length").parse().unwrap(),
        initial_depth: required(&fields, "initial_depth").parse().unwrap(),
        locals: required(&fields, "locals").parse().unwrap(),
        planner_return: required(&fields, "planner_return").parse().unwrap(),
        uops,
        uop_names: required(&fields, "uop_names")
            .split(',')
            .map(str::to_owned)
            .collect(),
        local_kinds: parse_numbers(required(&fields, "local_kinds")),
        opcode_fields: required(&fields, "opcode_fields")
            .split(',')
            .map(str::to_owned)
            .collect(),
        opcode_words: parse_numbers(required(&fields, "opcode_words")),
    };
    assert_eq!(trace.uops.len(), trace.length, "{} uops", trace.label);
    assert_eq!(trace.uop_names.len(), trace.length, "{} names", trace.label);
    assert_eq!(
        trace.local_kinds.len(),
        trace.locals,
        "{} locals",
        trace.label
    );
    assert_eq!(trace.opcode_fields.len(), trace.opcode_words.len());
    trace
}

fn parse_fixture(input: &str) -> Vec<Trace> {
    let mut traces = Vec::new();
    let mut label = None;
    let mut fields = HashMap::new();
    for line in input
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
    {
        if line == "end" {
            traces.push(parse_trace(
                label.take().expect("trace label"),
                std::mem::take(&mut fields),
            ));
            continue;
        }
        let (key, value) = line.split_once('\t').expect("tab-separated fixture line");
        match key {
            "trace" => {
                assert!(label.is_none(), "nested trace block");
                label = Some(value.to_owned());
            }
            _ => {
                assert!(label.is_some(), "field outside trace block");
                assert!(
                    fields.insert(key.to_owned(), value.to_owned()).is_none(),
                    "duplicate field {key}"
                );
            }
        }
    }
    assert!(label.is_none(), "unterminated trace block");
    traces
}

fn word(trace: &Trace, name: &str) -> u16 {
    let index = trace
        .opcode_fields
        .iter()
        .position(|field| field == name)
        .unwrap_or_else(|| panic!("missing captured opcode field {name}"));
    trace.opcode_words[index]
}

fn range(trace: &Trace, name: &str) -> OpcodeRange {
    OpcodeRange {
        first: word(trace, &format!("{name}.first")),
        last: word(trace, &format!("{name}.last")),
    }
}

fn captured_uop_id(traces: &[Trace], name: &str) -> Option<u16> {
    let mut found = None;
    for trace in traces {
        for (uop_name, uop) in trace.uop_names.iter().zip(&trace.uops) {
            if uop_name == name {
                assert!(
                    found.is_none_or(|value| value == uop.opcode),
                    "opcode id changed for {name}"
                );
                found = Some(uop.opcode);
            }
        }
    }
    found
}

fn opcodes_from_capture(traces: &[Trace]) -> LocalOpcodes {
    assert!(!traces.is_empty());
    let trace = &traces[0];
    let captured_names = trace
        .opcode_fields
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>();
    assert_eq!(
        captured_names, CAPTURED_FIELDS,
        "captured C opcode word order"
    );
    for other in &traces[1..] {
        assert_eq!(other.opcode_fields, trace.opcode_fields);
        assert_eq!(other.opcode_words, trace.opcode_words);
    }
    let extension_opcode = |name: &str| captured_uop_id(traces, name).unwrap_or(u16::MAX);
    let opcodes = LocalOpcodes {
        start: word(trace, "start"),
        warm: word(trace, "warm"),
        jump: word(trace, "jump"),
        nop: word(trace, "nop"),
        set_ip: word(trace, "set_ip"),
        validity: word(trace, "validity"),
        periodic: word(trace, "periodic"),
        resume: word(trace, "resume"),
        load: range(trace, "load"),
        borrow: range(trace, "borrow"),
        store: range(trace, "store"),
        small_int: range(trace, "small_int"),
        copy: range(trace, "copy"),
        swap: range(trace, "swap"),
        bit_set: range(trace, "bit_set"),
        bit_unset: range(trace, "bit_unset"),
        constant: word(trace, "constant"),
        constant_borrow: word(trace, "constant_borrow"),
        pop: word(trace, "pop"),
        pop_borrow: word(trace, "pop_borrow"),
        pop_int: word(trace, "pop_int"),
        pop_float: word(trace, "pop_float"),
        pop_unicode: word(trace, "pop_unicode"),
        tos_float: word(trace, "tos_float"),
        nos_float: word(trace, "nos_float"),
        tos_int: word(trace, "tos_int"),
        nos_int: word(trace, "nos_int"),
        tos_exact_int: word(trace, "tos_exact_int"),
        tos_overflow: word(trace, "tos_overflow"),
        nos_overflow: word(trace, "nos_overflow"),
        float_binary: [
            word(trace, "float_binary[0]"),
            word(trace, "float_binary[1]"),
            word(trace, "float_binary[2]"),
            word(trace, "float_binary[3]"),
        ],
        float_binary_inplace: [
            extension_opcode("_BINARY_OP_ADD_FLOAT_INPLACE"),
            extension_opcode("_BINARY_OP_ADD_FLOAT_INPLACE_RIGHT"),
            extension_opcode("_BINARY_OP_SUBTRACT_FLOAT_INPLACE"),
            extension_opcode("_BINARY_OP_SUBTRACT_FLOAT_INPLACE_RIGHT"),
            extension_opcode("_BINARY_OP_MULTIPLY_FLOAT_INPLACE"),
            extension_opcode("_BINARY_OP_MULTIPLY_FLOAT_INPLACE_RIGHT"),
            extension_opcode("_BINARY_OP_TRUEDIV_FLOAT_INPLACE"),
            extension_opcode("_BINARY_OP_TRUEDIV_FLOAT_INPLACE_RIGHT"),
        ],
        int_binary: [
            word(trace, "int_binary[0]"),
            word(trace, "int_binary[1]"),
            word(trace, "int_binary[2]"),
        ],
        float_compare: word(trace, "float_compare"),
        int_compare: word(trace, "int_compare"),
        bool_bool: word(trace, "bool_bool"),
        bool_int: word(trace, "bool_int"),
        iter_check: word(trace, "iter_check"),
        iter_exhaustion: word(trace, "iter_exhaustion"),
        iter_next: word(trace, "iter_next"),
        globals: word(trace, "globals"),
        builtins: word(trace, "builtins"),
        entry_globals: word(trace, "entry_globals"),
        local_int: word(trace, "local_int"),
        local_float: word(trace, "local_float"),
        push_null: word(trace, "push_null"),
        guard_nos_object_type_version: extension_opcode("_GUARD_NOS_OBJECT_TYPE_VERSION"),
        subscr_array_d: extension_opcode("_BINARY_OP_SUBSCR_ARRAY_D"),
        subscr_array_i: extension_opcode("_BINARY_OP_SUBSCR_ARRAY_I"),
    };
    assert_eq!(
        size_of::<LocalOpcodes>(),
        CANDIDATE_FIELDS.len() * size_of::<u16>()
    );
    assert_candidate_layout();
    for trace in traces {
        assert!(!trace.uops.iter().any(|uop| uop.opcode == u16::MAX));
    }
    opcodes
}

fn assert_candidate_layout() {
    macro_rules! word_field {
        ($name:literal, $field:ident) => {
            ($name, offset_of!(LocalOpcodes, $field))
        };
    }
    macro_rules! array_field {
        ($name:literal, $field:ident, $index:literal) => {
            (
                $name,
                offset_of!(LocalOpcodes, $field) + $index * size_of::<u16>(),
            )
        };
    }
    macro_rules! range_field {
        ($name:literal, $field:ident, $part:ident) => {
            (
                $name,
                offset_of!(LocalOpcodes, $field) + offset_of!(OpcodeRange, $part),
            )
        };
    }
    let actual = [
        word_field!("start", start),
        word_field!("warm", warm),
        word_field!("jump", jump),
        word_field!("nop", nop),
        word_field!("set_ip", set_ip),
        word_field!("validity", validity),
        word_field!("periodic", periodic),
        word_field!("resume", resume),
        range_field!("load.first", load, first),
        range_field!("load.last", load, last),
        range_field!("borrow.first", borrow, first),
        range_field!("borrow.last", borrow, last),
        range_field!("store.first", store, first),
        range_field!("store.last", store, last),
        range_field!("small_int.first", small_int, first),
        range_field!("small_int.last", small_int, last),
        range_field!("copy.first", copy, first),
        range_field!("copy.last", copy, last),
        range_field!("swap.first", swap, first),
        range_field!("swap.last", swap, last),
        range_field!("bit_set.first", bit_set, first),
        range_field!("bit_set.last", bit_set, last),
        range_field!("bit_unset.first", bit_unset, first),
        range_field!("bit_unset.last", bit_unset, last),
        word_field!("constant", constant),
        word_field!("constant_borrow", constant_borrow),
        word_field!("pop", pop),
        word_field!("pop_borrow", pop_borrow),
        word_field!("pop_int", pop_int),
        word_field!("pop_float", pop_float),
        word_field!("pop_unicode", pop_unicode),
        word_field!("tos_float", tos_float),
        word_field!("nos_float", nos_float),
        word_field!("tos_int", tos_int),
        word_field!("nos_int", nos_int),
        word_field!("tos_exact_int", tos_exact_int),
        word_field!("tos_overflow", tos_overflow),
        word_field!("nos_overflow", nos_overflow),
        array_field!("float_binary[0]", float_binary, 0),
        array_field!("float_binary[1]", float_binary, 1),
        array_field!("float_binary[2]", float_binary, 2),
        array_field!("float_binary[3]", float_binary, 3),
        array_field!("float_binary_inplace[0]", float_binary_inplace, 0),
        array_field!("float_binary_inplace[1]", float_binary_inplace, 1),
        array_field!("float_binary_inplace[2]", float_binary_inplace, 2),
        array_field!("float_binary_inplace[3]", float_binary_inplace, 3),
        array_field!("float_binary_inplace[4]", float_binary_inplace, 4),
        array_field!("float_binary_inplace[5]", float_binary_inplace, 5),
        array_field!("float_binary_inplace[6]", float_binary_inplace, 6),
        array_field!("float_binary_inplace[7]", float_binary_inplace, 7),
        array_field!("int_binary[0]", int_binary, 0),
        array_field!("int_binary[1]", int_binary, 1),
        array_field!("int_binary[2]", int_binary, 2),
        word_field!("float_compare", float_compare),
        word_field!("int_compare", int_compare),
        word_field!("bool_bool", bool_bool),
        word_field!("bool_int", bool_int),
        word_field!("iter_check", iter_check),
        word_field!("iter_exhaustion", iter_exhaustion),
        word_field!("iter_next", iter_next),
        word_field!("globals", globals),
        word_field!("builtins", builtins),
        word_field!("entry_globals", entry_globals),
        word_field!("local_int", local_int),
        word_field!("local_float", local_float),
        word_field!("push_null", push_null),
        word_field!(
            "guard_nos_object_type_version",
            guard_nos_object_type_version
        ),
        word_field!("subscr_array_d", subscr_array_d),
        word_field!("subscr_array_i", subscr_array_i),
    ];
    assert_eq!(
        actual.iter().map(|(name, _)| *name).collect::<Vec<_>>(),
        CANDIDATE_FIELDS
    );
    for (index, (name, offset)) in actual.iter().enumerate() {
        assert_eq!(*offset, index * size_of::<u16>(), "field {name}");
    }
}

fn range_contains(range: OpcodeRange, opcode: u16) -> bool {
    (range.first..=range.last).contains(&opcode)
}

fn is_supported(opcode: u16, ops: &LocalOpcodes) -> bool {
    [
        ops.start,
        ops.warm,
        ops.jump,
        ops.nop,
        ops.set_ip,
        ops.validity,
        ops.periodic,
        ops.resume,
        ops.constant,
        ops.constant_borrow,
        ops.pop,
        ops.pop_borrow,
        ops.pop_int,
        ops.pop_float,
        ops.pop_unicode,
        ops.tos_float,
        ops.nos_float,
        ops.tos_int,
        ops.nos_int,
        ops.tos_exact_int,
        ops.tos_overflow,
        ops.nos_overflow,
        ops.float_compare,
        ops.int_compare,
        ops.bool_bool,
        ops.bool_int,
        ops.iter_check,
        ops.iter_exhaustion,
        ops.iter_next,
        ops.globals,
        ops.builtins,
        ops.entry_globals,
        ops.local_int,
        ops.local_float,
        ops.push_null,
        ops.guard_nos_object_type_version,
        ops.subscr_array_d,
        ops.subscr_array_i,
    ]
    .contains(&opcode)
        || [
            ops.load,
            ops.borrow,
            ops.store,
            ops.small_int,
            ops.copy,
            ops.swap,
            ops.bit_set,
            ops.bit_unset,
        ]
        .iter()
        .any(|range| range_contains(*range, opcode))
        || ops.float_binary.contains(&opcode)
        || ops.float_binary_inplace.contains(&opcode)
        || ops.int_binary.contains(&opcode)
}

fn first_unsupported<'a>(trace: &'a Trace, ops: &LocalOpcodes) -> Option<(usize, &'a str)> {
    trace.uops.iter().enumerate().find_map(|(index, uop)| {
        (!is_supported(uop.opcode, ops)).then_some((index, trace.uop_names[index].as_str()))
    })
}

fn describe_decision(
    trace: &Trace,
    ops: &LocalOpcodes,
    result: &Result<Option<Vec<LocalStep>>, Inconsistent>,
) -> String {
    match result {
        Ok(Some(plan)) => format!("Ok(Some) plan_len={}", plan.len()),
        Ok(None) => match first_unsupported(trace, ops) {
            Some((index, opcode)) => {
                format!("Ok(None) reason=unsupported_opcode index={index} opcode={opcode}")
            }
            None => "Ok(None) reason=planner_refusal".to_owned(),
        },
        Err(_) => "Err reason=planner_error".to_owned(),
    }
}

#[derive(Clone, Debug, Default)]
struct FlowValue {
    locals: BTreeSet<usize>,
    array_reads: BTreeSet<usize>,
    local_type_guards: BTreeMap<usize, BTreeMap<usize, u16>>,
    direct_local: Option<usize>,
}

impl FlowValue {
    fn local(index: usize) -> Self {
        let mut local_type_guards = BTreeMap::new();
        local_type_guards.insert(index, BTreeMap::new());
        Self {
            locals: BTreeSet::from([index]),
            array_reads: BTreeSet::new(),
            local_type_guards,
            direct_local: Some(index),
        }
    }

    fn merge(left: &Self, right: &Self) -> Self {
        let mut merged = left.clone();
        merged.locals.extend(right.locals.iter().copied());
        merged.array_reads.extend(right.array_reads.iter().copied());
        for (local, guards) in &right.local_type_guards {
            merged
                .local_type_guards
                .entry(*local)
                .or_default()
                .extend(guards.iter().map(|(source, opcode)| (*source, *opcode)));
        }
        merged.direct_local = None;
        merged
    }
}

#[derive(Default)]
struct StackProvenance {
    used_locals: BTreeSet<usize>,
    index_feeding_locals: BTreeSet<usize>,
    arithmetic_feeding_locals: BTreeSet<usize>,
    first_sink: BTreeMap<usize, usize>,
    first_sink_type_guard: BTreeMap<usize, Option<(usize, u16)>>,
    float_operand_reads: BTreeMap<usize, BTreeSet<usize>>,
    subscript_index_reads: BTreeMap<usize, BTreeSet<usize>>,
}

fn record_first_sink(report: &mut StackProvenance, value: &FlowValue, sink: usize) {
    for local in &value.locals {
        if report.first_sink.contains_key(local) {
            continue;
        }
        report.first_sink.insert(*local, sink);
        let guard = if value.direct_local == Some(*local) {
            value
                .local_type_guards
                .get(local)
                .and_then(|guards| guards.iter().rev().find(|(source, _)| **source < sink))
                .map(|(source, opcode)| (*source, *opcode))
        } else {
            None
        };
        report.first_sink_type_guard.insert(*local, guard);
    }
}

fn pop_value(stack: &mut Vec<FlowValue>, trace: &Trace, index: usize) -> Result<FlowValue, String> {
    stack.pop().ok_or_else(|| {
        format!(
            "{} uop {index} {} underflowed provenance stack",
            trace.label, trace.uop_names[index]
        )
    })
}

fn numeric_uop(name: &str) -> bool {
    (name.starts_with("_BINARY_OP_") || name.starts_with("_COMPARE_OP_"))
        && (name.contains("_FLOAT") || name.contains("_INT"))
}

fn float_uop(name: &str) -> bool {
    (name.starts_with("_BINARY_OP_") || name.starts_with("_COMPARE_OP_")) && name.contains("_FLOAT")
}

fn stack_provenance(trace: &Trace, ops: &LocalOpcodes) -> Result<StackProvenance, String> {
    let mut report = StackProvenance::default();
    for uop in &trace.uops {
        if range_contains(ops.load, uop.opcode)
            || range_contains(ops.borrow, uop.opcode)
            || range_contains(ops.store, uop.opcode)
        {
            report.used_locals.insert(usize::from(uop.oparg));
        }
    }

    let mut locals = (0..trace.locals).map(FlowValue::local).collect::<Vec<_>>();
    let mut stack = vec![FlowValue::default(); trace.initial_depth];
    let warm = trace
        .uop_names
        .iter()
        .position(|name| name == "_MAKE_WARM")
        .ok_or_else(|| format!("{} has no warm marker", trace.label))?;

    for (index, uop) in trace
        .uops
        .iter()
        .enumerate()
        .skip(warm + 1)
        .take(trace.uops.len().saturating_sub(warm + 2))
    {
        let name = trace.uop_names[index].as_str();
        let opcode = uop.opcode;
        if range_contains(ops.load, opcode) || range_contains(ops.borrow, opcode) {
            let local = usize::from(uop.oparg);
            let value = locals
                .get(local)
                .ok_or_else(|| format!("{} uop {index} references local {local}", trace.label))?;
            stack.push(value.clone());
        } else if range_contains(ops.store, opcode) {
            let local = usize::from(uop.oparg);
            let value = pop_value(&mut stack, trace, index)?;
            let old = locals
                .get_mut(local)
                .ok_or_else(|| format!("{} uop {index} stores local {local}", trace.label))?;
            stack.push(std::mem::replace(old, value));
        } else if name == "_BINARY_OP_SUBSCR_ARRAY_D" || name == "_BINARY_OP_SUBSCR_ARRAY_I" {
            let index_value = pop_value(&mut stack, trace, index)?;
            let _array_value = pop_value(&mut stack, trace, index)?;
            record_first_sink(&mut report, &index_value, index);
            report
                .index_feeding_locals
                .extend(index_value.locals.iter().copied());
            report
                .subscript_index_reads
                .entry(index)
                .or_default()
                .extend(index_value.array_reads.iter().copied());
            let mut result = FlowValue::default();
            result.array_reads.insert(index);
            stack.push(result);
            stack.push(FlowValue::default());
            stack.push(FlowValue::default());
        } else if name == "_STORE_SUBSCR_ARRAY_D" {
            for _ in 0..3 {
                pop_value(&mut stack, trace, index)?;
            }
        } else if numeric_uop(name) {
            let right = pop_value(&mut stack, trace, index)?;
            let left = pop_value(&mut stack, trace, index)?;
            if float_uop(name) {
                record_first_sink(&mut report, &left, index);
                record_first_sink(&mut report, &right, index);
                report
                    .arithmetic_feeding_locals
                    .extend(left.locals.iter().chain(&right.locals).copied());
                report
                    .float_operand_reads
                    .entry(index)
                    .or_default()
                    .extend(left.array_reads.iter().chain(&right.array_reads).copied());
            }
            let result = FlowValue::merge(&left, &right);
            stack.push(result);
            stack.push(left);
            stack.push(right);
        } else if range_contains(ops.copy, opcode) {
            let depth = usize::from(uop.oparg);
            if depth == 0 || depth > stack.len() {
                return Err(format!(
                    "{} uop {index} {} has invalid copy depth {depth}",
                    trace.label, name
                ));
            }
            stack.push(stack[stack.len() - depth].clone());
        } else if range_contains(ops.swap, opcode) {
            let depth = usize::from(uop.oparg);
            if depth < 2 || depth > stack.len() {
                return Err(format!(
                    "{} uop {index} {} has invalid swap depth {depth}",
                    trace.label, name
                ));
            }
            let top = stack.len() - 1;
            let other = stack.len() - depth;
            stack.swap(top, other);
        } else if [
            ops.pop,
            ops.pop_borrow,
            ops.pop_int,
            ops.pop_float,
            ops.pop_unicode,
        ]
        .contains(&opcode)
        {
            pop_value(&mut stack, trace, index)?;
        } else if opcode == ops.constant
            || opcode == ops.constant_borrow
            || range_contains(ops.small_int, opcode)
            || opcode == ops.push_null
            || opcode == ops.iter_next
        {
            stack.push(FlowValue::default());
        } else if let Some(depth) = match name {
            "_GUARD_TOS_FLOAT" | "_GUARD_TOS_INT" | "_GUARD_TOS_EXACT_INT" => Some(1),
            "_GUARD_NOS_FLOAT" | "_GUARD_NOS_INT" => Some(2),
            _ => None,
        } {
            if depth > stack.len() {
                return Err(format!(
                    "{} uop {index} {} underflowed provenance stack",
                    trace.label, name
                ));
            }
            let position = stack.len() - depth;
            let value = &mut stack[position];
            if let Some(local) = value.direct_local {
                value
                    .local_type_guards
                    .entry(local)
                    .or_default()
                    .insert(index, opcode);
            }
        } else if [
            ops.start,
            ops.warm,
            ops.jump,
            ops.nop,
            ops.set_ip,
            ops.validity,
            ops.periodic,
            ops.resume,
            ops.tos_float,
            ops.nos_float,
            ops.tos_int,
            ops.nos_int,
            ops.tos_exact_int,
            ops.tos_overflow,
            ops.nos_overflow,
            ops.iter_check,
            ops.iter_exhaustion,
            ops.globals,
            ops.builtins,
            ops.entry_globals,
        ]
        .contains(&opcode)
            || name.starts_with("_GUARD_")
            || name.starts_with("_CHECK_")
            || name == "_ITER_CHECK_RANGE"
            || name == "_GUARD_NOT_EXHAUSTED_RANGE"
        {
            continue;
        } else {
            return Err(format!(
                "{} uop {index} {} has no provenance stack rule",
                trace.label, name
            ));
        }
    }
    Ok(report)
}

fn replay_inputs() -> Vec<Trace> {
    let traces = parse_fixture(include_str!("jes2-traces.txt"));
    assert_eq!(
        traces
            .iter()
            .map(|trace| trace.label.as_str())
            .collect::<Vec<_>>(),
        ["sparse", "fft_inverse", "fft_transform_internal"]
    );
    for trace in &traces {
        assert_eq!(trace.planner_return, 0);
        assert_eq!(
            trace.uop_names.last().map(String::as_str),
            Some("_JUMP_TO_TOP")
        );
    }
    traces
}

#[test]
fn replay_captured_sparse_and_fft_traces() {
    let traces = replay_inputs();
    let ops = opcodes_from_capture(&traces);
    let parent_replay = std::env::var_os("JES2_PARENT_REPLAY").is_some();
    for trace in &traces {
        let result = plan_loop_types(&trace.uops, &ops, &trace.local_kinds, trace.initial_depth);
        let decision = describe_decision(trace, &ops, &result);
        println!(
            "TRACE {} code={} file={} firstline={} source_line={} length={} initial_depth={} decision={}",
            trace.label,
            trace.code,
            trace.filename,
            trace.co_firstlineno,
            trace.source_line,
            trace.length,
            trace.initial_depth,
            decision
        );
        if trace.label == "sparse" && parent_replay {
            assert!(
                matches!(result, Ok(None)),
                "parent sparse result: {decision}"
            );
        } else if trace.label == "sparse" {
            let plan = result
                .expect("candidate sparse planner error")
                .expect("candidate sparse trace was not rewritten");
            let mut previous = 0;
            for (index, step) in plan.iter().enumerate() {
                assert!(
                    step.source < trace.length as u32,
                    "step {index} source {}",
                    step.source
                );
                if index != 0 {
                    assert!(
                        step.source >= previous,
                        "step {index} source went backwards"
                    );
                }
                assert_ne!(step.opcode, ops.periodic, "periodic at step {index}");
                previous = step.source;
            }
            assert_eq!(plan.last().map(|step| step.opcode), Some(ops.jump));
        } else {
            assert!(
                !matches!(result, Ok(Some(_))),
                "{} unexpectedly rewrote",
                trace.label
            );
        }
    }
}

fn same_decision(
    baseline: &Result<Option<Vec<LocalStep>>, Inconsistent>,
    changed: &Result<Option<Vec<LocalStep>>, Inconsistent>,
) -> bool {
    match (baseline, changed) {
        (Ok(Some(expected)), Ok(Some(actual))) => expected == actual,
        (Ok(None), Ok(None)) | (Err(_), Err(_)) => true,
        _ => false,
    }
}

fn flipped_kind(kind: u16, ops: &LocalOpcodes) -> Option<u16> {
    if kind == ops.local_int {
        Some(ops.local_float)
    } else if kind == ops.local_float {
        Some(ops.local_int)
    } else {
        None
    }
}

fn inserted_local_guards<'a>(
    plan: &'a [LocalStep],
    ops: &LocalOpcodes,
    local: usize,
) -> Vec<&'a LocalStep> {
    plan.iter()
        .filter(|step| {
            step.source == 0
                && usize::from(step.oparg) == local
                && [ops.local_int, ops.local_float].contains(&step.opcode)
        })
        .collect()
}

fn captured_used_locals(trace: &Trace, ops: &LocalOpcodes) -> BTreeSet<usize> {
    trace
        .uops
        .iter()
        .filter(|uop| {
            range_contains(ops.load, uop.opcode)
                || range_contains(ops.borrow, uop.opcode)
                || range_contains(ops.store, uop.opcode)
        })
        .map(|uop| usize::from(uop.oparg))
        .collect()
}

fn is_local_access(opcode: u16, ops: &LocalOpcodes) -> bool {
    range_contains(ops.load, opcode)
        || range_contains(ops.borrow, opcode)
        || range_contains(ops.store, opcode)
}

fn preserves_local_accesses(
    trace: &Trace,
    ops: &LocalOpcodes,
    plan: &[LocalStep],
    local: usize,
) -> bool {
    let accesses = trace
        .uops
        .iter()
        .enumerate()
        .filter(|(_, uop)| usize::from(uop.oparg) == local && is_local_access(uop.opcode, ops))
        .map(|(source, uop)| (source as u32, uop.opcode, uop.oparg))
        .collect::<BTreeSet<_>>();
    if accesses.is_empty()
        || accesses.iter().any(|access| {
            !plan
                .iter()
                .any(|step| (step.source, step.opcode, step.oparg) == *access)
        })
    {
        return false;
    }
    !plan
        .iter()
        .filter(|step| usize::from(step.oparg) == local && is_local_access(step.opcode, ops))
        .any(|step| !accesses.contains(&(step.source, step.opcode, step.oparg)))
}

fn preserves_first_sink_guard(
    trace: &Trace,
    analysis: &StackProvenance,
    plan: &[LocalStep],
    local: usize,
) -> bool {
    let Some(&sink) = analysis.first_sink.get(&local) else {
        return false;
    };
    let Some(Some((source, opcode))) = analysis.first_sink_type_guard.get(&local) else {
        return false;
    };
    let Some(original) = trace.uops.get(*source) else {
        return false;
    };
    if *source >= sink
        || original.opcode != *opcode
        || !matches!(
            trace.uop_names[*source].as_str(),
            "_GUARD_TOS_FLOAT"
                | "_GUARD_NOS_FLOAT"
                | "_GUARD_TOS_INT"
                | "_GUARD_NOS_INT"
                | "_GUARD_TOS_EXACT_INT"
        )
    {
        return false;
    }
    plan.iter()
        .find(|step| step.source == *source as u32)
        .is_some_and(|step| step.opcode == original.opcode && step.oparg == original.oparg)
}

fn safe_sink_plan(
    trace: &Trace,
    ops: &LocalOpcodes,
    analysis: &StackProvenance,
    plan: &[LocalStep],
    local: usize,
) -> bool {
    inserted_local_guards(plan, ops, local).is_empty()
        && preserves_first_sink_guard(trace, analysis, plan, local)
        && preserves_local_accesses(trace, ops, plan, local)
}

#[test]
fn captured_type_matrix_and_sparse_uop_mutations() {
    let traces = replay_inputs();
    let ops = opcodes_from_capture(&traces);
    let mut failures = Vec::new();
    let mut rule_cases = [0usize; 4];
    let mut rule_failures = [0usize; 4];
    let mut all_index_feeding = BTreeSet::new();
    let mut all_arithmetic_feeding = BTreeSet::new();
    let mut analyses = Vec::new();

    for trace in &traces {
        let analysis = match stack_provenance(trace, &ops) {
            Ok(analysis) => analysis,
            Err(reason) => {
                failures.push(format!("provenance {}: {reason}", trace.label));
                StackProvenance {
                    used_locals: captured_used_locals(trace, &ops),
                    ..StackProvenance::default()
                }
            }
        };
        println!(
            "PROVENANCE trace={} used={:?} index_feeding={:?} arithmetic_feeding={:?} first_sink={:?} first_sink_type_guard={:?}",
            trace.label,
            analysis.used_locals,
            analysis.index_feeding_locals,
            analysis.arithmetic_feeding_locals,
            analysis.first_sink,
            analysis.first_sink_type_guard
        );
        all_index_feeding.extend(
            analysis
                .index_feeding_locals
                .iter()
                .map(|local| (trace.label.clone(), *local)),
        );
        all_arithmetic_feeding.extend(
            analysis
                .arithmetic_feeding_locals
                .iter()
                .map(|local| (trace.label.clone(), *local)),
        );

        let protected = analysis
            .index_feeding_locals
            .union(&analysis.arithmetic_feeding_locals)
            .copied()
            .collect::<BTreeSet<_>>();
        let baseline = plan_loop_types(&trace.uops, &ops, &trace.local_kinds, trace.initial_depth);
        for (local, kind) in trace.local_kinds.iter().copied().enumerate() {
            let Some(other_kind) = flipped_kind(kind, &ops) else {
                continue;
            };
            let rule = if !analysis.used_locals.contains(&local) {
                Some(0)
            } else if protected.contains(&local) {
                Some(1)
            } else {
                Some(2)
            };
            let Some(rule) = rule else {
                continue;
            };
            for invalid_kind in [other_kind, 0] {
                let mut kinds = trace.local_kinds.clone();
                kinds[local] = invalid_kind;
                let result = plan_loop_types(&trace.uops, &ops, &kinds, trace.initial_depth);
                rule_cases[rule] += 1;
                match rule {
                    0 if !same_decision(&baseline, &result) => {
                        rule_failures[0] += 1;
                        failures.push(format!(
                            "rule a {} local {local} kind {kind}->{invalid_kind} changed baseline decision/steps",
                            trace.label
                        ));
                    }
                    1 => {
                        if let Ok(Some(plan)) = &result {
                            if safe_sink_plan(trace, &ops, &analysis, plan, local) {
                                let guard = analysis
                                    .first_sink_type_guard
                                    .get(&local)
                                    .copied()
                                    .flatten();
                                println!(
                                    "RULE_B_SAFE trace={} local={local} kind={kind}->{invalid_kind} first_sink={:?} guard={guard:?} steps={}",
                                    trace.label,
                                    analysis.first_sink.get(&local),
                                    plan.len()
                                );
                            } else {
                                rule_failures[1] += 1;
                                failures.push(format!(
                                    "rule b {} local {local} kind {kind}->{invalid_kind} planned without preserving its provenance guard/local accesses",
                                    trace.label
                                ));
                            }
                        }
                    }
                    2 => {
                        if let Ok(Some(plan)) = &result {
                            let guards = inserted_local_guards(plan, &ops, local);
                            if guards.iter().any(|step| step.opcode != invalid_kind) {
                                rule_failures[2] += 1;
                                failures.push(format!(
                                    "rule c {} local {local} kind {kind}->{invalid_kind} emitted guard opcodes {:?}",
                                    trace.label,
                                    guards.iter().map(|step| step.opcode).collect::<Vec<_>>()
                                ));
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
        analyses.push(analysis);
    }

    println!("DERIVED_INDEX_FEEDING={all_index_feeding:?}");
    println!("DERIVED_ARITHMETIC_FEEDING={all_arithmetic_feeding:?}");
    if all_index_feeding.is_empty() {
        failures.push("no index-feeding locals derived".to_owned());
    }
    if all_arithmetic_feeding.is_empty() {
        failures.push("no arithmetic-feeding locals derived".to_owned());
    }

    let sparse_index = traces
        .iter()
        .position(|trace| trace.label == "sparse")
        .expect("captured sparse trace");
    let sparse = &traces[sparse_index];
    let analysis = &analyses[sparse_index];
    let multiply = sparse
        .uop_names
        .iter()
        .position(|name| name == "_BINARY_OP_MULTIPLY_FLOAT_INPLACE");
    let multiply_d_feeders = multiply
        .and_then(|index| analysis.float_operand_reads.get(&index))
        .into_iter()
        .flatten()
        .copied()
        .filter(|index| sparse.uop_names[*index] == "_BINARY_OP_SUBSCR_ARRAY_D")
        .collect::<BTreeSet<_>>();
    let d_mutation = multiply_d_feeders.iter().next_back().copied();
    if let Some(index) = d_mutation {
        let replacement =
            captured_uop_id(&traces, "_BINARY_OP_SUBSCR_ARRAY_I").expect("captured array-I opcode");
        let mut uops = sparse.uops.clone();
        uops[index].opcode = replacement;
        let result = plan_loop_types(&uops, &ops, &sparse.local_kinds, sparse.initial_depth);
        rule_cases[3] += 1;
        println!(
            "MUTATION array-D-to-I trace=sparse index={index} decision={}",
            decision_kind(&result)
        );
        if matches!(result, Ok(Some(_))) {
            rule_failures[3] += 1;
            failures.push(format!(
                "rule d array-D-to-I at uop {index} unexpectedly planned"
            ));
        }
    } else {
        rule_failures[3] += 1;
        failures.push("rule d missing array-D result feeding sparse float multiply".to_owned());
        println!("MUTATION array-D-to-I shape=absent");
    }

    let i_feeding_index = analysis
        .subscript_index_reads
        .iter()
        .filter(|(sink, _)| sparse.uop_names[**sink] == "_BINARY_OP_SUBSCR_ARRAY_D")
        .flat_map(|(_, reads)| reads.iter().copied())
        .filter(|index| sparse.uop_names[*index] == "_BINARY_OP_SUBSCR_ARRAY_I")
        .min();
    if let Some(index) = i_feeding_index {
        let replacement =
            captured_uop_id(&traces, "_BINARY_OP_SUBSCR_ARRAY_D").expect("captured array-D opcode");
        let mut uops = sparse.uops.clone();
        uops[index].opcode = replacement;
        let result = plan_loop_types(&uops, &ops, &sparse.local_kinds, sparse.initial_depth);
        rule_cases[3] += 1;
        println!(
            "MUTATION array-I-to-D-index trace=sparse index={index} decision={}",
            decision_kind(&result)
        );
        if matches!(result, Ok(Some(_))) {
            rule_failures[3] += 1;
            failures.push(format!(
                "rule d array-I-to-D-index at uop {index} unexpectedly planned"
            ));
        }
    } else {
        rule_failures[3] += 1;
        failures.push("rule d missing array-I result feeding a sparse array index".to_owned());
        println!("MUTATION array-I-to-D-index shape=absent");
    }

    for (name, cases) in [
        ("a-unused-identical", rule_cases[0]),
        ("b-sensitive-refuse", rule_cases[1]),
        ("c-other-used-guarded", rule_cases[2]),
        ("d-uop-mutations-refuse", rule_cases[3]),
    ] {
        println!(
            "JES2_MATRIX rule={name} cases={cases} failures={}",
            rule_failures[match name.chars().next() {
                Some('a') => 0,
                Some('b') => 1,
                Some('c') => 2,
                _ => 3,
            }]
        );
    }
    println!("JES2_MATRIX failures={failures:?}");
    assert!(rule_cases[0] > 0, "rule a had no cases");
    assert!(rule_cases[1] > 0, "rule b had no cases");
    assert!(rule_cases[3] == 2, "rule d expected two mutation cases");
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

fn decision_kind(result: &Result<Option<Vec<LocalStep>>, Inconsistent>) -> &'static str {
    match result {
        Ok(Some(_)) => "Ok(Some)",
        Ok(None) => "Ok(None)",
        Err(_) => "Err",
    }
}
