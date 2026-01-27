"""Tests for the Golf Genius MCP Server."""

from __future__ import annotations

import os
from unittest.mock import patch

import httpx
import pytest
from pytest_httpx import HTTPXMock

# Ensure API key is set before importing main
os.environ["GOLF_GENIUS_API_KEY"] = "test-api-key-12345"

import main  # noqa: E402  (must come after env setup)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BASE = main.BASE_URL
KEY = "test-api-key-12345"
GET_PREFIX = f"{BASE}/{KEY}"      # GET URLs:  /api_v2/{key}/...
WRITE_PREFIX = f"{BASE}"          # Write URLs: /api_v2/...


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_client():
    """Reset the shared HTTP client before each test so pytest-httpx can intercept."""
    main._client = None
    yield
    main._client = None


# ---------------------------------------------------------------------------
# Pydantic Model Validation Tests
# ---------------------------------------------------------------------------


class TestEventCreateModel:
    def test_valid_event(self):
        event = main.EventCreate(name="Spring Open", start_date="2025-04-15")
        assert event.name == "Spring Open"
        assert event.event_type == "event"
        assert event.start_date == "2025-04-15"

    def test_empty_name_rejected(self):
        with pytest.raises(Exception):
            main.EventCreate(name="")

    def test_invalid_date_rejected(self):
        with pytest.raises(Exception):
            main.EventCreate(name="Test", start_date="not-a-date")

    def test_valid_date_formats(self):
        event = main.EventCreate(name="Test", start_date="2025-01-01", end_date="2025-12-31")
        assert event.start_date == "2025-01-01"
        assert event.end_date == "2025-12-31"


class TestEventUpdateModel:
    def test_partial_update(self):
        update = main.EventUpdate(name="New Name")
        assert update.name == "New Name"
        assert update.event_type is None

    def test_empty_update(self):
        update = main.EventUpdate()
        dumped = update.model_dump(exclude_none=True)
        assert dumped == {}

    def test_invalid_date_rejected(self):
        with pytest.raises(Exception):
            main.EventUpdate(start_date="2025/01/01")


class TestMemberRegistrationModel:
    def test_valid_registration(self):
        member = main.MemberRegistration(
            external_id="EXT-001",
            last_name="Woods",
            first_name="Tiger",
            email="tiger@example.com",
        )
        assert member.last_name == "Woods"
        assert member.email == "tiger@example.com"

    def test_invalid_email_rejected(self):
        with pytest.raises(Exception):
            main.MemberRegistration(
                external_id="EXT-001",
                last_name="Woods",
                email="not-an-email",
            )

    def test_empty_external_id_rejected(self):
        with pytest.raises(Exception):
            main.MemberRegistration(external_id="", last_name="Woods")

    def test_email_none_allowed(self):
        member = main.MemberRegistration(external_id="EXT-001", last_name="Woods")
        assert member.email is None


# ---------------------------------------------------------------------------
# Custom Exception Tests
# ---------------------------------------------------------------------------


class TestExceptions:
    def test_golf_genius_api_error(self):
        err = main.GolfGeniusAPIError(500, "Internal Server Error")
        assert err.status_code == 500
        assert "500" in str(err)

    def test_rate_limit_error(self):
        err = main.RateLimitError(retry_after=60)
        assert err.status_code == 429
        assert err.retry_after == 60

    def test_rate_limit_no_retry_after(self):
        err = main.RateLimitError()
        assert err.retry_after is None

    def test_authentication_error(self):
        err = main.AuthenticationError()
        assert err.status_code == 401

    def test_not_found_error(self):
        err = main.NotFoundError("Event")
        assert err.status_code == 404
        assert "Event not found" in str(err)


# ---------------------------------------------------------------------------
# URL Building Tests
# ---------------------------------------------------------------------------


