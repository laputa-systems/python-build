#include "Python.h"
#include <stdint.h>
#include <string.h>

static uint64_t counts[4];

static int
count_numeric_references(PyObject *obj, PyRefTracerEvent event, void *Py_UNUSED(data))
{
    if (obj == NULL || (event != PyRefTracer_CREATE && event != PyRefTracer_DESTROY)) {
        return 0;
    }
    int offset;
    if (PyFloat_CheckExact(obj)) {
        offset = 0;
    }
    else if (PyLong_CheckExact(obj)) {
        offset = 2;
    }
    else {
        return 0;
    }
    counts[offset + (event == PyRefTracer_DESTROY)]++;
    return 0;
}

PyAPI_FUNC(int)
begin_numeric_reference_trace(void)
{
    if (PyRefTracer_GetTracer(NULL) != NULL) {
        PyErr_SetString(PyExc_RuntimeError, "a reference tracer is already installed");
        return -1;
    }
    memset(counts, 0, sizeof(counts));
    return PyRefTracer_SetTracer(count_numeric_references, NULL);
}

PyAPI_FUNC(int)
end_numeric_reference_trace(void)
{
    return PyRefTracer_SetTracer(NULL, NULL);
}

PyAPI_FUNC(void)
numeric_reference_counts(uint64_t *result)
{
    memcpy(result, counts, sizeof(counts));
}
