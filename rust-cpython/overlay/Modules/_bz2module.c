/* _bz2 - Low-level Python interface to bzip2 compression. */

#ifndef Py_BUILD_CORE_BUILTIN
#  define Py_BUILD_CORE_MODULE 1
#endif

#ifndef PYTHON_RUST_BZ2
#  error "_bz2 must be built with the Rust bzip2 codec"
#endif

#include "Python.h"

#include <stddef.h>               // offsetof()

#include "pycore_pyatomic_ft_wrappers.h" // FT_ATOMIC_STORE_CHAR_RELAXED

typedef struct {
    PyTypeObject *bz2_compressor_type;
    PyTypeObject *bz2_decompressor_type;
} _bz2_state;

static inline _bz2_state *
get_module_state(PyObject *module)
{
    void *state = PyModule_GetState(module);
    assert(state != NULL);
    return (_bz2_state *)state;
}

static struct PyModuleDef _bz2module;

static inline _bz2_state *
find_module_state_by_def(PyTypeObject *type)
{
    PyObject *module = PyType_GetModuleByDef(type, &_bz2module);
    assert(module != NULL);
    return get_module_state(module);
}

/* On success, return value >= 0
   On failure, return -1 */
typedef struct {
    PyObject_HEAD
    PyObject *rust_state;
    int flushed;
    PyMutex mutex;
} BZ2Compressor;

typedef struct {
    PyObject_HEAD
    PyObject *rust_state;
    int bzerror;
    char eof;           /* Py_T_BOOL expects a char */
    PyObject *unused_data;
    char needs_input;
    PyMutex mutex;
} BZ2Decompressor;

#define _BZ2Compressor_CAST(op)     ((BZ2Compressor *)(op))
#define _BZ2Decompressor_CAST(op)   ((BZ2Decompressor *)(op))

/* Private codec calls keep Python's C-level object and exception contract. */
static PyObject *
rust_bz2_call(const char *name, PyObject *args)
{
    PyObject *module = PyImport_ImportModule("_bz2_rs");
    if (module == NULL) {
        return NULL;
    }
    PyObject *function = PyObject_GetAttrString(module, name);
    Py_DECREF(module);
    if (function == NULL) {
        return NULL;
    }
    PyObject *result = PyObject_CallObject(function, args);
    Py_DECREF(function);
    return result;
}

static PyObject *
rust_bz2_call_with_state(const char *name, PyObject *state)
{
    PyObject *args = PyTuple_Pack(1, state);
    if (args == NULL) {
        return NULL;
    }
    PyObject *result = rust_bz2_call(name, args);
    Py_DECREF(args);
    return result;
}

static PyObject *
rust_bz2_call_noargs(const char *name)
{
    PyObject *module = PyImport_ImportModule("_bz2_rs");
    if (module == NULL) {
        return NULL;
    }
    PyObject *function = PyObject_GetAttrString(module, name);
    Py_DECREF(module);
    if (function == NULL) {
        return NULL;
    }
    PyObject *result = PyObject_CallNoArgs(function);
    Py_DECREF(function);
    return result;
}

static PyObject *
compress(BZ2Compressor *compressor, char *data, Py_ssize_t len)
{
    PyObject *input = PyMemoryView_FromMemory(data, len, PyBUF_READ);
    if (input == NULL) {
        return NULL;
    }
    PyObject *args = PyTuple_Pack(2, compressor->rust_state, input);
    Py_DECREF(input);
    if (args == NULL) {
        return NULL;
    }
    PyObject *result = rust_bz2_call("compressor_compress", args);
    Py_DECREF(args);
    return result;
}

static PyObject *
finish_compression(BZ2Compressor *compressor)
{
    return rust_bz2_call_with_state("compressor_finish", compressor->rust_state);
}

/*[clinic input]
module _bz2
class _bz2.BZ2Compressor "BZ2Compressor *" "clinic_state()->bz2_compressor_type"
class _bz2.BZ2Decompressor "BZ2Decompressor *" "clinic_state()->bz2_decompressor_type"
[clinic start generated code]*/
/*[clinic end generated code: output=da39a3ee5e6b4b0d input=92348121632b94c4]*/

#define clinic_state() (find_module_state_by_def(type))
#include "clinic/_bz2module.c.h"
#undef clinic_state

/*[clinic input]
_bz2.BZ2Compressor.compress

    data: Py_buffer
    /

Provide data to the compressor object.

Returns a chunk of compressed data if possible, or b'' otherwise.

When you have finished providing data to the compressor, call the
flush() method to finish the compression process.
[clinic start generated code]*/

