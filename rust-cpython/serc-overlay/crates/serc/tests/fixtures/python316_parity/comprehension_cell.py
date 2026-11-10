def f():
    type Alias[T: [lambda: T for T in (T, [1])[1]]] = [lambda: T for T in T.__name__]
    return Alias
