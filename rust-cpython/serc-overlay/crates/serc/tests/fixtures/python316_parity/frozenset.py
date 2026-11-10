def f(a):
    """d"""
    return a in {"default", "y"}


def g(a):
    return a in {"default", "y"}


x = (("a", "ABC"), (1, 10))


def k(v):
    return v in {("a", "ABC"), "junk", (1, 10)}
