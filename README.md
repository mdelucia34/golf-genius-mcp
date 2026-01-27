# Golf Genius MCP Server

An MCP (Model Context Protocol) server that provides tools for interacting with the Golf Genius API v2. This server enables AI assistants to access golf tournament data, manage events, handle player rosters, and retrieve scoring information.

## Features

- **Event Management**: List, create, update, and delete golf events
- **Player Rosters**: Access master roster, register/unregister players, and manage event-specific rosters
- **Tournament Results**: Retrieve scoring data and tournament results in JSON, HTML, or XML
- **Round Management**: Handle tee sheets, pairings, and round data
- **Season & Category Data**: Access organizational structure
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

The application will automatically load the API key from the `.env` file if it exists.

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

## Available Tools

### Health Check
- `health_check` - Verify API connectivity and authentication status

### Event Management
- `list_events` - List golf events with optional filtering and pagination
- `get_event_details` - Get detailed information about a specific event
- `create_event` - Create a new golf event with validated inputs
- `update_event` - Update an existing golf event
- `delete_event` - Delete (archive) a golf event
- `list_event_rounds` - List all rounds for a specific event

### Player Management
- `list_master_roster` - List players from the master roster with search and pagination
- `get_player_details` - Get detailed information about a specific player
- `register_player_to_event` - Register a player to an event with email validation
- `unregister_player_from_event` - Remove a player from an event roster
- `get_event_roster` - Get the roster for a specific event

### Scoring & Results
- `get_tournament_results` - Get tournament results (JSON, HTML, or XML format)
- `get_round_tee_sheet` - Get the tee sheet and scores for a specific round

### Organizational Data
- `list_seasons` - List all seasons
- `list_categories` - List all categories with event counts

## Architecture

```
main.py
├── Configuration & Logging
├── Custom Exceptions (GolfGeniusAPIError, RateLimitError, AuthenticationError, NotFoundError)
├── Pydantic Models (EventCreate, EventUpdate, PlayerRegistration)
├── HTTP Client (shared connection pool, 30s timeout, retry on 429)
├── Tools — Health Check
├── Tools — Event Management (6 tools)
├── Tools — Player Management (5 tools)
├── Tools — Scoring & Results (2 tools)
├── Tools — Organizational Data (2 tools)
└── Entry Point
```

## API Documentation

This server integrates with the Golf Genius API v2. For detailed API documentation, visit: https://www.golfgenius.com/api/v2/docs

## Authentication

All API requests require a valid Golf Genius API key provided via the `GOLF_GENIUS_API_KEY` environment variable. The key is sent as a Bearer token in the Authorization header.

## Error Handling

The server includes structured error handling for:
- **Authentication errors** (401/403) — raised as `AuthenticationError`
- **Not found** (404) — raised as `NotFoundError`
- **Rate limiting** (429) — automatic retry with exponential backoff (up to 3 attempts)
- **Timeouts** — 30-second request timeout, 10-second connect timeout
- **Connection failures** — clear error messages for network issues
- **Input validation** — Pydantic models validate dates (YYYY-MM-DD), emails, and required fields

## Development

### Running Tests

```bash
uv run pytest
```

With coverage:
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

## License

This project is licensed under the MIT License.
