def f(log):
    try:
        try:
            pass
        finally:
            return 1
    finally:
        log.append("outer finally")
