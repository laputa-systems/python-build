use ruff_python_ast::StringFlags;
use ruff_python_ast::token::{TokenKind, Tokens};
use ruff_text_size::Ranged;

/// Report whether CPython 3.16's compiler can emit a `SyntaxWarning` for this module.
///
/// The answer over-approximates every compile-time warning site: invalid escapes, a numeric
/// literal directly followed by a name or keyword, identity comparison with a literal, calls and
/// subscripts of literals, an assertion on a non-empty tuple, `return`, `break`, or `continue`
/// directly inside `finally`, and `from . lazy import`. `false` means CPython emits none.
///
/// `ast_candidate` is the AST half of the answer, collected by the rules scan so the module
/// is not walked twice; the token half runs here because it needs the token stream.
pub(crate) fn may_warn(source: &str, tokens: &Tokens, ast_candidate: bool) -> bool {
    ast_candidate || tokens_may_warn(source, tokens)
}

fn tokens_may_warn(source: &str, tokens: &Tokens) -> bool {
    let mut previous_number_end = None;
    for token in tokens {
        let kind = token.kind();
        if previous_number_end == Some(token.start())
            && (kind == TokenKind::Identifier || kind.is_keyword())
        {
            return true;
        }
        previous_number_end =
            matches!(kind, TokenKind::Int | TokenKind::Float | TokenKind::Complex)
                .then(|| token.end());
        if matches!(
            kind,
            TokenKind::String | TokenKind::FStringMiddle | TokenKind::TStringMiddle
        ) && let Some(flags) = token.string_flags()
            && !flags.prefix().is_raw()
            && has_invalid_escape(source[token.range()].as_bytes(), flags.is_byte_string())
        {
            return true;
        }
    }
    false
}

fn has_invalid_escape(text: &[u8], bytes: bool) -> bool {
    let mut index = 0;
    while let Some(offset) = memchr::memchr(b'\\', &text[index..]) {
        let Some(&next) = text.get(index + offset + 1) else {
            return true;
        };
        let valid = matches!(
            next,
            b'\n' | b'\\' | b'\'' | b'"' | b'a' | b'b' | b'f' | b'n' | b'r' | b't' | b'v' | b'0'
                ..=b'3' | b'x'
        ) || (!bytes && matches!(next, b'N' | b'u' | b'U'));
        if !valid {
            return true;
        }
        index += offset + 2;
    }
    false
}

#[cfg(test)]
mod tests {
    use crate::{CompileOptions, PythonVersion, compile_with_options};

    fn may_warn(source: &str) -> bool {
        may_warn_for(PythonVersion::Py316, source)
    }

    fn may_warn_for(python_version: PythonVersion, source: &str) -> bool {
        let options = CompileOptions {
            python_version,
            ..CompileOptions::default()
        };
        compile_with_options(source, "<test>", &options)
            .unwrap_or_else(|error| panic!("{source:?}: {error}"))
            .may_warn()
    }

    #[test]
    fn every_cpython_compile_time_warning_is_a_candidate() {
        for source in [
            "x = '\\d'\n",
            "x = b'\\N{EM DASH}'\n",
            "x = b'\\u0041'\n",
            "x = '\\400'\n",
            "x = f'\\{x}'\n",
            "x = t'\\{x}'\n",
            "x = 1if y else 2\n",
            "x = 1jor 2\n",
            "x is 1\n",
            "x is not 'a'\n",
            "x is ('a' 'b')\n",
            "x is ()\n",
            "x is (1, None)\n",
            "1()\n",
            "None()\n",
            "__debug__()\n",
            "(1, 2)(3)\n",
            "[x](y)\n",
            "f'{x}'()\n",
            "('%s' % (x,))()\n",
            "1[0]\n",
            "{1, 2}[0]\n",
            "(lambda: 0)[0]\n",
            "'abc'['b']\n",
            "[a][1.0]\n",
            "(1, 2)[(0,)]\n",
            "assert (x, 'message')\n",
            "from . lazy import x\n",
            "type X[T: (1)()] = int\n",
        ] {
            assert!(may_warn(source), "{source:?}");
        }
        // CPython 3.16 sources that leave a finally body go to CPython's compiler instead.
        for source in [
            "def f():\n    try:\n        pass\n    finally:\n        return\n",
            "for x in y:\n    try:\n        pass\n    finally:\n        break\n",
            "while x:\n    try:\n        pass\n    finally:\n        continue\n",
        ] {
            assert!(may_warn_for(PythonVersion::Py315, source), "{source:?}");
        }
    }

    #[test]
    fn code_without_cpython_warnings_is_not_a_candidate() {
        for source in [
            "x = '\\n\\t\\\\\\'\\x41\\u0041\\N{EM DASH}\\101\\\n'\n",
            "x = rb'\\d' + r'\\d' + rf'\\{x}'\n",
            "x = 1 if y else 2\n",
            "x is None or x is not True or x is ...\n",
            "x is -1 or x is (a, b) or x is [] or x is f'a'\n",
            "x is y.z is not w()\n",
            "f(x)(y)\n",
            "(-1)()\n",
            "'{}'.format(x)\n",
            "'abc'[0] + 'abc'[i] + 'abc'[-1] + (1, 2)[-1:] + [1, 2][n] + {1: 2}[1]\n",
            "(x + y)[0] + (-1)[0] + 'a'[-1.0]\n",
            "assert x, 'message'\n",
            "assert ()\n",
            "try:\n    pass\nfinally:\n    for x in y:\n        break\n",
            "try:\n    pass\nfinally:\n    def f():\n        return\n",
            "from .lazy import x\n",
            "lazy from . import x\n",
        ] {
            assert!(!may_warn(source), "{source:?}");
        }
    }
}
