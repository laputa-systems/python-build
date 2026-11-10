//! Version 6 marshal records, written straight into the result bytes object
//! and read straight into Python objects.
//!
//! Nothing here allocates through Rust: the output buffer is the result
//! `bytes`, the writer's reference table and the reader's reference list use
//! Python's allocator, and no path can panic or unwind, which keeps Rust's
//! allocator and panic runtime out of the extension image.

use std::ffi::{c_char, c_int, c_void};
use std::mem::MaybeUninit;
use std::ptr;
use std::slice;

use cpython_sys::{
    PyASCIIObject, PyBool_Type, PyBytes_FromStringAndSize, PyBytes_Type, PyCapsule_New,
    PyComplex_FromDoubles, PyComplex_ImagAsDouble, PyComplex_RealAsDouble, PyComplex_Type,
    PyCode_Type, Py_DecRef, Py_IncRef, PyDict_New, PyDict_Next, PyDict_SetItem, PyDict_Type,
    PyErr_Clear, PyErr_Occurred, PyFloat_FromDouble, PyFloat_Type, PyFrozenSet_New,
    PyFrozenSet_Type, PyIter_Next, PyList_Append, PyList_GetItem, PyList_New, PyList_SetItem,
    PyList_Sort, PyList_Type, PyLong_AsLongLongAndOverflow, PyLong_Export, PyLong_FreeExport,
    PyLong_FromLongLong, PyLong_GetNativeLayout, PyLong_Type, PyLongExport, PyLongWriter_Create,
    PyLongWriter_Discard, PyLongWriter_Finish, PyMem_Calloc, PyMem_Free, PyMem_Realloc, PyModule_Add,
    PyModuleDef, PyModuleDef_HEAD_INIT, PyModuleDef_Init, PyModuleDef_Slot, PyObject,
    PyObject_GetIter, PyObject_IsTrue, PySet_Add, PySet_New, PySet_Type, PyTuple_GetItem,
    PyTuple_New, PyTuple_SetItem, PyTuple_Type, PyTypeObject, PyUnicode_AsEncodedString,
    PyUnicode_DecodeUTF8, PyUnicode_InternInPlace, PyUnicode_Type, Py_ssize_t, _Py_NoneStruct,
    _PyBytes_Resize, _object, PyFloatObject, PyListObject, PyTupleObject, PyVarObject, _longobject,
};
use cpython_sys::{PyBytes_AsString, PyBytes_Size, PyList_Size};

const FLAG_REF: u8 = 0x80;
const MAX_DEPTH: usize = 128;
const MAX_RECORD_SIZE: usize = 1 << 28;
const MARSHAL_SHIFT: u32 = 15;
const MARSHAL_MASK: u32 = (1 << MARSHAL_SHIFT) - 1;
/// Set on a table entry while its object is still being written. A second
/// occurrence before `complete` clears it is a recursive reference.
const ACTIVE: u32 = 0x8000_0000;
/// Set on a table entry and its `refs` record once the object is seen more
/// than once. Only shared objects carry FLAG_REF in the final record.
const SHARED: u32 = 0x4000_0000;
/// Low bits of a table value holding the index into the `refs` array.
const INDEX_MASK: u32 = 0x3fff_ffff;

/// The fixed-order fields of a code object, shared with `Python/marshal.c`.
#[repr(C)]
struct CodeParts {
    /// argcount, posonlyargcount, kwonlyargcount, stacksize, flags, firstlineno
    ints: [c_int; 6],
    /// code, consts, names, localsplusnames, localspluskinds, filename, name,
    /// qualname, linetable, exceptiontable
    objs: [*mut PyObject; 10],
}

unsafe extern "C" {
    fn _PyMarshal_RustUnicode(
        value: *mut PyObject,
        data: *mut *const c_char,
        len: *mut Py_ssize_t,
    ) -> c_int;
    fn _PyMarshal_RustCodeParts(value: *mut PyObject, parts: *mut CodeParts) -> c_int;
    fn _PyMarshal_RustBuildCode(parts: *const CodeParts) -> *mut PyObject;
    fn _PyMarshal_RustTupleSetItem(tuple: *mut PyObject, index: Py_ssize_t, item: *mut PyObject);
}

/// An owned reference.
struct PyRef(*mut PyObject);

impl PyRef {
    unsafe fn from_raw(value: *mut PyObject) -> Result<Self, ()> {
        if value.is_null() { Err(()) } else { Ok(Self(value)) }
    }

    fn as_ptr(&self) -> *mut PyObject {
        self.0
    }

    fn into_raw(self) -> *mut PyObject {
        let value = self.0;
        std::mem::forget(self);
        value
    }
}

