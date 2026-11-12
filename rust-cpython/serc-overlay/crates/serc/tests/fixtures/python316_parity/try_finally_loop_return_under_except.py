def f():
    try:
        try:
            pass
        except ValueError:
            pass
        else:
            raise KeyError("k")
        finally:
            for i in range(2):
                return 1
    except:
        pass
