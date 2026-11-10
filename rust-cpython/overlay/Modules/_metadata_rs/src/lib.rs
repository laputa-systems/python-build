use std::cell::UnsafeCell;
use std::ffi::{c_char, c_int, c_void};
use std::fs;
use std::ptr;
use std::slice;
use std::str;

use cpython_sys::METH_O;
use cpython_sys::METH_VARARGS;
use cpython_sys::PyErr_SetString;
use cpython_sys::PyExc_TypeError;
use cpython_sys::PyList_New;
use cpython_sys::PyList_SetItem;
use cpython_sys::PyMethodDef;
use cpython_sys::PyMethodDefFuncPointer;
use cpython_sys::PyModuleDef;
use cpython_sys::PyModuleDef_HEAD_INIT;
use cpython_sys::PyModuleDef_Slot;
use cpython_sys::PyModuleDef_Init;
use cpython_sys::Py_NewRef;
use cpython_sys::PyObject;
use cpython_sys::PyTuple_GetItem;
use cpython_sys::PyTuple_New;
use cpython_sys::PyTuple_SetItem;
use cpython_sys::PyTuple_Size;
use cpython_sys::PyUnicode_AsUTF8AndSize;
use cpython_sys::PyUnicode_FromStringAndSize;
use cpython_sys::Py_ssize_t;
use cpython_sys::_Py_NoneStruct;

fn normalize_name(name: &str) -> String {
    let mut normalized = String::with_capacity(name.len());
    let mut previous_underscore = false;
    for character in name.chars().flat_map(char::to_lowercase) {
        let character = if matches!(character, '-' | '.') {
            '_'
        } else {
            character
        };
        if character == '_' {
            if previous_underscore {
                continue;
            }
            previous_underscore = true;
        } else {
            previous_underscore = false;
        }
        normalized.push(character);
    }
    normalized
}

fn legacy_normalize_name(name: &str) -> String {
    name.to_lowercase().replace('-', "_")
}

fn metadata_children(
    root: &str,
    requested_name: &str,
    root_basename: &str,
) -> Option<Vec<String>> {
    let search_root = if root.is_empty() { "." } else { root };
    let children = fs::read_dir(search_root).ok()?;
    let root_lower = root_basename.to_lowercase();
    let egg_root = root_lower.ends_with(".egg");
    if !requested_name.is_ascii() || (egg_root && !root_basename.is_ascii()) {
        return None;
    }
    let filter_by_name = !requested_name.is_empty();
    let normalized_query = filter_by_name.then(|| normalize_name(requested_name));
    let legacy_query = filter_by_name.then(|| legacy_normalize_name(requested_name));
    let mut found = Vec::new();

    for result in children {
        let entry = result.ok()?;
        let child = entry.file_name();
        let child = child.to_str()?;
        if !child.is_ascii() {
            return None;
        }
        let child_lower = child.to_lowercase();

        let info_suffix = [".dist-info", ".egg-info"]
            .into_iter()
            .find(|suffix| child_lower.ends_with(suffix));
        if let Some(suffix) = info_suffix {
            let stem = &child_lower[..child_lower.len() - suffix.len()];
            let distribution_name = stem.split('-').next().unwrap_or("");
            let matches = normalized_query
                .as_ref()
                .is_none_or(|query| normalize_name(distribution_name) == *query);
            if matches {
                found.push(child.to_owned());
            }
            continue;
        }

        if egg_root && child_lower == "egg-info" {
            let egg_stem = &root_lower[..root_lower.len() - ".egg".len()];
            let distribution_name = egg_stem.split('-').next().unwrap_or("");
            let matches = legacy_query
                .as_ref()
                .is_none_or(|query| legacy_normalize_name(distribution_name) == *query);
            if matches {
                found.push(child.to_owned());
            }
        }
    }

    found.sort_by_key(|child| child.to_lowercase());
    found.reverse();

    Some(found)
}

unsafe fn unicode_text(object: *mut PyObject) -> Result<String, ()> {
    let mut length: Py_ssize_t = 0;
    let data = unsafe { PyUnicode_AsUTF8AndSize(object, &mut length) };
    if data.is_null() {
        return Err(());
    }
    if length < 0 {
        unsafe {
            PyErr_SetString(PyExc_TypeError, c"text has a negative encoded length".as_ptr());
        }
        return Err(());
    }
    let bytes = unsafe { slice::from_raw_parts(data.cast::<u8>(), length as usize) };
    let text = str::from_utf8(bytes).map_err(|_| ())?;
    Ok(text.to_owned())
}

unsafe fn python_text(text: &str) -> *mut PyObject {
    unsafe { PyUnicode_FromStringAndSize(text.as_ptr().cast::<c_char>(), text.len() as Py_ssize_t) }
}