impl Drop for PyRef {
    fn drop(&mut self) {
        unsafe { Py_DecRef(self.0) }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Int,
    Float,
    Complex,
    Bytes,
    Unicode,
    Tuple,
    List,
    Dict,
    Set,
    FrozenSet,
    Code,
}

fn kind_of(ty: *mut PyTypeObject) -> Option<Kind> {
    unsafe {
        if ty == ptr::addr_of_mut!(PyLong_Type) {
            Some(Kind::Int)
        } else if ty == ptr::addr_of_mut!(PyUnicode_Type) {
            Some(Kind::Unicode)
        } else if ty == ptr::addr_of_mut!(PyTuple_Type) {
            Some(Kind::Tuple)
        } else if ty == ptr::addr_of_mut!(PyList_Type) {
            Some(Kind::List)
        } else if ty == ptr::addr_of_mut!(PyFloat_Type) {
            Some(Kind::Float)
        } else if ty == ptr::addr_of_mut!(PyDict_Type) {
            Some(Kind::Dict)
        } else if ty == ptr::addr_of_mut!(PyBytes_Type) {
            Some(Kind::Bytes)
        } else if ty == ptr::addr_of_mut!(PyCode_Type) {
            Some(Kind::Code)
        } else if ty == ptr::addr_of_mut!(PyComplex_Type) {
            Some(Kind::Complex)
        } else if ty == ptr::addr_of_mut!(PySet_Type) {
            Some(Kind::Set)
        } else if ty == ptr::addr_of_mut!(PyFrozenSet_Type) {
            Some(Kind::FrozenSet)
        } else {
            None
        }
    }
}

struct UnicodeInfo {
    /// The bytes of a compact ASCII string, or null for any other string.
    data: *const c_char,
    length: usize,
    interned: bool,
    /// `PyUnicode_IS_ASCII`: the string's characters are all ASCII, whether
    /// or not its storage is compact ASCII.  A non-compact string can hold
    /// ASCII characters, and upstream picks its tag from this flag, not from
    /// the UTF-8 bytes.
    ascii: bool,
}

unsafe fn unicode_info(value: *mut PyObject) -> UnicodeInfo {
    let mut data: *const c_char = ptr::null();
    let mut length: Py_ssize_t = 0;
    let bits = unsafe { _PyMarshal_RustUnicode(value, &mut data, &mut length) };
    let state = unsafe { ptr::addr_of!((*value.cast::<PyASCIIObject>()).state) };
    UnicodeInfo {
        data: if bits & 1 != 0 { data } else { ptr::null() },
        length: length as usize,
        interned: bits & 2 != 0,
        ascii: unsafe { (*state).ascii() } != 0,
    }
}

/// One reference-table entry; an empty slot has a zero key.
struct Slot {
    key: usize,
    value: u32,
}

/// Writes one marshal record into a `bytes` object that grows in place.
/// Every non-singleton object is tracked by identity, as upstream CPython's
/// writer does: the first occurrence is written without FLAG_REF while its
/// type-byte offset is recorded, and `resolve` flags exactly the objects
/// seen more than once. Output depends only on the object graph, never on
/// ambient reference counts.
struct Encoder {
    bytes: *mut PyObject,
    buffer: *mut u8,
    length: usize,
    capacity: usize,
    slots: *mut Slot,
    mask: usize,
    entries: u32,
    /// One record per tracked object, in first-occurrence order: the type
    /// byte offset shifted left by one, ORed with 1 once shared.
    refs: *mut usize,
    refs_len: usize,
    refs_cap: usize,
    /// Output offsets of provisional TYPE_REF arguments, fixed up by
    /// `resolve` to the reader's reference indices.
    refpos: *mut usize,
    refpos_len: usize,
    refpos_cap: usize,
    allow_code: bool,
}

impl Encoder {
    unsafe fn new(allow_code: bool) -> Result<Self, ()> {
        const INITIAL: usize = 256;
        let bytes = unsafe { PyBytes_FromStringAndSize(ptr::null(), INITIAL as Py_ssize_t) };
        if bytes.is_null() {
            return Err(());
        }
        let buffer = unsafe { PyBytes_AsString(bytes) }.cast::<u8>();
        Ok(Self {
            bytes,
            buffer,
            length: 0,
            capacity: INITIAL,
            slots: ptr::null_mut(),
            mask: 0,
            entries: 0,
            refs: ptr::null_mut(),
            refs_len: 0,
            refs_cap: 0,
            refpos: ptr::null_mut(),
            refpos_len: 0,
            refpos_cap: 0,
            allow_code,
        })
    }

    /// Trim the result to the bytes written and hand it over.
    unsafe fn finish(mut self) -> Result<*mut PyObject, ()> {
        if unsafe { _PyBytes_Resize(&mut self.bytes, self.length as Py_ssize_t) } != 0 {
            return Err(());
        }
        let bytes = self.bytes;
        self.bytes = ptr::null_mut();
        Ok(bytes)
    }

    unsafe fn grow_output(&mut self, needed: usize) -> Result<(), ()> {
        let mut capacity = if self.capacity > (1 << 24) {
            self.capacity + (self.capacity >> 3)
        } else {
            self.capacity * 2 + 1024
        };
        if capacity < needed {
            capacity = needed;
        }
        if unsafe { _PyBytes_Resize(&mut self.bytes, capacity as Py_ssize_t) } != 0 {
            return Err(());
        }
        self.buffer = unsafe { PyBytes_AsString(self.bytes) }.cast::<u8>();
        self.capacity = capacity;
        Ok(())
    }

    #[inline]
    unsafe fn put(&mut self, data: &[u8]) -> Result<(), ()> {
        let end = self.length + data.len();
        if end > self.capacity {
            unsafe { self.grow_output(end) }?;
        }
        unsafe {
            ptr::copy_nonoverlapping(data.as_ptr(), self.buffer.add(self.length), data.len());
        }
        self.length = end;
        Ok(())
    }

    unsafe fn byte(&mut self, value: u8) -> Result<(), ()> {
        unsafe { self.put(&[value]) }
    }

    unsafe fn i32(&mut self, value: i32) -> Result<(), ()> {
        unsafe { self.put(&value.to_le_bytes()) }
    }

    unsafe fn u16(&mut self, value: u32) -> Result<(), ()> {
        unsafe { self.put(&(value as u16).to_le_bytes()) }
    }

    unsafe fn counted(&mut self, tag: u8, value: &[u8]) -> Result<(), ()> {
        if value.len() > i32::MAX as usize || value.len() > MAX_RECORD_SIZE {
            return Err(());
        }
        unsafe {
            self.byte(tag)?;
            self.i32(value.len() as i32)?;
            self.put(value)
        }
    }

    fn hash(key: usize) -> usize {
        ((key >> 3) as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15) as usize >> 32
    }

    unsafe fn probe(&self, key: usize) -> *mut Slot {
        let mut index = Self::hash(key) & self.mask;
        loop {
            let slot = unsafe { self.slots.add(index) };
            let found = unsafe { (*slot).key };
            if found == key || found == 0 {
                return slot;
            }
            index = (index + 1) & self.mask;
        }
    }

    unsafe fn grow_table(&mut self) -> Result<(), ()> {
        let old = self.slots;
        let old_size = if old.is_null() { 0 } else { self.mask + 1 };
        let size = if old_size == 0 { 64 } else { old_size * 2 };
        let slots = unsafe { PyMem_Calloc(size, std::mem::size_of::<Slot>()) }.cast::<Slot>();
        if slots.is_null() {
            return Err(());
        }
        self.slots = slots;
        self.mask = size - 1;
        for index in 0..old_size {
            let slot = unsafe { old.add(index) };
            let key = unsafe { (*slot).key };
            if key != 0 {
                let target = unsafe { self.probe(key) };
                unsafe { (*target).key = key };
                unsafe { (*target).value = (*slot).value };
            }
        }
        unsafe { PyMem_Free(old.cast::<c_void>()) };
        Ok(())
    }

