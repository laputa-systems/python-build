#ifndef Py_INTERNAL_ARRAYOBJECT_H
#define Py_INTERNAL_ARRAYOBJECT_H
#ifdef __cplusplus
extern "C" {
#endif

#ifndef Py_BUILD_CORE
#  error "this header requires Py_BUILD_CORE define"
#endif

struct arrayobject;

struct arraydescr {
    char typecode[3];
    int itemsize;
    PyObject *(*getitem)(struct arrayobject *, Py_ssize_t);
    int (*setitem)(struct arrayobject *, Py_ssize_t, PyObject *);
    int (*compareitems)(const void *, const void *, Py_ssize_t);
    int is_integer_type;
    int is_signed;
};

typedef struct arrayobject {
    PyObject_VAR_HEAD
    char *ob_item;
    Py_ssize_t allocated;
    const struct arraydescr *ob_descr;
    PyObject *weakreflist;
    Py_ssize_t ob_exports;
} arrayobject;

#ifdef __cplusplus
}
#endif
#endif
