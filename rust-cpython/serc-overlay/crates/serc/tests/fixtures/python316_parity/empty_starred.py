def f(g, b):
    return g(*(), b), [1, *[], 2]


class C1[T](*()):
    pass
