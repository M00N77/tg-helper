import asyncio
import httpx
import asyncio, aiohttp, json
TOKEN = "nqc5AoUVbPo7fVCkaCh4lK7UXaaodirpGYd0yIW+v9zxgFMJ4K+gE9JDmGyM+sbp"

COLUMN_ID = "0300b9c5-17a0-413f-a012-9b7b1857d914"  # Backlog

async def test():
    async with aiohttp.ClientSession() as s:
        r = await s.get(
            f"https://yougile.com/api-v2/tasks?columnId={COLUMN_ID}&limit=1",
            headers={"Authorization": f"Bearer {TOKEN}"}
        )
        data = await r.json()
        if data.get("content"):
            print(json.dumps(data["content"][0], indent=2, ensure_ascii=False))
        else:
            print("Пусто:", data)

asyncio.run(test())