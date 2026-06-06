"""Интеграция с YouGile канбан-доской."""
import aiohttp
import httpx
import logging
from typing import Dict, List, Optional


class YouGileClient:
    """Клиент для работы с API YouGile"""
    
    def __init__(self, api_token: str, board_id: str):
        self.api_token = api_token
        self.board_id = board_id
        self.base_url = "https://yougile.com/api-v2"
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }
    
    async def get_columns(self) -> List[Dict]:
        """Получить список колонок доски"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/boards/{self.board_id}/columns",
                headers=self.headers
            )
            response.raise_for_status()
            data = response.json()
            return data.get("content", [])
    
    async def create_card(
        self,
        title: str,
        description: str,
        column_id: str,
        assignee_ids: Optional[List[str]] = None
    ) -> Dict:
        """Создать карточку задачи"""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/cards",
                headers=self.headers,
                json={
                    "title": title,
                    "description": description,
                    "columnId": column_id,
                    "assignedUsersIds": assignee_ids or []
                }
            )
            response.raise_for_status()
            return response.json()
    
    async def move_card(self, card_id: str, column_id: str) -> Dict:
        """Переместить карточку в другую колонку"""
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{self.base_url}/cards/{card_id}",
                headers=self.headers,
                json={"columnId": column_id}
            )
            response.raise_for_status()
            return response.json()
    
    async def get_cards_in_column(self, column_id: str, limit: int = 50) -> List[Dict]:
        """Получить карточки в колонке"""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.base_url}/columns/{column_id}/cards",
                headers=self.headers,
                params={"limit": limit}
            )
            response.raise_for_status()
            data = response.json()
            return data.get("content", [])
    
    async def get_boards(self) -> list:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://yougile.com/api-v2/boards",
                headers={"Authorization": f"Bearer {self.api_token}"}
            ) as resp:
                data = await resp.json()
                return data.get("content", [])

    async def update_card(self, card_id: str, **kwargs) -> Dict:
        """Обновить карточку"""
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{self.base_url}/cards/{card_id}",
                headers=self.headers,
                json=kwargs
            )
            response.raise_for_status()
            return response.json()

    async def generate_token(
        self, login: str, password: str, company_name: str
    ) -> str:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://yougile.com/api-v2/auth/companies",
                json={"login": login, "password": password}
            ) as resp:
                data = await resp.json()

            companies = data.get("content", [])
            if not companies:
                raise RuntimeError("Нет доступных компаний")

            company_id = companies[0]["id"]

            async with session.post(
                "https://yougile.com/api-v2/auth/keys",
                json={"login": login, "password": password, "companyId": company_id}
            ) as resp:
                key_data = await resp.json()

            token = key_data.get("key")
            if not token:
                raise RuntimeError(f"Не удалось получить токен: {key_data}")
            return token