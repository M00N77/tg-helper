"""Тесты для командной работы: Team + TeamMember в repo.py и filters.py."""
import pytest

from src.db.models import Team, TeamMember
from src.db.repo import (
    add_team_member,
    create_team,
    get_team_by_chat,
    get_team_members,
    get_user_teams,
    remove_team_member,
    get_or_create_user,
)
from src.config import settings


@pytest.mark.asyncio
async def test_create_team(session):
    team = await create_team(
        session,
        name="Test Team",
        telegram_chat_id=-1001234567890,
        owner_telegram_id=6235799942,
    )
    assert team.id is not None
    assert team.name == "Test Team"
    assert team.chat_id == -1001234567890
    assert team.owner_telegram_id == 6235799942


@pytest.mark.asyncio
async def test_get_team_by_chat(session):
    team = await create_team(
        session,
        name="Find Me",
        telegram_chat_id=-1009999999001,
        owner_telegram_id=6235799942,
    )
    found = await get_team_by_chat(session, -1009999999001)
    assert found is not None
    assert found.id == team.id
    assert found.name == "Find Me"

    not_found = await get_team_by_chat(session, -1009999999999)
    assert not_found is None


@pytest.mark.asyncio
async def test_add_team_member(session):
    team = await create_team(
        session,
        name="Member Test",
        telegram_chat_id=-1001111111111,
        owner_telegram_id=6235799942,
    )
    member = await add_team_member(session, team.id, 111111111, role="member")
    assert member.id is not None
    assert member.team_id == team.id
    assert member.telegram_id == 111111111
    assert member.role == "member"


@pytest.mark.asyncio
async def test_add_team_member_admin(session):
    team = await create_team(
        session,
        name="Admin Test",
        telegram_chat_id=-1002222222222,
        owner_telegram_id=6235799942,
    )
    admin = await add_team_member(session, team.id, 6235799942, role="admin")
    assert admin.role == "admin"


@pytest.mark.asyncio
async def test_get_team_members(session):
    team = await create_team(
        session,
        name="Members List",
        telegram_chat_id=-1003333333333,
        owner_telegram_id=6235799942,
    )
    await add_team_member(session, team.id, 6235799942, role="admin")
    await add_team_member(session, team.id, 111111111, role="member")
    await add_team_member(session, team.id, 222222222, role="member")

    members = await get_team_members(session, team.id)
    assert len(members) == 3

    roles = {m.telegram_id: m.role for m in members}
    assert roles[6235799942] == "admin"
    assert roles[111111111] == "member"
    assert roles[222222222] == "member"


@pytest.mark.asyncio
async def test_remove_team_member(session):
    team = await create_team(
        session,
        name="Remove Test",
        telegram_chat_id=-1004444444444,
        owner_telegram_id=6235799942,
    )
    await add_team_member(session, team.id, 111111111, role="member")

    removed = await remove_team_member(session, team.id, 111111111)
    assert removed is True

    removed_twice = await remove_team_member(session, team.id, 111111111)
    assert removed_twice is False

    members = await get_team_members(session, team.id)
    assert len(members) == 0


@pytest.mark.asyncio
async def test_get_user_teams(session):
    team_a = await create_team(
        session,
        name="Team A",
        telegram_chat_id=-1005555555555,
        owner_telegram_id=6235799942,
    )
    team_b = await create_team(
        session,
        name="Team B",
        telegram_chat_id=-1006666666666,
        owner_telegram_id=6235799942,
    )
    await add_team_member(session, team_a.id, 6235799942, role="admin")
    await add_team_member(session, team_b.id, 6235799942, role="admin")

    teams = await get_user_teams(session, 6235799942)
    team_names = {t.name for t in teams}
    assert "Team A" in team_names
    assert "Team B" in team_names


@pytest.mark.asyncio
async def test_owner_allowed_ids():
    assert settings.owner_telegram_id == 6235799942
    assert isinstance(settings.all_allowed_ids, set)
    assert settings.owner_telegram_id in settings.all_allowed_ids


@pytest.mark.asyncio
async def test_team_cascade_delete(session):
    team = await create_team(
        session,
        name="Cascade Test",
        telegram_chat_id=-1007777777777,
        owner_telegram_id=6235799942,
    )
    await add_team_member(session, team.id, 111111111, role="member")
    await add_team_member(session, team.id, 222222222, role="member")

    await session.delete(team)
    await session.flush()

    members = await get_team_members(session, team.id)
    assert len(members) == 0
