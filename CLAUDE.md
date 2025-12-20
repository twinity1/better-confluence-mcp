# Project Guidelines

## IMPORTANT: Git & Publishing Rules

**NEVER execute these commands without explicit user permission:**
- `git commit`
- `git tag`
- `git push`
- `uv build`
- `uv publish`

Always show the user what you're about to do and wait for their explicit "go ahead" before executing.

## Development Workflow

**After modifying MCP server code**: You must wait for the user to restart Claude Code before calling the updated MCP tools. The MCP server runs as a subprocess and code changes are not reflected until restart.

## Project Overview

This is **Better Confluence MCP** - an MCP server for Confluence with local filesystem caching. It's designed specifically for coding agents like Claude Code, Cursor, and similar AI assistants.

### Key Concept

Coding agents work best with files. This MCP syncs Confluence spaces to local `.html` files, allowing agents to:
1. **Sync** spaces to local filesystem
2. **Edit** HTML files using native file tools
3. **Push** changes back to Confluence

## Available Tools

| Tool | Description |
|------|-------------|
| `confluence_sync_space` | Sync a Confluence space to local filesystem |
| `confluence_read_page` | Fetch a single page and save locally |
| `confluence_push_page_update` | Push local HTML changes back to Confluence |

## Authentication

The server supports two authentication methods:
- **Basic Auth** (Cloud): `CONFLUENCE_USERNAME` + `CONFLUENCE_API_TOKEN`
- **PAT** (Server/Data Center): `CONFLUENCE_PERSONAL_TOKEN`

## Transport

The server only supports **stdio** transport.

## Testing MCP Servers

### Using JSON-RPC from Command Line

MCP servers communicate via JSON-RPC over stdio. You can test directly:

```bash
# Start the server and send requests via stdin
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | uv run better-confluence-mcp

# Or use a heredoc for multiple requests
uv run better-confluence-mcp << 'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
EOF
```

### Python Test Script

```python
#!/usr/bin/env python3
"""Test MCP server via JSON-RPC."""
import subprocess
import json

def send_request(proc, method, params=None, req_id=1):
    request = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
        "params": params or {}
    }
    proc.stdin.write(json.dumps(request) + "\n")
    proc.stdin.flush()
    response = proc.stdout.readline()
    return json.loads(response)

# Start MCP server
proc = subprocess.Popen(
    ["uv", "run", "better-confluence-mcp"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True
)

# Initialize
init_response = send_request(proc, "initialize", {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "test", "version": "1.0"}
})
print("Init:", init_response)

# List tools
tools_response = send_request(proc, "tools/list", {}, req_id=2)
print("Tools:", json.dumps(tools_response, indent=2))

# Call a tool (example: sync space)
call_response = send_request(proc, "tools/call", {
    "name": "confluence_sync_space",
    "arguments": {"space_key": "DEV"}
}, req_id=3)
print("Result:", json.dumps(call_response, indent=2))

proc.terminate()
```

### Testing Best Practices
- Verify basic connectivity first with `initialize` and `tools/list`
- Test edge cases: invalid inputs, missing arguments
- Monitor error handling and error responses

## Running Tests

```bash
# Run all tests
uv run pytest tests/

# Run with coverage
uv run pytest tests/ --cov=src/mcp_atlassian

# Run specific test file
uv run pytest tests/unit/servers/test_confluence_server.py -v
```

## Local File Structure

After syncing, files are stored in `.better-confluence-mcp/`:

```
.better-confluence-mcp/
└── SPACE_KEY/
    ├── _metadata.json          # Space metadata with page tree
    └── page_id/
        ├── Page Title.html     # Page content
        └── child_page_id/
            └── Child Page.html
```

Each HTML file includes metadata in a comment header with page ID, version, and sync timestamp.

## Publishing

To publish a new version to PyPI:

```bash
# 1. Commit your changes
git add . && git commit -m "your commit message"

# 2. Create a version tag (e.g., v0.1.10)
git tag v0.1.10

# 3. Push commit and tag
git push && git push --tags

# 4. Build the package
uv build

# 5. Publish to PyPI
uv publish
```

The version is derived from git tags via `setuptools-scm` (configured in `pyproject.toml`).
