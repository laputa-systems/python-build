//! The C entry points through which a `--serc` interpreter's compile path calls Serc.
//!
//! `Python/pythonrun.c` declares the same functions, status codes, and buffer layout.

use std::ffi::c_int;
use std::ptr;
use std::slice;

use serc::{CompileOptions, OptimizationLevel, PythonVersion};

/// Bytes that Rust owns and lends to C until [`_PySerc_ReleaseBuffer`] returns them.
#[repr(C)]
pub struct SercBuffer {
    data: *mut u8,
    size: usize,
}

/// The buffer holds the module's marshal bytes and CPython emits no warning for its source.
pub const COMPILED: c_int = 0;
/// The buffer holds the module's marshal bytes and CPython may emit a warning for its source.
pub const COMPILED_MAY_WARN: c_int = 1;
/// Serc refused the source; the buffer holds its UTF-8 reason.
pub const REFUSED: c_int = 2;
/// The source uses grammar or control flow that only CPython's parser or compiler handles, so
/// the C compiler compiles it; the buffer holds the UTF-8 reason.
pub const CPYTHON_GRAMMAR: c_int = 3;

/// Compile one module for CPython 3.16 into `output`, returning one of the status codes.
///
/// `optimization_level` is 0, 1, or 2. `int_max_str_digits` is the interpreter's current
/// integer-string conversion limit. A nonzero `source_is_text` marks already-decoded UTF-8
/// text; otherwise the source is undecoded bytes, which must be ones that
/// [`_PySerc_DecodesAsUtf8`] accepts.
///
/// # Safety
///
/// `source` and `filename` must point to `source_size` and `filename_size` readable bytes, and
/// `output` to writable storage for one [`SercBuffer`] that the caller later passes to
/// [`_PySerc_ReleaseBuffer`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn _PySerc_CompileModule(
    source: *const u8,
    source_size: usize,
    source_is_text: c_int,
    filename: *const u8,
    filename_size: usize,
    optimization_level: c_int,
    int_max_str_digits: c_int,
    output: *mut SercBuffer,
) -> c_int {
    let source = unsafe { slice::from_raw_parts(source, source_size) };
    let filename = unsafe { slice::from_raw_parts(filename, filename_size) };
    let (status, bytes) = match compile(
        source,
        source_is_text != 0,
        filename,
        optimization_level,
        int_max_str_digits,
    ) {
        Ok(module) => (
            if module.may_warn() {
                COMPILED_MAY_WARN
            } else {
                COMPILED
            },
            module.marshal(),
        ),
        Err(CompileFailure::CpythonGrammar(reason)) => (CPYTHON_GRAMMAR, reason.into_bytes()),
        Err(CompileFailure::Refused(reason)) => (REFUSED, reason.into_bytes()),
    };
    let bytes = bytes.into_boxed_slice();
    let size = bytes.len();
    unsafe {
        output.write(SercBuffer {
            data: Box::into_raw(bytes).cast::<u8>(),
            size,
        });
    }
    status
}

enum CompileFailure {
    CpythonGrammar(String),
    Refused(String),
}

fn compile(
    source: &[u8],
    source_is_text: bool,
    filename: &[u8],
    optimization_level: c_int,
    int_max_str_digits: c_int,
) -> Result<serc::CompiledModule, CompileFailure> {
    let optimization_level = match optimization_level {
        0 => OptimizationLevel::Zero,
        1 => OptimizationLevel::One,
        2 => OptimizationLevel::Two,
        level => {
            return Err(CompileFailure::Refused(format!(
                "invalid optimization level {level}"
            )));
        }
    };
    let limit = u32::try_from(int_max_str_digits).map_err(|_| {
        CompileFailure::Refused(format!("invalid integer string limit {int_max_str_digits}"))
    })?;
    let options = CompileOptions {
        python_version: PythonVersion::Py316,
        optimization_level,
        ..CompileOptions::default()
    };
    let compiled = if source_is_text {
        let text = std::str::from_utf8(source).map_err(|error| {
            CompileFailure::CpythonGrammar(format!("source text is not UTF-8: {error}"))
        })?;
        serc::compile_with_python_filename_and_integer_limits(
            text,
            filename,
            &options,
            limit,
            Some(limit),
        )
    } else {
        serc::compile_bytes_with_python_filename_and_integer_limits(
            source,
            filename,
            &options,
            limit,
            Some(limit),
        )
    };
    compiled.map_err(|error| match error {
        serc::CompileError::RequiresCpythonParser(_) | serc::CompileError::RequiresCpythonCompiler(_) => {
            CompileFailure::CpythonGrammar(error.to_string())
        }
        error => CompileFailure::Refused(error.to_string()),
    })
}

/// Report whether CPython decodes this undecoded source as UTF-8 without its codec registry.
///
/// # Safety
///
/// `source` must point to `source_size` readable bytes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn _PySerc_DecodesAsUtf8(source: *const u8, source_size: usize) -> c_int {
    let source = unsafe { slice::from_raw_parts(source, source_size) };
    c_int::from(serc::decodes_as_utf8(source))
}

/// Free a buffer that [`_PySerc_CompileModule`] filled, leaving it empty.
///
/// # Safety
///
/// `buffer` must point to a [`SercBuffer`] that is empty or was filled by
/// [`_PySerc_CompileModule`] and not released since.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn _PySerc_ReleaseBuffer(buffer: *mut SercBuffer) {
    let buffer = unsafe { &mut *buffer };
    if !buffer.data.is_null() {
        drop(unsafe { Box::from_raw(ptr::slice_from_raw_parts_mut(buffer.data, buffer.size)) });
        buffer.data = ptr::null_mut();
        buffer.size = 0;
    }
}
