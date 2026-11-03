#define Py_BUILD_CORE
#include "Python.h"
#include "pycore_interp.h"
#include "pycore_optimizer.h"
#include "pycore_freelist_state.h"
#include "pycore_uop_ids.h"
#include <stdint.h>
#include <string.h>

#ifdef Py_GIL_DISABLED
#error This fixture requires the GIL build
#endif

enum { MAX_ROOTS = 16, MAX_TRACKED = Py_floats_MAXFREELIST + MAX_ROOTS };

typedef struct {
    PyMemAllocatorEx original;
    PyObject *observer;
    PyObject *observer_error;
    void *cached[MAX_TRACKED];
    uint64_t watched_bits;
    unsigned long thread;
    Py_ssize_t requests;
    Py_ssize_t failures;
    Py_ssize_t drains;
    Py_ssize_t watched_frees;
    Py_ssize_t callbacks;
    int cache_count;
    int fail;
} ProbeSession;

static ProbeSession *active;
static int draining;
static Py_ssize_t last_counts[5];
static uint64_t reference_counts[2];

static struct _Py_freelist *
float_cache(void)
{
    return &PyThreadState_Get()->interp->object_state.freelists.floats;
}

static void
empty_cache(void)
{
    struct _Py_freelist *cache = float_cache();
    void *current = cache->freelist;
    cache->freelist = NULL;
    cache->size = 0;
    while (current != NULL) {
        void *next = *(void **)current;
        PyObject_Free(current);
        current = next;
    }
}

static int
fill_cache(int count)
{
    PyObject *held[Py_floats_MAXFREELIST];
    empty_cache();
    int used = 0;
    while (used < count) {
        held[used] = PyFloat_FromDouble((double)used);
        if (held[used] == NULL) {
            while (used != 0) {
                Py_DECREF(held[--used]);
            }
            return -1;
        }
        used++;
    }
    while (used != 0) {
        Py_DECREF(held[--used]);
    }
    if (float_cache()->size != count) {
        PyErr_SetString(PyExc_AssertionError, "float cache did not fill");
        return -1;
    }
    return 0;
}

static int
fail_request(ProbeSession *session, size_t size)
{
    if (session->thread != PyThread_get_thread_ident() ||
        size != sizeof(PyFloatObject) || session->drains < 2) {
        return 0;
    }
    session->requests++;
    if (session->fail && session->failures == 0) {
        session->failures++;
        return 1;
    }
    return 0;
}

static void *
probe_malloc(void *ctx, size_t size)
{
    ProbeSession *session = ctx;
    if (fail_request(session, size)) {
        return NULL;
    }
    return session->original.malloc(session->original.ctx, size);
}

static void *
probe_calloc(void *ctx, size_t nelem, size_t elsize)
{
    ProbeSession *session = ctx;
    if (elsize != 0 && nelem > SIZE_MAX / elsize) {
        return session->original.calloc(session->original.ctx, nelem, elsize);
    }
    if (fail_request(session, nelem * elsize)) {
        return NULL;
    }
    return session->original.calloc(session->original.ctx, nelem, elsize);
}

static void *
probe_realloc(void *ctx, void *ptr, size_t size)
{
    ProbeSession *session = ctx;
    if (fail_request(session, size)) {
        return NULL;
    }
    return session->original.realloc(session->original.ctx, ptr, size);
}

static void
probe_free(void *ctx, void *ptr)
{
    ProbeSession *session = ctx;
    if (session->thread == PyThread_get_thread_ident() &&
        session->observer != NULL && session->callbacks == 0) {
        for (int i = 0; i < MAX_TRACKED; i++) {
            if (ptr == NULL || ptr != session->cached[i]) {
                continue;
            }
            session->cached[i] = NULL;
            uint64_t value;
            memcpy(&value, &((PyFloatObject *)ptr)->ob_fval, sizeof(value));
            if (value != session->watched_bits) {
                break;
            }
            session->watched_frees++;
            if (session->watched_frees == 2) {
                session->callbacks++;
                PyObject *saved = PyErr_GetRaisedException();
                PyObject *result = PyObject_CallNoArgs(session->observer);
                if (result == NULL) {
                    session->observer_error = PyErr_GetRaisedException();
                }
                Py_XDECREF(result);
                PyErr_SetRaisedException(saved);
            }
            break;
        }
    }
    session->original.free(session->original.ctx, ptr);
}

