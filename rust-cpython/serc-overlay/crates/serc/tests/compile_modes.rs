mod common;

use serc::{
    CompileMode, CompileOptions, OptimizationLevel, PythonVersion,
    compile_bytes_with_python_filename_and_integer_limits,
    compile_with_python_filename_and_integer_limits, compile_with_options,
};

struct Case {
    mode: &'static str,
    name: &'static str,
    source: &'static str,
}

const ORACLE_COMPILE: &str = "import marshal, sys; src, mode, opt = sys.argv[1], sys.argv[2], int(sys.argv[3]); code = compile(src, '<s>', mode, dont_inherit=True, optimize=opt); sys.stdout.buffer.write(marshal.dumps(code))";

const ORACLE_RUN: &str = r#"
import marshal, sys

code = marshal.loads(sys.stdin.buffer.read())
mode = sys.argv[1]
seen = []

def hook(value):
    seen.append(repr(value))

sys.displayhook = hook

class Manager:
    def __enter__(self):
        return 'manager'

    def __exit__(self, *args):
        return False

env = {'value': 6, 'values': [1, 2, 3], 'manager': Manager(), 'result': None}
try:
    if mode == 'eval':
        outcome = repr(eval(code, env))
    else:
        exec(code, env)
        outcome = 'executed'
except BaseException as error:
    outcome = 'raised:' + type(error).__name__
sys.stdout.write(outcome + '\n')
sys.stdout.write('\n'.join(seen))
"#;