static PyObject *
_bz2_BZ2Compressor_compress_impl(BZ2Compressor *self, Py_buffer *data)
/*[clinic end generated code: output=59365426e941fbcc input=85c963218070fc4c]*/
{
    PyObject *result = NULL;

    PyMutex_Lock(&self->mutex);
    if (self->flushed)
        PyErr_SetString(PyExc_ValueError, "Compressor has been flushed");
    else
        result = compress(self, data->buf, data->len);
    PyMutex_Unlock(&self->mutex);
    return result;
}

/*[clinic input]
_bz2.BZ2Compressor.flush

Finish the compression process.

Returns the compressed data left in internal buffers.

The compressor object may not be used after this method is called.
[clinic start generated code]*/

static PyObject *
_bz2_BZ2Compressor_flush_impl(BZ2Compressor *self)
/*[clinic end generated code: output=3ef03fc1b092a701 input=d64405d3c6f76691]*/
{
    PyObject *result = NULL;

    PyMutex_Lock(&self->mutex);
    if (self->flushed)
        PyErr_SetString(PyExc_ValueError, "Repeated call to flush()");
    else {
        self->flushed = 1;
        result = finish_compression(self);
    }
    PyMutex_Unlock(&self->mutex);
    return result;
}

/*[clinic input]
@classmethod
_bz2.BZ2Compressor.__new__

    compresslevel: int = 9
        Compression level, as a number between 1 and 9.
    /

Create a compressor object for compressing data incrementally.

For one-shot compression, use the compress() function instead.
[clinic start generated code]*/

static PyObject *
_bz2_BZ2Compressor_impl(PyTypeObject *type, int compresslevel)
/*[clinic end generated code: output=83346c96beaacad7 input=d4500d2a52c8b263]*/
{
    BZ2Compressor *self;

    if (!(1 <= compresslevel && compresslevel <= 9)) {
        PyErr_SetString(PyExc_ValueError,
                        "compresslevel must be between 1 and 9");
        return NULL;
    }

    assert(type != NULL && type->tp_alloc != NULL);
    self = (BZ2Compressor *)type->tp_alloc(type, 0);
    if (self == NULL) {
        return NULL;
    }

    self->rust_state = NULL;
    self->mutex = (PyMutex){0};
    PyObject *level = PyLong_FromLong(compresslevel);
    if (level == NULL) {
        goto error;
    }
    PyObject *args = PyTuple_Pack(1, level);
    Py_DECREF(level);
    if (args == NULL) {
        goto error;
    }
    self->rust_state = rust_bz2_call("compressor_new", args);
    Py_DECREF(args);
    if (self->rust_state == NULL) {
        goto error;
    }

    return (PyObject *)self;

error:
    Py_DECREF(self);
    return NULL;
}

static void
BZ2Compressor_dealloc(PyObject *op)
{
    BZ2Compressor *self = _BZ2Compressor_CAST(op);
    assert(!PyMutex_IsLocked(&self->mutex));
    Py_XDECREF(self->rust_state);
    PyTypeObject *tp = Py_TYPE(self);
    tp->tp_free((PyObject *)self);
    Py_DECREF(tp);
}

static PyMethodDef BZ2Compressor_methods[] = {
    _BZ2_BZ2COMPRESSOR_COMPRESS_METHODDEF
    _BZ2_BZ2COMPRESSOR_FLUSH_METHODDEF
    {NULL}
};

static PyType_Slot bz2_compressor_type_slots[] = {
    {Py_tp_dealloc, BZ2Compressor_dealloc},
    {Py_tp_methods, BZ2Compressor_methods},
    {Py_tp_new, _bz2_BZ2Compressor},
    {Py_tp_doc, (char *)_bz2_BZ2Compressor__doc__},
    {0, 0}
};

static PyType_Spec bz2_compressor_type_spec = {
    .name = "_bz2.BZ2Compressor",
    .basicsize = sizeof(BZ2Compressor),
    // Calling PyType_GetModuleState() on a subclass is not safe.
    // bz2_compressor_type_spec does not have Py_TPFLAGS_BASETYPE flag
    // which prevents to create a subclass.
    // So calling PyType_GetModuleState() in this file is always safe.
    .flags = (Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE),
    .slots = bz2_compressor_type_slots,
};

/* BZ2Decompressor class. */

