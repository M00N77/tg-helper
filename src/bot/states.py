from aiogram.fsm.state import State, StatesGroup


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
    waiting_digest_time = State()
    waiting_news_time = State()
    waiting_lead_hours = State()
    waiting_timezone = State()
    waiting_auto_reply_text = State()


class NewsTopicStates(StatesGroup):
    waiting_topic = State()


class KanbanStates(StatesGroup):
    waiting_token = State()


class KanbanAuthStates(StatesGroup):
    waiting_login = State()
    waiting_password = State()
    waiting_company = State()
    waiting_for_board = State()


class KanbanCardStates(StatesGroup):
    waiting_title = State()
    waiting_description = State()
    waiting_column = State()
    editing_title = State()
    editing_desc = State()
    moving_task = State()


class MenuStates(StatesGroup):
    waiting_chat_name = State()
    waiting_send_query = State()
    waiting_news_topic = State()


class MeetingStates(StatesGroup):
    waiting_url = State()