    unsafe fn refs_push(&mut self, value: usize) -> Result<(), ()> {
        if self.refs_len == self.refs_cap {
            let cap = if self.refs_cap == 0 { 256 } else { self.refs_cap * 2 };
            let grown = unsafe {
                PyMem_Realloc(
                    self.refs.cast::<c_void>(),
                    (cap * std::mem::size_of::<usize>()) as _,
                )
            }
            .cast::<usize>();
            if grown.is_null() {
                return Err(());
            }
            self.refs = grown;
            self.refs_cap = cap;
        }
        unsafe {
            *self.refs.add(self.refs_len) = value;
        }
        self.refs_len += 1;
        Ok(())
    }

    unsafe fn refpos_push(&mut self, value: usize) -> Result<(), ()> {
        if self.refpos_len == self.refpos_cap {
            let cap = if self.refpos_cap == 0 { 256 } else { self.refpos_cap * 2 };
            let grown = unsafe {
                PyMem_Realloc(
                    self.refpos.cast::<c_void>(),
                    (cap * std::mem::size_of::<usize>()) as _,
                )
            }
            .cast::<usize>();
            if grown.is_null() {
                return Err(());
            }
            self.refpos = grown;
            self.refpos_cap = cap;
        }
        unsafe {
            *self.refpos.add(self.refpos_len) = value;
        }
        self.refpos_len += 1;
        Ok(())
    }

    /// Flag exactly the objects seen more than once and fix up every
    /// provisional TYPE_REF argument to the reader's reference index.
    unsafe fn resolve(&mut self) -> Result<(), ()> {
        let mut index: u32 = 0;
        for entry in 0..self.refs_len {
            let record = unsafe { *self.refs.add(entry) };
            if record & 1 != 0 {
                let offset = record >> 1;
                if offset >= self.length {
                    return Err(());
                }
                unsafe {
                    *self.buffer.add(offset) |= FLAG_REF;
                }
                unsafe {
                    *self.refs.add(entry) = index as usize;
                }
                index = index.checked_add(1).ok_or(())?;
            }
        }
        for slot in 0..self.refpos_len {
            let position = unsafe { *self.refpos.add(slot) };
            if position + 4 > self.length {
                return Err(());
            }
            let provisional = unsafe { self.buffer.add(position) };
            let mut arg = [0u8; 4];
            unsafe {
                ptr::copy_nonoverlapping(provisional, arg.as_mut_ptr(), 4);
            }
            let entry = u32::from_le_bytes(arg) as usize;
            if entry >= self.refs_len {
                return Err(());
            }
            let resolved = unsafe { *self.refs.add(entry) };
            if resolved > u32::MAX as usize {
                return Err(());
            }
            unsafe {
                ptr::copy_nonoverlapping(
                    (resolved as u32).to_le_bytes().as_ptr(),
                    provisional,
                    4,
                );
            }
        }
        Ok(())
    }

    /// `Ok(Some(entry))` when the object was already recorded, in which
    /// case the caller writes a TYPE_REF with the provisional entry index;
    /// `Ok(None)` on the first occurrence, written plainly while its type
    /// byte offset is recorded. `Err` on a recursive reference.
    unsafe fn track(&mut self, object: *mut PyObject, active: bool) -> Result<Option<u32>, ()> {
        if self.slots.is_null() || (self.entries as usize + 1) * 4 > (self.mask + 1) * 3 {
            unsafe { self.grow_table() }?;
        }
        let key = object as usize;
        let slot = unsafe { self.probe(key) };
        if unsafe { (*slot).key } == key {
            let value = unsafe { (*slot).value };
            if value & ACTIVE != 0 {
                return Err(());
            }
            let entry = value & INDEX_MASK;
            unsafe {
                (*slot).value = value | SHARED;
                let record = self.refs.add(entry as usize);
                *record |= 1;
            }
            return Ok(Some(entry));
        }
        if self.entries >= INDEX_MASK {
            return Err(());
        }
        let entry = self.refs_len;
        if entry >= INDEX_MASK as usize {
            return Err(());
        }
        unsafe {
            self.refs_push(self.length << 1)?;
            (*slot).key = key;
            (*slot).value = entry as u32 | if active { ACTIVE } else { 0 };
            Py_IncRef(object);
        }
        self.entries += 1;
        Ok(None)
    }

    unsafe fn complete(&mut self, object: *mut PyObject) {
        if self.slots.is_null() {
            return;
        }
        let slot = unsafe { self.probe(object as usize) };
        if unsafe { (*slot).key } == object as usize {
            unsafe { (*slot).value &= !ACTIVE };
        }
    }

