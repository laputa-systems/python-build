use std::ffi::CStr;

use bzip2::{Action, Compress, Compression, Decompress, Error, Status};
use cpython_binding::arguments::{c_int_argument, exactly, ssize_argument};
use cpython_binding::ffi::{
    PyMethodDef, PyObject, PyUnicode_FromStringAndSize, Py_ssize_t,
};
use cpython_binding::module::PER_INTERPRETER_GIL;
use cpython_binding::values::{boolean, tuple};
use cpython_binding::{
    Borrowed, Buffer, BytesSink, BytesWriter, CapsuleState, Sink, Exception, Module, Outcome, Owned, Stop,
    capsule_state, fastcall, fastcall_arguments, into_return, method_table_end, new_capsule,
    noargs, raise, single_argument,
};

const OUTPUT_CHUNK: usize = 16 * 1024;
const SCRATCH: usize = 1024;

struct CompressorState {
    codec: Compress,
    finished: bool,
}

impl CapsuleState for CompressorState {
    const NAME: &'static CStr = c"_bz2_rs.Compressor";
}

struct DecompressorState {
    codec: Decompress,
    input: Vec<u8>,
    unused_data: Vec<u8>,
    eof: bool,
    failed: bool,
    needs_input: bool,
}

impl CapsuleState for DecompressorState {
    const NAME: &'static CStr = c"_bz2_rs.Decompressor";
}

enum Failure {
    Codec(Error),
    Python,
}

impl From<Error> for Failure {
    fn from(error: Error) -> Self {
        Self::Codec(error)
    }
}

impl From<Stop> for Failure {
    fn from(_: Stop) -> Self {
        Self::Python
    }
}

fn failure_stop(failure: Failure) -> Stop {
    match failure {
        Failure::Python => Stop::Raised,
        Failure::Codec(Error::Data | Error::DataMagic) => {
            raise(Exception::OSError, c"Invalid data stream")
        }
        Failure::Codec(Error::Param) => raise(
            Exception::ValueError,
            c"Internal error - invalid parameters passed to libbzip2",
        ),
        Failure::Codec(Error::Sequence) => raise(
            Exception::RuntimeError,
            c"Internal error - Invalid sequence of commands sent to libbzip2",
        ),
    }
}

fn compress_into(
    state: &mut CompressorState,
    input: &[u8],
    action: Action,
    sink: &mut BytesSink,
) -> Result<(), Failure> {
    let mut input_offset = 0usize;
    loop {
        let end = input.len().min(input_offset.saturating_add(u32::MAX as usize));
        let chunk = &input[input_offset..end];
        if sink.spare().len() < SCRATCH {
            sink.grow(sink.written().max(OUTPUT_CHUNK))?;
        }
        let before_in = state.codec.total_in();
        let before_out = state.codec.total_out();
        let status = state.codec.compress_uninit(chunk, sink.spare(), action)?;
        let consumed = (state.codec.total_in() - before_in) as usize;
        let produced = (state.codec.total_out() - before_out) as usize;
        unsafe { sink.advance(produced) };
        input_offset += consumed;

        if action == Action::Run && input_offset == input.len() {
            break;
        }
        if action == Action::Finish && status == Status::StreamEnd {
            state.finished = true;
            break;
        }
        if consumed == 0 && produced == 0 {
            return Err(Error::Sequence.into());
        }
    }
    Ok(())
}

fn compressor_new_call(argument: Borrowed<'_>) -> Outcome<Owned> {
    let level = c_int_argument(argument)?;
    if !(1..=9).contains(&level) {
        return Err(raise(
            Exception::ValueError,
            c"compresslevel must be between 1 and 9",
        ));
    }
    new_capsule(CompressorState {
        codec: Compress::new(Compression::new(level as u32), 30),
        finished: false,
    })
}

