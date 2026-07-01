from typing import TypedDict

from aiogram.fsm.state import State, StatesGroup


class TaskData(TypedDict, total=False):
    title: str
    description: str
    deadline: str | None
    assignee: str | None


class VoiceTaskState(TypedDict):
    tasks: list[TaskData]
    source_message_id: int


class LoginStates(StatesGroup):
    api_id = State()
    api_hash = State()
    phone = State()
    code = State()
    password_2fa = State()


class SettingsStates(StatesGroup):
    waiting_openai_key = State()
    waiting_gemini_key = State()
    waiting_gigachat_key = State()
    waiting_groq_key = State()
    waiting_digest_time = State()
    waiting_news_time = State()
    waiting_lead_hours = State()
    waiting_timezone = State()
    waiting_auto_reply_text = State()
    waiting_display_name = State()


class NewsTopicStates(StatesGroup):
    waiting_topic = State()


class KanbanAuthStates(StatesGroup):
    waiting_login = State()
    waiting_password = State()
    waiting_for_board = State()


class KanbanCardStates(StatesGroup):
    waiting_title = State()
    waiting_description = State()
    waiting_column = State()
    editing_title = State()
    editing_desc = State()
    moving_task = State()
    setting_deadline = State()


class MenuStates(StatesGroup):
    waiting_chat_name = State()
    waiting_send_query = State()
    waiting_news_topic = State()


class TeamStates(StatesGroup):
    waiting_team_name = State()
    waiting_chat_id = State()
    waiting_invite_username = State()


class MeetingStates(StatesGroup):
    waiting_url = State()
    waiting_task_edit = State()
    waiting_task_add = State()


class TaskCreationStates(StatesGroup):
    waiting_for_board = State()


class OnboardingStates(StatesGroup):
    waiting_display_name = State()


class DictStates(StatesGroup):
    waiting_for_single_term = State()
    waiting_for_file = State()