    // The recursive entry points use the C ABI so that no caller needs an
    // unwind path: nothing here can unwind, and the panic runtime stays out.
    #[allow(improper_ctypes_definitions)]
    unsafe extern "C" fn write_object(&mut self, value: *mut PyObject, depth: usize) -> Result<(), ()> {
        if depth >= MAX_DEPTH || self.length > MAX_RECORD_SIZE {
            return Err(());
        }
        if value == ptr::addr_of_mut!(_Py_NoneStruct) {
            return unsafe { self.byte(b'N') };
        }
        let ty = unsafe { (*value.cast::<_object>()).ob_type };
        if ty == ptr::addr_of_mut!(PyBool_Type) {
            let truth = unsafe { PyObject_IsTrue(value) };
            if truth < 0 {
                return Err(());
            }
            return unsafe { self.byte(if truth != 0 { b'T' } else { b'F' }) };
        }
        let kind = kind_of(ty).ok_or(())?;

        let mut text = None;
        if kind == Kind::Unicode {
            text = Some(unsafe { unicode_info(value) });
        }
        // An active record is a cycle this codec does not encode; the C
        // writer then applies the version's cycle rules and error text.
        let active = true;
        match unsafe { self.track(value, active) }? {
            Some(entry) => {
                unsafe { self.byte(b'r')? };
                unsafe { self.i32(entry as i32) }?;
                unsafe { self.refpos_push(self.length - 4) }?;
                return Ok(());
            }
            None => {}
        }

        unsafe {
            match kind {
                Kind::Int => self.write_int(value)?,
                Kind::Float => {
                    let number = (*value.cast::<PyFloatObject>()).ob_fval;
                    self.byte(b'g')?;
                    self.put(&number.to_le_bytes())?;
                }
                Kind::Complex => {
                    let real = PyComplex_RealAsDouble(value);
                    if real == -1.0 && !PyErr_Occurred().is_null() {
                        return Err(());
                    }
                    let imaginary = PyComplex_ImagAsDouble(value);
                    if imaginary == -1.0 && !PyErr_Occurred().is_null() {
                        return Err(());
                    }
                    self.byte(b'y')?;
                    self.put(&real.to_le_bytes())?;
                    self.put(&imaginary.to_le_bytes())?;
                }
                Kind::Bytes => {
                    let length = PyBytes_Size(value);
                    let data = PyBytes_AsString(value);
                    if length < 0 || data.is_null() {
                        return Err(());
                    }
                    let data = slice::from_raw_parts(data.cast::<u8>(), length as usize);
                    self.counted(b's', data)?;
                }
                Kind::Unicode => match text {
                    Some(info) => self.write_unicode(value, info)?,
                    None => return Err(()),
                },
                Kind::Tuple => self.write_sequence(value, depth, true)?,
                Kind::List => self.write_sequence(value, depth, false)?,
                Kind::Dict => self.write_dict(value, depth)?,
                Kind::Set | Kind::FrozenSet => self.write_set(value, depth, kind)?,
                Kind::Code => self.write_code(value, depth)?,
            }
            self.complete(value);
        }
        Ok(())
    }

    unsafe fn write_int(&mut self, value: *mut PyObject) -> Result<(), ()> {
        // A compact int is one digit and a sign in its tag.
        let long_value = unsafe { &(*value.cast::<_longobject>()).long_value };
        if long_value.lv_tag < 16 {
            let sign = 1 - (long_value.lv_tag & 3) as i64;
            let small = sign * long_value.ob_digit[0] as i64;
            if (i32::MIN as i64..=i32::MAX as i64).contains(&small) {
                unsafe {
                    self.byte(b'i')?;
                    return self.i32(small as i32);
                }
            }
        }
        let mut overflow: c_int = 0;
        let small = unsafe { PyLong_AsLongLongAndOverflow(value, &mut overflow) };
        if overflow == 0 {
            if small == -1 && !unsafe { PyErr_Occurred() }.is_null() {
                return Err(());
            }
            if (i32::MIN as i64..=i32::MAX as i64).contains(&small) {
                unsafe {
                    self.byte(b'i')?;
                    return self.i32(small as i32);
                }
            }
        }

        let mut export = MaybeUninit::<PyLongExport>::zeroed();
        if unsafe { PyLong_Export(value, export.as_mut_ptr()) } < 0 {
            return Err(());
        }
        let export = unsafe { export.assume_init() };
        let result = unsafe { self.write_long_digits(&export) };
        if !export.digits.is_null() {
            unsafe { PyLong_FreeExport(&export as *const PyLongExport as *mut PyLongExport) };
        }
        result
    }

    unsafe fn write_long_digits(&mut self, export: &PyLongExport) -> Result<(), ()> {
        unsafe {
            if export.digits.is_null() {
                let negative = export.value < 0;
                let magnitude = export.value.unsigned_abs();
                let mut count: i32 = 0;
                let mut rest = magnitude;
                loop {
                    rest >>= MARSHAL_SHIFT;
                    count += 1;
                    if rest == 0 {
                        break;
                    }
                }
                self.byte(b'l')?;
                self.i32(if negative { -count } else { count })?;
                let mut rest = magnitude;
                loop {
                    self.u16((rest & MARSHAL_MASK as u64) as u32)?;
                    rest >>= MARSHAL_SHIFT;
                    if rest == 0 {
                        return Ok(());
                    }
                }
            }

            let layout = PyLong_GetNativeLayout();
            let bits = (*layout).bits_per_digit as u32;
            let width = (*layout).digit_size;
            if bits % MARSHAL_SHIFT != 0
                || bits < MARSHAL_SHIFT
                || (width != 2 && width != 4)
                || (*layout).digits_order != -1
                || (*layout).digit_endianness != -1
                || export.ndigits < 1
            {
                return Err(());
            }
            let ratio = (bits / MARSHAL_SHIFT) as usize;
            let count = export.ndigits as usize;
            let digit = |index: usize| -> u32 {
                if width == 4 {
                    *export.digits.cast::<u32>().add(index)
                } else {
                    *export.digits.cast::<u16>().add(index) as u32
                }
            };
            let top = digit(count - 1);
            let mut shorts = (count - 1) * ratio;
            let mut rest = top;
            loop {
                rest >>= MARSHAL_SHIFT;
                shorts += 1;
                if rest == 0 {
                    break;
                }
            }
            if shorts > i32::MAX as usize || shorts * 2 > MAX_RECORD_SIZE {
                return Err(());
            }
            self.byte(b'l')?;
            self.i32(if export.negative != 0 { -(shorts as i32) } else { shorts as i32 })?;
            for index in 0..count - 1 {
                let mut rest = digit(index);
                for _ in 0..ratio {
                    self.u16(rest & MARSHAL_MASK)?;
                    rest >>= MARSHAL_SHIFT;
                }
            }
            let mut rest = top;
            loop {
                self.u16(rest & MARSHAL_MASK)?;
                rest >>= MARSHAL_SHIFT;
                if rest == 0 {
                    return Ok(());
                }
            }
        }
    }

