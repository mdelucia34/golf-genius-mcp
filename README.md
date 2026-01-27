# Golf Genius MCP Server

An MCP (Model Context Protocol) server that provides tools for interacting with the Golf Genius API v2. This server enables AI assistants to access golf tournament data, manage events, handle player rosters, create pairings, and retrieve scoring information.

## Features

- **Event Management**: List, create, update, and delete golf events
- **Player Rosters**: Master roster lookup, event roster management, member registration
- **Rounds & Scoring**: Create rounds, manage tee sheets, view tournament results
- **Divisions & Courses**: Manage event divisions and view course details
- **Pairings**: Create, update, and delete pairing groups (foursomes)
- **Health Check**: Verify API connectivity and authentication status

## Prerequisites

- Python 3.10 or higher
- A Golf Genius API key (contact Golf Genius support to obtain one)

## Installation

1. Clone this repository
2. Install dependencies:
   ```bash
   uv sync
   ```
3. For development (tests & linting):
   ```bash
   uv sync --extra dev
   ```

## Configuration

Set your Golf Genius API key. You can either:

**Option 1: Environment Variable**
```bash
export GOLF_GENIUS_API_KEY="your_api_key_here"
```

**Option 2: .env File**
Create a `.env` file in the project root:
```
GOLF_GENIUS_API_KEY=your_api_key_here
```

### Claude Desktop Integration

Add the following to your Claude Desktop configuration file (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "golf-genius": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/golf-genius-mcp", "main.py"],
      "env": {
        "GOLF_GENIUS_API_KEY": "your_api_key_here"
      }
    }
  }
}
```

## Usage

Run the MCP server:

```bash
uv run main.py
```

The server will start and listen for MCP protocol messages via stdio.

## API Authentication

The Golf Genius API uses a dual authentication scheme:

- **GET requests**: API key is embedded in the URL path
  ```
  https://www.golfgenius.com/api_v2/{api_key}/seasons
  ```
- **POST/PUT/DELETE requests**: API key is sent as a Bearer token in the Authorization header
  ```
  Authorization: Bearer {api_key}
  ```

The MCP server handles this automatically — you just provide the API key once.

## Available Tools (28 total)

### Health Check
- `health_check` — Verify API connectivity and authentication status

### Organizational Data
- `list_seasons` — List all seasons configured in the customer center
- `list_categories` — List all custom event categories with colors and event counts
- `list_directories` — List all event directories for organization

### Master Roster
- `list_master_roster` — List all club golfers with optional pagination and photos
- `get_master_roster_member` — Look up a specific member by email address
- `get_player_events` — List all events associated with a player

### Event Management
- `list_events` — List events with filtering by season, category, directory, and pagination
- `create_event` — Create a new event or league
- `update_event` — Update event details
- `delete_event` — Soft delete (archive) an event

### Event Roster (Members)
- `get_event_roster` — Get the roster for an event with pagination and photos
- `register_member_to_event` — Add a golfer to an event roster
- `update_member_in_event` — Update member details and round assignments
- `delete_member_from_event` — Remove a member from an event

### Rounds
- `list_event_rounds` — List all rounds for an event
- `create_round` — Create a new round
- `update_round` — Update round details
- `delete_round` — Delete a round
- `get_round_tee_sheet` — Get the tee sheet and scores for a round
- `get_round_tournaments` — Get tournament configurations for a round

### Courses & Divisions
- `get_event_courses` — Get courses with tee details and ratings
- `get_event_divisions` — List external divisions for an event
- `create_division` — Create a division
- `update_division` — Update a division
- `delete_division` — Delete a division

### Pairings
- `create_pairing` — Create a pairing group (foursome) with tee time
- `update_pairing` — Update a pairing group
- `delete_pairing` — Delete a pairing group

## Error Handling

- **Authentication errors** (401/403) — raised as `AuthenticationError`
- **Not found** (404) — raised as `NotFoundError`
- **Rate limiting** (429) — automatic retry with exponential backoff (up to 3 attempts)
- **Timeouts** — 30-second request timeout, 10-second connect timeout
- **Connection failures** — clear error messages for network issues
- **Input validation** — Pydantic models validate dates (YYYY-MM-DD), emails, and required fields

## Development

### Running Tests

```bash
uv run pytest --tb=short -v
```

### Linting

```bash
uv run ruff check .
```

### Contributing

1. Install development dependencies: `uv sync --extra dev`
2. Run tests: `uv run pytest`
3. Run linting: `uv run ruff check .`
4. Follow the existing code style and patterns

## API Documentation

Golf Genius API v2 docs: https://www.golfgenius.com/api/v2/docs

## License

This project is licensed under the MIT License.