class TestBuildUrl:
    def test_get_url_includes_api_key(self):
        url = main._build_url("GET", "/seasons")
        assert url == f"{BASE}/{KEY}/seasons"

    def test_post_url_excludes_api_key(self):
        url = main._build_url("POST", "/events")
        assert url == f"{BASE}/events"

    def test_put_url_excludes_api_key(self):
        url = main._build_url("PUT", "/events/42")
        assert url == f"{BASE}/events/42"

    def test_delete_url_excludes_api_key(self):
        url = main._build_url("DELETE", "/events/42")
        assert url == f"{BASE}/events/42"

    def test_strips_leading_slash(self):
        url = main._build_url("GET", "///seasons")
        assert "//" not in url.split("api_v2")[1].split(KEY)[1]


class TestWriteHeaders:
    def test_contains_bearer_token(self):
        headers = main._write_headers()
        assert headers["Authorization"] == f"Bearer {KEY}"
        assert headers["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# HTTP Client Tests
# ---------------------------------------------------------------------------


class TestGetClient:
    def test_creates_client(self):
        client = main._get_client()
        assert isinstance(client, httpx.AsyncClient)
        assert not client.is_closed

    def test_reuses_client(self):
        client1 = main._get_client()
        client2 = main._get_client()
        assert client1 is client2


# ---------------------------------------------------------------------------
# Helper Tests
# ---------------------------------------------------------------------------


class TestExtractHelper:
    def test_extracts_key_from_dict(self):
        result = main._extract({"events": [{"id": 1}]}, "events")
        assert result == [{"id": 1}]

    def test_returns_full_dict_when_key_missing(self):
        result = main._extract({"other": "data"}, "events")
        assert result == {"other": "data"}

    def test_returns_error_dict(self):
        result = main._extract({"error": "Something failed"}, "events")
        assert result == {"error": "Something failed"}

    def test_returns_list_as_is(self):
        """API may return a plain list instead of a dict with a nested key."""
        result = main._extract([{"id": 1}, {"id": 2}], "seasons")
        assert result == [{"id": 1}, {"id": 2}]

    def test_returns_empty_list_as_is(self):
        result = main._extract([], "seasons")
        assert result == []


# ---------------------------------------------------------------------------
# API Request Tests
# ---------------------------------------------------------------------------


class TestMakeApiRequest:
    @pytest.mark.asyncio
    async def test_successful_get(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{GET_PREFIX}/seasons",
            json={"seasons": [{"id": 1, "name": "2025"}]},
        )
        result = await main.make_api_request("GET", "/seasons")
        assert "seasons" in result

    @pytest.mark.asyncio
    async def test_get_url_contains_api_key_in_path(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"seasons": []})
        await main.make_api_request("GET", "/seasons")
        request = httpx_mock.get_requests()[0]
        assert KEY in str(request.url)

    @pytest.mark.asyncio
    async def test_post_uses_bearer_header(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{WRITE_PREFIX}/events",
            json={"id": 1},
            status_code=201,
        )
        await main.make_api_request("POST", "/events", json={"name": "Test"})
        request = httpx_mock.get_requests()[0]
        assert request.headers["authorization"] == f"Bearer {KEY}"

    @pytest.mark.asyncio
    async def test_post_url_does_not_contain_api_key_in_path(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"id": 1}, status_code=201)
        await main.make_api_request("POST", "/events", json={"name": "Test"})
        request = httpx_mock.get_requests()[0]
        # The KEY should not appear between /api_v2/ and /events
        path = str(request.url).replace(BASE, "")
        assert path == "/events"

    @pytest.mark.asyncio
    async def test_404_raises_not_found(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(status_code=404)
        with pytest.raises(main.NotFoundError):
            await main.make_api_request("GET", "/events/999")

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(status_code=401)
        with pytest.raises(main.AuthenticationError):
            await main.make_api_request("GET", "/events")

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(httpx.ReadTimeout("timed out"))
        result = await main.make_api_request("GET", "/events")
        assert "error" in result
        assert "timed out" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_connection_error_returns_error(self, httpx_mock: HTTPXMock):
        httpx_mock.add_exception(httpx.ConnectError("connection refused"))
        result = await main.make_api_request("GET", "/events")
        assert "error" in result
        assert "connect" in result["error"].lower()


# ---------------------------------------------------------------------------
# Tool Tests — Health Check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"seasons": []})
        result = await main.health_check()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_auth_failure(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(status_code=401)
        result = await main.health_check()
        assert result["status"] == "auth_error"


# ---------------------------------------------------------------------------
# Tool Tests — Organizational Data
# ---------------------------------------------------------------------------


class TestListSeasons:
    @pytest.mark.asyncio
    async def test_list_seasons(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"seasons": [{"id": 1, "name": "2025"}]})
        result = await main.list_seasons()
        assert len(result) == 1


class TestListCategories:
    @pytest.mark.asyncio
    async def test_list_categories(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"categories": [{"id": 1, "name": "Championship", "event_count": 5}]})
        result = await main.list_categories()
        assert result[0]["event_count"] == 5


