import asyncio, aiohttp

TOKEN = "nqc5AoUVbPo7fVCkaCh4lK7UXaaodirpGYd0yIW+v9zxgFMJ4K+gE9JDmGyM+sbp"

async def test():
    async with aiohttp.ClientSession() as s:
        r = await s.get(
            "https://yougile.com/api-v2/boards",
            headers={"Authorization": f"Bearer {TOKEN}"}
        )
        print(await r.json())
        print(r.status, await r.json())

asyncio.run(test())