    unsafe fn write_unicode(&mut self, value: *mut PyObject, info: UnicodeInfo) -> Result<(), ()> {
        let interned = info.interned;
        if !info.data.is_null() {
            let bytes = unsafe { slice::from_raw_parts(info.data.cast::<u8>(), info.length) };
            return unsafe { self.write_ascii(bytes, interned) };
        }
        let encoded = unsafe {
            PyUnicode_AsEncodedString(value, c"utf-8".as_ptr(), c"surrogatepass".as_ptr())
        };
        let encoded = unsafe { PyRef::from_raw(encoded) }?;
        let length = unsafe { PyBytes_Size(encoded.as_ptr()) };
        let data = unsafe { PyBytes_AsString(encoded.as_ptr()) };
        if length < 0 || data.is_null() {
            return Err(());
        }
        let bytes = unsafe { slice::from_raw_parts(data.cast::<u8>(), length as usize) };
        // Upstream writes an ASCII-content string in the ASCII form even when
        // its storage is not compact ASCII; the encoded bytes alone cannot
        // tell the two apart.
        if info.ascii {
            return unsafe { self.write_ascii(bytes, interned) };
        }
        unsafe { self.counted(if interned { b't' } else { b'u' }, bytes) }
    }

    unsafe fn write_ascii(&mut self, bytes: &[u8], interned: bool) -> Result<(), ()> {
        unsafe {
            if bytes.len() < 256 {
                self.byte(if interned { b'Z' } else { b'z' })?;
                self.byte(bytes.len() as u8)?;
                self.put(bytes)
            } else {
                self.counted(if interned { b'A' } else { b'a' }, bytes)
            }
        }
    }

    unsafe fn write_sequence(
        &mut self,
        value: *mut PyObject,
        depth: usize,
        tuple: bool,
    ) -> Result<(), ()> {
        let length = unsafe { (*value.cast::<PyVarObject>()).ob_size };
        if length < 0 || length as usize > MAX_RECORD_SIZE / 4 {
            return Err(());
        }
        unsafe {
            if tuple && length < 256 {
                self.byte(b')')?;
                self.byte(length as u8)?;
            } else {
                self.byte(if tuple { b'(' } else { b'[' })?;
                self.i32(length as i32)?;
            }
        }
        for index in 0..length {
            // No Python code runs while writing, so the items cannot change.
            let item = unsafe {
                if tuple {
                    *(*value.cast::<PyTupleObject>()).ob_item.as_ptr().offset(index)
                } else {
                    *(*value.cast::<PyListObject>()).ob_item.offset(index)
                }
            };
            if item.is_null() {
                return Err(());
            }
            unsafe { self.write_object(item, depth + 1) }?;
        }
        Ok(())
    }

    unsafe fn write_dict(&mut self, value: *mut PyObject, depth: usize) -> Result<(), ()> {
        unsafe { self.byte(b'{') }?;
        let mut position = 0;
        loop {
            let mut key = ptr::null_mut();
            let mut item = ptr::null_mut();
            if unsafe { PyDict_Next(value, &mut position, &mut key, &mut item) } == 0 {
                break;
            }
            unsafe { self.write_object(key, depth + 1) }?;
            unsafe { self.write_object(item, depth + 1) }?;
        }
        unsafe { self.byte(b'0') }
    }

    /// Sets are ordered by each element's standalone record so the bytes do
    /// not depend on hash randomization.
    unsafe fn write_set(
        &mut self,
        value: *mut PyObject,
        depth: usize,
        kind: Kind,
    ) -> Result<(), ()> {
        let iterator = unsafe { PyObject_GetIter(value) };
        let iterator = unsafe { PyRef::from_raw(iterator) }?;
        let pairs = unsafe { PyList_New(0) };
        let pairs = unsafe { PyRef::from_raw(pairs) }?;
        loop {
            let item = unsafe { PyIter_Next(iterator.as_ptr()) };
            if item.is_null() {
                if !unsafe { PyErr_Occurred() }.is_null() {
                    return Err(());
                }
                break;
            }
            let item = PyRef(item);
            let mut standalone = unsafe { Encoder::new(self.allow_code) }?;
            unsafe { standalone.write_object(item.as_ptr(), 0) }?;
            unsafe { standalone.resolve() }?;
            let key = PyRef(unsafe { standalone.finish() }?);
            let pair = unsafe { PyTuple_New(2) };
            let pair = unsafe { PyRef::from_raw(pair) }?;
            unsafe {
                PyTuple_SetItem(pair.as_ptr(), 0, key.into_raw());
                PyTuple_SetItem(pair.as_ptr(), 1, item.into_raw());
                if PyList_Append(pairs.as_ptr(), pair.as_ptr()) != 0 {
                    return Err(());
                }
            }
        }
        if unsafe { PyList_Sort(pairs.as_ptr()) } != 0 {
            return Err(());
        }
        let count = unsafe { PyList_Size(pairs.as_ptr()) };
        if count < 0 || count as usize > MAX_RECORD_SIZE / 4 {
            return Err(());
        }
        unsafe {
            self.byte(if kind == Kind::Set { b'<' } else { b'>' })?;
            self.i32(count as i32)?;
        }
        for index in 0..count {
            let pair = unsafe { PyList_GetItem(pairs.as_ptr(), index) };
            let item = unsafe { PyTuple_GetItem(pair, 1) };
            if item.is_null() {
                return Err(());
            }
            unsafe { self.write_object(item, depth + 1) }?;
        }
        Ok(())
    }

    unsafe fn write_code(&mut self, value: *mut PyObject, depth: usize) -> Result<(), ()> {
        if !self.allow_code {
            return Err(());
        }
        let mut parts = MaybeUninit::<CodeParts>::zeroed();
        if unsafe { _PyMarshal_RustCodeParts(value, parts.as_mut_ptr()) } != 0 {
            return Err(());
        }
        let parts = unsafe { parts.assume_init() };
        // The bytecode is a new reference; everything else is borrowed from
        // the code object.
        let _bytecode = PyRef(parts.objs[0]);
        unsafe { self.byte(b'c') }?;
        for number in parts.ints.iter().take(5) {
            unsafe { self.i32(*number) }?;
        }
        for object in parts.objs.iter().take(8) {
            unsafe { self.write_object(*object, depth + 1) }?;
        }
        unsafe { self.i32(parts.ints[5]) }?;
        for object in parts.objs.iter().skip(8) {
            unsafe { self.write_object(*object, depth + 1) }?;
        }
        Ok(())
    }
}

impl Drop for Encoder {
    fn drop(&mut self) {
        unsafe {
            if !self.slots.is_null() {
                for index in 0..=self.mask {
                    let key = (*self.slots.add(index)).key;
                    if key != 0 {
                        Py_DecRef(key as *mut PyObject);
                    }
                }
                PyMem_Free(self.slots.cast::<c_void>());
            }
            if !self.refs.is_null() {
                PyMem_Free(self.refs.cast::<c_void>());
            }
            if !self.refpos.is_null() {
                PyMem_Free(self.refpos.cast::<c_void>());
            }
            if !self.bytes.is_null() {
                Py_DecRef(self.bytes);
            }
        }
    }
}

enum Decoded {
    Object(PyRef),
    DictEnd,
}

/// Reads one marshal record.  Back references are owned in an array from
/// Python's allocator; the entry of a record still being read is null.
struct Decoder<'a> {
    input: &'a [u8],
    position: usize,
    references: *mut *mut PyObject,
    count: usize,
    capacity: usize,
    allow_code: bool,
}

