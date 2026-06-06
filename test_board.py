import asyncio, aiohttp

LOGIN = "kplatonglfc@gmail.com"
PASSWORD = "EXV9TKHUryBTUf7"

async def test():
    async with aiohttp.ClientSession() as s:
        # Вариант 1 — список компаний
        r = await s.post(
            "https://yougile.com/api-v2/auth/companies",
            json={"login": LOGIN, "password": PASSWORD}
        )
        print("companies:", r.status, await r.text())

        # Вариант 2 — создать ключ напрямую (без companyId)
        r2 = await s.post(
            "https://yougile.com/api-v2/auth/keys",
            json={"login": LOGIN, "password": PASSWORD}
        )
        print("create key:", r2.status, await r2.text())

asyncio.run(test())