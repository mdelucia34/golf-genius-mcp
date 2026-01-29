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
BASE_URL = "https://www.golfgenius.com/api_v2"
VERSION = "0.2.4"  # MCP Server version - Force string-only IDs to prevent Claude from sending numbers

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


class MemberRegistration(BaseModel):
    """Validated input for member registration to an event."""
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
#
# Golf Genius API auth:
#   GET  requests → API key embedded in URL path: /api_v2/{api_key}/...
#   POST/PUT/DELETE → Bearer token in Authorization header: /api_v2/...
# ---------------------------------------------------------------------------

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    """Return a shared httpx.AsyncClient, creating one if needed."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
        )
    return _client


def _build_url(method: str, endpoint: str) -> str:
    """Build the full URL for a Golf Genius API request.

    GET requests:  https://www.golfgenius.com/api_v2/{api_key}/{endpoint}
    Write requests: https://www.golfgenius.com/api_v2/{endpoint}
    """
    if method.upper() == "GET":
        return f"{BASE_URL}/{API_KEY}/{endpoint.lstrip('/')}"
    return f"{BASE_URL}/{endpoint.lstrip('/')}"


def _write_headers() -> Dict[str, str]:
    """Return headers for write operations (POST/PUT/DELETE)."""
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


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
) -> Any:
    """Make an authenticated request to the Golf Genius API.

    Auth routing:
    - GET requests: API key is embedded in the URL path.
    - POST/PUT/DELETE: API key sent as Bearer token in Authorization header.

    Features:
    - Shared connection pool via httpx.AsyncClient
    - 30-second request timeout (10-second connect)
    - Automatic retry (up to 3 attempts) on rate-limit (429) responses
    - Structured error responses for all failure modes
    """
    client = _get_client()
    url = _build_url(method, endpoint)

    # Add auth headers for write operations
    if method.upper() != "GET":
        headers = kwargs.pop("headers", {})
        headers.update(_write_headers())
        kwargs["headers"] = headers

    try:
        logger.info("API %s %s", method, url)
        response = await client.request(method, url, **kwargs)

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
    url = _build_url(method, endpoint)
    try:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.text
    except Exception as e:
        logger.exception("Raw request error on %s %s", method, endpoint)
        return f"Error fetching response: {str(e)}"


# ---------------------------------------------------------------------------
# Helper: safe result extraction
# ---------------------------------------------------------------------------

def _sanitize_ids(data: Any) -> Any:
    """Convert all large integer IDs to strings to prevent JavaScript precision loss.

    JavaScript cannot safely represent integers larger than 2^53 - 1 (9,007,199,254,740,991).
    Golf Genius IDs often exceed this limit, so we convert all numeric IDs to strings.
    Also uses id_str when available as the API provides both formats.
    """
    if isinstance(data, dict):
        sanitized = {}
        for key, value in data.items():
            # If there's an id_str field, use it to replace the numeric id
            if key == "id" and "id_str" in data:
                sanitized[key] = str(data["id_str"])
            # Convert numeric id fields to strings
            elif key == "id" and isinstance(value, (int, float)):
                sanitized[key] = str(int(value))
            # Recursively sanitize nested structures
            else:
                sanitized[key] = _sanitize_ids(value)
        return sanitized
    elif isinstance(data, list):
        return [_sanitize_ids(item) for item in data]
    else:
        return data


def _extract(result: Any, key: str) -> Any:
    """Extract a list from the API response, or return the result as-is.

    The Golf Genius API may return:
    - A dict with a nested key:  {"seasons": [...]}  → extract the list
    - A plain list directly:     [...]               → return as-is
    - An error dict:             {"error": "..."}    → return as-is

    All IDs are sanitized to strings to prevent JavaScript precision loss.
    """
    if isinstance(result, list):
        return _sanitize_ids(result)
    if isinstance(result, dict):
        if "error" in result:
            return result
        extracted = result.get(key, result)
        return _sanitize_ids(extracted)
    return result


# ---------------------------------------------------------------------------
# Tools — Health Check
# ---------------------------------------------------------------------------

@mcp.tool()
async def health_check() -> Dict[str, Any]:
    """Check API connectivity and authentication status.

    Returns the connection status, authentication validity, and server version.
    """
    logger.info("Running health check")
    try:
        result = await make_api_request("GET", "/seasons")
        if "error" in result:
            return {"status": "error", "message": result["error"], "version": VERSION}
        return {
            "status": "ok",
            "message": "Connected and authenticated successfully.",
            "version": VERSION,
            "server": "Golf Genius MCP Server"
        }
    except AuthenticationError:
        return {"status": "auth_error", "message": "API key is invalid or expired.", "version": VERSION}
    except Exception as e:
        return {"status": "error", "message": str(e), "version": VERSION}


# ---------------------------------------------------------------------------
# Tools — Organizational Data
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_seasons() -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """List all seasons configured in the customer center."""
    result = await make_api_request("GET", "/seasons")
    return _extract(result, "seasons")


@mcp.tool()
async def list_categories() -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """List all custom event categories with colors and event counts."""
    result = await make_api_request("GET", "/categories")
    return _extract(result, "categories")


@mcp.tool()
async def list_directories() -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """List all event directories for organization."""
    result = await make_api_request("GET", "/directories")
    return _extract(result, "directories")


# ---------------------------------------------------------------------------
# Tools — Master Roster
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_master_roster(
    page: Optional[int] = None,
    photo: Optional[bool] = None,
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """List all club golfers from the master roster.

    Args:
        page: Page number for pagination
        photo: Include player photos in response
    """
    params: Dict[str, Any] = {}
    if page is not None:
        params["page"] = max(1, page)
    if photo is not None:
        params["photo"] = str(photo).lower()

    result = await make_api_request("GET", "/master_roster?", params=params)
    return _extract(result, "players")


@mcp.tool()
async def get_master_roster_member(email: str) -> Dict[str, Any]:
    """Get a specific member from the master roster by email address.

    Args:
        email: Email address of the member to look up
    """
    if not email or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return {"error": "A valid email address is required."}

    result = await make_api_request("GET", f"/master_roster_member/{email}")
    return _sanitize_ids(result)


@mcp.tool()
async def get_player_events(player_id: str) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """List all events associated with a specific player.

    Args:
        player_id: The ID of the player (MUST be string to preserve precision for large IDs)
    """
    try:
        pid = int(player_id)
        if pid <= 0:
            return {"error": "player_id must be a positive integer."}
    except (ValueError, TypeError):
        return {"error": "player_id must be a valid integer."}

    result = await make_api_request("GET", f"/players/{player_id}")
    return _extract(result, "events")


# ---------------------------------------------------------------------------
# Tools — Event Management
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_events(
    season_id: Optional[str] = None,
    category_id: Optional[str] = None,
    directory_id: Optional[str] = None,
    archived: Optional[bool] = None,
    page: Optional[int] = None,
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """List golf events with optional filtering and pagination.

    Args:
        season_id: Filter by season ID (MUST be string to preserve precision for large IDs)
        category_id: Filter by category ID (MUST be string to preserve precision for large IDs)
        directory_id: Filter by directory ID (MUST be string to preserve precision for large IDs)
        archived: Include archived events
        page: Page number for pagination
    """
    params: Dict[str, Any] = {}
    if season_id is not None:
        params["season"] = str(season_id)
    if category_id is not None:
        params["category"] = str(category_id)
    if directory_id is not None:
        params["directory"] = str(directory_id)
    if archived is not None:
        params["archived"] = str(archived).lower()
    if page is not None:
        params["page"] = max(1, page)

    result = await make_api_request("GET", "/events?", params=params)
    return _extract(result, "events")


# @mcp.tool()  # Disabled for performance - read-only mode
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
        event_type: Type of event — 'event' or 'league' (default: "event")
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


# @mcp.tool()  # Disabled for performance - read-only mode
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


# @mcp.tool()  # Disabled for performance - read-only mode
async def delete_event(event_id: int) -> Dict[str, Any]:
    """Delete (archive) a golf event.

    Args:
        event_id: ID of the event to delete
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}

    logger.info("Deleting event %d", event_id)
    return await make_api_request("DELETE", f"/events/{event_id}")


