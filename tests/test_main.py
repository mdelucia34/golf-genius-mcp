"""Tests for the Golf Genius MCP Server."""

from __future__ import annotations

import os
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
from pytest_httpx import HTTPXMock

# Ensure API key is set before importing main
os.environ["GOLF_GENIUS_API_KEY"] = "test-api-key-12345"

import main  # noqa: E402  (must come after env setup)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BASE = main.BASE_URL


@pytest.fixture(autouse=True)
def _reset_client():
    """Reset the shared HTTP client before each test so pytest-httpx can intercept."""
    if main._client is not None and not main._client.is_closed:
        # We can't await close in a sync fixture, so just set to None
        pass
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


class TestPlayerRegistrationModel:
    def test_valid_registration(self):
        player = main.PlayerRegistration(
            external_id="EXT-001",
            last_name="Woods",
            first_name="Tiger",
            email="tiger@example.com",
        )
        assert player.last_name == "Woods"
        assert player.email == "tiger@example.com"

    def test_invalid_email_rejected(self):
        with pytest.raises(Exception):
            main.PlayerRegistration(
                external_id="EXT-001",
                last_name="Woods",
                email="not-an-email",
            )

    def test_empty_external_id_rejected(self):
        with pytest.raises(Exception):
            main.PlayerRegistration(external_id="", last_name="Woods")

    def test_email_none_allowed(self):
        player = main.PlayerRegistration(external_id="EXT-001", last_name="Woods")
        assert player.email is None


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
        assert "60" in str(err)

    def test_rate_limit_no_retry_after(self):
        err = main.RateLimitError()
        assert err.retry_after is None
        assert "Rate limited" in str(err)

    def test_authentication_error(self):
        err = main.AuthenticationError()
        assert err.status_code == 401
        assert "Invalid" in str(err)

    def test_not_found_error(self):
        err = main.NotFoundError("Event")
        assert err.status_code == 404
        assert "Event not found" in str(err)


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
    def test_extracts_key(self):
        result = main._extract({"events": [{"id": 1}]}, "events")
        assert result == [{"id": 1}]

    def test_returns_empty_list_for_missing_key(self):
        result = main._extract({"other": "data"}, "events")
        assert result == []

    def test_returns_error_dict(self):
        result = main._extract({"error": "Something failed"}, "events")
        assert result == {"error": "Something failed"}


# ---------------------------------------------------------------------------
# API Request Tests
# ---------------------------------------------------------------------------


class TestMakeApiRequest:
    @pytest.mark.asyncio
    async def test_successful_get(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE}/seasons",
            json={"seasons": [{"id": 1, "name": "2025"}]},
        )
        result = await main.make_api_request("GET", "/seasons")
        assert "seasons" in result
        assert result["seasons"][0]["id"] == 1

    @pytest.mark.asyncio
    async def test_404_raises_not_found(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=f"{BASE}/events/999", status_code=404)
        with pytest.raises(main.NotFoundError):
            await main.make_api_request("GET", "/events/999")

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=f"{BASE}/events", status_code=401)
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
        httpx_mock.add_response(
            url=f"{BASE}/seasons",
            json={"seasons": []},
        )
        result = await main.health_check()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_auth_failure(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=f"{BASE}/seasons", status_code=401)
        result = await main.health_check()
        assert result["status"] == "auth_error"


# ---------------------------------------------------------------------------
# Tool Tests — Events
# ---------------------------------------------------------------------------


class TestListEvents:
    @pytest.mark.asyncio
    async def test_list_events(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=httpx.URL(BASE + "/events", params={"limit": "100", "offset": "0"}),
            json={"events": [{"id": 1, "name": "Spring Open"}]},
        )
        result = await main.list_events()
        assert len(result) == 1
        assert result[0]["name"] == "Spring Open"

    @pytest.mark.asyncio
    async def test_list_events_with_pagination(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            json={"events": [{"id": 2}]},
        )
        result = await main.list_events(limit=10, offset=5)
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_limit_clamped(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"events": []})
        await main.list_events(limit=999)
        request = httpx_mock.get_requests()[0]
        assert "limit=100" in str(request.url)


class TestGetEventDetails:
    @pytest.mark.asyncio
    async def test_get_details(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE}/events/42",
            json={"id": 42, "name": "Club Championship"},
        )
        result = await main.get_event_details(42)
        assert result["id"] == 42

    @pytest.mark.asyncio
    async def test_invalid_id(self):
        result = await main.get_event_details(0)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_negative_id(self):
        result = await main.get_event_details(-1)
        assert "error" in result


class TestCreateEvent:
    @pytest.mark.asyncio
    async def test_create_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE}/events",
            json={"id": 100, "name": "Summer Classic"},
            status_code=201,
        )
        result = await main.create_event(
            name="Summer Classic",
            start_date="2025-07-01",
            end_date="2025-07-03",
        )
        assert result["name"] == "Summer Classic"

    @pytest.mark.asyncio
    async def test_invalid_date(self):
        result = await main.create_event(name="Test", start_date="bad-date")
        assert "error" in result
        assert "Validation" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_name(self):
        result = await main.create_event(name="")
        assert "error" in result


