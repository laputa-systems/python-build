
#include "Python.h"

#ifndef NDEBUG
#undef assert
#define assert(TEST) ((TEST) ? 0 : _Py_jit_assertion_failure(__LINE__))
#endif

#include "pycore_backoff.h"
#include "pycore_call.h"
#include "pycore_cell.h"
#include "pycore_ceval.h"
#include "pycore_code.h"
#include "pycore_descrobject.h"
#include "pycore_dict.h"
#include "pycore_emscripten_signal.h"
#include "pycore_floatobject.h"
#include "pycore_freelist.h"
#include "pycore_frame.h"
#include "pycore_function.h"
#include "pycore_genobject.h"
#include "pycore_import.h"
#include "pycore_interpframe.h"
#include "pycore_interpolation.h"
#include "pycore_intrinsics.h"
#include "pycore_lazyimportobject.h"
#include "pycore_jit.h"
#include "pycore_list.h"
#include "pycore_long.h"
#include "pycore_opcode_metadata.h"
#include "pycore_opcode_utils.h"
#include "pycore_optimizer.h"
#include "pycore_pyatomic_ft_wrappers.h"
#include "pycore_range.h"
#include "pycore_setobject.h"
#include "pycore_sliceobject.h"
#include "pycore_stackref.h"
#include "pycore_template.h"
#include "pycore_tuple.h"
#include "pycore_unicodeobject.h"

#include "ceval_macros.h"

#include "jit.h"

#ifndef NDEBUG
#undef assert
#define assert(TEST) ((TEST) ? 0 : _Py_jit_assertion_failure(__LINE__))
#endif

#undef CURRENT_OPERAND0_64
#define CURRENT_OPERAND0_64() (_operand0_64)

#undef CURRENT_OPERAND1_64
#define CURRENT_OPERAND1_64() (_operand1_64)


#undef CURRENT_OPARG
#undef CURRENT_OPERAND0_16
#undef CURRENT_OPERAND0_32
#undef CURRENT_OPERAND1_16
#undef CURRENT_OPERAND1_32

#if SUPPORTS_SMALL_CONSTS

#define CURRENT_OPARG() (_oparg_16)
#define CURRENT_OPERAND0_32() (_operand0_32)
#define CURRENT_OPERAND0_16() (_operand0_16)
#define CURRENT_OPERAND1_32() (_operand1_32)
#define CURRENT_OPERAND1_16() (_operand1_16)

#else

#define CURRENT_OPARG() (_oparg)
#define CURRENT_OPERAND0_32() (_operand0_64)
#define CURRENT_OPERAND0_16() (_operand0_64)
#define CURRENT_OPERAND1_32() (_operand1_64)
#define CURRENT_OPERAND1_16() (_operand1_64)

#endif


#undef CURRENT_TARGET
#define CURRENT_TARGET() (_target)

#undef TIER2_TO_TIER2
#define TIER2_TO_TIER2(EXECUTOR)                                           \
do {                                                                       \
    OPT_STAT_INC(traces_executed);                                         \
    _PyExecutorObject *_executor = (EXECUTOR);                             \
    jit_func_preserve_none jitted = _executor->jit_code;                   \
    __attribute__((musttail)) return jitted(_executor, frame, stack_pointer, tstate,  \
    _tos_cache0, _tos_cache1, _tos_cache2); \
} while (0)

#undef GOTO_TIER_ONE_SETUP
#define GOTO_TIER_ONE_SETUP \
    tstate->current_executor = NULL;                              \
    _PyFrame_SetStackPointer(frame, stack_pointer);

#undef LOAD_IP
#define LOAD_IP(UNUSED) \
    do {                \
    } while (0)

#undef LLTRACE_RESUME_FRAME
#ifdef Py_DEBUG
#define LLTRACE_RESUME_FRAME() (frame->lltrace = 0)
#else
#define LLTRACE_RESUME_FRAME() do {} while (0)
#endif

#define PATCH_JUMP(ALIAS)                                                 \
do {                                                                      \
    DECLARE_TARGET(ALIAS);                                                \
    __attribute__((musttail)) return ALIAS(current_executor, frame, stack_pointer, tstate,  \
    _tos_cache0, _tos_cache1, _tos_cache2); \
} while (0)

#undef JUMP_TO_JUMP_TARGET
#define JUMP_TO_JUMP_TARGET() PATCH_JUMP(_JIT_JUMP_TARGET)