class TestListDirectories:
    @pytest.mark.asyncio
    async def test_list_directories(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"directories": [{"id": 1, "name": "Main"}]})
        result = await main.list_directories()
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Tool Tests — Master Roster
# ---------------------------------------------------------------------------


class TestListMasterRoster:
    @pytest.mark.asyncio
    async def test_list_roster(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"players": [{"id": 1, "last_name": "Woods"}]})
        result = await main.list_master_roster()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_with_pagination(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"players": []})
        result = await main.list_master_roster(page=2)
        assert isinstance(result, list)


class TestGetMasterRosterMember:
    @pytest.mark.asyncio
    async def test_valid_email(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"id": 1, "email": "tiger@example.com"})
        result = await main.get_master_roster_member("tiger@example.com")
        assert result["email"] == "tiger@example.com"

    @pytest.mark.asyncio
    async def test_invalid_email(self):
        result = await main.get_master_roster_member("not-email")
        assert "error" in result


class TestGetPlayerEvents:
    @pytest.mark.asyncio
    async def test_get_events(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"events": [{"id": 1}]})
        result = await main.get_player_events(10)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_invalid_id(self):
        result = await main.get_player_events(0)
        assert "error" in result


# ---------------------------------------------------------------------------
# Tool Tests — Events
# ---------------------------------------------------------------------------


class TestListEvents:
    @pytest.mark.asyncio
    async def test_list_events(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"events": [{"id": 1, "name": "Spring Open"}]})
        result = await main.list_events()
        assert len(result) == 1
        assert result[0]["name"] == "Spring Open"

    @pytest.mark.asyncio
    async def test_with_filters(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"events": []})
        result = await main.list_events(season_id=1, page=2, archived=True)
        request = httpx_mock.get_requests()[0]
        url_str = str(request.url)
        assert "season_id=1" in url_str
        assert "page=2" in url_str
        assert "archived=true" in url_str


class TestCreateEvent:
    @pytest.mark.asyncio
    async def test_create_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"id": 100, "name": "Summer Classic"}, status_code=201)
        result = await main.create_event(name="Summer Classic", start_date="2025-07-01")
        assert result["name"] == "Summer Classic"

    @pytest.mark.asyncio
    async def test_invalid_date(self):
        result = await main.create_event(name="Test", start_date="bad-date")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_name(self):
        result = await main.create_event(name="")
        assert "error" in result


class TestUpdateEvent:
    @pytest.mark.asyncio
    async def test_update_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"id": 42, "name": "Updated"})
        result = await main.update_event(event_id=42, name="Updated")
        assert result["name"] == "Updated"

    @pytest.mark.asyncio
    async def test_no_fields(self):
        result = await main.update_event(event_id=42)
        assert "error" in result
        assert "No fields" in result["error"]

    @pytest.mark.asyncio
    async def test_invalid_id(self):
        result = await main.update_event(event_id=0, name="Test")
        assert "error" in result


