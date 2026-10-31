use std::ffi::CStr;
use std::mem;

use cpython_binding::arguments::{c_int_argument, exactly, index_argument};
use cpython_binding::ffi::{PyMethodDef, PyObject, Py_ssize_t};
use cpython_binding::module::PER_INTERPRETER_GIL;
use cpython_binding::values::boolean;
use cpython_binding::{
    BytesSink,
    Borrowed, Buffer, BytesWriter, CapsuleState, Exception, Module, Outcome, Owned, capsule_state, fastcall, fastcall_arguments, into_return, method_table_end, new_capsule,
    raise,
};
use cpython_deflate::{
    Deflater, InflateFlush, Inflater, RAW_WINDOW, ZLIB_WINDOW, Z_BUF_ERROR, Z_FINISH,
    Z_NO_FLUSH, Z_OK, Z_STREAM_END, compress_capacity_hint, copy_failure, crc32_call,
    decompress_capacity_hint, setup_failure, zlib_failure,
};

struct Compressor(Deflater);

impl CapsuleState for Compressor {
    const NAME: &'static CStr = c"_zlib_rs.Compressor";
}

struct Decompressor {
    stream: Inflater,
    eof: bool,
    unused_data: Vec<u8>,
    unconsumed_tail: Vec<u8>,
}

impl CapsuleState for Decompressor {
    const NAME: &'static CStr = c"_zlib_rs.Decompressor";
}

fn level_argument(object: Borrowed<'_>) -> Outcome<i32> {
    let level = c_int_argument(object)?;
    if !(-1..=9).contains(&level) {
        return Err(raise(Exception::ValueError, c"Bad compression level"));
    }
    Ok(level)
}

fn window_argument(object: Borrowed<'_>) -> Outcome<i32> {
    let window = c_int_argument(object)?;
    if window != ZLIB_WINDOW && window != RAW_WINDOW {
        return Err(raise(
            Exception::ValueError,
            c"unsupported DEFLATE window size",
        ));
    }
    Ok(window)
}

fn decompressor_state<'a>(object: Borrowed<'a>) -> Outcome<&'a mut Decompressor> {
    unsafe { capsule_state::<Decompressor>(object) }
}

