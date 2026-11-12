def f(log):
    try:
        try:
            raise KeyError("k")
        finally:
            return 1
    finally:
        log.append("outer finally")
