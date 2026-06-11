"""Интеграция с YouGile канбан-доской."""
from datetime import datetime
import time
import httpx
import logging
from typing import Dict, List, Optional

from rapidfuzz import fuzz, process


def _parse_deadline(s: str) -> dict:
    with_time = "T" in s
    dt = datetime.fromisoformat(s)
    if not with_time:
        dt = datetime(dt.year, dt.month, dt.day, tzinfo=dt.tzinfo)
    ts = int(dt.timestamp() * 1000)
    return {"deadline": ts, "withTime": with_time}


class YouGileClient:
    """Клиент для работы с API YouGile"""

    _BOARDS_CACHE_TTL = 60  # секунд

    def __init__(self, api_token: str, board_id: str | None = None):
        self.api_token = api_token
        self.board_id = board_id
        self.base_url = "https://yougile.com/api-v2"
        self.headers = {
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json"
        }
        self._boards_cache: list | None = None
        self._boards_cached_at: float = 0.0

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
        assignee_ids: Optional[list[str]] = None,
        deadline: str | None = None,
    ) -> Dict:
        """Создать карточку задачи"""
        self._require_board()
        payload: dict = {"title": title, "columnId": column_id}
        if description:
            payload["description"] = description
        if assignee_ids:
            payload["assigned"] = assignee_ids
        if deadline:
            payload["deadline"] = _parse_deadline(deadline)
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
        """Получить список досок (не требует board_id) с кэшем 60 сек"""
        now = time.monotonic()
        if self._boards_cache is not None and (now - self._boards_cached_at) < self._BOARDS_CACHE_TTL:
            return self._boards_cache
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/boards",
                headers=self.headers
            )
            if response.status_code == 404:
                logging.warning(
                    "[YouGile][get_boards] 404 — доски не найдены или нет прав"
                )
                self._boards_cache = []
                self._boards_cached_at = now
                return []
            if response.status_code != 200:
                body = response.text
                logging.warning(
                    f"[YouGile][get_boards] status={response.status_code} body={body}"
                )
                raise RuntimeError(
                    f"YouGile /boards вернул {response.status_code}: {body}"
                )
            data = response.json()
            result = data.get("content", [])
            self._boards_cache = result
            self._boards_cached_at = now
            return result

    async def get_users(self) -> list[dict]:
        """Получить список пользователей компании/проекта."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/users",
                headers=self.headers,
            )
            if response.status_code == 404:
                return []
            if response.status_code != 200:
                body = response.text
                logging.warning(
                    f"[YouGile][get_users] status={response.status_code} body={body}"
                )
                return []
            data = response.json()
            return data.get("content", [])

    async def resolve_user_by_name(self, name: str) -> str | None:
        """Нечеткий поиск пользователя YouGile по имени. Возвращает user_id или None."""
        users = await self.get_users()
        if not users or not name:
            return None
        choices = {u["id"]: u.get("name", "") for u in users}
        raw = process.extractOne(name, choices, scorer=fuzz.WRatio, score_cutoff=60)
        if raw:
            return raw[2]  # (match, score, key)
        return None

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

    async def find_task_by_title(self, title_hint: str, limit_per_col: int = 50) -> List[Dict]:
        """Найти задачи на доске по части названия (нечётко, по всем колонкам).
        Возвращает список совпавших карточек, отсортированный по релевантности."""
        self._require_board()
        title_hint = (title_hint or "").strip()
        if not title_hint:
            return []

        columns = await self.get_columns()
        all_cards: List[Dict] = []
        for col in columns:
            try:
                cards = await self.get_cards_in_column(col["id"], limit=limit_per_col)
            except Exception:
                continue
            all_cards.extend(cards)

        if not all_cards:
            return []

        hint_lower = title_hint.lower()

        # 1. Прямые подстрочные совпадения — самые точные.
        substring = [c for c in all_cards if hint_lower in (c.get("title", "").lower())]
        if substring:
            return substring

        # 2. Нечёткий поиск через rapidfuzz.
        scored = []
        for c in all_cards:
            title = c.get("title", "")
            if not title:
                continue
            score = fuzz.WRatio(title_hint, title)
            if score >= 60:
                scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored]

    async def close(self) -> None:
        """Совместимость: клиент использует короткоживущие httpx-сессии per-request,
        отдельного persistent-соединения закрывать не требуется."""
        return None

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