unsafe fn return_none() -> *mut PyObject {
    unsafe { Py_NewRef(ptr::addr_of_mut!(_Py_NoneStruct).cast::<PyObject>()) }
}

fn parse_metadata_result(input: &str) -> Option<(Vec<(String, String)>, String)> {
    let (headers, body_start) = mailparse::parse_headers(input.as_bytes()).ok()?;
    let mut fields = Vec::with_capacity(headers.len());
    for header in headers {
        let raw_name = header.get_key_raw();
        let input_start = input.as_bytes().as_ptr() as usize;
        let name_start = raw_name.as_ptr() as usize;
        let name_offset = name_start.checked_sub(input_start)?;
        if input.as_bytes().get(name_offset + raw_name.len()) != Some(&b':') {
            return None;
        }
        let name = str::from_utf8(raw_name).ok()?;
        if name.is_empty()
            || !name
                .bytes()
                .all(|byte| (33..=126).contains(&byte) && byte != b':')
        {
            return None;
        }

        let value = str::from_utf8(header.get_value_raw()).ok()?;
        if value.starts_with('\t') {
            return None;
        }
        fields.push((name.to_owned(), value.to_owned()));
    }

    let body = str::from_utf8(input.as_bytes().get(body_start..)?).ok()?;
    Some((fields, body.to_owned()))
}

unsafe fn fields_and_body(fields: &[(String, String)], body: &str) -> *mut PyObject {
    let parsed = unsafe { PyList_New(fields.len() as Py_ssize_t) };
    if parsed.is_null() {
        return ptr::null_mut();
    }

    for (index, (name, value)) in fields.iter().enumerate() {
        let pair = unsafe { PyTuple_New(2) };
        if pair.is_null() {
            unsafe { cpython_sys::Py_DecRef(parsed) };
            return ptr::null_mut();
        }
        let name_object = unsafe { python_text(name) };
        let value_object = unsafe { python_text(value) };
        if name_object.is_null() || value_object.is_null() {
            if !name_object.is_null() {
                unsafe { cpython_sys::Py_DecRef(name_object) };
            }
            if !value_object.is_null() {
                unsafe { cpython_sys::Py_DecRef(value_object) };
            }
            unsafe { cpython_sys::Py_DecRef(pair) };
            unsafe { cpython_sys::Py_DecRef(parsed) };
            return ptr::null_mut();
        }
        if unsafe { PyTuple_SetItem(pair, 0, name_object) } != 0
            || unsafe { PyTuple_SetItem(pair, 1, value_object) } != 0
        {
            unsafe { cpython_sys::Py_DecRef(pair) };
            unsafe { cpython_sys::Py_DecRef(parsed) };
            return ptr::null_mut();
        }
        if unsafe { PyList_SetItem(parsed, index as Py_ssize_t, pair) } != 0 {
            unsafe { cpython_sys::Py_DecRef(parsed) };
            return ptr::null_mut();
        }
    }

    let body_object = unsafe { python_text(body) };
    let result = unsafe { PyTuple_New(2) };
    if body_object.is_null() || result.is_null() {
        if !body_object.is_null() {
            unsafe { cpython_sys::Py_DecRef(body_object) };
        }
        if !result.is_null() {
            unsafe { cpython_sys::Py_DecRef(result) };
        }
        unsafe { cpython_sys::Py_DecRef(parsed) };
        return ptr::null_mut();
    }
    if unsafe { PyTuple_SetItem(result, 0, parsed) } != 0
        || unsafe { PyTuple_SetItem(result, 1, body_object) } != 0
    {
        unsafe { cpython_sys::Py_DecRef(result) };
        return ptr::null_mut();
    }
    result
}

unsafe extern "C" fn parse_metadata(
    _module: *mut PyObject,
    text_object: *mut PyObject,
) -> *mut PyObject {
    let text = match unsafe { unicode_text(text_object) } {
        Ok(text) => text,
        Err(()) => return ptr::null_mut(),
    };
    match parse_metadata_result(&text) {
        Some((fields, body)) => unsafe { fields_and_body(&fields, &body) },
        None => unsafe { return_none() },
    }
}