class TestUpdateEvent:
    @pytest.mark.asyncio
    async def test_update_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE}/events/42",
            json={"id": 42, "name": "Updated Name"},
        )
        result = await main.update_event(event_id=42, name="Updated Name")
        assert result["name"] == "Updated Name"

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
        httpx_mock.add_response(url=f"{BASE}/events/42", json={"status": "deleted"})
        result = await main.delete_event(event_id=42)
        assert result["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_invalid_id(self):
        result = await main.delete_event(event_id=-5)
        assert "error" in result


# ---------------------------------------------------------------------------
# Tool Tests — Players
# ---------------------------------------------------------------------------


class TestListMasterRoster:
    @pytest.mark.asyncio
    async def test_list_roster(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            json={"players": [{"id": 1, "last_name": "Woods"}]},
        )
        result = await main.list_master_roster()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_search(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(json={"players": []})
        result = await main.list_master_roster(search="Tiger")
        assert isinstance(result, list)


class TestGetPlayerDetails:
    @pytest.mark.asyncio
    async def test_get_player(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE}/master_roster/10",
            json={"id": 10, "last_name": "Nicklaus"},
        )
        result = await main.get_player_details(10)
        assert result["last_name"] == "Nicklaus"

    @pytest.mark.asyncio
    async def test_invalid_id(self):
        result = await main.get_player_details(0)
        assert "error" in result


class TestRegisterPlayer:
    @pytest.mark.asyncio
    async def test_register_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE}/events/1/roster",
            json={"status": "registered"},
            status_code=201,
        )
        result = await main.register_player_to_event(
            event_id=1,
            external_id="EXT-100",
            last_name="Palmer",
            first_name="Arnold",
            email="arnie@example.com",
        )
        assert result["status"] == "registered"

    @pytest.mark.asyncio
    async def test_invalid_email(self):
        result = await main.register_player_to_event(
            event_id=1,
            external_id="EXT-100",
            last_name="Palmer",
            email="bad-email",
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_event_id(self):
        result = await main.register_player_to_event(
            event_id=0,
            external_id="EXT-100",
            last_name="Palmer",
        )
        assert "error" in result


class TestUnregisterPlayer:
    @pytest.mark.asyncio
    async def test_unregister_success(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE}/events/1/roster/10",
            json={"status": "removed"},
        )
        result = await main.unregister_player_from_event(event_id=1, player_id=10)
        assert result["status"] == "removed"

    @pytest.mark.asyncio
    async def test_invalid_ids(self):
        result = await main.unregister_player_from_event(event_id=0, player_id=1)
        assert "error" in result
        result = await main.unregister_player_from_event(event_id=1, player_id=0)
        assert "error" in result


class TestGetEventRoster:
    @pytest.mark.asyncio
    async def test_get_roster(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE}/events/1/roster",
            json={"roster": [{"player_id": 1}]},
        )
        result = await main.get_event_roster(1)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_invalid_id(self):
        result = await main.get_event_roster(-1)
        assert "error" in result


# ---------------------------------------------------------------------------
# Tool Tests — Scoring & Results
# ---------------------------------------------------------------------------


class TestGetTournamentResults:
    @pytest.mark.asyncio
    async def test_json_results(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            json={"results": [{"place": 1, "player": "Woods"}]},
        )
        result = await main.get_tournament_results(round_id=5)
        assert "results" in result

    @pytest.mark.asyncio
    async def test_html_results(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(text="<html>results</html>")
        result = await main.get_tournament_results(round_id=5, format_type="html")
        assert "<html>" in result

    @pytest.mark.asyncio
    async def test_invalid_format(self):
        result = await main.get_tournament_results(round_id=5, format_type="csv")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_id(self):
        result = await main.get_tournament_results(round_id=0)
        assert "error" in result


class TestGetRoundTeeSheet:
    @pytest.mark.asyncio
    async def test_get_tee_sheet(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            json={"tee_sheet": {"groups": []}},
        )
        result = await main.get_round_tee_sheet(round_id=5)
        assert "tee_sheet" in result

    @pytest.mark.asyncio
    async def test_invalid_id(self):
        result = await main.get_round_tee_sheet(round_id=0)
        assert "error" in result


class TestListEventRounds:
    @pytest.mark.asyncio
    async def test_list_rounds(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE}/events/1/rounds",
            json={"rounds": [{"id": 1, "name": "Round 1"}]},
        )
        result = await main.list_event_rounds(1)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_invalid_id(self):
        result = await main.list_event_rounds(0)
        assert "error" in result


# ---------------------------------------------------------------------------
# Tool Tests — Organizational Data
# ---------------------------------------------------------------------------


class TestListSeasons:
    @pytest.mark.asyncio
    async def test_list_seasons(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE}/seasons",
            json={"seasons": [{"id": 1, "name": "2025"}]},
        )
        result = await main.list_seasons()
        assert len(result) == 1


class TestListCategories:
    @pytest.mark.asyncio
    async def test_list_categories(self, httpx_mock: HTTPXMock):
        httpx_mock.add_response(
            url=f"{BASE}/categories",
            json={"categories": [{"id": 1, "name": "Championship", "event_count": 5}]},
        )
        result = await main.list_categories()
        assert result[0]["event_count"] == 5


# ---------------------------------------------------------------------------
# Entry Point Tests
# ---------------------------------------------------------------------------


class TestMain:
    def test_exits_without_api_key(self):
        with patch.object(main, "API_KEY", None):
            with pytest.raises(SystemExit):
                main.main()