# ---------------------------------------------------------------------------
# Tools — Event Roster (Members)
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_event_roster(
    event_id: str,
    page: Optional[int] = None,
    photo: Optional[bool] = None,
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Get the roster of golfers for a specific event.

    Args:
        event_id: ID of the event (MUST be string to preserve precision for large IDs)
        page: Page number for pagination
        photo: Include player photos in response
    """
    try:
        eid = int(event_id)
        if eid <= 0:
            return {"error": "event_id must be a positive integer."}
    except (ValueError, TypeError):
        return {"error": "event_id must be a valid integer."}

    params: Dict[str, Any] = {}
    if page is not None:
        params["page"] = max(1, page)
    if photo is not None:
        params["photo"] = str(photo).lower()

    result = await make_api_request("GET", f"/events/{event_id}/roster?", params=params)
    return _extract(result, "roster")


# @mcp.tool()  # Disabled for performance - read-only mode
async def register_member_to_event(
    event_id: int,
    external_id: str,
    last_name: str,
    first_name: Optional[str] = None,
    email: Optional[str] = None,
    rounds: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Register a golfer to an event roster.

    Args:
        event_id: ID of the event
        external_id: External identifier for the member (required)
        last_name: Member's last name (required)
        first_name: Member's first name
        email: Member's email address
        rounds: List of round assignments for the member
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}

    try:
        validated = MemberRegistration(
            external_id=external_id,
            last_name=last_name,
            first_name=first_name,
            email=email,
            rounds=rounds,
        )
    except Exception as e:
        return {"error": f"Validation error: {e}"}

    payload = validated.model_dump(exclude_none=True)
    logger.info("Registering member %s to event %d", validated.last_name, event_id)
    return await make_api_request("POST", f"/events/{event_id}/members", json=payload)


# @mcp.tool()  # Disabled for performance - read-only mode
async def update_member_in_event(
    event_id: int,
    member_id: int,
    last_name: Optional[str] = None,
    first_name: Optional[str] = None,
    email: Optional[str] = None,
    rounds: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Update a member's details in an event roster.

    Args:
        event_id: ID of the event
        member_id: ID of the member to update
        last_name: Updated last name
        first_name: Updated first name
        email: Updated email address
        rounds: Updated round assignments
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}
    if member_id <= 0:
        return {"error": "member_id must be a positive integer."}

    payload: Dict[str, Any] = {}
    if last_name:
        payload["last_name"] = last_name
    if first_name:
        payload["first_name"] = first_name
    if email:
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return {"error": "Invalid email format."}
        payload["email"] = email
    if rounds is not None:
        payload["rounds"] = rounds

    if not payload:
        return {"error": "No fields provided to update."}

    logger.info("Updating member %d in event %d", member_id, event_id)
    return await make_api_request("PUT", f"/events/{event_id}/members/{member_id}", json=payload)


# @mcp.tool()  # Disabled for performance - read-only mode
async def delete_member_from_event(
    event_id: int,
    member_id: int,
) -> Dict[str, Any]:
    """Remove a member from an event roster.

    Args:
        event_id: ID of the event
        member_id: ID of the member to remove
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}
    if member_id <= 0:
        return {"error": "member_id must be a positive integer."}

    logger.info("Removing member %d from event %d", member_id, event_id)
    return await make_api_request("DELETE", f"/events/{event_id}/members/{member_id}")


