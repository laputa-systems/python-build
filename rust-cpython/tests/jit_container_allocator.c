#include "Python.h"
#include <stdint.h>
#include <string.h>

enum { DRAINED_CONTAINER_COUNT = 128 };

typedef struct HookSession HookSession;

typedef struct {
    PyMemAllocatorEx original;
    PyMemAllocatorDomain domain;
    HookSession *session;
} DomainHook;

struct HookSession {
    DomainHook domains[2];
    PyMemAllocatorDomain selected_domain;
    size_t selected_size;
    uint64_t requests;
    uint64_t failures;
    uint64_t fail_at;
    void *watched_free;
    uint64_t watched_frees;
    unsigned long thread;
};

static int hooks_active;
static uint64_t last_counts[2];
static PyObject *watched_object;
static PyTypeObject *watched_type;
static destructor original_deallocator;
static uint64_t watched_destructions;
static uint64_t replacement_calls;
static int replace_during_destroy;

static int
fail_request(DomainHook *hook, size_t size)
{
    HookSession *session = hook->session;
    if (PyThread_get_thread_ident() != session->thread ||
        hook->domain != session->selected_domain ||
        size != session->selected_size) {
        return 0;
    }
    session->requests++;
    if (session->fail_at != 0 && session->requests == session->fail_at) {
        session->failures++;
        return 1;
    }
    return 0;
}

static void *
hook_malloc(void *ctx, size_t size)
{
    DomainHook *hook = ctx;
    if (fail_request(hook, size)) {
        return NULL;
    }
    return hook->original.malloc(hook->original.ctx, size);
}

static void *
hook_calloc(void *ctx, size_t count, size_t size)
{
    DomainHook *hook = ctx;
    if ((size == 0 || count <= SIZE_MAX / size) &&
        fail_request(hook, count * size)) {
        return NULL;
    }
    return hook->original.calloc(hook->original.ctx, count, size);
}

static void *
hook_realloc(void *ctx, void *ptr, size_t size)
{
    DomainHook *hook = ctx;
    if (fail_request(hook, size)) {
        return NULL;
    }
    return hook->original.realloc(hook->original.ctx, ptr, size);
}

static void
hook_free(void *ctx, void *ptr)
{
    DomainHook *hook = ctx;
    if (ptr != NULL && ptr == hook->session->watched_free &&
        hook->domain == PYMEM_DOMAIN_OBJ) {
        hook->session->watched_frees++;
    }
    hook->original.free(hook->original.ctx, ptr);
}

static void
start_hooks(HookSession *session)
{
    session->thread = PyThread_get_thread_ident();
    hooks_active = 1;
    for (int i = 0; i < 2; i++) {
        DomainHook *hook = &session->domains[i];
        hook->domain = i == 0 ? PYMEM_DOMAIN_MEM : PYMEM_DOMAIN_OBJ;
        hook->session = session;
        PyMem_GetAllocator(hook->domain, &hook->original);
        PyMemAllocatorEx wrapper = {
            .ctx = hook,
            .malloc = hook_malloc,
            .calloc = hook_calloc,
            .realloc = hook_realloc,
            .free = hook_free,
        };
        PyMem_SetAllocator(hook->domain, &wrapper);
    }
}

static void
stop_hooks(HookSession *session)
{
    for (int i = 0; i < 2; i++) {
        DomainHook *hook = &session->domains[i];
        PyMem_SetAllocator(hook->domain, &hook->original);
        PyMemAllocatorEx restored;
        PyMem_GetAllocator(hook->domain, &restored);
        if (restored.ctx != hook->original.ctx ||
            restored.malloc != hook->original.malloc ||
            restored.calloc != hook->original.calloc ||
            restored.realloc != hook->original.realloc ||
            restored.free != hook->original.free) {
            Py_FatalError("container fixture failed to restore allocator ownership");
        }
    }
    last_counts[0] = session->requests;
    last_counts[1] = session->failures;
    hooks_active = 0;
}

static PyObject *
new_container(int kind)
{
    return kind == 0 ? PyList_New(0) : PySlice_New(Py_None, Py_None, Py_None);
}

static void
release_containers(PyObject **objects, int count)
{
    for (int i = 0; i < count; i++) {
        Py_XDECREF(objects[i]);
    }
}

