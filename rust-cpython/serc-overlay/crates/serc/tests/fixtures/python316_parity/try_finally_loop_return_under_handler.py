def f():
    try:
        try:
            raise KeyError("k")
        finally:
            for i in range(2):
                return 1
    except:
        pass