unsafe extern "C" fn scan_path(_module: *mut PyObject, arguments: *mut PyObject) -> *mut PyObject {
    let count = unsafe { PyTuple_Size(arguments) };
    if count != 3 {
        unsafe {
            PyErr_SetString(
                PyExc_TypeError,
                c"scan_path() takes exactly three arguments".as_ptr(),
            );
        }
        return ptr::null_mut();
    }
    let root_object = unsafe { PyTuple_GetItem(arguments, 0) };
    let name_object = unsafe { PyTuple_GetItem(arguments, 1) };
    let basename_object = unsafe { PyTuple_GetItem(arguments, 2) };
    if root_object.is_null() || name_object.is_null() || basename_object.is_null() {
        return ptr::null_mut();
    }
    let root = match unsafe { unicode_text(root_object) } {
        Ok(root) => root,
        Err(()) => return ptr::null_mut(),
    };
    let name = match unsafe { unicode_text(name_object) } {
        Ok(name) => name,
        Err(()) => return ptr::null_mut(),
    };
    let basename = match unsafe { unicode_text(basename_object) } {
        Ok(basename) => basename,
        Err(()) => return ptr::null_mut(),
    };
    let Some(children) = metadata_children(&root, &name, &basename) else {
        return unsafe { return_none() };
    };

    let result = unsafe { PyList_New(children.len() as Py_ssize_t) };
    if result.is_null() {
        return ptr::null_mut();
    }
    for (index, child) in children.iter().enumerate() {
        let item = unsafe { python_text(child) };
        if item.is_null() {
            unsafe { cpython_sys::Py_DecRef(result) };
            return ptr::null_mut();
        }
        if unsafe { PyList_SetItem(result, index as Py_ssize_t, item) } != 0 {
            unsafe { cpython_sys::Py_DecRef(result) };
            return ptr::null_mut();
        }
    }
    result
}

unsafe extern "C" fn module_exec(_module: *mut PyObject) -> c_int {
    0
}

pub extern "C" fn _metadata_rs_clear(_module: *mut PyObject) -> c_int {
    0
}

pub extern "C" fn _metadata_rs_free(_module: *mut c_void) {}

struct ModuleDef {
    ffi: UnsafeCell<PyModuleDef>,
}

impl ModuleDef {
    fn init_multi_phase(&'static self) -> *mut PyObject {
        unsafe { PyModuleDef_Init(self.ffi.get()) }
    }
}

unsafe impl Sync for ModuleDef {}

struct ModuleSlots([PyModuleDef_Slot; 3]);

unsafe impl Sync for ModuleSlots {}

const PY_MOD_EXEC: c_int = 85;
const PY_MOD_MULTIPLE_INTERPRETERS: c_int = 86;
const PY_MOD_PER_INTERPRETER_GIL_SUPPORTED: *mut c_void = 2 as *mut c_void;

// This extension has no mutable interpreter-owned state and supports multiple
// interpreters with separate GILs.
static _METADATA_RS_MODULE_SLOTS: ModuleSlots = ModuleSlots([
    PyModuleDef_Slot {
        slot: PY_MOD_EXEC,
        value: module_exec as *const () as *mut c_void,
    },
    PyModuleDef_Slot {
        slot: PY_MOD_MULTIPLE_INTERPRETERS,
        value: PY_MOD_PER_INTERPRETER_GIL_SUPPORTED,
    },
    PyModuleDef_Slot {
        slot: 0,
        value: ptr::null_mut(),
    },
]);

static _METADATA_RS_METHODS: [PyMethodDef; 3] = [
    PyMethodDef {
        ml_name: c"parse_metadata".as_ptr() as *mut c_char,
        ml_meth: PyMethodDefFuncPointer {
            PyCFunction: parse_metadata,
        },
        ml_flags: METH_O,
        ml_doc: c"Parse a supported core metadata message.".as_ptr() as *mut c_char,
    },
    PyMethodDef {
        ml_name: c"scan_path".as_ptr() as *mut c_char,
        ml_meth: PyMethodDefFuncPointer {
            PyCFunction: scan_path,
        },
        ml_flags: METH_VARARGS,
        ml_doc: c"Discover package metadata entries in a filesystem path.".as_ptr() as *mut c_char,
    },
    PyMethodDef::zeroed(),
];

static _METADATA_RS_MODULE: ModuleDef = ModuleDef {
    ffi: UnsafeCell::new(PyModuleDef {
        m_base: PyModuleDef_HEAD_INIT,
        m_name: c"_metadata_rs".as_ptr() as *mut _,
        m_doc: c"Rust metadata parsing and filesystem discovery.".as_ptr() as *mut _,
        m_size: 0,
        m_methods: &_METADATA_RS_METHODS as *const PyMethodDef as *mut _,
        m_slots: _METADATA_RS_MODULE_SLOTS.0.as_ptr() as *mut PyModuleDef_Slot,
        m_traverse: None,
        m_clear: Some(_metadata_rs_clear),
        m_free: Some(_metadata_rs_free),
    }),
};

#[unsafe(no_mangle)]
pub extern "C" fn PyInit__metadata_rs() -> *mut PyObject {
    _METADATA_RS_MODULE.init_multi_phase()
}
