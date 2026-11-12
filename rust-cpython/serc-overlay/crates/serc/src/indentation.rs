use ruff_python_ast::token::{TokenKind, Tokens};
use ruff_text_size::{Ranged, TextRange};

use crate::CompileError;

/// CPython's tokenizer has 100 indentation slots, including the module-level slot.
/// Count logical INDENT/DEDENT tokens so strings, comments, continuation lines and
/// indentation width do not consume extra slots. Run before semantic validation or
/// lowering can discard unreachable suites.
pub(crate) fn first_excess(tokens: &Tokens) -> Option<TextRange> {
    let mut depth = 0;
    for token in tokens {
        match token.kind() {
            TokenKind::Indent => {
                depth += 1;
                if depth == 100 {
                    return Some(token.range());
                }
            }
            TokenKind::Dedent => depth -= 1,
            _ => {}
        }
    }
    None
}

pub(crate) fn excess_error(range: TextRange) -> CompileError {
    CompileError::invalid_syntax("too many levels of indentation", range)
}

/// CPython's space and tab widths for one indentation level.
const TABSIZE: usize = 8;
const ALTTABSIZE: usize = 1;

/// Report whether CPython's tokenizer rejects this source's indentation.
///
/// CPython tracks each indentation level as a pair of columns: one with tabs expanded to
/// eight, and one with tabs expanded to one. Ruff compares a single column, so a source
/// can nest an `if` body at a width CPython's stack does not contain. The caller leaves
/// such a module to CPython, which raises the exact `IndentationError` or `TabError`.
pub(crate) fn cpython_layout_error(source: &str, tokens: &Tokens) -> bool {
    let bytes = source.as_bytes();
    // CPython also checks the column of backslash continuation lines
    // (`cont_line_col`), which this replay does not model. Leave any module that mixes
    // tabs with backslash continuations to CPython; routing is exact.
    if bytes.contains(&b'\t')
        && bytes
            .windows(2)
            .any(|pair| pair == b"\\\n" || pair == b"\\\r")
    {
        return true;
    }
    let mut stack = vec![(0usize, 0usize)];
    let mut at_logical_start = true;
    for token in tokens {
        match token.kind() {
            TokenKind::Newline => {
                at_logical_start = true;
                continue;
            }
            TokenKind::Indent | TokenKind::Dedent | TokenKind::EndOfFile => continue,
            kind if kind.is_trivia() => continue,
            _ => {}
        }
        if !at_logical_start {
            continue;
        }
        at_logical_start = false;
        let start = usize::from(token.start());
        let line_start = bytes[..start]
            .iter()
            .rposition(|byte| *byte == b'\n')
            .map_or(0, |index| index + 1);
        let mut column = 0;
        let mut alternate = 0;
        for &byte in &bytes[line_start..start] {
            match byte {
                b' ' => {
                    column += 1;
                    alternate += 1;
                }
                b'\t' => {
                    column = (column / TABSIZE + 1) * TABSIZE;
                    alternate = (alternate / ALTTABSIZE + 1) * ALTTABSIZE;
                }
                0x0c => {
                    column = 0;
                    alternate = 0;
                }
                _ => return true,
            }
        }
        let &(top_column, top_alternate) = stack.last().expect("module level is present");
        if column == top_column {
            if alternate != top_alternate {
                return true;
            }
        } else if column > top_column {
            if alternate <= top_alternate {
                return true;
            }
            stack.push((column, alternate));
        } else {
            while stack.len() > 1 && column < stack.last().expect("module level is present").0 {
                stack.pop();
            }
            let &(top_column, top_alternate) = stack.last().expect("module level is present");
            if column != top_column || alternate != top_alternate {
                return true;
            }
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use ruff_python_parser::{Mode, ParseOptions, parse_unchecked};

    fn layout_error(source: &str) -> bool {
        let parsed = parse_unchecked(source, ParseOptions::from(Mode::Module));
        super::cpython_layout_error(source, parsed.tokens())
    }

    #[test]
    fn mixed_tab_and_space_nesting_is_left_to_cpython() {
        for source in [
            "if a:\n \t   if b:\n          pass\n",
            "if a:\n \t   if b:\n          x = 1\n",
            "if a:\n \t   if b:\n          if c:\n                   pass\n",
        ] {
            assert!(layout_error(source), "{source:?}");
        }
    }

    #[test]
    fn consistent_indentation_compiles() {
        for source in [
            "if a:\n\tif b:\n\t\tpass\n",
            "if a:\n    if b:\n        pass\n",
            "def f():\n \t       return 1\n",
            "x = 1\nif a:\n\tx = 2\n",
        ] {
            assert!(!layout_error(source), "{source:?}");
        }
    }
}