#undef JUMP_TO_ERROR
#define JUMP_TO_ERROR() PATCH_JUMP(_JIT_ERROR_TARGET)

#define TIER_TWO 2

#ifdef Py_DEBUG
#define ASSERT_WITHIN_STACK_BOUNDS(F, L) _Py_jit_assert_within_stack_bounds(frame, stack_pointer, (L))
#else
#define ASSERT_WITHIN_STACK_BOUNDS(F, L) (void)0
#endif

#if defined(__APPLE__) && defined(__aarch64__) && !defined(Py_GIL_DISABLED) && \
    !defined(Py_DEBUG) && !defined(Py_REF_DEBUG) && \
    !defined(Py_STACKREF_DEBUG) && !defined(Py_TRACE_REFS)
static inline PyObject *
jit_float_from_double(PyThreadState *tstate, double value)
{
    struct _Py_freelists *freelists = _Py_freelists_GetForInterpreter(tstate->interp);
    PyFloatObject *obj = (PyFloatObject *)_PyFreeList_Pop(&freelists->floats);
    if (obj == NULL) {
        return PyFloat_FromDouble(value);
    }
    obj->ob_fval = value;
    return (PyObject *)obj;
}

static inline _PyStackRef
jit_long_from_value(PyThreadState *tstate, stwodigits value)
{
    if (_PY_IS_SMALL_INT(value)) {
        PyObject *obj = (PyObject *)&_PyLong_SMALL_INTS[(sdigit)value + _PY_NSMALLNEGINTS];
        return PyStackRef_FromPyObjectBorrow(obj);
    }
    if (value < -(stwodigits)PyLong_MASK || value > (stwodigits)PyLong_MASK) {
        return PyStackRef_NULL;
    }
    struct _Py_freelists *freelists = _Py_freelists_GetForInterpreter(tstate->interp);
    PyLongObject *obj = (PyLongObject *)_PyFreeList_Pop(&freelists->ints);
    if (obj == NULL) {
        return PyStackRef_NULL;
    }
    _PyLong_SetSignAndDigitCount(obj, value < 0 ? -1 : 1, 1);
    obj->long_value.ob_digit[0] = (digit)(value < 0 ? -value : value);
    return PyStackRef_FromPyObjectStealMortal((PyObject *)obj);
}

static inline _PyStackRef
jit_compact_long_add(PyThreadState *tstate, PyLongObject *left, PyLongObject *right)
{
    stwodigits value = (stwodigits)_PyLong_CompactValue(left) + _PyLong_CompactValue(right);
    _PyStackRef result = jit_long_from_value(tstate, value);
    return PyStackRef_IsNull(result) ? _PyCompactLong_Add(left, right) : result;
}

static inline _PyStackRef
jit_compact_long_subtract(PyThreadState *tstate, PyLongObject *left, PyLongObject *right)
{
    stwodigits value = (stwodigits)_PyLong_CompactValue(left) - _PyLong_CompactValue(right);
    _PyStackRef result = jit_long_from_value(tstate, value);
    return PyStackRef_IsNull(result) ? _PyCompactLong_Subtract(left, right) : result;
}

static inline _PyStackRef
jit_compact_long_multiply(PyThreadState *tstate, PyLongObject *left, PyLongObject *right)
{
    stwodigits value = (stwodigits)_PyLong_CompactValue(left) * _PyLong_CompactValue(right);
    _PyStackRef result = jit_long_from_value(tstate, value);
    return PyStackRef_IsNull(result) ? _PyCompactLong_Multiply(left, right) : result;
}

static inline void
jit_dealloc(PyThreadState *tstate, PyObject *obj)
{
    if (_PyRuntime.ref_tracer.tracer_func == NULL) {
        struct _Py_freelists *freelists = _Py_freelists_GetForInterpreter(tstate->interp);
        if (PyFloat_CheckExact(obj)) {
            _PyFreeList_Free(&freelists->floats, obj, Py_floats_MAXFREELIST, PyObject_Free);
            return;
        }
        if (PyLong_CheckExact(obj) && _PyLong_IsCompact((PyLongObject *)obj) &&
            !_PyLong_IsSmallInt((PyLongObject *)obj)) {
            _PyFreeList_Free(&freelists->ints, obj, Py_ints_MAXFREELIST, PyObject_Free);
            return;
        }
    }
    _Py_Dealloc(obj);
}