const CASES: &[Case] = &[
    Case { mode: "eval", name: "constant", source: "42" },
    Case { mode: "eval", name: "string", source: "'a string'" },
    Case { mode: "eval", name: "bytes", source: "b'x'" },
    Case { mode: "eval", name: "name", source: "value" },
    Case { mode: "eval", name: "tuple", source: "1, 2, value" },
    Case { mode: "eval", name: "unpacking", source: "(*values, 4)" },
    Case { mode: "eval", name: "arithmetic", source: "value * 2 + 3" },
    Case { mode: "eval", name: "conditional", source: "value if value > 0 else -value" },
    Case { mode: "eval", name: "boolean", source: "value and False or 7" },
    Case { mode: "eval", name: "comparison", source: "0 < value < 10" },
    Case { mode: "eval", name: "lambda", source: "(lambda x=2: x + value)()" },
    Case { mode: "eval", name: "list_comprehension", source: "[x * 2 for x in values if x]" },
    Case { mode: "eval", name: "nested_comprehension", source: "[[x + y for y in values] for x in values]" },
    Case { mode: "eval", name: "dict_comprehension", source: "{x: x + 1 for x in values}" },
    Case { mode: "eval", name: "set_comprehension", source: "{x for x in values}" },
    Case { mode: "eval", name: "generator", source: "sum(x for x in values)" },
    Case { mode: "eval", name: "walrus", source: "(result := value + 1)" },
    Case { mode: "eval", name: "f_string", source: "f'{value:04d}'" },
    Case { mode: "eval", name: "t_string", source: "t'value={value}'" },
    Case { mode: "eval", name: "debug", source: "__debug__" },
    Case { mode: "eval", name: "multiline", source: "(\n    value +\n    2\n)\n" },
    Case { mode: "eval", name: "leading_blank", source: "\n\nvalue\n" },
    Case { mode: "eval", name: "leading_space", source: "  value" },
    Case { mode: "eval", name: "trailing_comment", source: "value # comment\n" },
    Case { mode: "eval", name: "utf8_cookie", source: "# coding: utf-8\n'café'\n" },
    Case { mode: "eval", name: "empty", source: "" },
    Case { mode: "eval", name: "comment_only", source: "# comment\n" },
    Case { mode: "eval", name: "assignment", source: "value = 1" },
    Case { mode: "eval", name: "multiple_expressions", source: "1; 2" },
    Case { mode: "eval", name: "yield", source: "(yield 1)" },
    Case { mode: "eval", name: "await", source: "await value" },
    Case { mode: "eval", name: "unparenthesized_walrus", source: "result := 1" },
    Case { mode: "single", name: "constant", source: "42\n" },
    Case { mode: "single", name: "string", source: "'a string'\n" },
    Case { mode: "single", name: "none", source: "None\n" },
    Case { mode: "single", name: "assignment", source: "result = value + 1\n" },
    Case { mode: "single", name: "semicolon", source: "result = 1; result + 2\n" },
    Case { mode: "single", name: "multiple_displays", source: "1; 2; None\n" },
    Case { mode: "single", name: "tuple", source: "1, 2, value\n" },
    Case { mode: "single", name: "list_comprehension", source: "[x * 2 for x in values]\n" },
    Case { mode: "single", name: "lambda", source: "(lambda x: x + 1)(value)\n" },
    Case { mode: "single", name: "walrus", source: "(result := value + 1)\n" },
    Case { mode: "single", name: "if", source: "if value:\n    value\n    value + 1\n" },
    Case { mode: "single", name: "folded_if", source: "if True:\n    42\n" },
    Case { mode: "single", name: "compound_string", source: "if True:\n    'a string'\n" },
    Case { mode: "single", name: "compound_no_newline", source: "if True:\n    42" },
    Case { mode: "single", name: "inline_compound", source: "if True: value\n" },
    Case { mode: "single", name: "inline_compound_no_newline", source: "if True: value" },
    Case { mode: "single", name: "for", source: "for x in values:\n    x\n" },
    Case { mode: "single", name: "while", source: "while value < 5:\n    value\n    value += 1\n" },
    Case { mode: "single", name: "with", source: "with manager:\n    value\n" },
    Case { mode: "single", name: "try", source: "try:\n    value\nfinally:\n    value + 1\n" },
    Case { mode: "single", name: "match", source: "match value:\n    case 3:\n        'three'\n    case _:\n        'other'\n" },
    Case { mode: "single", name: "function", source: "def f():\n    'doc'\n    42\n    return value\n" },
    Case { mode: "single", name: "class", source: "class C:\n    'doc'\n    42\n    result = value\n" },
    Case { mode: "single", name: "annotation", source: "result: int = value\n" },
    Case { mode: "single", name: "assert", source: "assert value == 3\n" },
    Case { mode: "single", name: "debug", source: "__debug__\n" },
    Case { mode: "single", name: "future", source: "from __future__ import annotations\n" },
    Case { mode: "single", name: "no_newline", source: "value" },
    Case { mode: "single", name: "leading_blank", source: "\n\nvalue\n" },
    Case { mode: "single", name: "leading_space", source: "  value\n" },
    Case { mode: "single", name: "blank", source: "\n" },
    Case { mode: "single", name: "empty", source: "" },
    Case { mode: "single", name: "comment_only", source: "# comment\n" },
    Case { mode: "single", name: "multiline_statements", source: "result = 1\nresult = 2\n" },
    Case { mode: "single", name: "multiple_compounds", source: "if True:\n    pass\nif True:\n    pass\n" },
    Case { mode: "single", name: "incomplete_compound", source: "if value:\n" },
];

const EXEC_CASES: &[Case] = &[
    Case { mode: "exec", name: "assignment", source: "answer = 42\n" },
    Case { mode: "exec", name: "docstring", source: "\"doc\"\nvalue = 1\n" },
    Case { mode: "exec", name: "function", source: "def f(a=1):\n    'doc'\n    return a\n" },
    Case { mode: "exec", name: "condition", source: "if value:\n    result = value\nelse:\n    result = 0\n" },
    Case { mode: "exec", name: "comprehension", source: "result = [x * 2 for x in values if x]\n" },
];

fn compile_mode(mode: &str) -> CompileMode {
    match mode {
        "exec" => CompileMode::Exec,
        "eval" => CompileMode::Eval,
        "single" => CompileMode::Single,
        other => panic!("unknown mode {other}"),
    }
}

