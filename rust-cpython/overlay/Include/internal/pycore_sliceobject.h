#ifndef Py_INTERNAL_SLICEOBJECT_H
#define Py_INTERNAL_SLICEOBJECT_H
#ifdef __cplusplus
extern "C" {
#endif

#include "pycore_pymem.h"

#ifdef _Py_JIT_TSTATE_LIFECYCLE
PyAPI_FUNC(PyObject *) _PyBuildSlice_ConsumeRefsWithThreadState(PyThreadState *, PyObject *, PyObject *, PyObject *);
PyAPI_FUNC(int) _PySlice_UnpackWithThreadState(PyThreadState *, PyObject *, Py_ssize_t *, Py_ssize_t *, Py_ssize_t *);
PyAPI_FUNC(void) _PySlice_DeallocWithThreadState(PyThreadState *, PyObject *);
PyAPI_DATA(destructor) const _PySlice_StandardDealloc;
#endif

#ifndef Py_BUILD_CORE
#  error "this header requires Py_BUILD_CORE define"
#endif


/* runtime lifecycle */

PyAPI_FUNC(PyObject *)
_PyBuildSlice_ConsumeRefs(PyObject *start, PyObject *stop, PyObject *step);

#ifdef __cplusplus
}
#endif
#endif /* !Py_INTERNAL_SLICEOBJECT_H */
