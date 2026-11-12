//! CPython 3.16's code generator rejects a code unit whose frame blocks nest deeper than
//! `CO_MAXBLOCKS`. This pass mirrors each `_PyCompile_PushFBlock` call in `Python/codegen.c`.

use ruff_python_ast::visitor::{Visitor, walk_expr};
use ruff_python_ast::{self as ast, Expr, Stmt};
use ruff_text_size::{Ranged, TextRange};

use crate::{CompileError, OptimizationLevel};

use super::statement::suite_contains_yield_in_function_scope;

const CO_MAXBLOCKS: usize = 21;

pub(crate) fn validate(
    body: &[Stmt],
    optimization_level: OptimizationLevel,
) -> Result<(), CompileError> {
    let mut limit = BlockLimit {
        future_annotations: body.iter().any(|statement| {
            matches!(statement, Stmt::ImportFrom(import) if import.level == 0
                && import.module.as_deref() == Some("__future__")
                && import.names.iter().any(|alias| alias.name.as_str() == "annotations"))
        }),
        compiles_assertions: optimization_level == OptimizationLevel::Zero,
        exceeded: None,
    };
    limit.body(body, Unit::new(UnitKind::Module, false));
    match limit.exceeded {
        Some(range) => Err(CompileError::invalid_syntax(
            "too many statically nested blocks",
            range,
        )),
        None => Ok(()),
    }
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum UnitKind {
    Module,
    Class,
    Function,
    Annotation,
}

#[derive(Clone, Copy)]
struct Unit {
    kind: UnitKind,
    depth: usize,
    sees_class_scope: bool,
}

impl Unit {
    const fn new(kind: UnitKind, sees_class_scope: bool) -> Self {
        Self {
            kind,
            depth: 0,
            sees_class_scope,
        }
    }

    const fn annotation_scope(self) -> Self {
        Self::new(
            UnitKind::Annotation,
            self.sees_class_scope || matches!(self.kind, UnitKind::Class),
        )
    }
}

struct BlockLimit {
    future_annotations: bool,
    compiles_assertions: bool,
    exceeded: Option<TextRange>,
}

impl BlockLimit {
    fn push(&mut self, unit: Unit, range: TextRange) -> Option<Unit> {
        if self.exceeded.is_some() {
            return None;
        }
        if unit.depth >= CO_MAXBLOCKS {
            self.exceeded = Some(range);
            return None;
        }
        Some(Unit {
            depth: unit.depth + 1,
            ..unit
        })
    }

    fn body(&mut self, body: &[Stmt], unit: Unit) {
        for statement in body {
            if self.exceeded.is_some() {
                return;
            }
            self.statement(statement, unit);
        }
    }

    fn statement(&mut self, statement: &Stmt, unit: Unit) {
        if crate::stack_growth_required() {
            return crate::grow_stack(|| self.statement(statement, unit));
        }
        match statement {
            Stmt::FunctionDef(function) => self.function(function, unit),
            Stmt::ClassDef(class) => self.class(class, unit),
            Stmt::TypeAlias(alias) => {
                let scope = match &alias.type_params {
                    Some(parameters) => self.type_parameters(parameters, unit),
                    None => Unit::new(UnitKind::Annotation, matches!(unit.kind, UnitKind::Class)),
                };
                self.expr(
                    &alias.value,
                    Unit::new(UnitKind::Annotation, scope.sees_class_scope),
                );
            }
            Stmt::Return(statement) => self.optional_expr(statement.value.as_deref(), unit),
            Stmt::Delete(statement) => self.exprs(&statement.targets, unit),
            Stmt::Assign(statement) => {
                self.expr(&statement.value, unit);
                self.exprs(&statement.targets, unit);
            }
            Stmt::AugAssign(statement) => {
                self.expr(&statement.target, unit);
                self.expr(&statement.value, unit);
            }
            Stmt::AnnAssign(statement) => self.annotated_assignment(statement, unit),
            Stmt::For(statement) => {
                if statement.is_async {
                    self.expr(&statement.iter, unit);
                    if let Some(inner) = self.push(unit, statement.range) {
                        self.expr(&statement.target, inner);
                        self.body(&statement.body, inner);
                    }
                } else if let Some(inner) = self.push(unit, statement.range) {
                    self.expr(&statement.iter, inner);
                    self.expr(&statement.target, inner);
                    self.body(&statement.body, inner);
                }
                self.body(&statement.orelse, unit);
            }
            Stmt::While(statement) => {
                if let Some(inner) = self.push(unit, statement.range) {
                    self.expr(&statement.test, inner);
                    self.body(&statement.body, inner);
                }
                self.body(&statement.orelse, unit);
            }
            Stmt::If(statement) => {
                self.expr(&statement.test, unit);
                self.body(&statement.body, unit);
                for clause in &statement.elif_else_clauses {
                    self.optional_expr(clause.test.as_ref(), unit);
                    self.body(&clause.body, unit);
                }
            }
            Stmt::With(statement) => self.with_items(&statement.items, &statement.body, unit),
            Stmt::Match(statement) => {
                self.expr(&statement.subject, unit);
                for case in &statement.cases {
                    self.optional_expr(case.guard.as_deref(), unit);
                    self.body(&case.body, unit);
                }
            }
            Stmt::Raise(statement) => {
                self.optional_expr(statement.exc.as_deref(), unit);
                self.optional_expr(statement.cause.as_deref(), unit);
            }
            Stmt::Try(statement) => self.try_statement(statement, unit),
            Stmt::Assert(statement) => {
                if self.compiles_assertions {
                    self.expr(&statement.test, unit);
                    self.optional_expr(statement.msg.as_deref(), unit);
                }
            }
            Stmt::Expr(statement) => self.expr(&statement.value, unit),
            Stmt::Import(_)
            | Stmt::ImportFrom(_)
            | Stmt::Global(_)
            | Stmt::Nonlocal(_)
            | Stmt::Pass(_)
            | Stmt::Break(_)
            | Stmt::Continue(_)
            | Stmt::IpyEscapeCommand(_) => {}
        }
    }

    fn function(&mut self, function: &ast::StmtFunctionDef, unit: Unit) {
        for decorator in &function.decorator_list {
            self.expr(&decorator.expression, unit);
        }
        self.defaults(&function.parameters, unit);
        let scope = match &function.type_params {
            Some(parameters) => self.type_parameters(parameters, unit),
            None => unit,
        };
        if !self.future_annotations {
            let annotations = scope.annotation_scope();
            for parameter in &function.parameters {
                self.optional_expr(
                    parameter.as_parameter().annotation.as_deref(),
                    annotations,
                );
            }
            self.optional_expr(function.returns.as_deref(), annotations);
        }
        let mut body = Unit::new(UnitKind::Function, false);
        if function.is_async || suite_contains_yield_in_function_scope(&function.body, false) {
            match self.push(body, function.range) {
                Some(inner) => body = inner,
                None => return,
            }
        }
        self.body(&function.body, body);
    }

    fn class(&mut self, class: &ast::StmtClassDef, unit: Unit) {
        for decorator in &class.decorator_list {
            self.expr(&decorator.expression, unit);
        }
        let header = match &class.type_params {
            Some(parameters) => self.type_parameters(parameters, unit),
            None => unit,
        };
        if let Some(arguments) = &class.arguments {
            self.exprs(&arguments.args, header);
            for keyword in &arguments.keywords {
                self.expr(&keyword.value, header);
            }
        }
        self.body(&class.body, Unit::new(UnitKind::Class, false));
    }

    fn type_parameters(&mut self, parameters: &ast::TypeParams, unit: Unit) -> Unit {
        let scope = Unit::new(UnitKind::Annotation, matches!(unit.kind, UnitKind::Class));
        for parameter in parameters {
            if let ast::TypeParam::TypeVar(parameter) = parameter {
                self.optional_expr(parameter.bound.as_deref(), scope);
            }
            self.optional_expr(parameter.default(), scope);
        }
        scope
    }

    fn defaults(&mut self, parameters: &ast::Parameters, unit: Unit) {
        for parameter in parameters.iter_non_variadic_params() {
            self.optional_expr(parameter.default.as_deref(), unit);
        }
    }

    fn annotated_assignment(&mut self, statement: &ast::StmtAnnAssign, unit: Unit) {
        if let Some(value) = &statement.value {
            self.expr(value, unit);
            self.expr(&statement.target, unit);
        }
        match statement.target.as_ref() {
            Expr::Name(_) => {
                if statement.simple
                    && !self.future_annotations
                    && matches!(unit.kind, UnitKind::Module | UnitKind::Class)
                {
                    self.expr(&statement.annotation, unit.annotation_scope());
                }
            }
            Expr::Attribute(attribute) if statement.value.is_none() => {
                self.expr(&attribute.value, unit);
            }
            Expr::Subscript(subscript) if statement.value.is_none() => {
                self.expr(&subscript.value, unit);
                self.expr(&subscript.slice, unit);
            }
            _ => {}
        }
    }

    fn with_items(&mut self, items: &[ast::WithItem], body: &[Stmt], unit: Unit) {
        let Some((item, rest)) = items.split_first() else {
            self.body(body, unit);
            return;
        };
        self.expr(&item.context_expr, unit);
        if let Some(inner) = self.push(unit, item.context_expr.range()) {
            self.optional_expr(item.optional_vars.as_deref(), inner);
            self.with_items(rest, body, inner);
        }
    }

    fn try_statement(&mut self, statement: &ast::StmtTry, unit: Unit) {
        if statement.finalbody.is_empty() {
            self.try_except(statement, unit);
            return;
        }
        if let Some(inner) = self.push(unit, statement.range) {
            if statement.handlers.is_empty() {
                self.body(&statement.body, inner);
            } else {
                self.try_except(statement, inner);
            }
        }
        self.body(&statement.finalbody, unit);
        if let Some(inner) = self.push(unit, statement.range) {
            self.body(&statement.finalbody, inner);
        }
    }

    fn try_except(&mut self, statement: &ast::StmtTry, unit: Unit) {
        if let Some(inner) = self.push(unit, statement.range) {
            self.body(&statement.body, inner);
        }
        self.body(&statement.orelse, unit);
        let Some(handlers) = self.push(unit, statement.range) else {
            return;
        };
        for handler in &statement.handlers {
            let ast::ExceptHandler::ExceptHandler(handler) = handler;
            self.optional_expr(handler.type_.as_deref(), handlers);
            if let Some(cleanup) = self.push(handlers, handler.range) {
                self.body(&handler.body, cleanup);
            }
        }
    }

    fn exprs(&mut self, expressions: &[Expr], unit: Unit) {
        for expression in expressions {
            self.expr(expression, unit);
        }
    }

    fn optional_expr(&mut self, expression: Option<&Expr>, unit: Unit) {
        if let Some(expression) = expression {
            self.expr(expression, unit);
        }
    }

    fn expr(&mut self, expression: &Expr, unit: Unit) {
        if self.exceeded.is_none() {
            ExpressionBlocks { limit: self, unit }.visit_expr(expression);
        }
    }

    fn comprehension(
        &mut self,
        range: TextRange,
        generators: &[ast::Comprehension],
        elements: &[Option<&Expr>],
        inlined: bool,
        unit: Unit,
    ) {
        let Some(first) = generators.first() else {
            return;
        };
        self.expr(&first.iter, unit);
        let mut inner = if inlined {
            if generators.iter().any(|generator| binds_name(&generator.target)) {
                match self.push(unit, range) {
                    Some(inner) => inner,
                    None => return,
                }
            } else {
                unit
            }
        } else {
            Unit::new(UnitKind::Function, false)
        };
        for (index, generator) in generators.iter().enumerate() {
            if index > 0 {
                self.expr(&generator.iter, inner);
            }
            if generator.is_async {
                match self.push(inner, generator.range) {
                    Some(nested) => inner = nested,
                    None => return,
                }
            }
            self.expr(&generator.target, inner);
            self.exprs(&generator.ifs, inner);
        }
        for element in elements {
            self.optional_expr(*element, inner);
        }
    }
}

fn binds_name(target: &Expr) -> bool {
    match target {
        Expr::Name(_) => true,
        Expr::Starred(starred) => binds_name(&starred.value),
        Expr::Tuple(tuple) => tuple.elts.iter().any(binds_name),
        Expr::List(list) => list.elts.iter().any(binds_name),
        _ => false,
    }
}

struct ExpressionBlocks<'limit> {
    limit: &'limit mut BlockLimit,
    unit: Unit,
}