static void
start_hooks(ProbeSession *session)
{
    PyMem_GetAllocator(PYMEM_DOMAIN_OBJ, &session->original);
    PyMemAllocatorEx hook = {
        session, probe_malloc, probe_calloc, probe_realloc, probe_free
    };
    session->thread = PyThread_get_thread_ident();
    active = session;
    PyMem_SetAllocator(PYMEM_DOMAIN_OBJ, &hook);
}

static void
stop_hooks(ProbeSession *session)
{
    PyMem_SetAllocator(PYMEM_DOMAIN_OBJ, &session->original);
    PyMemAllocatorEx restored;
    PyMem_GetAllocator(PYMEM_DOMAIN_OBJ, &restored);
    if (restored.ctx != session->original.ctx ||
        restored.malloc != session->original.malloc ||
        restored.calloc != session->original.calloc ||
        restored.realloc != session->original.realloc ||
        restored.free != session->original.free) {
        Py_FatalError("float probe allocator restoration failed");
    }
    active = NULL;
    last_counts[0] = session->requests;
    last_counts[1] = session->failures;
    last_counts[2] = session->drains;
    last_counts[3] = session->watched_frees;
    last_counts[4] = session->callbacks;
}

static PyObject *
cache_size(PyObject *self, PyObject *unused)
{
    return PyLong_FromSsize_t(float_cache()->size);
}

static PyObject *
drain(PyObject *self, PyObject *unused)
{
    if (draining) {
        if (active == NULL) {
            PyErr_SetString(PyExc_AssertionError, "drain has no allocator session");
            return NULL;
        }
        if (fill_cache(active->cache_count) < 0) {
            return NULL;
        }
        active->drains++;
    }
    Py_RETURN_NONE;
}

static PyObject *
counts(PyObject *self, PyObject *unused)
{
    return Py_BuildValue("(nnnnn)", last_counts[0], last_counts[1], last_counts[2],
                         last_counts[3], last_counts[4]);
}

static PyObject *
run_oom(PyObject *self, PyObject *args)
{
    PyObject *function;
    PyObject *arguments;
    int fail;
    int cache_count = 0;
    if (!PyArg_ParseTuple(args, "OOp|i:run_oom", &function, &arguments, &fail, &cache_count)) {
        return NULL;
    }
    if (active != NULL || !PyTuple_Check(arguments) || !PyCallable_Check(function) ||
        cache_count < 0 || cache_count > 1) {
        PyErr_SetString(PyExc_ValueError, "invalid or nested float allocator call");
        return NULL;
    }
    ProbeSession session = {.fail = fail, .cache_count = cache_count};
    int gc_enabled = PyGC_Disable();
    empty_cache();
    draining = 1;
    start_hooks(&session);
    PyObject *result = PyObject_CallObject(function, arguments);
    int expected_error = PyErr_ExceptionMatches(PyExc_MemoryError);
    stop_hooks(&session);
    draining = 0;
    if (gc_enabled) {
        PyGC_Enable();
    }
    if (session.drains < 2 || session.requests == 0) {
        Py_XDECREF(result);
        PyErr_SetString(PyExc_AssertionError, "armed float allocation hook was not called");
        return NULL;
    }
    if (fail && (session.failures != 1 || result != NULL || !expected_error)) {
        Py_XDECREF(result);
        PyErr_SetString(PyExc_AssertionError, "float allocation failure did not propagate MemoryError");
        return NULL;
    }
    return result;
}