# ---------------------------------------------------------------------------
# Tools — Rounds
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_event_rounds(event_id: str) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """List all rounds for a specific event.

    Args:
        event_id: ID of the event (MUST be string to preserve precision for large IDs)
    """
    try:
        eid = int(event_id)
        if eid <= 0:
            return {"error": "event_id must be a positive integer."}
    except (ValueError, TypeError):
        return {"error": "event_id must be a valid integer."}

    result = await make_api_request("GET", f"/events/{event_id}/rounds")
    return _extract(result, "rounds")


# @mcp.tool()  # Disabled for performance - read-only mode
async def create_round(
    event_id: int,
    name: Optional[str] = None,
    date: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a new round for an event.

    Args:
        event_id: ID of the event
        name: Name of the round
        date: Date of the round in YYYY-MM-DD format
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}

    if date and not DATE_PATTERN.match(date):
        return {"error": "Date must be in YYYY-MM-DD format."}

    payload: Dict[str, Any] = {}
    if name:
        payload["name"] = name
    if date:
        payload["date"] = date

    logger.info("Creating round for event %d", event_id)
    return await make_api_request("POST", f"/events/{event_id}/rounds", json=payload)


# @mcp.tool()  # Disabled for performance - read-only mode
async def update_round(
    event_id: int,
    round_id: int,
    name: Optional[str] = None,
    date: Optional[str] = None,
) -> Dict[str, Any]:
    """Update an existing round.

    Args:
        event_id: ID of the event
        round_id: ID of the round to update
        name: New name for the round
        date: New date in YYYY-MM-DD format
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}
    if round_id <= 0:
        return {"error": "round_id must be a positive integer."}

    if date and not DATE_PATTERN.match(date):
        return {"error": "Date must be in YYYY-MM-DD format."}

    payload: Dict[str, Any] = {}
    if name:
        payload["name"] = name
    if date:
        payload["date"] = date

    if not payload:
        return {"error": "No fields provided to update."}

    logger.info("Updating round %d in event %d", round_id, event_id)
    return await make_api_request("PUT", f"/events/{event_id}/rounds/{round_id}", json=payload)


# @mcp.tool()  # Disabled for performance - read-only mode
async def delete_round(
    event_id: int,
    round_id: int,
) -> Dict[str, Any]:
    """Delete a round from an event.

    Args:
        event_id: ID of the event
        round_id: ID of the round to delete
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}
    if round_id <= 0:
        return {"error": "round_id must be a positive integer."}

    logger.info("Deleting round %d from event %d", round_id, event_id)
    return await make_api_request("DELETE", f"/events/{event_id}/rounds/{round_id}")


