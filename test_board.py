import asyncio
import httpx

TOKEN = "nqc5AoUVbPo7fVCkaCh4lK7UXaaodirpGYd0yIW+v9zxgFMJ4K+gE9JDmGyM+sbp"

async def test():
    async with httpx.AsyncClient() as client:
        r = await client.get(
            "https://yougile.com/api-v2/boards",
            headers={"Authorization": f"Bearer {TOKEN}"}
        )
        print(r.status_code, r.text)

asyncio.run(test())