try:
    pass
finally:
    y
    try:
        pass
    finally:
        x

try:
    a
except:
    pass
finally:
    try:
        pass
    finally:
        pass
