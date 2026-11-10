use ruff_python_ast::token::{TokenKind, Tokens};
use ruff_python_ast::visitor::{Visitor, walk_expr, walk_stmt};
use ruff_python_ast::{
    Expr, InterpolatedStringElement, InterpolatedStringElements, InterpolatedStringFormatSpec,
    Pattern, Stmt,
};
use ruff_python_parser::{LexicalErrorType, ParseError, ParseErrorType};

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

/// CPython's PEG parser stops at 6000 stack slots (`MAXSTACK`), and Serc does not
/// model that budget exactly. Measured on the C compiler, an `if`/`elif` arm costs one
/// slot (5965 bare arms parse, 5966 overflow), as does a unary or `not` level (5965
/// nested unary minuses parse); a bracket level costs about 27 (with 199 nested
/// parentheses per arm, 626 arms parse and 627 overflow; 190 parentheses leave room for
/// 853 unary minuses); a compound statement level about 7 (inside 99 nested `def`s, 196
/// parentheses parse and 197 overflow). A module whose estimate reaches this budget is
/// left to CPython; routing is exact, so the margin below 6000 only costs Serc coverage
/// on pathological sources.
pub(crate) const PARSER_STACK_BUDGET: usize = 5000;

/// Estimated CPython parser-stack slots per bracket nesting level.
pub(crate) const PARSER_STACK_PER_BRACKET: usize = 27;

/// Estimated CPython parser-stack slots per compound statement nesting level.
pub(crate) const PARSER_STACK_PER_BLOCK: usize = 7;

/// CPython's f-string lexer allows three nested replacement fields per literal.
pub(crate) const MAX_FORMAT_SPEC_DEPTH: usize = 3;

/// Name the source form that Serc must leave to CPython's parser and symbol table.
///
/// Each of these is valid to Ruff but rejected by CPython after or during its parse:
/// a parenthesized assignment-expression target, a `__debug__` match-pattern capture,
/// a format spec nested deeper than CPython's lexer allows, and a module whose estimated
/// parser stack approaches CPython's limit.
pub(crate) fn source_rules(suite: &[Stmt], tokens: &Tokens) -> Option<&'static str> {
    if assignment_expression_after_parenthesized_target(tokens) {
        return Some("a parenthesized assignment-expression target is invalid");
    }
    if captures_debug_pattern(suite) {
        return Some("a __debug__ match capture is invalid");
    }
    if has_deep_format_spec(suite) {
        return Some("an f-string format spec nests more expressions than CPython allows");
    }
    if parser_stack_estimate(suite, tokens) >= PARSER_STACK_BUDGET {
        return Some("the source approaches CPython's parser stack limit");
    }
    None
}

/// Name a `\N{...}` escape that Ruff's Unicode table rejected, if any.
///
/// Ruff pins `unicode_names2` 1.3.0, which predates the interpreter's Unicode data. Routing
/// the module to CPython resolves every name, and CPython rejects an unknown name itself.
pub(crate) fn unicode_name_escape(errors: &[ParseError]) -> Option<&'static str> {
    errors
        .iter()
        .any(|error| {
            matches!(
                error.error,
                ParseErrorType::Lexical(LexicalErrorType::UnicodeError)
            )
        })
        .then_some("a \\N{...} escape needs CPython's Unicode name table")
}

/// Ruff accepts `((x) := 1)` and `if (x) := 1:` where CPython reports an invalid target.
/// In valid source the token before `:=` is always the NAME it binds.
fn assignment_expression_after_parenthesized_target(tokens: &Tokens) -> bool {
    let mut previous = None;
    for token in tokens {
        let kind = token.kind();
        if kind == TokenKind::ColonEqual && previous == Some(TokenKind::Rpar) {
            return true;
        }
        if !kind.is_trivia() {
            previous = Some(kind);
        }
    }
    false
}

/// CPython's symbol table rejects every match capture that binds `__debug__`; Ruff's
/// parser leaves capture names to that later pass.
fn captures_debug_pattern(suite: &[Stmt]) -> bool {
    struct CapturesDebug {
        found: bool,
    }

    impl<'a> Visitor<'a> for CapturesDebug {
        fn visit_stmt(&mut self, statement: &'a Stmt) {
            if self.found {
                return;
            }
            if let Stmt::Match(match_statement) = statement
                && match_statement
                    .cases
                    .iter()
                    .any(|case| binds_debug(&case.pattern))
            {
                self.found = true;
            } else {
                walk_stmt(self, statement);
            }
        }
    }

    let mut captures = CapturesDebug { found: false };
    captures.visit_body(suite);
    captures.found
}

