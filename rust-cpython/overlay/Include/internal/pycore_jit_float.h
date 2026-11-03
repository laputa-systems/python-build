#ifndef Py_INTERNAL_JIT_FLOAT_CAPABILITY_H
#define Py_INTERNAL_JIT_FLOAT_CAPABILITY_H

#ifndef Py_BUILD_CORE
#  error "this header requires Py_BUILD_CORE define"
#endif

#if defined(__has_feature)
#  if __has_feature(address_sanitizer) || __has_feature(thread_sanitizer) || __has_feature(memory_sanitizer) || __has_feature(undefined_behavior_sanitizer)
#    define _Py_JIT_FLOAT_SANITIZED 1
#  endif
#endif

#if defined(__APPLE__) && defined(__aarch64__) && defined(_Py_JIT) && \
    SIZEOF_VOID_P == 8 && !defined(Py_GIL_DISABLED) && !defined(Py_DEBUG) && \
    !defined(Py_REF_DEBUG) && !defined(Py_TRACE_REFS) && \
    !defined(Py_STACKREF_DEBUG) && !defined(Py_STATS) && \
    !defined(_Py_ADDRESS_SANITIZER) && !defined(_Py_THREAD_SANITIZER) && \
    !defined(_Py_MEMORY_SANITIZER) && \
    !defined(_Py_JIT_FLOAT_SANITIZED) && !defined(__SANITIZE_ADDRESS__) && \
    !defined(__SANITIZE_THREAD__) && !defined(__SANITIZE_UNDEFINED__)
#  define _Py_JIT_FLOAT_RAW 1
#else
#  define _Py_JIT_FLOAT_RAW 0
#endif

typedef struct _PyJitFloatContext _PyJitFloatContext;

#endif

#if !defined(_Py_JIT_FLOAT_DECLARE_ONLY) && !defined(Py_INTERNAL_JIT_FLOAT_H)
#define Py_INTERNAL_JIT_FLOAT_H

#include "pycore_stackref.h"
#include "pycore_freelist_state.h"
#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#if _Py_JIT_FLOAT_RAW

#define _Py_JIT_FLOAT_CAPACITY 16

typedef enum {
    _Py_JIT_FLOAT_ALLOCATE = 0,
    _Py_JIT_FLOAT_INPLACE_LEFT = 1,
    _Py_JIT_FLOAT_INPLACE_RIGHT = 2,
} _PyJitFloatPrepareMode;

typedef enum {
    _Py_JIT_FLOAT_FREE,
    _Py_JIT_FLOAT_LIVE_NEW,
    _Py_JIT_FLOAT_LIVE_ADOPTED,
} _PyJitFloatCellState;

typedef struct {
    PyFloatObject *backing;
    double value;
    Py_ssize_t owners;
    Py_ssize_t borrowers;
    _PyJitFloatCellState state;
} _PyJitFloatCell;

struct _PyJitFloatContext {
    bool active;
    uint8_t capacity;
    uint8_t used_count;
    uint8_t free_count;
    uint8_t free_slots[_Py_JIT_FLOAT_CAPACITY];
    _PyInterpreterFrame *frame;
    struct _Py_freelist *freelist;
    _PyJitFloatCell cells[_Py_JIT_FLOAT_CAPACITY];
};

static inline bool
_PyJitFloat_IsRaw(_PyStackRef ref)
{
    return (ref.bits & Py_TAG_BITS) == Py_TAG_INVALID &&
        ref.bits != Py_TAG_INVALID;
}

static inline bool
_PyJitFloat_IsBorrowed(_PyStackRef ref)
{
    assert(_PyJitFloat_IsRaw(ref));
    return (ref.bits & 4) != 0;
}

static inline unsigned
_PyJitFloat_Slot(_PyStackRef ref)
{
    assert(_PyJitFloat_IsRaw(ref));
    return (unsigned)(ref.bits >> 3) - 1;
}

static inline _PyStackRef
_PyJitFloat_Handle(unsigned slot, bool borrowed)
{
    assert(slot < _Py_JIT_FLOAT_CAPACITY);
    return (_PyStackRef){.bits = ((uintptr_t)(slot + 1) << 3) |
        Py_TAG_INVALID | (borrowed ? 4 : 0)};
}

static inline _PyJitFloatCell *
_PyJitFloat_Cell(_PyJitFloatContext *ctx, _PyStackRef ref)
{
    unsigned slot = _PyJitFloat_Slot(ref);
    assert(ctx != NULL && ctx->active && slot < ctx->used_count);
    assert(ctx->cells[slot].state != _Py_JIT_FLOAT_FREE);
    return &ctx->cells[slot];
}

static inline double
_PyJitFloat_Read(_PyJitFloatContext *ctx, _PyStackRef ref)
{
    if (_PyJitFloat_IsRaw(ref)) {
        return _PyJitFloat_Cell(ctx, ref)->value;
    }
    PyObject *obj = PyStackRef_AsPyObjectBorrow(ref);
    assert(PyFloat_CheckExact(obj));
    return ((PyFloatObject *)obj)->ob_fval;
}

static inline bool
_PyJitFloat_CanAllocate(_PyJitFloatContext *ctx)
{
    return ctx != NULL && ctx->active &&
        (ctx->free_count != 0 ||
         (ctx->used_count < ctx->capacity && ctx->freelist->size > 0));
}

PyAPI_FUNC(_PyJitFloatContext *) _PyJitFloat_GetContext(PyThreadState *tstate);
PyAPI_FUNC(bool) _PyJitFloat_Activate(PyThreadState *tstate,
    _PyInterpreterFrame *frame, _PyJitFloatContext *ctx, unsigned capacity);
PyAPI_FUNC(_PyStackRef) _PyJitFloat_New(_PyJitFloatContext *ctx, double value);
PyAPI_FUNC(bool) _PyJitFloat_CanInplace(_PyJitFloatContext *ctx, _PyStackRef target);
PyAPI_FUNC(_PyStackRef) _PyJitFloat_Inplace(PyThreadState *tstate,
    _PyJitFloatContext *ctx, _PyStackRef target, double value);
PyAPI_FUNC(_PyStackRef) _PyJitFloat_Dup(_PyJitFloatContext *ctx, _PyStackRef ref);
PyAPI_FUNC(_PyStackRef) _PyJitFloat_Borrow(_PyJitFloatContext *ctx, _PyStackRef ref);
PyAPI_FUNC(_PyStackRef) _PyJitFloat_MakeHeapSafe(_PyJitFloatContext *ctx, _PyStackRef ref);
PyAPI_FUNC(void) _PyJitFloat_Materialize(PyThreadState *tstate,
    _PyInterpreterFrame *frame, _PyStackRef *stack_pointer,
    _PyJitFloatContext *ctx, _PyStackRef *const *extra_roots, unsigned extra_count);
PyAPI_FUNC(void) _PyJitFloat_Close(PyThreadState *tstate,
    _PyInterpreterFrame *frame, _PyJitFloatContext *ctx, _PyStackRef *closing);

#endif

#ifdef __cplusplus
}
#endif

#endif