class TestDeleteEvent:
    @pytest.mark.asyncio
    async def test_delete_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"status": "deleted"})
        result = await main.delete_event(event_id=42)
        assert result["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_invalid_id(self):
        result = await main.delete_event(event_id=-5)
        assert "error" in result


# ---------------------------------------------------------------------------
# Tool Tests — Event Roster (Members)
# ---------------------------------------------------------------------------


class TestGetEventRoster:
    @pytest.mark.asyncio
    async def test_get_roster(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"roster": [{"player_id": 1}]})
        result = await main.get_event_roster(1)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_invalid_id(self):
        result = await main.get_event_roster(-1)
        assert "error" in result


class TestRegisterMember:
    @pytest.mark.asyncio
    async def test_register_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"status": "registered"}, status_code=201)
        result = await main.register_member_to_event(
            event_id=1, external_id="EXT-100", last_name="Palmer",
            first_name="Arnold", email="arnie@example.com",
        )
        assert result["status"] == "registered"

    @pytest.mark.asyncio
    async def test_invalid_email(self):
        result = await main.register_member_to_event(
            event_id=1, external_id="EXT-100", last_name="Palmer", email="bad",
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_event_id(self):
        result = await main.register_member_to_event(
            event_id=0, external_id="EXT-100", last_name="Palmer",
        )
        assert "error" in result


class TestUpdateMember:
    @pytest.mark.asyncio
    async def test_update_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"status": "updated"})
        result = await main.update_member_in_event(event_id=1, member_id=10, last_name="Nicklaus")
        assert result["status"] == "updated"

    @pytest.mark.asyncio
    async def test_no_fields(self):
        result = await main.update_member_in_event(event_id=1, member_id=10)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_ids(self):
        result = await main.update_member_in_event(event_id=0, member_id=10, last_name="X")
        assert "error" in result


class TestDeleteMember:
    @pytest.mark.asyncio
    async def test_delete_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"status": "removed"})
        result = await main.delete_member_from_event(event_id=1, member_id=10)
        assert result["status"] == "removed"

    @pytest.mark.asyncio
    async def test_invalid_ids(self):
        result = await main.delete_member_from_event(event_id=0, member_id=1)
        assert "error" in result
        result = await main.delete_member_from_event(event_id=1, member_id=0)
        assert "error" in result


# ---------------------------------------------------------------------------
# Tool Tests — Rounds
# ---------------------------------------------------------------------------


class TestListEventRounds:
    @pytest.mark.asyncio
    async def test_list_rounds(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"rounds": [{"id": 1, "name": "Round 1"}]})
        result = await main.list_event_rounds(1)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_invalid_id(self):
        result = await main.list_event_rounds(0)
        assert "error" in result


class TestCreateRound:
    @pytest.mark.asyncio
    async def test_create_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"id": 10, "name": "Round 1"}, status_code=201)
        result = await main.create_round(event_id=1, name="Round 1", date="2025-07-01")
        assert result["name"] == "Round 1"

    @pytest.mark.asyncio
    async def test_invalid_date(self):
        result = await main.create_round(event_id=1, date="bad")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_event_id(self):
        result = await main.create_round(event_id=0)
        assert "error" in result


class TestUpdateRound:
    @pytest.mark.asyncio
    async def test_update_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"id": 10, "name": "Updated"})
        result = await main.update_round(event_id=1, round_id=10, name="Updated")
        assert result["name"] == "Updated"

    @pytest.mark.asyncio
    async def test_no_fields(self):
        result = await main.update_round(event_id=1, round_id=10)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_ids(self):
        result = await main.update_round(event_id=0, round_id=10, name="X")
        assert "error" in result


class TestDeleteRound:
    @pytest.mark.asyncio
    async def test_delete_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"status": "deleted"})
        result = await main.delete_round(event_id=1, round_id=10)
        assert result["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_invalid_ids(self):
        result = await main.delete_round(event_id=0, round_id=10)
        assert "error" in result


class TestGetRoundTeeSheet:
    @pytest.mark.asyncio
    async def test_get_tee_sheet(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"tee_sheet": {"groups": []}})
        result = await main.get_round_tee_sheet(event_id=1, round_id=5)
        assert "tee_sheet" in result

    @pytest.mark.asyncio
    async def test_invalid_ids(self):
        result = await main.get_round_tee_sheet(event_id=0, round_id=5)
        assert "error" in result
        result = await main.get_round_tee_sheet(event_id=1, round_id=0)
        assert "error" in result


