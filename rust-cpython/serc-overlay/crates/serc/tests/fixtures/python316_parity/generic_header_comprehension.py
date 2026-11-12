class C[T]([T for _ in (1,)]):
    pass


class D[T, U](dict[T, [lambda: U for _ in (1,)]], metaclass={T: U for _ in ()}.get(1, type)):
    pass


class E[__P]([__P for _ in (1,)]):
    pass


class Outer:
    T = "class"

    class Inner[U]([T for _ in (1,)], [U for _ in (1,)]):
        pass


class F[T]([[j for j in i] for i in r], x={k: T for k in r}):
    pass
