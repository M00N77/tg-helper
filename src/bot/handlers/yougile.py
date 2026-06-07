"""Интеграция с YouGile канбан-доской."""
import httpx
import logging
from typing import Dict, List, Optional


class YouGileClient:
    """Клиент для работы с API YouGile"""

    def __init__(self, api_token: str, board_id: str | None = None):
        self.api_token = api_token
        self.board_id = board_id
        self.base_url = "https://yougile.com/api-v2"
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }

    def _require_board(self) -> None:
        if not self.board_id:
            raise ValueError("board_id is required for this operation")

    async def get_columns(self) -> List[Dict]:
        """Получить список колонок доски"""
        self._require_board()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/columns",
                headers=self.headers,
                params={"boardId": self.board_id}
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
        self._require_board()
        payload = {"title": title, "columnId": column_id}
        if description:
            payload["description"] = description
        logging.warning(f"[YouGile][create_card] payload={payload}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/tasks",
                headers=self.headers,
                json=payload
            )
            if response.status_code >= 400:
                body = response.text
                logging.warning(
                    f"[YouGile][create_card] status={response.status_code} body={body}"
                )
                raise RuntimeError(f"YouGile POST /tasks вернул {response.status_code}: {body}")
            return response.json()

    async def move_card(self, card_id: str, column_id: str) -> Dict:
        """Переместить карточку в другую колонку"""
        self._require_board()
        payload = {"columnId": column_id}
        logging.warning(f"[YouGile][move_card] PUT /tasks/{card_id} payload={payload}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(
                f"{self.base_url}/tasks/{card_id}",
                headers=self.headers,
                json=payload
            )
            if response.status_code >= 400:
                body = response.text
                logging.warning(
                    f"[YouGile][move_card] status={response.status_code} body={body}"
                )
                raise RuntimeError(f"YouGile PUT /tasks/{card_id} вернул {response.status_code}: {body}")
            return response.json()

    async def get_cards_in_column(self, column_id: str, limit: int = 50) -> List[Dict]:
        """Получить карточки в колонке"""
        self._require_board()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/tasks",
                headers=self.headers,
                params={"columnId": column_id, "limit": limit}
            )
            response.raise_for_status()
            data = response.json()
            return data.get("content", [])

    async def get_boards(self) -> list:
        """Получить список досок (не требует board_id)"""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/boards",
                headers=self.headers
            )
            if response.status_code != 200:
                body = response.text
                logging.warning(
                    f"[YouGile][get_boards] status={response.status_code} body={body}"
                )
                raise RuntimeError(
                    f"YouGile /boards вернул {response.status_code}: {body}"
                )
            data = response.json()
            return data.get("content", [])

    async def update_card(self, card_id: str, **kwargs) -> Dict:
        """Обновить карточку"""
        self._require_board()
        logging.warning(f"[YouGile][update_card] PUT /tasks/{card_id} payload={kwargs}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.put(
                f"{self.base_url}/tasks/{card_id}",
                headers=self.headers,
                json=kwargs
            )
            if response.status_code >= 400:
                body = response.text
                logging.warning(
                    f"[YouGile][update_card] status={response.status_code} body={body}"
                )
                raise RuntimeError(f"YouGile PUT /tasks/{card_id} вернул {response.status_code}: {body}")
            return response.json()

    async def get_task(self, task_id: str) -> Dict:
        """Получить данные одной задачи"""
        self._require_board()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/tasks/{task_id}",
                headers=self.headers,
            )
            response.raise_for_status()
            return response.json()

    async def delete_task(self, task_id: str) -> None:
        """Удалить задачу"""
        self._require_board()
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.delete(
                f"{self.base_url}/tasks/{task_id}",
                headers=self.headers,
            )
            if response.status_code not in (200, 204):
                body = response.text
                raise RuntimeError(
                    f"YouGile DELETE /tasks вернул {response.status_code}: {body}"
                )

    async def generate_token(
        self, login: str, password: str, company_name: str
    ) -> str:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{self.base_url}/auth/companies",
                json={"login": login, "password": password}
            )
            if response.status_code != 200:
                body = response.text
                logging.warning(
                    f"[YouGile][auth/companies] status={response.status_code} body={body}"
                )
                raise RuntimeError(f"Ошибка авторизации: {body}")

            data = response.json()
            companies = data.get("content", [])
            if not companies:
                raise RuntimeError("Аккаунт не привязан ни к одной компании")

            # TODO: учитывать company_name, если у пользователя несколько компаний
            company_id = companies[0]["id"]

            response = await client.post(
                f"{self.base_url}/auth/keys",
                json={"login": login, "password": password, "companyId": company_id}
            )
            if response.status_code not in (200, 201):
                body = response.text
                logging.warning(
                    f"[YouGile][auth/keys] status={response.status_code} body={body}"
                )
                raise RuntimeError(f"Ошибка получения ключа: {body}")

            key_data = response.json()
            if "key" not in key_data:
                raise RuntimeError(f"Ответ не содержит ключ: {key_data}")

            return key_data["key"]