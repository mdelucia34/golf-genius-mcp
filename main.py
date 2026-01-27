"""Golf Genius MCP Server - Model Context Protocol server for the Golf Genius API v2."""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any, Dict, List, Optional, Union

import httpx
from dotenv import load_dotenv
from mcp.server import FastMCP
from pydantic import BaseModel, Field, field_validator
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ---------------------------------------------------------------------------
# Configuration & Logging
# ---------------------------------------------------------------------------

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("golf-genius-mcp")

API_KEY = os.getenv("GOLF_GENIUS_API_KEY")
BASE_URL = "https://api.golfgenius.com/v2"

# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------


class GolfGeniusAPIError(Exception):
    """Base exception for Golf Genius API errors."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"API Error {status_code}: {message}")


class RateLimitError(GolfGeniusAPIError):
    """Raised when the API returns a 429 rate-limit response."""

    def __init__(self, retry_after: Optional[int] = None):
        self.retry_after = retry_after
        super().__init__(429, f"Rate limited. Retry after {retry_after}s" if retry_after else "Rate limited.")


class AuthenticationError(GolfGeniusAPIError):
    """Raised when the API returns a 401/403 response."""

    def __init__(self, message: str = "Invalid or expired API key."):
        super().__init__(401, message)


class NotFoundError(GolfGeniusAPIError):
    """Raised when the API returns a 404 response."""

    def __init__(self, resource: str = "Resource"):
        super().__init__(404, f"{resource} not found.")


# ---------------------------------------------------------------------------
# Pydantic Models for Input Validation
# ---------------------------------------------------------------------------

DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class EventCreate(BaseModel):
    """Validated input for creating an event."""
    name: str = Field(..., min_length=1, max_length=255)
    event_type: str = Field(default="event")
    external_id: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_date(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not DATE_PATTERN.match(v):
            raise ValueError("Date must be in YYYY-MM-DD format")
        return v


class EventUpdate(BaseModel):
    """Validated input for updating an event."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    event_type: Optional[str] = None
    external_id: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    @field_validator("start_date", "end_date")
    @classmethod
    def validate_date(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not DATE_PATTERN.match(v):
            raise ValueError("Date must be in YYYY-MM-DD format")
        return v


class PlayerRegistration(BaseModel):
    """Validated input for player registration."""
    external_id: str = Field(..., min_length=1)
    last_name: str = Field(..., min_length=1)
    first_name: Optional[str] = None
    email: Optional[str] = None
    rounds: Optional[List[Dict[str, Any]]] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Invalid email format")
        return v


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("Golf Genius API Server")

# ---------------------------------------------------------------------------
# HTTP Client with Connection Pooling, Timeouts & Retry
# ---------------------------------------------------------------------------

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    """Return a shared httpx.AsyncClient, creating one if needed."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={
                "Authorization": f"Bearer {API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
    return _client


@retry(
    retry=retry_if_exception_type(RateLimitError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    reraise=True,
)
async def make_api_request(
    method: str,
    endpoint: str,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Make an authenticated request to the Golf Genius API.

    Features:
    - Shared connection pool via httpx.AsyncClient
    - 30-second request timeout (10-second connect)
    - Automatic retry (up to 3 attempts) on rate-limit (429) responses
    - Structured error responses for all failure modes
    """
    client = _get_client()
    try:
        logger.info("API %s %s", method, endpoint)
        response = await client.request(method, endpoint, **kwargs)

        # Handle specific status codes
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            logger.warning("Rate limited on %s %s (retry-after: %s)", method, endpoint, retry_after)
            raise RateLimitError(retry_after=int(retry_after) if retry_after else None)

        if response.status_code in (401, 403):
            logger.error("Authentication failure on %s %s", method, endpoint)
            raise AuthenticationError()

        if response.status_code == 404:
            logger.warning("Not found: %s %s", method, endpoint)
            raise NotFoundError(endpoint)

        response.raise_for_status()
        return response.json()

    except (RateLimitError, AuthenticationError, NotFoundError):
        raise
    except httpx.HTTPStatusError as e:
        logger.error("HTTP error %s on %s %s: %s", e.response.status_code, method, endpoint, e.response.text)
        return {"error": f"API Error {e.response.status_code}: {e.response.text}"}
    except httpx.TimeoutException:
        logger.error("Timeout on %s %s", method, endpoint)
        return {"error": "Request timed out. Please try again."}
    except httpx.ConnectError:
        logger.error("Connection failed for %s %s", method, endpoint)
        return {"error": "Unable to connect to Golf Genius API. Check your network connection."}
    except Exception as e:
        logger.exception("Unexpected error on %s %s", method, endpoint)
        return {"error": f"Request failed: {str(e)}"}


async def make_raw_request(method: str, endpoint: str, **kwargs: Any) -> str:
    """Make a request and return the raw text response (for HTML/XML)."""
    client = _get_client()
    try:
        response = await client.request(method, endpoint, **kwargs)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logger.exception("Raw request error on %s %s", method, endpoint)
        return f"Error fetching response: {str(e)}"


# ---------------------------------------------------------------------------
# Helper: safe result extraction
# ---------------------------------------------------------------------------

def _extract(result: Dict[str, Any], key: str) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Extract a list from the API response, or return the full error dict."""
    if "error" in result:
        return result
    return result.get(key, [])


# ---------------------------------------------------------------------------
# Tools — Health Check
# ---------------------------------------------------------------------------

@mcp.tool()
async def health_check() -> Dict[str, Any]:
    """Check API connectivity and authentication status.

    Returns the connection status, authentication validity, and API version info.
    """
    logger.info("Running health check")
    try:
        result = await make_api_request("GET", "/seasons")
        if "error" in result:
            return {"status": "error", "message": result["error"]}
        return {"status": "ok", "message": "Connected and authenticated successfully."}
    except AuthenticationError:
        return {"status": "auth_error", "message": "API key is invalid or expired."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# Tools — Event Management
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_events(
    season_id: Optional[int] = None,
    category_id: Optional[int] = None,
    directory_id: Optional[int] = None,
    archived: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """List golf events with optional filtering and pagination.

    Args:
        season_id: Filter by season ID
        category_id: Filter by category ID
        directory_id: Filter by directory ID
        archived: Include archived events
        limit: Maximum number of events to return (1-100, default 100)
        offset: Number of records to skip for pagination (default 0)
    """
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    params: Dict[str, Any] = {"limit": limit, "offset": offset}
    if season_id is not None:
        params["season_id"] = season_id
    if category_id is not None:
        params["category_id"] = category_id
    if directory_id is not None:
        params["directory_id"] = directory_id
    if archived:
        params["archived"] = "true"

    result = await make_api_request("GET", "/events", params=params)
    return _extract(result, "events")


@mcp.tool()
async def get_event_details(event_id: int) -> Dict[str, Any]:
    """Get detailed information about a specific event.

    Args:
        event_id: The ID of the event to retrieve
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}
    return await make_api_request("GET", f"/events/{event_id}")