impl Drop for Decoder<'_> {
    fn drop(&mut self) {
        unsafe {
            for index in 0..self.count {
                let object = *self.references.add(index);
                if !object.is_null() {
                    Py_DecRef(object);
                }
            }
            PyMem_Free(self.references.cast::<c_void>());
        }
    }
}

impl<'a> Decoder<'a> {
    fn take(&mut self, size: usize) -> Result<&'a [u8], ()> {
        if size > MAX_RECORD_SIZE {
            return Err(());
        }
        let end = self.position.checked_add(size).ok_or(())?;
        let bytes = self.input.get(self.position..end).ok_or(())?;
        self.position = end;
        Ok(bytes)
    }

    fn u8(&mut self) -> Result<u8, ()> {
        let byte = *self.input.get(self.position).ok_or(())?;
        self.position += 1;
        Ok(byte)
    }

    fn i32(&mut self) -> Result<i32, ()> {
        let bytes: [u8; 4] = self.take(4)?.try_into().map_err(|_| ())?;
        Ok(i32::from_le_bytes(bytes))
    }

    fn eight(&mut self) -> Result<[u8; 8], ()> {
        self.take(8)?.try_into().map_err(|_| ())
    }

    fn size(&mut self) -> Result<usize, ()> {
        let count = self.i32()?;
        if count < 0 || count as usize > MAX_RECORD_SIZE / 2 {
            return Err(());
        }
        Ok(count as usize)
    }

    /// Append an entry (owning `value`, or null while reserved).
    unsafe fn push(&mut self, value: *mut PyObject) -> Result<usize, ()> {
        let index = self.count;
        if index >= 0x7fff_fffe {
            return Err(());
        }
        if index == self.capacity {
            let capacity = if self.capacity == 0 { 64 } else { self.capacity * 2 };
            let bytes = capacity * std::mem::size_of::<*mut PyObject>();
            let grown = unsafe { PyMem_Realloc(self.references.cast::<c_void>(), bytes) };
            if grown.is_null() {
                return Err(());
            }
            self.references = grown.cast::<*mut PyObject>();
            self.capacity = capacity;
        }
        unsafe { *self.references.add(index) = value };
        self.count += 1;
        Ok(index)
    }

    unsafe fn reserve(&mut self, flag: bool) -> Result<Option<usize>, ()> {
        if !flag {
            return Ok(None);
        }
        Ok(Some(unsafe { self.push(ptr::null_mut()) }?))
    }

    unsafe fn insert(&mut self, index: Option<usize>, value: *mut PyObject) -> Result<(), ()> {
        if let Some(index) = index {
            unsafe {
                Py_IncRef(value);
                *self.references.add(index) = value;
            }
        }
        Ok(())
    }

    unsafe fn finish_ref(&mut self, flag: bool, value: *mut PyObject) -> Result<Decoded, ()> {
        let value = unsafe { PyRef::from_raw(value) }?;
        if flag {
            unsafe { Py_IncRef(value.as_ptr()) };
            if unsafe { self.push(value.as_ptr()) }.is_err() {
                unsafe { Py_DecRef(value.as_ptr()) };
                return Err(());
            }
        }
        Ok(Decoded::Object(value))
    }

    unsafe fn item(&mut self, depth: usize) -> Result<PyRef, ()> {
        match unsafe { self.decode(depth) }? {
            Decoded::Object(item) => Ok(item),
            Decoded::DictEnd => Err(()),
        }
    }

    unsafe fn text(&mut self, bytes: &[u8], errors: *const c_char) -> Result<PyRef, ()> {
        let value = unsafe { PyUnicode_DecodeUTF8(bytes.as_ptr().cast(), bytes.len() as Py_ssize_t, errors) };
        unsafe { PyRef::from_raw(value) }
    }

    unsafe fn intern(value: PyRef) -> Result<PyRef, ()> {
        let mut pointer = value.into_raw();
        unsafe { PyUnicode_InternInPlace(&mut pointer) };
        unsafe { PyRef::from_raw(pointer) }
    }

    unsafe fn decode_long(&mut self) -> Result<PyRef, ()> {
        let signed = self.i32()?;
        let count = signed.checked_abs().ok_or(())? as usize;
        if count == 0 || count > MAX_RECORD_SIZE / 2 {
            return Err(());
        }
        let layout = unsafe { PyLong_GetNativeLayout() };
        let bits = unsafe { (*layout).bits_per_digit } as u32;
        let width = unsafe { (*layout).digit_size };
        if bits % MARSHAL_SHIFT != 0
            || bits < MARSHAL_SHIFT
            || (width != 2 && width != 4)
            || unsafe { (*layout).digits_order } != -1
            || unsafe { (*layout).digit_endianness } != -1
        {
            return Err(());
        }
        let ratio = ((bits / MARSHAL_SHIFT) as usize).max(1);
        let size = 1 + (count - 1) / ratio;
        let top_shorts = 1 + (count - 1) % ratio;
        let mut digits: *mut c_void = ptr::null_mut();
        let writer = unsafe { PyLongWriter_Create((signed < 0) as c_int, size as Py_ssize_t, &mut digits) };
        if writer.is_null() {
            return Err(());
        }
        for index in 0..size {
            let shorts = if index + 1 == size { top_shorts } else { ratio };
            let mut digit: u32 = 0;
            for offset in 0..shorts {
                let short = match self.take(2) {
                    Ok(pair) => pair.first().copied().unwrap_or(0) as u32
                        | (pair.get(1).copied().unwrap_or(0) as u32) << 8,
                    Err(()) => {
                        unsafe { PyLongWriter_Discard(writer) };
                        return Err(());
                    }
                };
                // An unnormalized number or an out-of-range short is not data
                // the fast reader accepts; the C reader reports it.
                if short > MARSHAL_MASK || (index + 1 == size && offset + 1 == shorts && short == 0) {
                    unsafe { PyLongWriter_Discard(writer) };
                    return Err(());
                }
                digit |= short << (MARSHAL_SHIFT * offset as u32);
            }
            unsafe {
                if width == 4 {
                    *digits.cast::<u32>().add(index) = digit;
                } else {
                    *digits.cast::<u16>().add(index) = digit as u16;
                }
            }
        }
        unsafe { PyRef::from_raw(PyLongWriter_Finish(writer)) }
    }

    unsafe fn decode_code(&mut self, flag: bool, depth: usize) -> Result<Decoded, ()> {
        if !self.allow_code {
            return Err(());
        }
        let index = unsafe { self.reserve(flag) }?;
        let mut parts = MaybeUninit::<CodeParts>::zeroed();
        let parts = unsafe { &mut *parts.as_mut_ptr() };
        let result = unsafe { self.fill_code(parts, depth) };
        let code = match result {
            Ok(()) => unsafe { _PyMarshal_RustBuildCode(parts) },
            Err(()) => ptr::null_mut(),
        };
        for object in parts.objs {
            if !object.is_null() {
                unsafe { Py_DecRef(object) };
            }
        }
        let code = unsafe { PyRef::from_raw(code) }?;
        unsafe { self.insert(index, code.as_ptr()) }?;
        Ok(Decoded::Object(code))
    }

    unsafe fn fill_code(&mut self, parts: &mut CodeParts, depth: usize) -> Result<(), ()> {
        for number in parts.ints.iter_mut().take(5) {
            *number = self.i32()?;
        }
        for object in parts.objs.iter_mut().take(8) {
            *object = unsafe { self.item(depth + 1) }?.into_raw();
        }
        parts.ints[5] = self.i32()?;
        for object in parts.objs.iter_mut().skip(8) {
            *object = unsafe { self.item(depth + 1) }?.into_raw();
        }
        Ok(())
    }

    #[allow(improper_ctypes_definitions)]
    unsafe extern "C" fn decode(&mut self, depth: usize) -> Result<Decoded, ()> {
        if depth >= MAX_DEPTH {
            return Err(());
        }
        let code = self.u8()?;
        let flag = code & FLAG_REF != 0;
        let tag = code & !FLAG_REF;
        match tag {
            b'0' if !flag => Ok(Decoded::DictEnd),
            b'N' if !flag => {
                unsafe { Py_IncRef(ptr::addr_of_mut!(_Py_NoneStruct)) };
                Ok(Decoded::Object(PyRef(ptr::addr_of_mut!(_Py_NoneStruct))))
            }
            b'T' | b'F' if !flag => {
                let value = unsafe { cpython_sys::PyBool_FromLong((tag == b'T') as _) };
                Ok(Decoded::Object(unsafe { PyRef::from_raw(value) }?))
            }
            b'i' => {
                let value = unsafe { PyLong_FromLongLong(self.i32()? as i64) };
                unsafe { self.finish_ref(flag, value) }
            }
            b'I' => {
                let value = unsafe { PyLong_FromLongLong(i64::from_le_bytes(self.eight()?)) };
                unsafe { self.finish_ref(flag, value) }
            }
            b'l' => {
                let value = unsafe { self.decode_long() }?;
                unsafe { self.finish_ref(flag, value.into_raw()) }
            }
            b'g' => {
                let value = unsafe { PyFloat_FromDouble(f64::from_le_bytes(self.eight()?)) };
                unsafe { self.finish_ref(flag, value) }
            }
            b'y' => {
                let real = f64::from_le_bytes(self.eight()?);
                let imaginary = f64::from_le_bytes(self.eight()?);
                let value = unsafe { PyComplex_FromDoubles(real, imaginary) };
                unsafe { self.finish_ref(flag, value) }
            }
            b's' => {
                let count = self.size()?;
                let bytes = self.take(count)?;
                let value = unsafe {
                    PyBytes_FromStringAndSize(bytes.as_ptr().cast(), count as Py_ssize_t)
                };
                unsafe { self.finish_ref(flag, value) }
            }
            b'a' | b'A' | b'u' | b't' => {
                let count = self.size()?;
                let bytes = self.take(count)?;
                if (tag == b'a' || tag == b'A') && !bytes.is_ascii() {
                    return Err(());
                }
                let mut value = unsafe { self.text(bytes, c"surrogatepass".as_ptr()) }?;
                if tag == b'A' || tag == b't' {
                    value = unsafe { Self::intern(value) }?;
                }
                unsafe { self.finish_ref(flag, value.into_raw()) }
            }
            b'z' | b'Z' => {
                let count = self.u8()? as usize;
                let bytes = self.take(count)?;
                if !bytes.is_ascii() {
                    return Err(());
                }
                let mut value = unsafe { self.text(bytes, c"strict".as_ptr()) }?;
                if tag == b'Z' {
                    value = unsafe { Self::intern(value) }?;
                }
                unsafe { self.finish_ref(flag, value.into_raw()) }
            }
            b')' | b'(' | b'[' => {
                let count = if tag == b')' { self.u8()? as usize } else { self.size()? };
                let index = unsafe { self.reserve(flag) }?;
                let value = unsafe {
                    if tag == b'[' {
                        PyList_New(count as Py_ssize_t)
                    } else {
                        PyTuple_New(count as Py_ssize_t)
                    }
                };
                let value = unsafe { PyRef::from_raw(value) }?;
                if tag == b'[' {
                    unsafe { self.insert(index, value.as_ptr()) }?;
                }
                for offset in 0..count {
                    let item = unsafe { self.item(depth + 1) }?;
                    unsafe {
                        if tag == b'[' {
                            if PyList_SetItem(value.as_ptr(), offset as Py_ssize_t, item.into_raw()) != 0 {
                                return Err(());
                            }
                        } else {
                            _PyMarshal_RustTupleSetItem(
                                value.as_ptr(),
                                offset as Py_ssize_t,
                                item.into_raw(),
                            );
                        }
                    }
                }
                if tag != b'[' {
                    unsafe { self.insert(index, value.as_ptr()) }?;
                }
                Ok(Decoded::Object(value))
            }
            b'{' => {
                let index = unsafe { self.reserve(flag) }?;
                let value = unsafe { PyRef::from_raw(PyDict_New()) }?;
                unsafe { self.insert(index, value.as_ptr()) }?;
                loop {
                    let key = match unsafe { self.decode(depth + 1) }? {
                        Decoded::Object(key) => key,
                        Decoded::DictEnd => break,
                    };
                    let item = unsafe { self.item(depth + 1) }?;
                    if unsafe { PyDict_SetItem(value.as_ptr(), key.as_ptr(), item.as_ptr()) } != 0 {
                        return Err(());
                    }
                }
                Ok(Decoded::Object(value))
            }
            b'c' => unsafe { self.decode_code(flag, depth) },
            b'<' | b'>' => {
                let count = self.size()?;
                let index = unsafe { self.reserve(flag) }?;
                let set = unsafe { PyRef::from_raw(PySet_New(ptr::null_mut())) }?;
                if tag == b'<' {
                    unsafe { self.insert(index, set.as_ptr()) }?;
                }
                for _ in 0..count {
                    let item = unsafe { self.item(depth + 1) }?;
                    if unsafe { PySet_Add(set.as_ptr(), item.as_ptr()) } != 0 {
                        return Err(());
                    }
                }
                if tag == b'<' {
                    Ok(Decoded::Object(set))
                } else {
                    let frozen = unsafe { PyRef::from_raw(PyFrozenSet_New(set.as_ptr())) }?;
                    unsafe { self.insert(index, frozen.as_ptr()) }?;
                    Ok(Decoded::Object(frozen))
                }
            }
            b'r' if !flag => {
                let index = self.size()?;
                if index >= self.count {
                    return Err(());
                }
                let value = unsafe { *self.references.add(index) };
                if value.is_null() {
                    return Err(());
                }
                unsafe { Py_IncRef(value) };
                Ok(Decoded::Object(PyRef(value)))
            }
            _ => Err(()),
        }
    }
}

