#ifndef Py_INTERNAL_ARRAY_H
#define Py_INTERNAL_ARRAY_H
#ifdef __cplusplus
extern "C" {
#endif

#ifndef Py_BUILD_CORE
#  error "this header requires Py_BUILD_CORE define"
#endif

PyAPI_FUNC(int) _PyArray_GetSpecializationKind(PyObject *op);
PyAPI_FUNC(int) _PyArray_ReadDoubleItem(PyObject *op, Py_ssize_t index, double *value);
PyAPI_FUNC(int) _PyArray_ReadIntItem(PyObject *op, Py_ssize_t index, int *value);

#ifdef __cplusplus
}
#endif
#endif
