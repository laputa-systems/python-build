use std::cell::UnsafeCell;
use std::ffi::{CString, c_char, c_int, c_void};
use std::mem::MaybeUninit;
use std::ptr;
use std::slice;

use cpython_binding::{Borrowed, Buffer};
use cpython_sys::{
    METH_FASTCALL, PyBytes_AsStringAndSize,
    PyBytes_FromStringAndSize, PyErr_CheckSignals, PyErr_NoMemory, PyErr_Occurred,
    PyErr_SetFromErrno, PyErr_SetString, PyEval_RestoreThread, PyEval_SaveThread,
    PyExc_OSError, PyExc_TypeError, PyExc_ValueError, PyLong_AsLong, PyLong_AsSsize_t,
    PyLong_FromLong, PyLong_FromSsize_t, PyMethodDef, PyMethodDefFuncPointer,
    PyModuleDef, PyModuleDef_Slot, PyModuleDef_HEAD_INIT, PyModuleDef_Init,
    PyObject, PyObject_IsTrue, PyTuple_GetItem, PyTuple_Size,
    Py_ssize_t,
};

unsafe extern "C" {
    static mut environ: *mut *mut c_char;
}

fn last_errno() -> c_int {
    std::io::Error::last_os_error().raw_os_error().unwrap_or(libc::EIO)
}

unsafe fn integer(object: *mut PyObject) -> Option<c_int> {
    let value = unsafe { PyLong_AsLong(object) };
    if value == -1 && !unsafe { PyErr_Occurred() }.is_null() {
        return None;
    }
    if value < c_int::MIN as _ || value > c_int::MAX as _ {
        unsafe { PyErr_SetString(PyExc_ValueError, c"integer is out of range".as_ptr()) };
        return None;
    }
    Some(value as c_int)
}

unsafe fn bytes_cstring(object: *mut PyObject) -> Option<CString> {
    let mut data = ptr::null_mut();
    let mut length = 0;
    if unsafe { PyBytes_AsStringAndSize(object, &mut data, &mut length) } < 0 {
        return None;
    }
    let bytes = unsafe { slice::from_raw_parts(data.cast::<u8>(), length as usize) };
    match CString::new(bytes) {
        Ok(value) => Some(value),
        Err(_) => {
            unsafe { PyErr_SetString(PyExc_ValueError, c"embedded null byte".as_ptr()) };
            None
        }
    }
}

unsafe fn read_impl(args: *mut *mut PyObject, nargs: Py_ssize_t) -> *mut PyObject {
    if nargs != 2 {
        unsafe { PyErr_SetString(PyExc_TypeError, c"read() takes 2 arguments".as_ptr()) };
        return ptr::null_mut();
    }
    let Some(fd) = (unsafe { integer(*args) }) else {
        return ptr::null_mut();
    };
    let count = unsafe { PyLong_AsSsize_t(*args.add(1)) };
    if count == -1 && !unsafe { PyErr_Occurred() }.is_null() {
        return ptr::null_mut();
    }
    if count < 0 {
        unsafe { PyErr_SetString(PyExc_ValueError, c"negative read length".as_ptr()) };
        return ptr::null_mut();
    }
    let mut bytes = Vec::<u8>::new();
    if bytes.try_reserve_exact(count as usize).is_err() {
        unsafe { PyErr_NoMemory() };
        return ptr::null_mut();
    }
    loop {
        let thread = unsafe { PyEval_SaveThread() };
        let read_count = unsafe { libc::read(fd, bytes.as_mut_ptr().cast(), count as usize) };
        let error = if read_count < 0 { last_errno() } else { 0 };
        unsafe { PyEval_RestoreThread(thread) };
        if read_count >= 0 {
            return unsafe {
                PyBytes_FromStringAndSize(bytes.as_ptr().cast(), read_count as Py_ssize_t)
            };
        }
        if error == libc::EINTR {
            if unsafe { PyErr_CheckSignals() } < 0 {
                return ptr::null_mut();
            }
            continue;
        }
        unsafe { set_errno(error) };
        return ptr::null_mut();
    }
}

unsafe fn set_errno(error: c_int) {
    #[cfg(target_os = "macos")]
    unsafe { *libc::__error() = error };
    #[cfg(target_os = "linux")]
    unsafe { *libc::__errno_location() = error };
    unsafe { PyErr_SetFromErrno(PyExc_OSError) };
}