/// Serialize a supported value; NULL without an exception when the value is
/// not one this codec handles.
unsafe extern "C" fn api_dumps(value: *mut PyObject, allow_code: c_int) -> *mut PyObject {
    let result = unsafe {
        match Encoder::new(allow_code != 0) {
            Ok(mut encoder) => match encoder.write_object(value, 0) {
                Ok(()) => match encoder.resolve() {
                    Ok(()) => encoder.finish(),
                    Err(()) => Err(()),
                },
                Err(()) => Err(()),
            },
            Err(()) => Err(()),
        }
    };
    match result {
        Ok(bytes) => bytes,
        Err(()) => {
            unsafe { PyErr_Clear() };
            ptr::null_mut()
        }
    }
}

/// Read a supported record; NULL without an exception when the data is not
/// one this codec accepts.
unsafe extern "C" fn api_loads(data: *const c_char, size: Py_ssize_t, allow_code: c_int) -> *mut PyObject {
    let input = if size > 0 {
        unsafe { slice::from_raw_parts(data.cast::<u8>(), size as usize) }
    } else {
        &[]
    };
    let mut decoder = Decoder {
        input,
        position: 0,
        references: ptr::null_mut(),
        count: 0,
        capacity: 0,
        allow_code: allow_code != 0,
    };
    match unsafe { decoder.decode(0) } {
        Ok(Decoded::Object(value)) => value.into_raw(),
        Ok(Decoded::DictEnd) | Err(()) => {
            unsafe { PyErr_Clear() };
            ptr::null_mut()
        }
    }
}

