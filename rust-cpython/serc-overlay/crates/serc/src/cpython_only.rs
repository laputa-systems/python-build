use ruff_python_ast::visitor::{Visitor, walk_expr, walk_stmt};
use ruff_python_ast::{Expr, Stmt};

/// The deepest statement and expression nesting Serc compiles.
///
/// CPython's parser stops at a fixed recursion budget whose cost per nesting level depends
/// on the grammar rules involved; the cheapest observed overflow is at 747 nested lambda
/// defaults. Serc does not model that budget, so it leaves deeper sources to CPython.
pub(crate) const MAX_NESTING: usize = 200;

/// Name the grammar in this module that only CPython's own parser defines, if any.
///
/// The `barry_as_FLUFL` future changes the grammar of the module that imports it, and nesting
/// deeper than [`MAX_NESTING`] approaches CPython's parser recursion limit.
pub(crate) fn grammar(suite: &[Stmt]) -> Option<&'static str> {
    if imports_barry_as_flufl(suite) {
        return Some("the barry_as_FLUFL future changes the grammar");
    }
    let mut nesting = Nesting {
        depth: 0,
        exceeded: false,
    };
    nesting.visit_body(suite);
    nesting
        .exceeded
        .then_some("the source nests more than 200 statements and expressions deep")
}

fn imports_barry_as_flufl(suite: &[Stmt]) -> bool {
    suite
        .iter()
        .enumerate()
        .take_while(|(index, statement)| match statement {
            Stmt::Expr(expression) => *index == 0 && expression.value.is_string_literal_expr(),
            Stmt::ImportFrom(import) => {
                import.level == 0
                    && import
                        .module
                        .as_ref()
                        .is_some_and(|module| module.as_str() == "__future__")
            }
            _ => false,
        })
        .any(|(_, statement)| {
            matches!(statement, Stmt::ImportFrom(import)
                if import.names.iter().any(|alias| alias.name.as_str() == "barry_as_FLUFL"))
        })
}

struct Nesting {
    depth: usize,
    exceeded: bool,
}

impl<'a> Visitor<'a> for Nesting {
    fn visit_stmt(&mut self, statement: &'a Stmt) {
        if self.exceeded {
            return;
        }
        self.depth += 1;
        if self.depth > MAX_NESTING {
            self.exceeded = true;
        } else {
            walk_stmt(self, statement);
        }
        self.depth -= 1;
    }

    fn visit_expr(&mut self, expression: &'a Expr) {
        if self.exceeded {
            return;
        }
        self.depth += 1;
        if self.depth > MAX_NESTING {
            self.exceeded = true;
        } else {
            walk_expr(self, expression);
        }
        self.depth -= 1;
    }
}

#[cfg(test)]
mod tests {
    use crate::{CompileError, CompileOptions, PythonVersion, compile_with_options};

    fn compile(source: &str) -> Result<crate::CompiledModule, CompileError> {
        let options = CompileOptions {
            python_version: PythonVersion::Py316,
            ..CompileOptions::default()
        };
        compile_with_options(source, "<test>", &options)
    }

    #[test]
    fn grammar_only_cpython_defines_is_left_to_cpython() {
        for source in [
            "from __future__ import barry_as_FLUFL\nx = 1 <> 2\n".to_owned(),
            "'''doc'''\nfrom __future__ import annotations, barry_as_FLUFL\n".to_owned(),
            format!("x = {}1\n", "-".repeat(super::MAX_NESTING)),
            format!("x = 1{}\n", " + 1".repeat(super::MAX_NESTING)),
        ] {
            assert!(
                matches!(compile(&source), Err(CompileError::RequiresCpythonParser(_))),
                "{source:?}"
            );
        }
    }

    #[test]
    fn ordinary_nesting_and_futures_compile() {
        for source in [
            "from __future__ import annotations\nx = 1\n".to_owned(),
            "x = 1\nfrom __future__ import barry_as_FLUFL\n".to_owned(),
            format!("x = {}1\n", "-".repeat(super::MAX_NESTING - 3)),
        ] {
            assert!(
                !matches!(compile(&source), Err(CompileError::RequiresCpythonParser(_))),
                "{source:?}"
            );
        }
    }
}