impl<'ast> Visitor<'ast> for ExpressionBlocks<'_> {
    fn visit_expr(&mut self, expression: &'ast Expr) {
        if crate::stack_growth_required() {
            return crate::grow_stack(|| self.visit_expr(expression));
        }
        if self.limit.exceeded.is_some() {
            return;
        }
        let inlined = !self.unit.sees_class_scope;
        match expression {
            Expr::ListComp(comprehension) => self.limit.comprehension(
                comprehension.range,
                &comprehension.generators,
                &[Some(&comprehension.elt)],
                inlined,
                self.unit,
            ),
            Expr::SetComp(comprehension) => self.limit.comprehension(
                comprehension.range,
                &comprehension.generators,
                &[Some(&comprehension.elt)],
                inlined,
                self.unit,
            ),
            Expr::DictComp(comprehension) => self.limit.comprehension(
                comprehension.range,
                &comprehension.generators,
                &[comprehension.key.as_deref(), Some(&comprehension.value)],
                inlined,
                self.unit,
            ),
            Expr::Generator(generator) => self.limit.comprehension(
                generator.range,
                &generator.generators,
                &[Some(&generator.elt)],
                false,
                self.unit,
            ),
            Expr::Lambda(lambda) => {
                if let Some(parameters) = &lambda.parameters {
                    self.limit.defaults(parameters, self.unit);
                }
                self.limit
                    .expr(&lambda.body, Unit::new(UnitKind::Function, false));
            }
            _ => walk_expr(self, expression),
        }
    }
}