@mcp.tool()
async def get_round_tee_sheet(
    event_id: str,
    round_id: str,
    include_all_custom_fields: Optional[bool] = None,
) -> Dict[str, Any]:
    """Get the tee sheet and scores for a specific round.

    Args:
        event_id: ID of the event (MUST be string to preserve precision for large IDs)
        round_id: ID of the round (MUST be string to preserve precision for large IDs)
        include_all_custom_fields: Include all custom fields in the response
    """
    try:
        eid = int(event_id)
        if eid <= 0:
            return {"error": "event_id must be a positive integer."}
    except (ValueError, TypeError):
        return {"error": "event_id must be a valid integer."}

    try:
        rid = int(round_id)
        if rid <= 0:
            return {"error": "round_id must be a positive integer."}
    except (ValueError, TypeError):
        return {"error": "round_id must be a valid integer."}

    params: Dict[str, Any] = {}
    if include_all_custom_fields is not None:
        params["include_all_custom_fields"] = str(include_all_custom_fields).lower()

    result = await make_api_request(
        "GET", f"/events/{event_id}/rounds/{round_id}/tee_sheet?", params=params
    )
    return _sanitize_ids(result)


@mcp.tool()
async def get_round_tournaments(
    event_id: str,
    round_id: str,
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Get tournament configurations for a specific round.

    Args:
        event_id: ID of the event (MUST be string to preserve precision for large IDs)
        round_id: ID of the round (MUST be string to preserve precision for large IDs)
    """
    try:
        eid = int(event_id)
        if eid <= 0:
            return {"error": "event_id must be a positive integer."}
    except (ValueError, TypeError):
        return {"error": "event_id must be a valid integer."}

    try:
        rid = int(round_id)
        if rid <= 0:
            return {"error": "round_id must be a positive integer."}
    except (ValueError, TypeError):
        return {"error": "round_id must be a valid integer."}

    result = await make_api_request(
        "GET", f"/events/{event_id}/rounds/{round_id}/tournaments"
    )
    return _extract(result, "tournaments")


@mcp.tool()
async def get_tournament_results(
    event_id: str,
    round_id: str,
    tournament_id: str,
    format: str = "html",
) -> Union[str, Dict[str, Any]]:
    """Get tournament results for a specific tournament in a round.

    Args:
        event_id: ID of the event (MUST be string to preserve precision for large IDs)
        round_id: ID of the round (MUST be string to preserve precision for large IDs)
        tournament_id: ID of the tournament (MUST be string to preserve precision for large IDs)
        format: Response format - 'html' (strongly recommended) or 'json'
    """
    try:
        eid = int(event_id)
        if eid <= 0:
            return {"error": "event_id must be a positive integer."}
    except (ValueError, TypeError):
        return {"error": "event_id must be a valid integer."}

    try:
        rid = int(round_id)
        if rid <= 0:
            return {"error": "round_id must be a positive integer."}
    except (ValueError, TypeError):
        return {"error": "round_id must be a valid integer."}

    try:
        tid = int(tournament_id)
        if tid <= 0:
            return {"error": "tournament_id must be a positive integer."}
    except (ValueError, TypeError):
        return {"error": "tournament_id must be a valid integer."}

    if format not in ("html", "json"):
        return {"error": "format must be either 'html' or 'json'."}

    endpoint = f"/events/{event_id}/rounds/{round_id}/tournaments/{tournament_id}.{format}"

    if format == "html":
        logger.info("Getting HTML tournament results for tournament %d", tournament_id)
        return await make_raw_request("GET", endpoint)
    else:
        logger.info("Getting JSON tournament results for tournament %d", tournament_id)
        result = await make_api_request("GET", endpoint)
        return _sanitize_ids(result)


# ---------------------------------------------------------------------------
# Tools — Courses & Divisions
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_event_courses(event_id: str) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Get the list of selected courses for an event, including tee details and ratings.

    Args:
        event_id: ID of the event (MUST be string to preserve precision for large IDs)
    """
    try:
        eid = int(event_id)
        if eid <= 0:
            return {"error": "event_id must be a positive integer."}
    except (ValueError, TypeError):
        return {"error": "event_id must be a valid integer."}

    result = await make_api_request("GET", f"/events/{event_id}/courses")
    return _extract(result, "courses")


@mcp.tool()
async def get_event_divisions(event_id: str) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """List external divisions for an event.

    Args:
        event_id: ID of the event (MUST be string to preserve precision for large IDs)
    """
    try:
        eid = int(event_id)
        if eid <= 0:
            return {"error": "event_id must be a positive integer."}
    except (ValueError, TypeError):
        return {"error": "event_id must be a valid integer."}

    result = await make_api_request("GET", f"/events/{event_id}/divisions")
    return _extract(result, "divisions")


# @mcp.tool()  # Disabled for performance - read-only mode
async def create_division(
    event_id: int,
    name: str,
) -> Dict[str, Any]:
    """Create an external division for an event.

    Args:
        event_id: ID of the event
        name: Name of the division
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}
    if not name.strip():
        return {"error": "Division name is required."}

    logger.info("Creating division '%s' for event %d", name, event_id)
    return await make_api_request("POST", f"/events/{event_id}/divisions", json={"name": name})


# @mcp.tool()  # Disabled for performance - read-only mode
async def update_division(
    event_id: int,
    division_id: int,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """Update an external division.

    Args:
        event_id: ID of the event
        division_id: ID of the division to update
        name: New name for the division
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}
    if division_id <= 0:
        return {"error": "division_id must be a positive integer."}

    payload: Dict[str, Any] = {}
    if name:
        payload["name"] = name

    if not payload:
        return {"error": "No fields provided to update."}

    logger.info("Updating division %d in event %d", division_id, event_id)
    return await make_api_request("PUT", f"/events/{event_id}/divisions/{division_id}", json=payload)


