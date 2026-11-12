def terminal_and():
    9 and x


def terminal_or():
    0xf or x


def terminal_partial():
    x and 9 and y


def terminal_decisive():
    1.0 or x and y


def terminal_nested():
    1j and (x or y)


def leading():
    0 or x
    z = 1


def leading_conditional():
    "" or (a if b else c)
    z = 1


def leading_chain():
    None or a < b < c
    z = 1


9 and x