fn compressor_compress_call(arguments: &[Borrowed<'_>]) -> Outcome<Owned> {
    let [state, input] = *exactly::<2>(
        arguments,
        c"compressor_compress() takes exactly two arguments",
    )?;
    let state = unsafe { capsule_state::<CompressorState>(state) }?;
    if state.finished {
        return Err(raise(Exception::ValueError, c"Compressor has been flushed"));
    }
    let input = Buffer::acquire(input)?;
    let mut output = BytesSink::with_capacity(SCRATCH)?;
    compress_into(state, input.as_bytes(), Action::Run, &mut output).map_err(failure_stop)?;
    output.finish()
}

fn compressor_finish_call(argument: Borrowed<'_>) -> Outcome<Owned> {
    let state = unsafe { capsule_state::<CompressorState>(argument) }?;
    if state.finished {
        return Err(raise(Exception::ValueError, c"Compressor has been flushed"));
    }
    let mut output = BytesSink::with_capacity(SCRATCH)?;
    compress_into(state, &[], Action::Finish, &mut output).map_err(failure_stop)?;
    output.finish()
}

fn decompressor_new_call() -> Outcome<Owned> {
    new_capsule(DecompressorState {
        codec: Decompress::new(false),
        input: Vec::new(),
        unused_data: Vec::new(),
        eof: false,
        failed: false,
        needs_input: true,
    })
}

fn decompress_into(
    state: &mut DecompressorState,
    fresh: &[u8],
    max_length: Py_ssize_t,
) -> Result<Owned, Failure> {
    if max_length == 0 {
        state.input.extend_from_slice(fresh);
        state.needs_input = state.input.is_empty();
        return Ok(BytesWriter::copy_of(b"")?);
    }
    let limit = if max_length < 0 {
        isize::MAX as usize
    } else {
        max_length as usize
    };
    let owned = !state.input.is_empty();
    if owned {
        state.input.extend_from_slice(fresh);
    }
    let DecompressorState { codec, input, .. } = &mut *state;
    let source: &[u8] = if owned { input } else { fresh };
    let mut output = BytesSink::with_capacity(limit.min(OUTPUT_CHUNK))?;
    let mut probe = [0u8; SCRATCH];
    let mut consumed_total = 0usize;
    let mut eof = false;
    loop {
        let remaining = limit - output.written();
        if remaining == 0 {
            break;
        }
        let rest = &source[consumed_total..];
        let chunk = &rest[..rest.len().min(u32::MAX as usize)];
        let before_in = codec.total_in();
        let before_out = codec.total_out();
        let spare = output.spare().len();
        let (status, room, scratch);
        if spare > 0 {
            room = spare.min(remaining);
            status = codec.decompress_uninit(chunk, &mut output.spare()[..room])?;
            scratch = 0;
        } else {
            room = SCRATCH.min(remaining);
            status = codec.decompress(chunk, &mut probe[..room])?;
            scratch = (codec.total_out() - before_out) as usize;
        }
        let consumed = (codec.total_in() - before_in) as usize;
        let produced = (codec.total_out() - before_out) as usize;
        consumed_total += consumed;
        if scratch > 0 {
            let capacity = output.written();
            let grown = capacity.saturating_mul(2).min(limit).max(capacity + scratch);
            output.grow(grown - capacity)?;
            output.append(&probe[..scratch])?;
        } else {
            unsafe { output.advance(produced) };
        }

        if status == Status::StreamEnd {
            eof = true;
            break;
        }
        if max_length >= 0 && output.written() == limit {
            break;
        }
        if consumed == 0 && produced == 0 {
            break;
        }
        if consumed_total == source.len() && produced < room {
            break;
        }
    }
    if owned {
        input.drain(..consumed_total);
    } else {
        input.extend_from_slice(&fresh[consumed_total..]);
    }
    if eof {
        state.eof = true;
        state.unused_data.extend_from_slice(&state.input);
        state.input.clear();
        state.needs_input = false;
    } else {
        state.needs_input = state.input.is_empty();
    }
    Ok(output.finish()?)
}

fn decompressor_decompress_call(arguments: &[Borrowed<'_>]) -> Outcome<Owned> {
    let [state, input, max_length] = *exactly::<3>(
        arguments,
        c"decompressor_decompress() takes exactly three arguments",
    )?;
    let state = unsafe { capsule_state::<DecompressorState>(state) }?;
    if state.eof {
        return Err(raise(Exception::ValueError, c"End of stream already reached"));
    }
    if state.failed {
        return Err(raise(
            Exception::ValueError,
            c"Decompressor is unusable after a previous error",
        ));
    }
    let max_length = ssize_argument(max_length)?;
    let input = Buffer::acquire(input)?;
    decompress_into(state, input.as_bytes(), max_length).map_err(|failure| {
        state.failed = true;
        state.needs_input = false;
        state.input.clear();
        failure_stop(failure)
    })
}

fn decompressor_state_call(argument: Borrowed<'_>) -> Outcome<Owned> {
    let state = unsafe { capsule_state::<DecompressorState>(argument) }?;
    tuple([
        boolean(state.eof),
        boolean(state.needs_input),
        BytesWriter::copy_of(&state.unused_data)?,
    ])
}

fn bzlib_version_call() -> Outcome<Owned> {
    let version = unsafe { CStr::from_ptr(libbz2_rs_sys::BZ2_bzlibVersion()) };
    let bytes = version.to_bytes();
    let text = unsafe {
        PyUnicode_FromStringAndSize(bytes.as_ptr().cast(), bytes.len() as Py_ssize_t)
    };
    unsafe { Owned::from_new_reference(text) }
}

unsafe extern "C" fn bzlib_version(
    _module: *mut PyObject,
    _ignored: *mut PyObject,
) -> *mut PyObject {
    into_return(bzlib_version_call())
}

unsafe extern "C" fn compressor_new(_module: *mut PyObject, level: *mut PyObject) -> *mut PyObject {
    into_return(compressor_new_call(unsafe { Borrowed::from_raw(level) }))
}

unsafe extern "C" fn compressor_compress(
    _module: *mut PyObject,
    args: *mut *mut PyObject,
    nargs: Py_ssize_t,
) -> *mut PyObject {
    into_return(compressor_compress_call(unsafe { fastcall_arguments(args, nargs) }))
}

unsafe extern "C" fn compressor_finish(
    _module: *mut PyObject,
    capsule: *mut PyObject,
) -> *mut PyObject {
    into_return(compressor_finish_call(unsafe { Borrowed::from_raw(capsule) }))
}

unsafe extern "C" fn decompressor_new(
    _module: *mut PyObject,
    _ignored: *mut PyObject,
) -> *mut PyObject {
    into_return(decompressor_new_call())
}

unsafe extern "C" fn decompressor_decompress(
    _module: *mut PyObject,
    args: *mut *mut PyObject,
    nargs: Py_ssize_t,
) -> *mut PyObject {
    into_return(decompressor_decompress_call(unsafe { fastcall_arguments(args, nargs) }))
}

unsafe extern "C" fn decompressor_state(
    _module: *mut PyObject,
    capsule: *mut PyObject,
) -> *mut PyObject {
    into_return(decompressor_state_call(unsafe { Borrowed::from_raw(capsule) }))
}

static METHODS: [PyMethodDef; 8] = [
    noargs(c"bzlib_version", bzlib_version, c"Return the libbz2 version string."),
    single_argument(c"compressor_new", compressor_new, c"Create a Rust bzip2 compression stream."),
    fastcall(c"compressor_compress", compressor_compress, c"Compress the next input block."),
    single_argument(c"compressor_finish", compressor_finish, c"Finish a Rust bzip2 compression stream."),
    noargs(c"decompressor_new", decompressor_new, c"Create a Rust bzip2 decompression stream."),
    fastcall(
        c"decompressor_decompress",
        decompressor_decompress,
        c"Decompress the next input block.",
    ),
    single_argument(
        c"decompressor_state",
        decompressor_state,
        c"Return the decompressor's public state.",
    ),
    method_table_end(),
];

static MODULE: Module = Module::new(c"_bz2_rs", c"Rust bzip2 streaming codec.", &METHODS)
    .with_slots(&PER_INTERPRETER_GIL);

#[unsafe(no_mangle)]
pub extern "C" fn PyInit__bz2_rs() -> *mut PyObject {
    MODULE.init()
}
