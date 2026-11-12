async def f():
    y = [i async for i in r()]


async def g():
    _ = [(x,
        2*x)
        async for x
        in [1,2,3] if (x > 0
                       and x < 100
                       and x != 50)]
