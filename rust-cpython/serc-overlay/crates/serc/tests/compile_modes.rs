mod common;

use serc::{
    CompileMode, CompileOptions, OptimizationLevel, PythonVersion,
    compile_bytes_with_python_filename_and_integer_limits,
    compile_with_python_filename_and_integer_limits, compile_with_options,
};

#[derive(serde::Deserialize)]
struct Case {
    mode: String,
    name: String,
    source: String,
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

fn cases() -> Vec<Case> {
    serde_json::from_str(include_str!("fixtures/source_modes.json")).unwrap()
}

fn compile_mode(mode: &str) -> CompileMode {
    mode.parse().expect("fixture selects a supported source mode")
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
    let expected = oracle_compile(&case.source, &case.mode, level);
    let compiled = compile_with_options(&case.source, "<s>", &compile_options(&case.mode, level));
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
        execute(&actual, &case.mode),
        execute(&expected, &case.mode),
        "{} {} opt={level}: execution differs for {}",
        case.mode,
        case.name,
        case.source.escape_debug()
    );
}

#[test]
fn eval_and_single_match_cpython_316_marshal_and_behavior() {
    let cases = cases();
    for level in 0..=2 {
        for case in cases.iter().filter(|case| case.mode != "exec") {
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
    let cases = cases();
    for level in 0..=2 {
        for case in cases.iter().filter(|case| case.mode == "exec") {
            let expected = oracle_compile(&case.source, &case.mode, level).unwrap();
            let compiled = compile_with_options(&case.source, "<s>", &compile_options(&case.mode, level))
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