static PyObject *
run_full(PyObject *self, PyObject *args)
{
    PyObject *function;
    PyObject *arguments;
    PyObject *observer;
    PyObject *watched;
    PyObject *roots;
    if (!PyArg_ParseTuple(args, "OOOOO:run_full", &function, &arguments,
                          &roots, &watched, &observer)) {
        return NULL;
    }
    if (active != NULL || !PyTuple_Check(arguments) || !PyCallable_Check(function) ||
        !PyCallable_Check(observer) || !PyFloat_CheckExact(watched) ||
        !PyList_CheckExact(roots) || PyList_GET_SIZE(roots) > MAX_ROOTS) {
        PyErr_SetString(PyExc_ValueError, "invalid or nested full-cache observer call");
        return NULL;
    }
    if (fill_cache(Py_floats_MAXFREELIST) < 0) {
        return NULL;
    }
    ProbeSession session = {.observer = observer};
    for (Py_ssize_t i = 0; i < PyList_GET_SIZE(roots); i++) {
        PyObject *root = PyList_GET_ITEM(roots, i);
        if (!PyFloat_CheckExact(root)) {
            PyErr_SetString(PyExc_TypeError, "observer roots must be exact floats");
            return NULL;
        }
        if (Py_REFCNT(root) != 1) {
            PyErr_SetString(PyExc_AssertionError, "observer roots must be solely owned by their holder");
            return NULL;
        }
        session.cached[Py_floats_MAXFREELIST + i] = root;
    }
    memcpy(&session.watched_bits, &((PyFloatObject *)watched)->ob_fval,
           sizeof(session.watched_bits));
    void *current = float_cache()->freelist;
    for (int i = 0; i < Py_floats_MAXFREELIST; i++) {
        if (current == NULL) {
            PyErr_SetString(PyExc_AssertionError, "short float cache chain");
            return NULL;
        }
        session.cached[i] = current;
        current = *(void **)current;
    }
    int gc_enabled = PyGC_Disable();
    start_hooks(&session);
    PyObject *result = PyObject_CallObject(function, arguments);
    stop_hooks(&session);
    if (gc_enabled) {
        PyGC_Enable();
    }
    if (session.observer_error != NULL) {
        Py_XDECREF(result);
        PyErr_SetRaisedException(session.observer_error);
        return NULL;
    }
    if (result != NULL && session.callbacks != 1) {
        Py_DECREF(result);
        PyErr_SetString(PyExc_AssertionError, "full-cache float destructor observer did not run");
        return NULL;
    }
    return result;
}

static int
trace_float(PyObject *object, PyRefTracerEvent event, void *unused)
{
    if (object != NULL && PyFloat_CheckExact(object) &&
        (event == PyRefTracer_CREATE || event == PyRefTracer_DESTROY)) {
        reference_counts[event == PyRefTracer_DESTROY]++;
    }
    return 0;
}

static PyObject *
run_traced(PyObject *self, PyObject *args)
{
    PyObject *function;
    PyObject *arguments;
    if (!PyArg_ParseTuple(args, "OO:run_traced", &function, &arguments)) {
        return NULL;
    }
    void *previous_data;
    PyRefTracer previous = PyRefTracer_GetTracer(&previous_data);
    if (previous != NULL || !PyTuple_Check(arguments) || !PyCallable_Check(function)) {
        PyErr_SetString(PyExc_ValueError, "invalid or nested reference trace");
        return NULL;
    }
    reference_counts[0] = reference_counts[1] = 0;
    if (PyRefTracer_SetTracer(trace_float, NULL) < 0) {
        return NULL;
    }
    PyObject *result = PyObject_CallObject(function, arguments);
    PyObject *error = PyErr_GetRaisedException();
    if (PyRefTracer_SetTracer(previous, previous_data) < 0) {
        Py_FatalError("float probe reference tracer restoration failed");
    }
    PyErr_SetRaisedException(error);
    if (result == NULL) {
        return NULL;
    }
    return Py_BuildValue("(NKK)", result,
                         (unsigned long long)reference_counts[0],
                         (unsigned long long)reference_counts[1]);
}

