use ruff_python_ast::token::{TokenKind, Tokens};
use ruff_python_ast::visitor::{Visitor, walk_except_handler, walk_expr, walk_stmt};
use ruff_python_ast::{self as ast, CmpOp, Expr, Number, Operator, Stmt, StringFlags};
use ruff_text_size::Ranged;

/// Report whether CPython 3.16's compiler can emit a `SyntaxWarning` for this module.
///
/// The answer over-approximates every compile-time warning site: invalid escapes, a numeric
/// literal directly followed by a name or keyword, identity comparison with a literal, calls and
/// subscripts of literals, an assertion on a non-empty tuple, `return`, `break`, or `continue`
/// directly inside `finally`, and `from . lazy import`. `false` means CPython emits none.
pub(crate) fn may_warn(source: &str, tokens: &Tokens, suite: &[Stmt]) -> bool {
    tokens_may_warn(source, tokens) || {
        let mut scan = Scan {
            source,
            found: false,
            blocks: Vec::new(),
        };
        scan.visit_body(suite);
        scan.found
    }
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
        previous_number_end = matches!(
            kind,
            TokenKind::Int | TokenKind::Float | TokenKind::Complex
        )
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
            b'\n' | b'\\' | b'\'' | b'"' | b'a' | b'b' | b'f' | b'n' | b'r' | b't' | b'v'
                | b'0'..=b'3' | b'x'
        ) || (!bytes && matches!(next, b'N' | b'u' | b'U'));
        if !valid {
            return true;
        }
        index += offset + 2;
    }
    false
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum Block {
    Finally,
    Function,
    Loop,
}

struct Scan<'source> {
    source: &'source str,
    found: bool,
    blocks: Vec<Block>,
}

impl Scan<'_> {
    fn in_finally(&self) -> bool {
        self.blocks.last() == Some(&Block::Finally)
    }

    fn visit_block(&mut self, block: Block, body: &[Stmt]) {
        self.blocks.push(block);
        self.visit_body(body);
        self.blocks.pop();
    }
}

impl<'a> Visitor<'a> for Scan<'_> {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        if self.found {
            return;
        }
        if crate::stack_growth_required() {
            return crate::grow_stack(|| self.visit_stmt(statement));
        }
        match statement {
            Stmt::FunctionDef(function) => {
                for decorator in &function.decorator_list {
                    self.visit_decorator(decorator);
                }
                if let Some(type_params) = &function.type_params {
                    self.visit_type_params(type_params);
                }
                self.visit_parameters(&function.parameters);
                if let Some(returns) = &function.returns {
                    self.visit_annotation(returns);
                }
                self.visit_block(Block::Function, &function.body);
            }
            Stmt::For(for_statement) => {
                self.visit_expr(&for_statement.target);
                self.visit_expr(&for_statement.iter);
                self.visit_block(Block::Loop, &for_statement.body);
                self.visit_body(&for_statement.orelse);
            }
            Stmt::While(while_statement) => {
                self.visit_expr(&while_statement.test);
                self.visit_block(Block::Loop, &while_statement.body);
                self.visit_body(&while_statement.orelse);
            }
            Stmt::Try(try_statement) => {
                self.visit_body(&try_statement.body);
                for handler in &try_statement.handlers {
                    walk_except_handler(self, handler);
                }
                self.visit_body(&try_statement.orelse);
                self.visit_block(Block::Finally, &try_statement.finalbody);
            }
            Stmt::TypeAlias(alias) => {
                self.visit_expr(&alias.name);
                if let Some(type_params) = &alias.type_params {
                    self.visit_type_params(type_params);
                }
                self.visit_expr(&alias.value);
            }
            Stmt::Return(_) | Stmt::Break(_) | Stmt::Continue(_) if self.in_finally() => {
                self.found = true;
            }
            Stmt::Assert(assert) if matches!(&*assert.test, Expr::Tuple(tuple) if !tuple.elts.is_empty()) => {
                self.found = true;
            }
            Stmt::ImportFrom(import)
                if import.level > 0
                    && !import.is_lazy
                    && import.module.as_ref().is_some_and(|module| {
                        module.as_str() == "lazy"
                            && self.source.as_bytes()[..module.start().to_usize()].last() != Some(&b'.')
                    }) =>
            {
                self.found = true;
            }
            _ => walk_stmt(self, statement),
        }
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        if self.found {
            return;
        }
        if crate::stack_growth_required() {
            return crate::grow_stack(|| self.visit_expr(expression));
        }
        self.found = match expression {
            Expr::Compare(compare) => compare.ops.iter().enumerate().any(|(index, op)| {
                matches!(op, CmpOp::Is | CmpOp::IsNot)
                    && (is_identity_literal(&compare.operands[index])
                        || is_identity_literal(&compare.operands[index + 1]))
            }),
            Expr::Call(call) => is_constant(&call.func) || is_display(&call.func),
            Expr::Subscript(subscript) => {
                is_unsubscriptable(&subscript.value)
                    || (is_sequence_literal(&subscript.value)
                        && index_type_is_not_integer(&subscript.slice))
            }
            _ => false,
        };
        if !self.found {
            walk_expr(self, expression);
        }
    }
}

