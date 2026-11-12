unique_name_1 = unique_name_5 = [1]
name_0 = 42
unique_name_7: {name_0 for name_0 in unique_name_1}
unique_name_2: {
    0: (lambda: name_0 for unique_name_4 in unique_name_5)
    for unique_name_6 in [1]
    if name_0
}
a: {n for n in x}
b: [n for _ in y]


def f(a: {n for n in x}, b: [n for _ in y], c: [m for m in n]) -> {k: n for k in z}:
    pass


def outer():
    n = 1

    def g(a: {n for n in x}, b: [n for _ in y]):
        pass

    return g
