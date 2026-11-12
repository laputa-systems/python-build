def f():
    try:
        pass
    except Exception:
        global a
    else:
        print(a)


def g():
    try:
        pass
    except* Exception:
        global a
    else:
        print(a)