fn binds_debug(pattern: &Pattern) -> bool {
    match pattern {
        Pattern::MatchAs(pattern) => {
            pattern
                .name
                .as_ref()
                .is_some_and(|name| name.as_str() == "__debug__")
                || pattern.pattern.as_deref().is_some_and(binds_debug)
        }
        Pattern::MatchStar(pattern) => pattern
            .name
            .as_ref()
            .is_some_and(|name| name.as_str() == "__debug__"),
        Pattern::MatchMapping(pattern) => {
            pattern
                .rest
                .as_ref()
                .is_some_and(|name| name.as_str() == "__debug__")
                || pattern.patterns.iter().any(binds_debug)
        }
        Pattern::MatchSequence(pattern) => pattern.patterns.iter().any(binds_debug),
        Pattern::MatchOr(pattern) => pattern.patterns.iter().any(binds_debug),
        Pattern::MatchClass(pattern) => {
            pattern.arguments.patterns.iter().any(binds_debug)
                || pattern.arguments.keywords.iter().any(|keyword| {
                    keyword.attr.as_str() == "__debug__" || binds_debug(&keyword.pattern)
                })
        }
        Pattern::MatchValue(_) | Pattern::MatchSingleton(_) => false,
    }
}

/// CPython's lexer stops at `MAX_EXPR_NESTING` replacement fields inside one f-string or
/// t-string. A nested literal starts a new budget, so only format-spec elements count.
fn has_deep_format_spec(suite: &[Stmt]) -> bool {
    struct DeepFormatSpec {
        found: bool,
    }

    impl<'a> Visitor<'a> for DeepFormatSpec {
        fn visit_expr(&mut self, expression: &'a Expr) {
            if self.found {
                return;
            }
            let too_deep = match expression {
                Expr::FString(fstring) => fstring
                    .value
                    .f_strings()
                    .any(|literal| format_spec_depth(&literal.elements) > MAX_FORMAT_SPEC_DEPTH),
                Expr::TString(tstring) => tstring
                    .value
                    .as_slice()
                    .iter()
                    .any(|literal| format_spec_depth(&literal.elements) > MAX_FORMAT_SPEC_DEPTH),
                _ => false,
            };
            if too_deep {
                self.found = true;
            } else {
                walk_expr(self, expression);
            }
        }
    }

    let mut deep = DeepFormatSpec { found: false };
    deep.visit_body(suite);
    deep.found
}

fn format_spec_depth(elements: &InterpolatedStringElements) -> usize {
    elements
        .iter()
        .map(format_spec_element_depth)
        .max()
        .unwrap_or(0)
}

fn format_spec_element_depth(element: &InterpolatedStringElement) -> usize {
    match element {
        InterpolatedStringElement::Interpolation(element) => {
            1 + element
                .format_spec
                .as_deref()
                .map_or(0, format_spec_nested_depth)
        }
        InterpolatedStringElement::Literal(_) => 0,
    }
}

fn format_spec_nested_depth(spec: &InterpolatedStringFormatSpec) -> usize {
    spec.elements
        .iter()
        .map(format_spec_element_depth)
        .max()
        .unwrap_or(0)
}

