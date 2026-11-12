def f():
    try:
        pass
    finally:
        try:
            return
        finally:
            pass
