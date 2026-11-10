class C:
    T = "class"
    a: [i for i in r]
    b: {n for n in x}
    c: {k: v for k, v in y if k}

    def f(self, x: [[j for j in i] for i in r]) -> {T for _ in ()}:
        pass

    def g[U](self, x: [U for _ in (1,)]):
        pass

    type A = [T for _ in (1,)]

    class D[U]([T for _ in (1,)], [U for _ in (1,)]):
        pass