/// Estimate CPython's peak parser stack as the sum of the deepest bracket nesting at
/// [`PARSER_STACK_PER_BRACKET`] slots per level, the deepest compound statement nesting
/// at [`PARSER_STACK_PER_BLOCK`], the deepest expression nesting and the longest
/// `if`/`elif` chain at one slot each. Summing module-wide maxima only over-estimates.
pub(crate) fn parser_stack_estimate(suite: &[Stmt], tokens: &Tokens) -> usize {
    #[derive(Default)]
    struct Shape {
        blocks: usize,
        max_blocks: usize,
        expressions: usize,
        max_expressions: usize,
        max_arms: usize,
    }

    impl<'a> Visitor<'a> for Shape {
        fn visit_stmt(&mut self, statement: &'a Stmt) {
            if let Stmt::If(if_statement) = statement {
                self.max_arms = self.max_arms.max(if_statement.elif_else_clauses.len() + 1);
            }
            self.blocks += 1;
            self.max_blocks = self.max_blocks.max(self.blocks);
            walk_stmt(self, statement);
            self.blocks -= 1;
        }

        fn visit_expr(&mut self, expression: &'a Expr) {
            self.expressions += 1;
            self.max_expressions = self.max_expressions.max(self.expressions);
            walk_expr(self, expression);
            self.expressions -= 1;
        }
    }

    // Redundant parentheses leave no AST node, so bracket depth comes from the tokens.
    let mut brackets = 0usize;
    let mut max_brackets = 0usize;
    for token in tokens {
        match token.kind() {
            TokenKind::Lpar | TokenKind::Lsqb | TokenKind::Lbrace => {
                brackets += 1;
                max_brackets = max_brackets.max(brackets);
            }
            TokenKind::Rpar | TokenKind::Rsqb | TokenKind::Rbrace => {
                brackets = brackets.saturating_sub(1);
            }
            _ => {}
        }
    }
    let mut shape = Shape::default();
    shape.visit_body(suite);
    PARSER_STACK_PER_BRACKET * max_brackets
        + PARSER_STACK_PER_BLOCK * shape.max_blocks
        + shape.max_expressions
        + shape.max_arms
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

    #[test]
    fn parenthesized_assignment_expression_targets_are_left_to_cpython() {
        for source in [
            "((x) := 1)\n",
            "(((x)) := 1)\n",
            "if (x) := 1: pass\n",
            "if ((x) := 1): pass\n",
            "while (x) := 1: pass\n",
            "print((x) := 1)\n",
        ] {
            assert!(
                matches!(compile(source), Err(CompileError::RequiresCpythonParser(_))),
                "{source:?}"
            );
        }
        for source in [
            "(x := 1)\n",
            "((x := 1))\n",
            "if (x := 1): pass\n",
            "(x) = 1\n",
        ] {
            assert!(
                !matches!(compile(source), Err(CompileError::RequiresCpythonParser(_))),
                "{source:?}"
            );
        }
    }

    #[test]
    fn debug_pattern_captures_are_left_to_cpython() {
        for source in [
            "match 1:\n case __debug__: pass\n",
            "match 1:\n case __debug__ if True: pass\n",
            "match 1:\n case (__debug__): pass\n",
            "match [1]:\n case [*__debug__]: pass\n",
            "match 1:\n case {**__debug__}: pass\n",
            "match 1:\n case C(__debug__=x): pass\n",
        ] {
            assert!(
                matches!(compile(source), Err(CompileError::RequiresCpythonParser(_))),
                "{source:?}"
            );
        }
        for source in [
            "x = __debug__\n",
            "match 1:\n case x if x is __debug__: pass\n",
            "match 1:\n case [*rest]: pass\n",
        ] {
            assert!(
                !matches!(compile(source), Err(CompileError::RequiresCpythonParser(_))),
                "{source:?}"
            );
        }
    }

    #[test]
    fn deep_format_specs_are_left_to_cpython() {
        for source in [
            "f'{x:{y:{z:{w}}}}'\n",
            "f'{x:{y:{z:{w:{v}}}}}'\n",
            "t'{x:{y:{z:{w}}}}'\n",
        ] {
            assert!(
                matches!(compile(source), Err(CompileError::RequiresCpythonParser(_))),
                "{source:?}"
            );
        }
        for source in [
            "f'{x:{y}}'\n",
            "f'{x:{y:{z}}}'\n",
            "f'{x:{f\"{y:{z:{w}}}\"}}'\n",
            "t'{x:{y:{z}}}'\n",
            "t'{x:{t\"{y:{z:{w}}}\"}}'\n",
        ] {
            assert!(
                !matches!(compile(source), Err(CompileError::RequiresCpythonParser(_))),
                "{source:?}"
            );
        }
    }

    #[test]
    fn sources_near_cpythons_parser_stack_are_left_to_cpython() {
        let chain = |arms: usize, parens: usize| {
            let body = format!("x = {}1{}", "(".repeat(parens), ")".repeat(parens));
            format!("if x:\n    {body}\n") + &format!("elif x:\n    {body}\n").repeat(arms - 1)
        };
        let nested_defs = |depth: usize, parens: usize| {
            let mut source = String::new();
            for level in 0..depth {
                source += &format!("{}def f():\n", " ".repeat(level));
            }
            source + &format!("{}x = {}1{}\n", " ".repeat(depth), "(".repeat(parens), ")".repeat(parens))
        };
        // Each overflows CPython's parser (measured on the C compiler) and must route.
        for source in [
            chain(5966, 0),
            chain(6000, 0),
            chain(4650, 50),
            chain(1950, 150),
            chain(627, 199),
            nested_defs(99, 199),
            format!("x = {}{}1{}\n", "(".repeat(190), "-".repeat(854), ")".repeat(190)),
        ] {
            assert!(
                matches!(compile(&source), Err(CompileError::RequiresCpythonParser(_))),
                "{}",
                &source[..source.len().min(60)]
            );
        }
        for source in [chain(1, 0), chain(100, 0), chain(4000, 0), chain(2, 150), nested_defs(99, 100)] {
            assert!(
                !matches!(compile(&source), Err(CompileError::RequiresCpythonParser(_))),
                "{}",
                &source[..source.len().min(60)]
            );
        }
    }

    #[test]
    fn unicode_name_escapes_are_left_to_cpython() {
        for source in [
            "x = '\\N{MODIFIER LETTER ARMENIAN SMALL EH}'\n",
            "x = '\\N{NO SUCH NAME}'\n",
        ] {
            assert!(
                matches!(compile(source), Err(CompileError::RequiresCpythonParser(_))),
                "{source:?}"
            );
        }
        for source in ["x = '\\N{EM DASH}'\n", "x = '\\N{LATIN SMALL LETTER A}'\n"] {
            assert!(
                !matches!(compile(source), Err(CompileError::RequiresCpythonParser(_))),
                "{source:?}"
            );
        }
    }
}