unsafe fn write_impl(args: *mut *mut PyObject, nargs: Py_ssize_t) -> *mut PyObject {
    if nargs != 2 {
        unsafe { PyErr_SetString(PyExc_TypeError, c"write() takes 2 arguments".as_ptr()) };
        return ptr::null_mut();
    }
    let Some(fd) = (unsafe { integer(*args) }) else {
        return ptr::null_mut();
    };
    let Ok(buffer) = Buffer::acquire(unsafe { Borrowed::from_raw((*args.add(1)).cast()) }) else {
        return ptr::null_mut();
    };
    let bytes = buffer.as_bytes();
    loop {
        let thread = unsafe { PyEval_SaveThread() };
        let written = unsafe { libc::write(fd, bytes.as_ptr().cast(), bytes.len()) };
        let error = if written < 0 { last_errno() } else { 0 };
        unsafe { PyEval_RestoreThread(thread) };
        if written >= 0 {
            drop(buffer);
            return unsafe { PyLong_FromSsize_t(written as Py_ssize_t) };
        }
        if error == libc::EINTR {
            if unsafe { PyErr_CheckSignals() } < 0 {
                drop(buffer);
                return ptr::null_mut();
            }
            continue;
        }
        drop(buffer);
        unsafe { set_errno(error) };
        return ptr::null_mut();
    }
}

#[cfg(target_os = "macos")]
unsafe fn add_closefrom(_actions: *mut libc::posix_spawn_file_actions_t) -> c_int {
    libc::ENOSYS
}

#[cfg(all(target_os = "linux", target_env = "gnu"))]
unsafe fn add_closefrom(actions: *mut libc::posix_spawn_file_actions_t) -> c_int {
    unsafe { libc::posix_spawn_file_actions_addclosefrom_np(actions, 3) }
}

#[cfg(all(target_os = "linux", target_env = "musl"))]
unsafe fn add_closefrom(_actions: *mut libc::posix_spawn_file_actions_t) -> c_int {
    libc::ENOSYS
}

unsafe fn spawn_impl(args: *mut *mut PyObject, nargs: Py_ssize_t) -> *mut PyObject {
    if nargs != 10 {
        unsafe { PyErr_SetString(PyExc_TypeError, c"posix_spawn() takes 10 arguments".as_ptr()) };
        return ptr::null_mut();
    }
    let Some(executable) = (unsafe { bytes_cstring(*args) }) else {
        return ptr::null_mut();
    };
    let argv_object = unsafe { *args.add(1) };
    let argc = unsafe { PyTuple_Size(argv_object) };
    if argc < 0 {
        return ptr::null_mut();
    }
    let mut argv = Vec::with_capacity(argc as usize);
    for index in 0..argc {
        let Some(value) = (unsafe { bytes_cstring(PyTuple_GetItem(argv_object, index)) }) else {
            return ptr::null_mut();
        };
        argv.push(value);
    }
    let mut argv_pointers: Vec<*mut c_char> = argv.iter().map(|value| value.as_ptr().cast_mut()).collect();
    argv_pointers.push(ptr::null_mut());

    let signals_object = unsafe { *args.add(2) };
    let signals_count = unsafe { PyTuple_Size(signals_object) };
    if signals_count < 0 {
        return ptr::null_mut();
    }
    let close_fds = unsafe { PyObject_IsTrue(*args.add(3)) };
    if close_fds < 0 {
        return ptr::null_mut();
    }
    let mut fds = [0; 6];
    for (index, fd) in fds.iter_mut().enumerate() {
        let Some(value) = (unsafe { integer(*args.add(4 + index)) }) else {
            return ptr::null_mut();
        };
        *fd = value;
    }
    let [p2cread, p2cwrite, c2pread, c2pwrite, errread, errwrite] = fds;

    let mut actions = MaybeUninit::<libc::posix_spawn_file_actions_t>::uninit();
    let mut attrs = MaybeUninit::<libc::posix_spawnattr_t>::uninit();
    let mut error = unsafe { libc::posix_spawn_file_actions_init(actions.as_mut_ptr()) };
    if error != 0 {
        return unsafe { PyLong_FromLong(-(error as libc::c_long)) };
    }
    let actions = unsafe { actions.assume_init_mut() };
    error = unsafe { libc::posix_spawnattr_init(attrs.as_mut_ptr()) };
    if error != 0 {
        unsafe { libc::posix_spawn_file_actions_destroy(actions) };
        return unsafe { PyLong_FromLong(-(error as libc::c_long)) };
    }
    let attrs = unsafe { attrs.assume_init_mut() };

    for fd in [p2cwrite, c2pread, errread] {
        if error == 0 && fd != -1 {
            error = unsafe { libc::posix_spawn_file_actions_addclose(actions, fd) };
        }
    }
    for (fd, dest) in [(p2cread, 0), (c2pwrite, 1), (errwrite, 2)] {
        if error == 0 && fd != -1 {
            error = unsafe { libc::posix_spawn_file_actions_adddup2(actions, fd, dest) };
        }
    }
    if error == 0 && close_fds != 0 {
        error = unsafe { add_closefrom(actions) };
    }
    if error == 0 && signals_count > 0 {
        let mut signals = MaybeUninit::<libc::sigset_t>::uninit();
        error = unsafe { libc::sigemptyset(signals.as_mut_ptr()) };
        if error == 0 {
            let mut signals = unsafe { signals.assume_init() };
            for index in 0..signals_count {
                let Some(signum) = (unsafe { integer(PyTuple_GetItem(signals_object, index)) }) else {
                    unsafe { libc::posix_spawnattr_destroy(attrs) };
                    unsafe { libc::posix_spawn_file_actions_destroy(actions) };
                    return ptr::null_mut();
                };
                error = unsafe { libc::sigaddset(&mut signals, signum) };
                if error != 0 {
                    error = last_errno();
                    break;
                }
            }
            if error == 0 {
                error = unsafe { libc::posix_spawnattr_setsigdefault(attrs, &signals) };
            }
            if error == 0 {
                error = unsafe {
                    libc::posix_spawnattr_setflags(attrs, libc::POSIX_SPAWN_SETSIGDEF as libc::c_short)
                };
            }
        } else {
            error = last_errno();
        }
    }

    let mut pid: libc::pid_t = 0;
    if error == 0 {
        let thread = unsafe { PyEval_SaveThread() };
        error = unsafe {
            libc::posix_spawn(
                &mut pid, executable.as_ptr(), actions, attrs,
                argv_pointers.as_mut_ptr(), environ,
            )
        };
        unsafe { PyEval_RestoreThread(thread) };
    }
    unsafe { libc::posix_spawnattr_destroy(attrs) };
    unsafe { libc::posix_spawn_file_actions_destroy(actions) };
    unsafe { PyLong_FromLong(if error == 0 { pid as _ } else { -(error as libc::c_long) }) }
}

