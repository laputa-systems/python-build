async def f():
    y = [[1 async for i in r] async for j in r]


async def g():
    y = {k: [i async for i in k] async for k in r}