fn is_constant(expression: &Expr) -> bool {
    match expression {
        Expr::NumberLiteral(_)
        | Expr::StringLiteral(_)
        | Expr::BytesLiteral(_)
        | Expr::BooleanLiteral(_)
        | Expr::NoneLiteral(_)
        | Expr::EllipsisLiteral(_) => true,
        Expr::Name(name) => name.id.as_str() == "__debug__",
        _ => false,
    }
}

fn is_joined_string(expression: &Expr) -> bool {
    match expression {
        Expr::FString(_) => true,
        Expr::BinOp(binary) => {
            binary.op == Operator::Mod && matches!(&*binary.left, Expr::StringLiteral(_))
        }
        _ => false,
    }
}

fn is_display(expression: &Expr) -> bool {
    is_joined_string(expression)
        || matches!(
            expression,
            Expr::Tuple(_)
                | Expr::List(_)
                | Expr::ListComp(_)
                | Expr::Dict(_)
                | Expr::DictComp(_)
                | Expr::Set(_)
                | Expr::SetComp(_)
                | Expr::Generator(_)
                | Expr::TString(_)
        )
}

fn is_identity_literal(expression: &Expr) -> bool {
    match expression {
        Expr::Tuple(tuple) => tuple.elts.iter().all(is_constant),
        Expr::BooleanLiteral(_) | Expr::NoneLiteral(_) | Expr::EllipsisLiteral(_) => false,
        Expr::Name(_) => false,
        _ => is_constant(expression),
    }
}

fn is_unsubscriptable(expression: &Expr) -> bool {
    matches!(
        expression,
        Expr::NumberLiteral(_)
            | Expr::BooleanLiteral(_)
            | Expr::NoneLiteral(_)
            | Expr::EllipsisLiteral(_)
            | Expr::Set(_)
            | Expr::SetComp(_)
            | Expr::Generator(_)
            | Expr::TString(_)
            | Expr::Lambda(_)
    ) || matches!(expression, Expr::Name(name) if name.id.as_str() == "__debug__")
}

fn is_sequence_literal(expression: &Expr) -> bool {
    is_joined_string(expression)
        || matches!(
            expression,
            Expr::StringLiteral(_)
                | Expr::BytesLiteral(_)
                | Expr::Tuple(_)
                | Expr::List(_)
                | Expr::ListComp(_)
        )
}

fn index_type_is_not_integer(index: &Expr) -> bool {
    match index {
        Expr::NumberLiteral(ast::ExprNumberLiteral { value, .. }) => !matches!(value, Number::Int(_)),
        Expr::BooleanLiteral(_) | Expr::Name(_) => false,
        Expr::Lambda(_) => true,
        _ => is_constant(index) || is_display(index),
    }
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