unsafe extern "C" fn read(
    _module: *mut PyObject, args: *mut *mut PyObject, nargs: Py_ssize_t,
) -> *mut PyObject {
    unsafe { read_impl(args, nargs) }
}

unsafe extern "C" fn write(
    _module: *mut PyObject, args: *mut *mut PyObject, nargs: Py_ssize_t,
) -> *mut PyObject {
    unsafe { write_impl(args, nargs) }
}

unsafe extern "C" fn posix_spawn(
    _module: *mut PyObject, args: *mut *mut PyObject, nargs: Py_ssize_t,
) -> *mut PyObject {
    unsafe { spawn_impl(args, nargs) }
}

static METHODS: [PyMethodDef; 4] = [
    PyMethodDef {
        ml_name: c"read".as_ptr() as *mut c_char,
        ml_meth: PyMethodDefFuncPointer { PyCFunctionFast: read },
        ml_flags: METH_FASTCALL,
        ml_doc: c"Read from a ready child pipe.".as_ptr() as *mut c_char,
    },
    PyMethodDef {
        ml_name: c"write".as_ptr() as *mut c_char,
        ml_meth: PyMethodDefFuncPointer { PyCFunctionFast: write },
        ml_flags: METH_FASTCALL,
        ml_doc: c"Write to a ready child pipe.".as_ptr() as *mut c_char,
    },
    PyMethodDef {
        ml_name: c"posix_spawn".as_ptr() as *mut c_char,
        ml_meth: PyMethodDefFuncPointer { PyCFunctionFast: posix_spawn },
        ml_flags: METH_FASTCALL,
        ml_doc: c"Launch a child with POSIX file actions and signal defaults.".as_ptr() as *mut c_char,
    },
    PyMethodDef::zeroed(),
];

struct ModuleSlots([PyModuleDef_Slot; 3]);
unsafe impl Sync for ModuleSlots {}

unsafe extern "C" fn module_exec(_module: *mut PyObject) -> c_int {
    0
}

static MODULE_SLOTS: ModuleSlots = ModuleSlots([
    PyModuleDef_Slot { slot: 85, value: module_exec as *const () as *mut c_void },
    PyModuleDef_Slot { slot: 86, value: 2 as *mut c_void },
    PyModuleDef_Slot { slot: 0, value: ptr::null_mut() },
]);

struct ModuleDef(UnsafeCell<PyModuleDef>);
unsafe impl Sync for ModuleDef {}

static MODULE: ModuleDef = ModuleDef(UnsafeCell::new(PyModuleDef {
    m_base: PyModuleDef_HEAD_INIT,
    m_name: c"_subprocess_rs".as_ptr() as *mut c_char,
    m_doc: c"Rust POSIX child launch and pipe transfer.".as_ptr() as *mut c_char,
    m_size: 0,
    m_methods: METHODS.as_ptr() as *mut PyMethodDef,
    m_slots: MODULE_SLOTS.0.as_ptr() as *mut PyModuleDef_Slot,
    m_traverse: None,
    m_clear: None,
    m_free: None,
}));

#[unsafe(no_mangle)]
pub unsafe extern "C" fn PyInit__subprocess_rs() -> *mut PyObject {
    unsafe { PyModuleDef_Init(MODULE.0.get()) }
}