@mcp.tool()
async def create_event(
    name: str,
    event_type: str = "event",
    external_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new golf event.

    Args:
        name: Name of the event (required)
        event_type: Type of event (default: "event")
        external_id: External identifier for the event
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
    """
    try:
        validated = EventCreate(
            name=name,
            event_type=event_type,
            external_id=external_id,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:
        return {"error": f"Validation error: {e}"}

    payload = {"name": validated.name, "event_type": validated.event_type}
    if validated.external_id:
        payload["external_id"] = validated.external_id
    if validated.start_date:
        payload["start_date"] = validated.start_date
    if validated.end_date:
        payload["end_date"] = validated.end_date

    logger.info("Creating event: %s", validated.name)
    return await make_api_request("POST", "/events", json=payload)


@mcp.tool()
async def update_event(
    event_id: int,
    name: Optional[str] = None,
    event_type: Optional[str] = None,
    external_id: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Update an existing golf event.

    Args:
        event_id: ID of the event to update
        name: New name for the event
        event_type: New type for the event
        external_id: New external identifier
        start_date: New start date in YYYY-MM-DD format
        end_date: New end date in YYYY-MM-DD format
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}

    try:
        validated = EventUpdate(
            name=name,
            event_type=event_type,
            external_id=external_id,
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as e:
        return {"error": f"Validation error: {e}"}

    payload = validated.model_dump(exclude_none=True)
    if not payload:
        return {"error": "No fields provided to update."}

    logger.info("Updating event %d", event_id)
    return await make_api_request("PUT", f"/events/{event_id}", json=payload)


@mcp.tool()
async def delete_event(event_id: int) -> Dict[str, Any]:
    """Delete (archive) a golf event.

    Args:
        event_id: ID of the event to delete
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}

    logger.info("Deleting event %d", event_id)
    return await make_api_request("DELETE", f"/events/{event_id}")