# @mcp.tool()  # Disabled for performance - read-only mode
async def delete_division(
    event_id: int,
    division_id: int,
) -> Dict[str, Any]:
    """Delete an external division from an event.

    Args:
        event_id: ID of the event
        division_id: ID of the division to delete
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}
    if division_id <= 0:
        return {"error": "division_id must be a positive integer."}

    logger.info("Deleting division %d from event %d", division_id, event_id)
    return await make_api_request("DELETE", f"/events/{event_id}/divisions/{division_id}")


# ---------------------------------------------------------------------------
# Tools — Pairings
# ---------------------------------------------------------------------------

# @mcp.tool()  # Disabled for performance - read-only mode
async def create_pairing(
    event_id: int,
    round_id: int,
    players: List[Dict[str, Any]],
    tee_time: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a pairing group (foursome) for a round.

    Args:
        event_id: ID of the event
        round_id: ID of the round
        players: List of player dicts with player details
        tee_time: Tee time for the group (e.g. "08:00 AM")
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}
    if round_id <= 0:
        return {"error": "round_id must be a positive integer."}
    if not players:
        return {"error": "At least one player is required."}

    payload: Dict[str, Any] = {"players": players}
    if tee_time:
        payload["tee_time"] = tee_time

    logger.info("Creating pairing for event %d round %d", event_id, round_id)
    return await make_api_request(
        "POST", f"/events/{event_id}/rounds/{round_id}/pairing_groups", json=payload
    )


# @mcp.tool()  # Disabled for performance - read-only mode
async def update_pairing(
    event_id: int,
    round_id: int,
    pairing_group_id: int,
    players: Optional[List[Dict[str, Any]]] = None,
    tee_time: Optional[str] = None,
) -> Dict[str, Any]:
    """Update a pairing group in a round.

    Args:
        event_id: ID of the event
        round_id: ID of the round
        pairing_group_id: ID of the pairing group to update
        players: Updated list of player dicts
        tee_time: Updated tee time
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}
    if round_id <= 0:
        return {"error": "round_id must be a positive integer."}
    if pairing_group_id <= 0:
        return {"error": "pairing_group_id must be a positive integer."}

    payload: Dict[str, Any] = {}
    if players is not None:
        payload["players"] = players
    if tee_time is not None:
        payload["tee_time"] = tee_time

    if not payload:
        return {"error": "No fields provided to update."}

    logger.info("Updating pairing %d in event %d round %d", pairing_group_id, event_id, round_id)
    return await make_api_request(
        "PUT",
        f"/events/{event_id}/rounds/{round_id}/pairing_groups/{pairing_group_id}",
        json=payload,
    )


# @mcp.tool()  # Disabled for performance - read-only mode
async def delete_pairing(
    event_id: int,
    round_id: int,
    pairing_group_id: int,
) -> Dict[str, Any]:
    """Delete a pairing group from a round's tee sheet.

    Args:
        event_id: ID of the event
        round_id: ID of the round
        pairing_group_id: ID of the pairing group to delete
    """
    if event_id <= 0:
        return {"error": "event_id must be a positive integer."}
    if round_id <= 0:
        return {"error": "round_id must be a positive integer."}
    if pairing_group_id <= 0:
        return {"error": "pairing_group_id must be a positive integer."}

    logger.info("Deleting pairing %d from event %d round %d", pairing_group_id, event_id, round_id)
    return await make_api_request(
        "DELETE",
        f"/events/{event_id}/rounds/{round_id}/pairing_groups/{pairing_group_id}",
    )


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
