#include "Python.h"

#include "pycore_ceval.h"
#include "pycore_frame.h"
#include "pycore_jit.h"
#include "pycore_jit_float.h"

#include "jit.h"

#if _Py_JIT_FLOAT_RAW
_Static_assert(offsetof(_PyThreadStateImpl, jit_float_context) >
               offsetof(_PyThreadStateImpl, jit_tracer_state),
               "raw float context must follow JIT tracer state");

_PyJitFloatContext *
_PyJitFloat_GetContext(PyThreadState *tstate)
{
    return ((_PyThreadStateImpl *)tstate)->jit_float_context;
}
#endif

_Py_CODEUNIT *
_PyJIT_Entry(
    _PyExecutorObject *exec, _PyInterpreterFrame *frame, _PyStackRef *stack_pointer, PyThreadState *tstate
) {
    // Note that this is *not* a tail call
    jit_func_preserve_none jitted = (jit_func_preserve_none)exec->jit_code;
#if _Py_JIT_FLOAT_RAW
    _PyThreadStateImpl *tstate_impl = (_PyThreadStateImpl *)tstate;
    _PyJitFloatContext *previous = tstate_impl->jit_float_context;
    _PyJitFloatContext context;
    context.active = false;
    tstate_impl->jit_float_context = &context;
    _Py_CODEUNIT *result = jitted(exec, frame, stack_pointer, tstate,
                                  PyStackRef_ZERO_BITS, PyStackRef_ZERO_BITS, PyStackRef_ZERO_BITS);
    if (context.active) {
        Py_FatalError("JIT exited with active raw float context");
    }
    tstate_impl->jit_float_context = previous;
    return result;
#else
    return jitted(exec, frame, stack_pointer, tstate,
                  PyStackRef_ZERO_BITS, PyStackRef_ZERO_BITS, PyStackRef_ZERO_BITS);
#endif
}
