# Changelog

All notable changes to the Golf Genius MCP Server will be documented in this file.

## [0.2.4] - 2026-01-28

### Fixed
- **Force string-only IDs in type signatures**: Changed all ID parameters from `Union[int, str]` to `str` only. This forces Claude Desktop to use string IDs and prevents it from choosing to send numeric IDs (which would be corrupted by JavaScript before reaching the MCP server).
  - Affected parameters: `season_id`, `category_id`, `directory_id`, `event_id`, `round_id`, `player_id`, `tournament_id`
  - Type hints now explicitly require strings: `season_id: Optional[str]` instead of `season_id: Optional[Union[int, str]]`
  - This ensures Claude Desktop cannot send corrupted numeric IDs

## [0.2.3] - 2026-01-28

### Fixed
- **JavaScript precision issue for IDs in response data**: All numeric IDs in API responses are now converted to strings before being sent to Claude Desktop. This fixes the issue where event IDs like `12300956988786918579` were being corrupted to `12300956988786920000` when returned in the response JSON.
  - Added `_sanitize_ids()` function to recursively convert all numeric IDs to strings
  - Uses `id_str` field from API when available (Golf Genius provides both formats)
  - Applied to all response data via updated `_extract()` helper
  - Also sanitized direct API responses in `get_master_roster_member`, `get_round_tee_sheet`, and `get_tournament_results`

## [0.2.2] - 2026-01-28

### Fixed
- **JavaScript precision issue for large IDs**: Season IDs and other large integer IDs were being corrupted when passed from Claude Desktop due to JavaScript's 53-bit integer limit. All ID parameters now accept both `int` and `str` types and are converted to strings when making API requests.
  - Affected IDs: `season_id`, `category_id`, `directory_id`, `event_id`, `round_id`, `player_id`, `tournament_id`, `member_id`, `division_id`, `pairing_group_id`
  - Example: Season ID `12275544763217790397` was being corrupted to `12275544763217790000`
  - All enabled tools now properly handle large IDs as strings

### Added
- Version tracking in `health_check` tool to help diagnose which version of the MCP server is running
- Comprehensive troubleshooting guide ([TROUBLESHOOTING.md](TROUBLESHOOTING.md))

## [0.2.1] - 2026-01-28

### Added
- Server version information in health check responses

## [0.2.0] - Previous Release

### Added
- Initial release with 15 read-only tools
- Health check, seasons, events, rounds, roster management
- Comprehensive test suite
- Read-only mode for performance optimization