fn compress_once_call(arguments: &[Borrowed<'_>]) -> Outcome<Owned> {
    let [input, level, window] = *exactly::<3>(arguments, c"compress() takes exactly three arguments")?;
    let input = Buffer::acquire(input)?;
    let level = level_argument(level)?;
    let window = window_argument(window)?;
    let input = input.as_bytes();
    let mut compressor = Deflater::new(level, window, None)
        .map_err(|code| setup_failure(code, "while compressing data"))?;
    let bound = compressor.bound(input.len());
    let mut output = BytesSink::with_capacity(bound)?;
    compressor
        .run(input, Z_FINISH, &mut output)
        .map_err(|error| compressor.failure(error, "while compressing data"))?;
    let code = compressor.end();
    if code != Z_OK {
        return Err(zlib_failure(
            compressor.stream(),
            code,
            "while finishing compression",
        ));
    }
    output.finish()
}

fn decompress_once_call(arguments: &[Borrowed<'_>]) -> Outcome<Owned> {
    let [input, window] = *exactly::<2>(arguments, c"decompress() takes exactly two arguments")?;
    let input = Buffer::acquire(input)?;
    let window = window_argument(window)?;
    let input = input.as_bytes();
    let mut decompressor = Inflater::new(window)
        .map_err(|code| setup_failure(code, "while preparing to decompress data"))?;
    let mut output = BytesSink::with_capacity(decompress_capacity_hint(input.len()))?;
    let result = decompressor.run(input, InflateFlush::FinishLast, None, &mut output)?;
    if result.code != Z_STREAM_END {
        return Err(zlib_failure(
            decompressor.stream(),
            result.code,
            "while decompressing data",
        ));
    }
    let code = decompressor.end();
    if code != Z_OK {
        return Err(zlib_failure(
            decompressor.stream(),
            code,
            "while finishing decompression",
        ));
    }
    output.finish()
}

fn compressor_new_call(arguments: &[Borrowed<'_>]) -> Outcome<Owned> {
    let [level, window] = *exactly::<2>(arguments, c"compressor() takes exactly two arguments")?;
    let level = level_argument(level)?;
    let window = window_argument(window)?;
    let compressor = Deflater::new(level, window, None)
        .map_err(|code| setup_failure(code, "while creating compression object"))?;
    new_capsule(Compressor(compressor))
}

fn compressor_compress_call(arguments: &[Borrowed<'_>]) -> Outcome<Owned> {
    let [state, input] = *exactly::<2>(arguments, c"compress() takes exactly two arguments")?;
    let compressor = unsafe { capsule_state::<Compressor>(state) }?;
    let input = Buffer::acquire(input)?;
    let input = input.as_bytes();
    let mut output = BytesSink::with_capacity(compress_capacity_hint(input.len()))?;
    compressor
        .0
        .run(input, Z_NO_FLUSH, &mut output)
        .map_err(|error| compressor.0.failure(error, "while compressing data"))?;
    output.finish()
}

fn compressor_flush_call(arguments: &[Borrowed<'_>]) -> Outcome<Owned> {
    let [state, mode] = *exactly::<2>(arguments, c"flush() takes exactly two arguments")?;
    let compressor = unsafe { capsule_state::<Compressor>(state) }?;
    let mode = c_int_argument(mode)?;
    if mode == Z_NO_FLUSH {
        return BytesWriter::copy_of(b"");
    }
    let mut output = BytesSink::with_capacity(1024)?;
    let code = compressor
        .0
        .run(&[], mode, &mut output)
        .map_err(|error| compressor.0.failure(error, "while flushing"))?;
    if code == Z_STREAM_END && mode == Z_FINISH {
        let code = compressor.0.end();
        if code != Z_OK {
            return Err(zlib_failure(
                compressor.0.stream(),
                code,
                "while finishing compression",
            ));
        }
    } else if code != Z_OK && code != Z_BUF_ERROR {
        return Err(zlib_failure(compressor.0.stream(), code, "while flushing"));
    }
    output.finish()
}

fn compressor_copy_call(arguments: &[Borrowed<'_>]) -> Outcome<Owned> {
    let [state] = *exactly::<1>(arguments, c"copy() takes exactly one argument")?;
    let source = unsafe { capsule_state::<Compressor>(state) }?;
    if !source.0.is_live() {
        return Err(raise(Exception::ValueError, c"Inconsistent stream state"));
    }
    let copy = source.0.duplicate().map_err(|code| {
        copy_failure(
            source.0.stream(),
            code,
            c"Can't allocate memory for compression object",
            "while copying compression object",
        )
    })?;
    new_capsule(Compressor(copy))
}

fn decompressor_new_call(arguments: &[Borrowed<'_>]) -> Outcome<Owned> {
    let [window] = *exactly::<1>(arguments, c"decompressor() takes exactly one argument")?;
    let window = window_argument(window)?;
    let stream = Inflater::new(window)
        .map_err(|code| setup_failure(code, "while creating decompression object"))?;
    new_capsule(Decompressor {
        stream,
        eof: false,
        unused_data: Vec::new(),
        unconsumed_tail: Vec::new(),
    })
}

fn save_unconsumed(
    decompressor: &mut Decompressor,
    input: &[u8],
    consumed: usize,
    code: i32,
    had_unconsumed_tail: bool,
) {
    let leftover = &input[consumed.min(input.len())..];
    if code == Z_STREAM_END {
        if !leftover.is_empty() {
            decompressor.unused_data.extend_from_slice(leftover);
        }
        decompressor.unconsumed_tail.clear();
        return;
    }
    if !leftover.is_empty() || had_unconsumed_tail {
        decompressor.unconsumed_tail = leftover.to_vec();
    }
}

fn decompressor_decompress_call(arguments: &[Borrowed<'_>]) -> Outcome<Owned> {
    let [state, input, max_length] =
        *exactly::<3>(arguments, c"decompress() takes exactly three arguments")?;
    let decompressor = decompressor_state(state)?;
    let input = Buffer::acquire(input)?;
    let max_length = index_argument(max_length)?;
    if max_length < 0 {
        return Err(raise(
            Exception::ValueError,
            c"max_length must be non-negative",
        ));
    }
    let input = input.as_bytes();
    let limit = (max_length != 0).then_some(max_length as usize);
    let mut capacity = decompress_capacity_hint(input.len());
    if let Some(limit) = limit {
        capacity = capacity.min(limit);
    }
    let mut output = BytesSink::with_capacity(capacity)?;
    let result = decompressor
        .stream
        .run(input, InflateFlush::Sync, limit, &mut output)?;
    let had_unconsumed_tail = !decompressor.unconsumed_tail.is_empty();
    save_unconsumed(
        decompressor,
        input,
        result.consumed,
        result.code,
        had_unconsumed_tail,
    );
    if result.code == Z_STREAM_END {
        decompressor.eof = true;
    } else if result.code != Z_OK && result.code != Z_BUF_ERROR {
        return Err(zlib_failure(
            decompressor.stream.stream(),
            result.code,
            "while decompressing data",
        ));
    }
    output.finish()
}

fn decompressor_flush_call(arguments: &[Borrowed<'_>]) -> Outcome<Owned> {
    let [state, length] = *exactly::<2>(arguments, c"flush() takes exactly two arguments")?;
    let decompressor = decompressor_state(state)?;
    let length = index_argument(length)?;
    if length <= 0 {
        return Err(raise(
            Exception::ValueError,
            c"length must be greater than zero",
        ));
    }
    if decompressor.eof {
        return BytesWriter::copy_of(&[]);
    }
    let input = mem::take(&mut decompressor.unconsumed_tail);
    let capacity = decompress_capacity_hint(input.len()).min(length as usize);
    let mut output = match BytesSink::with_capacity(capacity) {
        Ok(output) => output,
        Err(stop) => {
            decompressor.unconsumed_tail = input;
            return Err(stop);
        }
    };
    let result = match decompressor
        .stream
        .run(&input, InflateFlush::FinishLast, None, &mut output)
    {
        Ok(result) => result,
        Err(stop) => {
            decompressor.unconsumed_tail = input;
            return Err(stop);
        }
    };
    save_unconsumed(
        decompressor,
        &input,
        result.consumed,
        result.code,
        !input.is_empty(),
    );
    if result.code != Z_OK && result.code != Z_BUF_ERROR && result.code != Z_STREAM_END {
        return Err(zlib_failure(
            decompressor.stream.stream(),
            result.code,
            "while decompressing data",
        ));
    }
    if result.code == Z_STREAM_END {
        decompressor.eof = true;
        let code = decompressor.stream.end();
        if code != Z_OK {
            return Err(zlib_failure(
                decompressor.stream.stream(),
                code,
                "while finishing decompression",
            ));
        }
    }
    output.finish()
}

fn decompressor_copy_call(arguments: &[Borrowed<'_>]) -> Outcome<Owned> {
    let [state] = *exactly::<1>(arguments, c"copy() takes exactly one argument")?;
    let source = decompressor_state(state)?;
    if source.eof || !source.stream.is_live() {
        return Err(raise(Exception::ValueError, c"Inconsistent stream state"));
    }
    let stream = source.stream.duplicate().map_err(|code| {
        copy_failure(
            source.stream.stream(),
            code,
            c"Can't allocate memory for decompression object",
            "while copying decompression object",
        )
    })?;
    new_capsule(Decompressor {
        stream,
        eof: source.eof,
        unused_data: source.unused_data.clone(),
        unconsumed_tail: source.unconsumed_tail.clone(),
    })
}

fn decompressor_eof_call(arguments: &[Borrowed<'_>]) -> Outcome<Owned> {
    let [state] = *exactly::<1>(arguments, c"eof() takes exactly one argument")?;
    Ok(boolean(decompressor_state(state)?.eof))
}

fn decompressor_unused_data_call(arguments: &[Borrowed<'_>]) -> Outcome<Owned> {
    let [state] = *exactly::<1>(arguments, c"unused_data() takes exactly one argument")?;
    BytesWriter::copy_of(&decompressor_state(state)?.unused_data)
}

fn decompressor_unconsumed_tail_call(arguments: &[Borrowed<'_>]) -> Outcome<Owned> {
    let [state] = *exactly::<1>(arguments, c"unconsumed_tail() takes exactly one argument")?;
    BytesWriter::copy_of(&decompressor_state(state)?.unconsumed_tail)
}

macro_rules! entry_point {
    ($name:ident, $call:ident) => {
        unsafe extern "C" fn $name(
            _module: *mut PyObject,
            args: *mut *mut PyObject,
            nargs: Py_ssize_t,
        ) -> *mut PyObject {
            into_return($call(unsafe { fastcall_arguments(args, nargs) }))
        }
    };
}

entry_point!(compress_once, compress_once_call);
entry_point!(decompress_once, decompress_once_call);
entry_point!(compressor_new, compressor_new_call);
entry_point!(compressor_compress, compressor_compress_call);
entry_point!(compressor_flush, compressor_flush_call);
entry_point!(compressor_copy, compressor_copy_call);
entry_point!(decompressor_new, decompressor_new_call);
entry_point!(decompressor_decompress, decompressor_decompress_call);
entry_point!(decompressor_eof, decompressor_eof_call);
entry_point!(decompressor_unused_data, decompressor_unused_data_call);
entry_point!(decompressor_unconsumed_tail, decompressor_unconsumed_tail_call);
entry_point!(decompressor_flush, decompressor_flush_call);
entry_point!(decompressor_copy, decompressor_copy_call);
entry_point!(crc32_update, crc32_call);

static METHODS: [PyMethodDef; 15] = [
    fastcall(c"compress_once", compress_once, c"Compress one zlib or raw-DEFLATE stream."),
    fastcall(c"decompress_once", decompress_once, c"Decompress one zlib or raw-DEFLATE stream."),
    fastcall(c"compressor", compressor_new, c"Create a zlib or raw-DEFLATE compressor."),
    fastcall(c"compress", compressor_compress, c"Compress one input block."),
    fastcall(c"flush", compressor_flush, c"Flush a compressor."),
    fastcall(c"compressor_copy", compressor_copy, c"Copy a compressor."),
    fastcall(c"decompressor", decompressor_new, c"Create a zlib or raw-DEFLATE decompressor."),
    fastcall(c"decompress", decompressor_decompress, c"Decompress one input block."),
    fastcall(c"eof", decompressor_eof, c"Return whether the DEFLATE stream ended."),
    fastcall(c"unused_data", decompressor_unused_data, c"Return bytes following the DEFLATE stream."),
    fastcall(
        c"unconsumed_tail",
        decompressor_unconsumed_tail,
        c"Return compressed input not yet consumed.",
    ),
    fastcall(c"decompressor_flush", decompressor_flush, c"Finish a decompressor object."),
    fastcall(c"decompressor_copy", decompressor_copy, c"Copy a decompressor."),
    fastcall(c"crc32", crc32_update, c"Compute a seeded CRC-32 checksum."),
    method_table_end(),
];

static MODULE: Module =
    Module::new(c"_zlib_rs", c"Rust DEFLATE codecs for zlib.", &METHODS)
        .with_slots(&PER_INTERPRETER_GIL);

#[unsafe(no_mangle)]
pub extern "C" fn PyInit__zlib_rs() -> *mut PyObject {
    MODULE.init()
}
