def f(log):
    try:
        try:
            raise KeyError("k")
        except ValueError:
            pass
        else:
            pass
        finally:
            return 1
    finally:
        log.append("outer finally")
