def f():
    try:
        try:
            pass
        except ValueError:
            pass
        else:
            raise KeyError("k")
        finally:
            return 1
    except:
        pass
