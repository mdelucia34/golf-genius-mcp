# Troubleshooting Guide - Golf Genius MCP Server

## How to Verify Claude Desktop is Using the Correct MCP Version

### Step 1: Check the Server Version

In Claude Desktop, ask:
```
Use the health_check tool
```

The response should show:
- **version**: "0.2.1" (or current version)
- **status**: "ok"
- **server**: "Golf Genius MCP Server"

If you see an older version number or no version field, the server needs to be restarted.

---

## How to Restart/Reload the MCP Server

### Method 1: Restart Claude Desktop (Recommended)

1. **Quit Claude Desktop completely**
   - Windows: Right-click the system tray icon → Exit
   - Or: File → Exit (Ctrl+Q)

2. **Wait 5 seconds** to ensure all processes are closed

3. **Relaunch Claude Desktop**

4. **Verify the reload** by running `health_check` again

### Method 2: Reload MCP Servers (if available in your Claude version)

Some versions of Claude Desktop may have a "Reload MCP Servers" option:
- Look in Settings → Developer → Reload MCP Servers
- This feature may not be available in all versions

---

## How to Clear Cache

### Claude Desktop Cache Locations

**Windows:**
```
%APPDATA%\Claude\
C:\Users\<YourUsername>\AppData\Roaming\Claude\
```

**Mac:**
```
~/Library/Application Support/Claude/
```

**Linux:**
```
~/.config/Claude/
```

### Steps to Clear Cache:

1. **Quit Claude Desktop completely**

2. **Navigate to the cache directory** (see above)

3. **Delete or rename these folders** (if they exist):
   - `Cache/`
   - `Code Cache/`
   - `GPUCache/`
   - `Service Worker/`

4. **Keep these files** (DO NOT DELETE):
   - `claude_desktop_config.json` (your MCP configuration)
   - Any authentication/credentials files

5. **Restart Claude Desktop**

---

## Common Issues and Solutions

### Issue: Season IDs are corrupted (last 4 digits become zeros)

**Problem:** JavaScript in Claude Desktop cannot safely handle integers larger than `9,007,199,254,740,991` (2^53 - 1). Golf Genius season IDs like `12275544763217790397` exceed this limit, causing precision loss.

**Example:**
- Correct ID: `12275544763217790397`
- Corrupted ID: `12275544763217790000` (last 4 digits become zeros)

**Solution (Fixed in v0.2.2):**
All ID parameters now accept both strings and integers. The Golf Genius API returns IDs as strings, and the MCP server now preserves them as strings throughout.

**What changed:**
- `list_events(season_id: int)` → `list_events(season_id: Union[int, str])`
- All event_id, round_id, player_id, etc. parameters now accept strings
- IDs are automatically converted to strings when passed to the API

**If you're still seeing this issue:**
1. Restart Claude Desktop to load version 0.2.2+
2. Verify version with `health_check` (should show version ≥ 0.2.2)
3. The season IDs from `list_seasons` will work correctly in `list_events`

---

### Issue: "Tool not found" or old version showing

**Solution:**
1. Verify your config file path is correct: [claude_desktop_config.json](C:/Users/mdelu/AppData/Roaming/Claude/claude_desktop_config.json)
2. Check that the `--directory` path points to the correct location:
   ```json
   "args": ["run", "--directory", "C:/Users/mdelu/Claude Code Projects/golf-genius-mcp", "main.py"]
   ```
3. Restart Claude Desktop

### Issue: list_events returns 100 events instead of 6 for 2026

**Problem:** The `season_id` parameter is not being passed.

**Solution:** Make sure to specify the season ID when asking Claude:
```
Use list_events with season_id 12275544763217790397 to show 2026 events
```

Or first get the season ID:
```
Use list_seasons to find the 2026 season ID, then use list_events with that season_id
```

### Issue: API key errors or authentication failures

**Solution:**
1. Check that your API key in `claude_desktop_config.json` is correct
2. Verify the key hasn't expired by testing directly:
   ```bash
   cd "C:\Users\mdelu\Claude Code Projects\golf-genius-mcp"
   uv run python -c "import asyncio; from main import health_check; print(asyncio.run(health_check()))"
   ```

### Issue: MCP server not starting

**Check the logs:**
1. Claude Desktop logs are typically in:
   - Windows: `%APPDATA%\Claude\logs\`
   - Mac: `~/Library/Logs/Claude/`

2. Look for errors related to "golf-genius" MCP server

3. Common issues:
   - Python not found → Install Python 3.10+
   - `uv` not found → Install uv: `pip install uv`
   - Import errors → Run `uv sync` in the project directory

---

## Testing the MCP Server Directly

To verify the MCP server works independently of Claude Desktop:

```bash
cd "C:\Users\mdelu\Claude Code Projects\golf-genius-mcp"

# Test health check
uv run python -c "import asyncio; from main import health_check; print(asyncio.run(health_check()))"

# Test list_seasons
uv run python -c "import asyncio; from main import list_seasons; print(asyncio.run(list_seasons()))"

# Test list_events for 2026
uv run python test_2026_events.py
```

---

## How to Enable Debug Logging

To see detailed API calls and responses:

1. Edit [main.py](main.py) line 23:
   ```python
   # Change from:
   logging.basicConfig(level=logging.INFO, ...)

   # To:
   logging.basicConfig(level=logging.DEBUG, ...)
   ```

2. Restart Claude Desktop

3. Check Claude Desktop logs for detailed output

---

## Getting Help

If issues persist:

1. Run the health check and note the version
2. Check Claude Desktop logs for errors
3. Test the server directly (commands above)
4. Report the issue with:
   - Version number from health_check
   - Error messages from logs
   - Steps to reproduce