class TestGetRoundTournaments:
    @pytest.mark.asyncio
    async def test_get_tournaments(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"tournaments": [{"id": 1}]})
        result = await main.get_round_tournaments(event_id=1, round_id=5)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_invalid_ids(self):
        result = await main.get_round_tournaments(event_id=0, round_id=5)
        assert "error" in result


# ---------------------------------------------------------------------------
# Tool Tests — Courses & Divisions
# ---------------------------------------------------------------------------


class TestGetEventCourses:
    @pytest.mark.asyncio
    async def test_get_courses(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"courses": [{"id": 1, "name": "Pebble Beach"}]})
        result = await main.get_event_courses(1)
        assert result[0]["name"] == "Pebble Beach"

    @pytest.mark.asyncio
    async def test_invalid_id(self):
        result = await main.get_event_courses(0)
        assert "error" in result


class TestGetEventDivisions:
    @pytest.mark.asyncio
    async def test_get_divisions(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"divisions": [{"id": 1, "name": "A Flight"}]})
        result = await main.get_event_divisions(1)
        assert result[0]["name"] == "A Flight"


class TestCreateDivision:
    @pytest.mark.asyncio
    async def test_create_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"id": 5, "name": "B Flight"}, status_code=201)
        result = await main.create_division(event_id=1, name="B Flight")
        assert result["name"] == "B Flight"

    @pytest.mark.asyncio
    async def test_empty_name(self):
        result = await main.create_division(event_id=1, name="   ")
        assert "error" in result


class TestUpdateDivision:
    @pytest.mark.asyncio
    async def test_update_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"id": 5, "name": "C Flight"})
        result = await main.update_division(event_id=1, division_id=5, name="C Flight")
        assert result["name"] == "C Flight"

    @pytest.mark.asyncio
    async def test_no_fields(self):
        result = await main.update_division(event_id=1, division_id=5)
        assert "error" in result


class TestDeleteDivision:
    @pytest.mark.asyncio
    async def test_delete_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"status": "deleted"})
        result = await main.delete_division(event_id=1, division_id=5)
        assert result["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_invalid_ids(self):
        result = await main.delete_division(event_id=0, division_id=5)
        assert "error" in result


# ---------------------------------------------------------------------------
# Tool Tests — Pairings
# ---------------------------------------------------------------------------


class TestCreatePairing:
    @pytest.mark.asyncio
    async def test_create_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"id": 1}, status_code=201)
        result = await main.create_pairing(
            event_id=1, round_id=5, players=[{"name": "Woods"}], tee_time="08:00 AM",
        )
        assert result["id"] == 1

    @pytest.mark.asyncio
    async def test_empty_players(self):
        result = await main.create_pairing(event_id=1, round_id=5, players=[])
        assert "error" in result


class TestUpdatePairing:
    @pytest.mark.asyncio
    async def test_update_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"id": 1, "tee_time": "09:00 AM"})
        result = await main.update_pairing(
            event_id=1, round_id=5, pairing_group_id=1, tee_time="09:00 AM",
        )
        assert result["tee_time"] == "09:00 AM"

    @pytest.mark.asyncio
    async def test_no_fields(self):
        result = await main.update_pairing(event_id=1, round_id=5, pairing_group_id=1)
        assert "error" in result


class TestDeletePairing:
    @pytest.mark.asyncio
    async def test_delete_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"status": "deleted"})
        result = await main.delete_pairing(event_id=1, round_id=5, pairing_group_id=1)
        assert result["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_invalid_ids(self):
        result = await main.delete_pairing(event_id=0, round_id=5, pairing_group_id=1)
        assert "error" in result


# ---------------------------------------------------------------------------
# Entry Point Tests
# ---------------------------------------------------------------------------


class TestMain:
    def test_exits_without_api_key(self):
        with patch.object(main, "API_KEY", None):
            with pytest.raises(SystemExit):
                main.main()