static inline void
jit_stackref_close(PyThreadState *tstate, _PyStackRef ref)
{
    if (PyStackRef_RefcountOnObject(ref)) {
        PyObject *obj = PyStackRef_AsPyObjectBorrow(ref);
        _Py_DECREF_STAT_INC();
        if (--obj->ob_refcnt == 0) {
            jit_dealloc(tstate, obj);
        }
    }
}

static inline void
jit_stackref_close_specialized(PyThreadState *tstate, _PyStackRef ref, destructor destruct)
{
    if ((destruct == _PyFloat_ExactDealloc || destruct == _PyLong_ExactDealloc) &&
        _PyRuntime.ref_tracer.tracer_func == NULL) {
        jit_stackref_close(tstate, ref);
        return;
    }
    PyStackRef_CLOSE_SPECIALIZED(ref, destruct);
}

#define PyFloat_FromDouble(VALUE) jit_float_from_double(tstate, (VALUE))
#define _PyCompactLong_Add(LEFT, RIGHT) jit_compact_long_add(tstate, (LEFT), (RIGHT))
#define _PyCompactLong_Subtract(LEFT, RIGHT) jit_compact_long_subtract(tstate, (LEFT), (RIGHT))
#define _PyCompactLong_Multiply(LEFT, RIGHT) jit_compact_long_multiply(tstate, (LEFT), (RIGHT))
#define PyStackRef_CLOSE(REF) jit_stackref_close(tstate, (REF))
#define PyStackRef_XCLOSE(REF) jit_stackref_close(tstate, (REF))
#define PyStackRef_CLOSE_SPECIALIZED(REF, DESTRUCT) \
    jit_stackref_close_specialized(tstate, (REF), (DESTRUCT))
#endif

__attribute__((preserve_none)) _Py_CODEUNIT *
_JIT_ENTRY(
    _PyExecutorObject *executor, _PyInterpreterFrame *frame, _PyStackRef *stack_pointer, PyThreadState *tstate,
    _PyStackRef _tos_cache0, _PyStackRef _tos_cache1, _PyStackRef _tos_cache2
) {
    // Locals that the instruction implementations expect to exist:
    _PyExecutorObject *current_executor = executor;
    int oparg;
    int uopcode = _JIT_OPCODE;
    _Py_CODEUNIT *next_instr;
    // Other stuff we need handy:
#if SIZEOF_VOID_P == 8
    PATCH_VALUE(uint64_t, _operand0_64, _JIT_OPERAND0)
    PATCH_VALUE(uint64_t, _operand1_64, _JIT_OPERAND1)
#else
    assert(SIZEOF_VOID_P == 4);
    PATCH_VALUE(uint32_t, _operand0_hi, _JIT_OPERAND0_HI)
    PATCH_VALUE(uint32_t, _operand0_lo, _JIT_OPERAND0_LO)
    uint64_t _operand0_64 = ((uint64_t)_operand0_hi << 32) | _operand0_lo;
    PATCH_VALUE(uint32_t, _operand1_hi, _JIT_OPERAND1_HI)
    PATCH_VALUE(uint32_t, _operand1_lo, _JIT_OPERAND1_LO)
    uint64_t _operand1_64 = ((uint64_t)_operand1_hi << 32) | _operand1_lo;
#endif
#if SUPPORTS_SMALL_CONSTS
    PATCH_VALUE(uint32_t, _operand0_32, _JIT_OPERAND0_32)
    PATCH_VALUE(uint32_t, _operand1_32, _JIT_OPERAND1_32)
    PATCH_VALUE(uint16_t, _operand0_16, _JIT_OPERAND0_16)
    PATCH_VALUE(uint16_t, _operand1_16, _JIT_OPERAND1_16)
    PATCH_VALUE(uint16_t, _oparg_16, _JIT_OPARG_16)
#else
    PATCH_VALUE(uint16_t, _oparg, _JIT_OPARG)
#endif
    PATCH_VALUE(uint32_t, _target, _JIT_TARGET)
    OPT_STAT_INC(uops_executed);
    UOP_STAT_INC(uopcode, execution_count);
    switch (uopcode) {
        // The actual instruction definition gets inserted here:
        CASE
        default:
            Py_UNREACHABLE();
    }
    PATCH_JUMP(_JIT_CONTINUE);
}