static int
update_decompressor_state(BZ2Decompressor *decompressor, PyObject *state)
{
    if (!PyTuple_Check(state) || PyTuple_GET_SIZE(state) != 3) {
        PyErr_SetString(PyExc_SystemError,
                        "Rust bzip2 decoder returned invalid state");
        return -1;
    }
    int eof = PyObject_IsTrue(PyTuple_GET_ITEM(state, 0));
    if (eof < 0) {
        return -1;
    }
    int needs_input = PyObject_IsTrue(PyTuple_GET_ITEM(state, 1));
    if (needs_input < 0) {
        return -1;
    }
    PyObject *unused_data = PyTuple_GET_ITEM(state, 2);
    Py_INCREF(unused_data);
    Py_XSETREF(decompressor->unused_data, unused_data);
    FT_ATOMIC_STORE_CHAR_RELAXED(decompressor->eof, eof);
    FT_ATOMIC_STORE_CHAR_RELAXED(decompressor->needs_input, needs_input);
    return 0;
}

static PyObject *
decompress(BZ2Decompressor *decompressor, char *data, Py_ssize_t len,
           Py_ssize_t max_length)
{
    PyObject *input = PyMemoryView_FromMemory(data, len, PyBUF_READ);
    if (input == NULL) {
        return NULL;
    }
    PyObject *limit = PyLong_FromSsize_t(max_length);
    if (limit == NULL) {
        Py_DECREF(input);
        return NULL;
    }
    PyObject *args = PyTuple_Pack(3, decompressor->rust_state, input, limit);
    Py_DECREF(input);
    Py_DECREF(limit);
    if (args == NULL) {
        return NULL;
    }
    PyObject *result = rust_bz2_call("decompressor_decompress", args);
    Py_DECREF(args);
    if (result == NULL) {
        if (PyErr_ExceptionMatches(PyExc_OSError)
                || PyErr_ExceptionMatches(PyExc_ValueError)
                || PyErr_ExceptionMatches(PyExc_RuntimeError)) {
            decompressor->bzerror = 1;
            FT_ATOMIC_STORE_CHAR_RELAXED(decompressor->needs_input, 0);
        }
        return NULL;
    }

    PyObject *state = rust_bz2_call_with_state(
        "decompressor_state", decompressor->rust_state);
    if (state == NULL) {
        Py_DECREF(result);
        return NULL;
    }
    int status = update_decompressor_state(decompressor, state);
    Py_DECREF(state);
    if (status < 0) {
        Py_DECREF(result);
        return NULL;
    }
    return result;
}

/*[clinic input]
_bz2.BZ2Decompressor.decompress

    data: Py_buffer
    max_length: Py_ssize_t=-1

Decompress *data*, returning uncompressed data as bytes.

If *max_length* is nonnegative, returns at most *max_length* bytes
of decompressed data.  If this limit is reached and further output
can be produced, *self.needs_input* will be set to ``False``.  In
this case, the next call to *decompress()* may provide *data* as b''
to obtain more of the output.

If all of the input data was decompressed and returned (either
because this was less than *max_length* bytes, or because
*max_length* was negative), *self.needs_input* will be set to True.

Attempting to decompress data after the end of stream is reached
raises an EOFError.  Any data found after the end of the stream is
ignored and saved in the unused_data attribute.
[clinic start generated code]*/

static PyObject *
_bz2_BZ2Decompressor_decompress_impl(BZ2Decompressor *self, Py_buffer *data,
                                     Py_ssize_t max_length)
/*[clinic end generated code: output=23e41045deb240a3 input=7f68faa9ff7a1b51]*/
{
    PyObject *result = NULL;

    PyMutex_Lock(&self->mutex);
    if (self->eof) {
        PyErr_SetString(PyExc_EOFError, "End of stream already reached");
    }
    else if (self->bzerror) {
        // The codec state cannot be reused after a data or sequence error.
        PyErr_SetString(PyExc_ValueError,
                        "Decompressor is unusable after a previous error");
    }
    else {
        result = decompress(self, data->buf, data->len, max_length);
    }
    PyMutex_Unlock(&self->mutex);
    return result;
}

/*[clinic input]
@classmethod
_bz2.BZ2Decompressor.__new__

Create a decompressor object for decompressing data incrementally.

For one-shot decompression, use the decompress() function instead.
[clinic start generated code]*/