static PyObject *
exit_state(PyObject *self, PyObject *args)
{
    PyObject *object;
    Py_ssize_t index;
    if (!PyArg_ParseTuple(args, "On:exit_state", &object, &index)) {
        return NULL;
    }
    if (!Py_IS_TYPE(object, &_PyUOpExecutor_Type)) {
        PyErr_SetString(PyExc_TypeError, "expected executor");
        return NULL;
    }
    _PyExecutorObject *executor = (_PyExecutorObject *)object;
    if (index < 0 || (size_t)index >= executor->code_size) {
        PyErr_SetString(PyExc_IndexError, "uop index out of range");
        return NULL;
    }
    const _PyUOpInstruction *uop = &executor->trace[index];
    if (uop->format != UOP_FORMAT_JUMP || uop->jump_target >= executor->code_size) {
        PyErr_SetString(PyExc_ValueError, "uop has no exit");
        return NULL;
    }
    uintptr_t start = (uintptr_t)executor->exits;
    for (uint32_t i = uop->jump_target;
         i < executor->code_size && i <= uop->jump_target + 2; i++) {
        const _PyUOpInstruction *destination = &executor->trace[i];
        uintptr_t address = (uintptr_t)destination->operand0;
        if (address >= start && address - start < executor->exit_count * sizeof(_PyExitData) &&
            (address - start) % sizeof(_PyExitData) == 0) {
            _PyExitData *exit = (_PyExitData *)address;
            return Py_BuildValue("(IKk)", (unsigned int)exit->temperature.value_and_backoff,
                                 (unsigned long long)(uintptr_t)exit->executor,
                                 (unsigned long)exit->target);
        }
    }
    PyErr_SetString(PyExc_ValueError, "uop has no executor exit data");
    return NULL;
}

static PyObject *
executor_children(PyObject *self, PyObject *object)
{
    if (!Py_IS_TYPE(object, &_PyUOpExecutor_Type)) {
        PyErr_SetString(PyExc_TypeError, "expected executor");
        return NULL;
    }
    _PyExecutorObject *executor = (_PyExecutorObject *)object;
    PyObject *result = PyList_New(0);
    if (result == NULL) {
        return NULL;
    }
    for (uint32_t i = 0; i < executor->exit_count; i++) {
        _PyExitData *exit = &executor->exits[i];
        _PyExecutorObject *child = exit->executor;
        if (child == NULL || child->code_size == 0) {
            Py_DECREF(result);
            PyErr_SetString(PyExc_AssertionError, "executor has an empty exit destination");
            return NULL;
        }
        if (child->trace[0].opcode != _START_EXECUTOR_r00) {
            continue;
        }
        PyObject *item = Py_BuildValue("(KO)",
            (unsigned long long)exit->target * 2,
            child);
        if (item == NULL) {
            Py_DECREF(result);
            return NULL;
        }
        int error = PyList_Append(result, item);
        Py_DECREF(item);
        if (error < 0) {
            Py_DECREF(result);
            return NULL;
        }
    }
    return result;
}

static PyMethodDef methods[] = {
    {"cache_size", cache_size, METH_NOARGS, NULL},
    {"drain", drain, METH_NOARGS, NULL},
    {"counts", counts, METH_NOARGS, NULL},
    {"run_oom", run_oom, METH_VARARGS, NULL},
    {"run_full", run_full, METH_VARARGS, NULL},
    {"run_traced", run_traced, METH_VARARGS, NULL},
    {"exit_state", exit_state, METH_VARARGS, NULL},
    {"executor_children", executor_children, METH_O, NULL},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT, "_jit_raw_float_probe", NULL, -1, methods
};

PyMODINIT_FUNC
PyInit__jit_raw_float_probe(void)
{
    return PyModule_Create(&module);
}