#[repr(C)]
struct Api {
    dumps: unsafe extern "C" fn(*mut PyObject, c_int) -> *mut PyObject,
    loads: unsafe extern "C" fn(*const c_char, Py_ssize_t, c_int) -> *mut PyObject,
}

static API: Api = Api {
    dumps: api_dumps,
    loads: api_loads,
};

struct ModuleDef(std::cell::UnsafeCell<PyModuleDef>);

unsafe impl Sync for ModuleDef {}

struct ModuleSlots([PyModuleDef_Slot; 3]);

unsafe impl Sync for ModuleSlots {}

const PY_MOD_EXEC: c_int = 85;
const PY_MOD_MULTIPLE_INTERPRETERS: c_int = 86;
const PY_MOD_PER_INTERPRETER_GIL_SUPPORTED: *mut c_void = 2 as *mut c_void;

unsafe extern "C" fn module_exec(module: *mut PyObject) -> c_int {
    let capsule = unsafe {
        PyCapsule_New(
            ptr::addr_of!(API).cast_mut().cast::<c_void>(),
            c"_marshal_rs._api".as_ptr(),
            None,
        )
    };
    if capsule.is_null() {
        return -1;
    }
    unsafe { PyModule_Add(module, c"_api".as_ptr(), capsule) }
}

static MODULE_SLOTS: ModuleSlots = ModuleSlots([
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

static MODULE: ModuleDef = ModuleDef(std::cell::UnsafeCell::new(PyModuleDef {
    m_base: PyModuleDef_HEAD_INIT,
    m_name: c"_marshal_rs".as_ptr() as *mut _,
    m_doc: c"Rust support for Python marshal records".as_ptr() as *mut _,
    m_size: 0,
    m_methods: ptr::null_mut(),
    m_slots: MODULE_SLOTS.0.as_ptr() as *mut PyModuleDef_Slot,
    m_traverse: None,
    m_clear: None,
    m_free: None,
}));

#[unsafe(no_mangle)]
pub extern "C" fn PyInit__marshal_rs() -> *mut PyObject {
    unsafe { PyModuleDef_Init(MODULE.0.get()) }
}