static PyObject *
_bz2_BZ2Decompressor_impl(PyTypeObject *type)
/*[clinic end generated code: output=5150d51ccaab220e input=b87413ce51853528]*/
{
    BZ2Decompressor *self;

    assert(type != NULL && type->tp_alloc != NULL);
    self = (BZ2Decompressor *)type->tp_alloc(type, 0);
    if (self == NULL) {
        return NULL;
    }

    self->mutex = (PyMutex){0};
    self->rust_state = NULL;
    self->bzerror = 0;
    self->needs_input = 1;
    self->unused_data = Py_GetConstant(Py_CONSTANT_EMPTY_BYTES);
    if (self->unused_data == NULL) {
        goto error;
    }

    self->rust_state = rust_bz2_call_noargs("decompressor_new");
    if (self->rust_state == NULL) {
        goto error;
    }

    return (PyObject *)self;

error:
    Py_DECREF(self);
    return NULL;
}

static void
BZ2Decompressor_dealloc(PyObject *op)
{
    BZ2Decompressor *self = _BZ2Decompressor_CAST(op);
    assert(!PyMutex_IsLocked(&self->mutex));

    Py_XDECREF(self->rust_state);
    Py_CLEAR(self->unused_data);

    PyTypeObject *tp = Py_TYPE(self);
    tp->tp_free((PyObject *)self);
    Py_DECREF(tp);
}

static PyMethodDef BZ2Decompressor_methods[] = {
    _BZ2_BZ2DECOMPRESSOR_DECOMPRESS_METHODDEF
    {NULL}
};

PyDoc_STRVAR(BZ2Decompressor_eof__doc__,
"True if the end-of-stream marker has been reached.");

PyDoc_STRVAR(BZ2Decompressor_unused_data__doc__,
"Data found after the end of the compressed stream.");

PyDoc_STRVAR(BZ2Decompressor_needs_input_doc,
"True if more input is needed before more decompressed data can be produced.");

static PyObject *
BZ2Decompressor_unused_data_get(PyObject *op, void *Py_UNUSED(ignored))
{
    BZ2Decompressor *self = _BZ2Decompressor_CAST(op);
    if (!FT_ATOMIC_LOAD_CHAR_RELAXED(self->eof)) {
        return Py_GetConstant(Py_CONSTANT_EMPTY_BYTES);
    }
    PyMutex_Lock(&self->mutex);
    assert(self->unused_data != NULL);
    PyObject *result = Py_NewRef(self->unused_data);
    PyMutex_Unlock(&self->mutex);
    return result;
}

static PyGetSetDef BZ2Decompressor_getset[] = {
    {"unused_data", BZ2Decompressor_unused_data_get, NULL,
     BZ2Decompressor_unused_data__doc__},
    {NULL},
};

static PyMemberDef BZ2Decompressor_members[] = {
    {"eof", Py_T_BOOL, offsetof(BZ2Decompressor, eof),
     Py_READONLY, BZ2Decompressor_eof__doc__},
    {"needs_input", Py_T_BOOL, offsetof(BZ2Decompressor, needs_input), Py_READONLY,
     BZ2Decompressor_needs_input_doc},
    {NULL}
};

static PyType_Slot bz2_decompressor_type_slots[] = {
    {Py_tp_dealloc, BZ2Decompressor_dealloc},
    {Py_tp_methods, BZ2Decompressor_methods},
    {Py_tp_doc, (char *)_bz2_BZ2Decompressor__doc__},
    {Py_tp_members, BZ2Decompressor_members},
    {Py_tp_getset, BZ2Decompressor_getset},
    {Py_tp_new, _bz2_BZ2Decompressor},
    {0, 0}
};

static PyType_Spec bz2_decompressor_type_spec = {
    .name = "_bz2.BZ2Decompressor",
    .basicsize = sizeof(BZ2Decompressor),
    // Calling PyType_GetModuleState() on a subclass is not safe.
    // bz2_decompressor_type_spec does not have Py_TPFLAGS_BASETYPE flag
    // which prevents to create a subclass.
    // So calling PyType_GetModuleState() in this file is always safe.
    .flags = (Py_TPFLAGS_DEFAULT | Py_TPFLAGS_IMMUTABLETYPE),
    .slots = bz2_decompressor_type_slots,
};


PyDoc_STRVAR(bzlib_version_info__doc__,
"_bz2.bzlib_version_info\n\
\n\
Bzlib version information as a named tuple.");

static PyStructSequence_Field bzlib_version_info_fields[] = {
    {"major", "Major release number"},
    {"minor", "Minor release number"},
    {"patch", "Patch release number"},
    {0}
};