@mcp.tool()
async def list_event_rounds(event_id: int) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """List all rounds for a specific event.

    Args:
        event_id: ID of the event
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}

    result = await make_api_request("GET", f"/events/{event_id}/rounds")
    return _extract(result, "rounds")


# ---------------------------------------------------------------------------
# Tools — Player Management
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_master_roster(
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """List players from the master roster with optional search and pagination.

    Args:
        search: Search term for player names
        limit: Maximum number of players to return (1-100, default 100)
        offset: Number of records to skip for pagination (default 0)
    """
    limit = max(1, min(limit, 100))
    offset = max(0, offset)

    params: Dict[str, Any] = {"limit": limit, "offset": offset}
    if search:
        params["search"] = search

    result = await make_api_request("GET", "/master_roster", params=params)
    return _extract(result, "players")


@mcp.tool()
async def get_player_details(player_id: int) -> Dict[str, Any]:
    """Get detailed information about a specific player.

    Args:
        player_id: The ID of the player to retrieve
    """
    if player_id <= 0:
        return {"error": "player_id must be a positive integer."}

    return await make_api_request("GET", f"/master_roster/{player_id}")


@mcp.tool()
async def register_player_to_event(
    event_id: int,
    external_id: str,
    last_name: str,
    first_name: Optional[str] = None,
    email: Optional[str] = None,
    rounds: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Register a player to an event.

    Args:
        event_id: ID of the event
        external_id: External identifier for the player (required)
        last_name: Player's last name (required)
        first_name: Player's first name
        email: Player's email address
        rounds: List of rounds the player is registered for
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}

    try:
        validated = PlayerRegistration(
            external_id=external_id,
            last_name=last_name,
            first_name=first_name,
            email=email,
            rounds=rounds,
        )
    except Exception as e:
        return {"error": f"Validation error: {e}"}

    payload = validated.model_dump(exclude_none=True)
    logger.info("Registering player %s to event %d", validated.last_name, event_id)
    return await make_api_request("POST", f"/events/{event_id}/roster", json=payload)


@mcp.tool()
async def unregister_player_from_event(
    event_id: int,
    player_id: int,
) -> Dict[str, Any]:
    """Remove a player from an event roster.

    Args:
        event_id: ID of the event
        player_id: ID of the player to remove
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}
    if player_id <= 0:
        return {"error": "player_id must be a positive integer."}

    logger.info("Unregistering player %d from event %d", player_id, event_id)
    return await make_api_request("DELETE", f"/events/{event_id}/roster/{player_id}")


@mcp.tool()
async def get_event_roster(
    event_id: int,
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Get the roster for a specific event.

    Args:
        event_id: ID of the event
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}

    result = await make_api_request("GET", f"/events/{event_id}/roster")
    return _extract(result, "roster")


# ---------------------------------------------------------------------------
# Tools — Scoring & Results
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_tournament_results(
    round_id: int,
    format_type: str = "json",
) -> Any:
    """Get tournament results for a specific round.

    Args:
        round_id: ID of the round
        format_type: Format of results ('json', 'html', or 'xml')
    """
    if round_id <= 0:
        return {"error": "round_id must be a positive integer."}

    valid_formats = ("json", "html", "xml")
    if format_type not in valid_formats:
        return {"error": f"format_type must be one of: {', '.join(valid_formats)}"}

    params = {"format": format_type}
    if format_type in ("html", "xml"):
        return await make_raw_request(
            "GET", f"/rounds/{round_id}/tournament_results", params=params
        )
    return await make_api_request(
        "GET", f"/rounds/{round_id}/tournament_results", params=params
    )


@mcp.tool()
async def get_round_tee_sheet(round_id: int) -> Dict[str, Any]:
    """Get the tee sheet and scores for a specific round.

    Args:
        round_id: ID of the round
    """
    if round_id <= 0:
        return {"error": "round_id must be a positive integer."}

    return await make_api_request("GET", f"/rounds/{round_id}/tee_sheet_and_scores")


# ---------------------------------------------------------------------------
# Tools — Organizational Data
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_seasons() -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """List all seasons."""
    result = await make_api_request("GET", "/seasons")
    return _extract(result, "seasons")


@mcp.tool()
async def list_categories() -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """List all categories with event counts."""
    result = await make_api_request("GET", "/categories")
    return _extract(result, "categories")


# ---------------------------------------------------------------------------
# Entry Point (consolidated)
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for the golf-genius-mcp command."""
    if not API_KEY:
        logger.error("GOLF_GENIUS_API_KEY environment variable is not set.")
        print("Error: GOLF_GENIUS_API_KEY environment variable is not set.")
        print("Please set your Golf Genius API key and try again.")
        sys.exit(1)

    logger.info("Starting Golf Genius MCP Server")
    mcp.run()


if __name__ == "__main__":
    main()