fn optimization(level: u32) -> OptimizationLevel {
    match level {
        0 => OptimizationLevel::Zero,
        1 => OptimizationLevel::One,
        2 => OptimizationLevel::Two,
        other => panic!("unknown optimization level {other}"),
    }
}

fn compile_options(mode: &str, level: u32) -> CompileOptions {
    CompileOptions {
        python_version: PythonVersion::Py316,
        optimization_level: optimization(level),
        mode: compile_mode(mode),
        ..CompileOptions::default()
    }
}

fn oracle_compile(source: &str, mode: &str, level: u32) -> Option<Vec<u8>> {
    let output = common::cpython::command_316()
        .args(["-B", "-c", ORACLE_COMPILE, source, mode, &level.to_string()])
        .output()
        .unwrap();
    output.status.success().then_some(output.stdout)
}

fn execute(marshal: &[u8], mode: &str) -> String {
    let mut command = common::cpython::command_316();
    command.args(["-B", "-c", ORACLE_RUN, mode]);
    let output = common::cpython::run_with_input(&mut command, marshal);
    assert!(
        output.status.success(),
        "{mode}: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    String::from_utf8(output.stdout).unwrap()
}

fn assert_case(case: &Case, level: u32) {
    let expected = oracle_compile(case.source, case.mode, level);
    let compiled = compile_with_options(case.source, "<s>", &compile_options(case.mode, level));
    let Some(expected) = expected else {
        assert!(
            compiled.is_err(),
            "{} {} opt={level}: Serc accepted source CPython 3.16 rejects: {}",
            case.mode,
            case.name,
            case.source.escape_debug()
        );
        return;
    };
    let compiled = compiled.unwrap_or_else(|error| {
        panic!(
            "{} {} opt={level}: Serc refused source CPython 3.16 accepts: {error}\n{}",
            case.mode,
            case.name,
            case.source.escape_debug()
        )
    });
    let actual = compiled.marshal();
    assert_eq!(
        actual, expected,
        "{} {} opt={level}: marshal bytes differ for {}",
        case.mode,
        case.name,
        case.source.escape_debug()
    );
    assert_eq!(
        execute(&actual, case.mode),
        execute(&expected, case.mode),
        "{} {} opt={level}: execution differs for {}",
        case.mode,
        case.name,
        case.source.escape_debug()
    );
}

#[test]
fn eval_and_single_match_cpython_316_marshal_and_behavior() {
    for level in 0..=2 {
        for case in CASES {
            assert_case(case, level);
        }
    }
}

#[test]
fn ffi_entrypoints_honor_nonexec_modes() {
    for level in 0..=2 {
        for (mode, source) in [("eval", "value + 1\n"), ("single", "value\n")] {
            let expected = oracle_compile(source, mode, level).unwrap();
            let options = compile_options(mode, level);
            let text = compile_with_python_filename_and_integer_limits(
                source,
                b"<s>",
                &options,
                4300,
                None,
            )
            .unwrap();
            let bytes = compile_bytes_with_python_filename_and_integer_limits(
                source.as_bytes(),
                b"<s>",
                &options,
                4300,
                None,
            )
            .unwrap();
            assert_eq!(text.marshal(), expected, "{mode} opt={level} text");
            assert_eq!(bytes.marshal(), expected, "{mode} opt={level} bytes");
        }
    }
}

#[test]
fn exec_compilation_is_unchanged() {
    for level in 0..=2 {
        for case in EXEC_CASES {
            let expected = oracle_compile(case.source, case.mode, level).unwrap();
            let compiled = compile_with_options(case.source, "<s>", &compile_options(case.mode, level))
                .unwrap();
            assert_eq!(
                compiled.marshal(),
                expected,
                "exec {} opt={level}",
                case.name
            );
        }
    }
}
