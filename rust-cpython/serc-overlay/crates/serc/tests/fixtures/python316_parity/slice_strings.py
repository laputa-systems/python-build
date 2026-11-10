def f(x):
    return x["slice_a":"slice b"]


def g(x, slice_a):
    return x["slice_a":"slice b"]


def h(x):
    y = x["slice_a":"slice b"]
    return y.slice_a


def i(x):
    y = x["slice_a":"slice b", 1:"slice_a"]
    return y, "slice_a"
