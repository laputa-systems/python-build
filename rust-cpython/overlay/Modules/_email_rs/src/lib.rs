use cpython_binding::ffi::{
    PyBytes_FromStringAndSize, PyErr_NoMemory, PyList_New, PyList_SetItem, PyObject, Py_ssize_t,
};
use cpython_binding::{
    Borrowed, Buffer, Exception, Module, Outcome, Owned, Stop, fastcall, into_return,
    method_table_end, raise,
};
use mail_parser::MessageParser;

fn valid_header_block(raw: &[u8]) -> Option<usize> {
    let mut position = 0;
    let mut fields = 0;
    let mut have_field = false;

    while position < raw.len() {
        let line_end = raw[position..]
            .iter()
            .position(|byte| *byte == b'\n')
            .map_or(raw.len(), |offset| position + offset + 1);
        let mut content_end = line_end;
        if line_end > position && raw[line_end - 1] == b'\n' {
            content_end -= 1;
            if content_end > position && raw[content_end - 1] == b'\r' {
                content_end -= 1;
            }
        }
        let line = &raw[position..content_end];
        if line.is_empty() || line.iter().any(|byte| *byte == b'\r') {
            return None;
        }

        if line[0] == b' ' || line[0] == b'\t' {
            if !have_field {
                return None;
            }
        } else {
            let colon = line.iter().position(|byte| *byte == b':')?;
            let name = &line[..colon];
            if name.is_empty()
                || !name
                    .iter()
                    .all(|byte| matches!(*byte, b'!'..=b'9' | b';'..=b'~'))
            {
                return None;
            }
            fields += 1;
            have_field = true;
        }

        if line
            .iter()
            .any(|byte| *byte == 0 || (*byte < b' ' && *byte != b'\t') || *byte == 0x7f)
        {
            return None;
        }
        position = line_end;
    }

    Some(fields)
}

fn parse_header_fields(raw: &[u8]) -> Option<Vec<&[u8]>> {
    if raw.is_empty() {
        return Some(Vec::new());
    }
    if raw.len() > u32::MAX as usize {
        return None;
    }
    let field_count = valid_header_block(raw)?;
    let message = MessageParser::default().parse_headers(raw)?;
    let headers = message.headers();
    if headers.len() != field_count {
        return None;
    }

    let mut fields = Vec::with_capacity(field_count);
    let mut previous_end = 0;
    for header in headers {
        let start = header.offset_field() as usize;
        let end = header.offset_end() as usize;
        if start != previous_end || end < start || end > raw.len() {
            return None;
        }
        fields.push(&raw[start..end]);
        previous_end = end;
    }
    (previous_end == raw.len()).then_some(fields)
}

fn python_fields(fields: &[&[u8]]) -> Outcome<Owned> {
    let result = unsafe { Owned::from_new_reference(PyList_New(fields.len() as Py_ssize_t)) }?;
    for (index, field) in fields.iter().enumerate() {
        if field.len() > isize::MAX as usize {
            unsafe { PyErr_NoMemory() };
            return Err(Stop::Raised);
        }
        let item = unsafe {
            Owned::from_new_reference(PyBytes_FromStringAndSize(
                field.as_ptr().cast(),
                field.len() as Py_ssize_t,
            ))
        }?;
        if unsafe { PyList_SetItem(result.as_ptr(), index as Py_ssize_t, item.into_raw()) } != 0 {
            return Err(Stop::Raised);
        }
    }
    Ok(result)
}

fn fields_of(object: Borrowed<'_>) -> Outcome<Owned> {
    let buffer = Buffer::acquire(object)?;
    let fields = parse_header_fields(buffer.as_bytes()).ok_or(Stop::Unsupported)?;
    python_fields(&fields)
}

/// Return original RFC header fields when the block uses strict field syntax.
///
/// # Safety
/// `args` is a valid CPython fast-call argument array.
unsafe extern "C" fn parse_fields(
    _module: *mut PyObject,
    args: *mut *mut PyObject,
    nargs: Py_ssize_t,
) -> *mut PyObject {
    if nargs != 1 {
        raise(
            Exception::TypeError,
            c"parse_header_fields() takes exactly one argument",
        );
        return core::ptr::null_mut();
    }
    into_return(fields_of(unsafe { Borrowed::from_raw(*args) }))
}

static METHODS: [cpython_binding::ffi::PyMethodDef; 2] = [
    fastcall(
        c"parse_header_fields",
        parse_fields,
        c"Parse a strict RFC message header block",
    ),
    method_table_end(),
];

static MODULE: Module = Module::new(c"_email_rs", c"Rust RFC email header parsing", &METHODS);

#[unsafe(no_mangle)]
pub extern "C" fn PyInit__email_rs() -> *mut PyObject {
    MODULE.init()
}