PyAPI_FUNC(PyObject *)
call_with_container_allocator(PyObject *callable, PyObject *args,
                              int domain, Py_ssize_t request_size,
                              int fail_at, int drain_mask)
{
    if (hooks_active || !PyCallable_Check(callable) || !PyTuple_Check(args) ||
        (domain != PYMEM_DOMAIN_MEM && domain != PYMEM_DOMAIN_OBJ) ||
        request_size <= 0 || fail_at < 0 || drain_mask < 0 || drain_mask > 3) {
        PyErr_SetString(PyExc_ValueError, "invalid container allocator fixture call");
        return NULL;
    }
    PyObject *drained[2 * DRAINED_CONTAINER_COUNT] = {0};
    int count = 0;
    for (int kind = 0; kind < 2; kind++) {
        if (!(drain_mask & (1 << kind))) {
            continue;
        }
        for (int i = 0; i < DRAINED_CONTAINER_COUNT; i++) {
            drained[count] = new_container(kind);
            if (drained[count] == NULL) {
                release_containers(drained, count);
                return NULL;
            }
            count++;
        }
    }
    int gc_enabled = PyGC_Disable();
    HookSession session = {
        .selected_domain = domain,
        .selected_size = (size_t)request_size,
        .fail_at = (uint64_t)fail_at,
    };
    start_hooks(&session);
    PyObject *result = PyObject_CallObject(callable, args);
    int memory_error = result == NULL && PyErr_ExceptionMatches(PyExc_MemoryError);
    stop_hooks(&session);
    release_containers(drained, count);
    if (gc_enabled) {
        PyGC_Enable();
    }
    if (result == NULL) {
        if (fail_at != 0 && session.failures == 0 && memory_error) {
            PyErr_SetString(PyExc_AssertionError, "MemoryError occurred outside the armed hook");
        }
        return NULL;
    }
    if (session.requests == 0 || (fail_at != 0 && session.failures == 0)) {
        Py_DECREF(result);
        PyErr_SetString(PyExc_AssertionError, "armed container allocator hook was not called");
        return NULL;
    }
    if (fail_at != 0) {
        Py_DECREF(result);
        PyErr_SetString(PyExc_AssertionError, "container operation swallowed allocator failure");
        return NULL;
    }
    return result;
}

PyAPI_FUNC(void)
container_allocator_counts(uint64_t *counts)
{
    memcpy(counts, last_counts, sizeof(last_counts));
}

static void
replacement_deallocator(PyObject *obj)
{
    if (obj == watched_object) {
        replacement_calls++;
    }
    original_deallocator(obj);
}

static int
watch_container_destroy(PyObject *obj, PyRefTracerEvent event, void *Py_UNUSED(data))
{
    if (obj == watched_object && event == PyRefTracer_DESTROY) {
        watched_destructions++;
        if (replace_during_destroy) {
            watched_type->tp_dealloc = replacement_deallocator;
        }
    }
    return 0;
}

PyAPI_FUNC(PyObject *)
check_container_deallocator(PyObject *callable, int kind,
                            Py_ssize_t preheader_size, int replace_during_callback)
{
    if (hooks_active || !PyCallable_Check(callable) || kind < 0 || kind > 1 ||
        preheader_size != 2 * (Py_ssize_t)sizeof(uintptr_t) ||
        PyRefTracer_GetTracer(NULL) != NULL) {
        PyErr_SetString(PyExc_ValueError, "invalid container deallocator fixture call");
        return NULL;
    }
    PyObject *holder = PyList_New(DRAINED_CONTAINER_COUNT);
    if (holder == NULL) {
        return NULL;
    }
    for (int i = 0; i < DRAINED_CONTAINER_COUNT; i++) {
        PyObject *obj = new_container(kind);
        if (obj == NULL) {
            Py_DECREF(holder);
            return NULL;
        }
        PyList_SET_ITEM(holder, i, obj);
    }
    PyObject *args = PyTuple_Pack(1, holder);
    Py_DECREF(holder);
    if (args == NULL) {
        return NULL;
    }
    PyObject *fill[DRAINED_CONTAINER_COUNT] = {0};
    for (int i = 0; i < DRAINED_CONTAINER_COUNT; i++) {
        fill[i] = new_container(kind);
        if (fill[i] == NULL) {
            release_containers(fill, i);
            Py_DECREF(args);
            return NULL;
        }
    }
    release_containers(fill, DRAINED_CONTAINER_COUNT);
    watched_object = PyList_GET_ITEM(PyTuple_GET_ITEM(args, 0), 0);
    watched_type = Py_TYPE(watched_object);
    original_deallocator = watched_type->tp_dealloc;
    watched_destructions = 0;
    replacement_calls = 0;
    replace_during_destroy = replace_during_callback;
    int gc_enabled = PyGC_Disable();
    HookSession session = {
        .selected_domain = PYMEM_DOMAIN_OBJ,
        .selected_size = SIZE_MAX,
        .watched_free = (char *)watched_object - preheader_size,
    };
    start_hooks(&session);
    if (!replace_during_callback) {
        watched_type->tp_dealloc = replacement_deallocator;
    }
    if (PyRefTracer_SetTracer(watch_container_destroy, NULL) < 0) {
        watched_type->tp_dealloc = original_deallocator;
        stop_hooks(&session);
        Py_DECREF(args);
        if (gc_enabled) {
            PyGC_Enable();
        }
        return NULL;
    }
    PyObject *result = PyObject_CallObject(callable, args);
    watched_type->tp_dealloc = original_deallocator;
    if (PyRefTracer_SetTracer(NULL, NULL) < 0) {
        Py_XDECREF(result);
        result = NULL;
    }
    stop_hooks(&session);
    Py_DECREF(args);
    if (gc_enabled) {
        PyGC_Enable();
    }
    watched_object = NULL;
    watched_type = NULL;
    original_deallocator = NULL;
    if (result == NULL) {
        return NULL;
    }
    Py_DECREF(result);
    uint64_t expected_replacement_calls = replace_during_callback ? 0 : 1;
    if (watched_destructions != 1 || replacement_calls != expected_replacement_calls ||
        session.watched_frees != 1) {
        PyErr_Format(PyExc_AssertionError,
                     "container deallocation changed: destroy=%llu replacement=%llu free=%llu",
                     (unsigned long long)watched_destructions,
                     (unsigned long long)replacement_calls,
                     (unsigned long long)session.watched_frees);
        return NULL;
    }
    Py_RETURN_NONE;
}