static PyStructSequence_Desc bzlib_version_info_desc = {
    "_bz2.bzlib_version_info",        /* name */
    bzlib_version_info__doc__,        /* doc */
    bzlib_version_info_fields,        /* fields */
    3
};

/* BZ2_bzlibVersion() returns a string with a trailing suffix, for example
   "1.0.8, 13-Jul-2019" for bzip2 or "1.1.0-libbz2-rs-sys-0.2.5" for
   libbz2-rs.  sscanf() stops at the suffix; components which were not
   parsed are left zero.  This is deliberate -- a zero is more useful
   here than a hard error. */
static PyObject *
make_bzlib_version_info(PyTypeObject *type, const char *string)
{
    PyObject *version;
    int pos = 0;
    unsigned int major = 0, minor = 0, patch = 0;

    sscanf(string, "%u.%u.%u", &major, &minor, &patch);

    version = PyStructSequence_New(type);
    if (version == NULL) {
        return NULL;
    }

#define SetIntItem(VALUE) \
    PyStructSequence_SET_ITEM(version, pos++, PyLong_FromUnsignedLong(VALUE)); \
    if (PyErr_Occurred()) { \
        Py_DECREF(version); \
        return NULL; \
    }

    SetIntItem(major)
    SetIntItem(minor)
    SetIntItem(patch)
#undef SetIntItem

    return version;
}


/* Module initialization. */

static int
_bz2_exec(PyObject *module)
{
    _bz2_state *state = get_module_state(module);
    state->bz2_compressor_type = (PyTypeObject *)PyType_FromModuleAndSpec(module,
                                                            &bz2_compressor_type_spec, NULL);
    if (state->bz2_compressor_type == NULL) {
        return -1;
    }
    if (PyModule_AddType(module, state->bz2_compressor_type) < 0) {
        return -1;
    }

    state->bz2_decompressor_type = (PyTypeObject *)PyType_FromModuleAndSpec(module,
                                                         &bz2_decompressor_type_spec, NULL);
    if (state->bz2_decompressor_type == NULL) {
        return -1;
    }
    if (PyModule_AddType(module, state->bz2_decompressor_type) < 0) {
        return -1;
    }

    /* bzlib_version */
    PyObject *version = rust_bz2_call("bzlib_version", NULL);
    if (version == NULL) {
        return -1;
    }
    const char *version_string = PyUnicode_AsUTF8(version);
    if (version_string == NULL) {
        Py_DECREF(version);
        return -1;
    }
    PyTypeObject *version_type;
    version_type = PyStructSequence_NewType(&bzlib_version_info_desc);
    if (version_type == NULL) {
        Py_DECREF(version);
        return -1;
    }
    PyObject *version_info = make_bzlib_version_info(version_type, version_string);
    Py_DECREF(version_type);
    if (version_info == NULL) {
        Py_DECREF(version);
        return -1;
    }
    if (PyModule_Add(module, "bzlib_version", version) < 0) {
        Py_DECREF(version_info);
        return -1;
    }
    if (PyModule_Add(module, "bzlib_version_info", version_info) < 0) {
        return -1;
    }
    return 0;
}

static int
_bz2_traverse(PyObject *module, visitproc visit, void *arg)
{
    _bz2_state *state = get_module_state(module);
    Py_VISIT(state->bz2_compressor_type);
    Py_VISIT(state->bz2_decompressor_type);
    return 0;
}

static int
_bz2_clear(PyObject *module)
{
    _bz2_state *state = get_module_state(module);
    Py_CLEAR(state->bz2_compressor_type);
    Py_CLEAR(state->bz2_decompressor_type);
    return 0;
}

static void
_bz2_free(void *module)
{
    (void)_bz2_clear((PyObject *)module);
}

static struct PyModuleDef_Slot _bz2_slots[] = {
    _Py_ABI_SLOT,
    {Py_mod_exec, _bz2_exec},
    {Py_mod_multiple_interpreters, Py_MOD_PER_INTERPRETER_GIL_SUPPORTED},
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
    {0, NULL}
};

static struct PyModuleDef _bz2module = {
    .m_base = PyModuleDef_HEAD_INIT,
    .m_name = "_bz2",
    .m_size = sizeof(_bz2_state),
    .m_traverse = _bz2_traverse,
    .m_clear = _bz2_clear,
    .m_free = _bz2_free,
    .m_slots = _bz2_slots,
};

PyMODINIT_FUNC
PyInit__bz2(void)
{
    return PyModuleDef_Init(&_bz2module);
}
