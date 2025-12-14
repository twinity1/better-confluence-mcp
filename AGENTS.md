# AGENTS

> **Audience**: LLM-driven engineering agents

This file provides guidance for autonomous coding agents working inside the **Better Confluence MCP** repository.

---

## What This MCP Does

This MCP is designed for coding agents. It syncs Confluence spaces to local HTML files, so agents can:
1. **Sync** spaces to `.better-confluence-mcp/` directory
2. **Edit** HTML files using native file tools
3. **Push** changes back to Confluence

No complex API calls mid-conversation. Just simple file operations.

---

## Repository map

| Path | Purpose |
| --- | --- |
| `src/mcp_atlassian/` | Library source code (Python ≥ 3.10) |
| `  ├─ confluence/` | Confluence client, mixins, and operations |
| `  ├─ models/` | Pydantic data models for API responses |
| `  ├─ servers/` | FastMCP server implementations |
| `  ├─ local_storage.py` | Local filesystem caching logic |
| `  └─ utils/` | Shared utilities (auth, logging, SSL) |
| `tests/` | Pytest test suite with fixtures |

---

## Available MCP Tools

| Tool | Description |
|------|-------------|
| `confluence_sync_space` | Sync a space to local filesystem (incremental by default) |
| `confluence_read_page` | Fetch a single page and save locally |
| `confluence_push_page_update` | Push local HTML changes back to Confluence |

---

## Mandatory dev workflow

```bash
uv sync --frozen --all-extras --dev  # install dependencies
pre-commit install                    # setup hooks
pre-commit run --all-files           # Ruff + Prettier + Pyright
uv run pytest                        # run full test suite
```

*Tests must pass* and *lint/typing must be clean* before committing.

---

## Testing the MCP Server

### Using MCP Inspector (Recommended)

```bash
npx @modelcontextprotocol/inspector uv --directory . run better-confluence-mcp
```

### Using JSON-RPC from Command Line

MCP servers communicate via JSON-RPC over stdio. Test directly:

```bash
# Initialize the server
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | uv run better-confluence-mcp

# Multiple requests with heredoc
uv run better-confluence-mcp << 'EOF'
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
EOF
```

### JSON-RPC Methods

| Method | Description |
|--------|-------------|
| `initialize` | Initialize the MCP session |
| `tools/list` | List available tools |
| `tools/call` | Call a tool with arguments |

### Tool Call Example

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "tools/call",
  "params": {
    "name": "confluence_sync_space",
    "arguments": {"space_key": "DEV"}
  }
}
```

---

## Core MCP patterns

**Tool naming**: `confluence_{action}` (e.g., `confluence_sync_space`)

**Architecture**:
- **Mixins**: Functionality split into focused mixins extending base clients
- **Models**: All data structures extend `ApiModel` base class
- **Local Storage**: Tree-based filesystem cache in `.better-confluence-mcp/`

---

## Development rules

1. **Package management**: ONLY use `uv`, NEVER `pip`
2. **Branching**: NEVER work on `main`, always create feature branches
3. **Type safety**: All functions require type hints
4. **Testing**: New features need tests, bug fixes need regression tests
5. **Commits**: Use trailers for attribution, never mention tools/AI

---

## Code conventions

* **Language**: Python ≥ 3.10
* **Line length**: 88 characters maximum
* **Imports**: Absolute imports, sorted by ruff
* **Naming**: `snake_case` functions, `PascalCase` classes
* **Docstrings**: Google-style for all public APIs
* **Error handling**: Specific exceptions only

---

## Development guidelines

1. Do what has been asked; nothing more, nothing less
2. NEVER create files unless absolutely necessary
3. Always prefer editing existing files
4. Follow established patterns and maintain consistency
5. Run `pre-commit run --all-files` before committing
6. Fix bugs immediately when reported

---

## Quick reference

```bash
# Running the server
uv run better-confluence-mcp         # Start server
uv run better-confluence-mcp -v      # Verbose mode

# Testing with Inspector
npx @modelcontextprotocol/inspector uv --directory . run better-confluence-mcp

# Git workflow
git checkout -b feature/description   # New feature
git checkout -b fix/issue-description # Bug fix
git commit --trailer "Reported-by:<name>"      # Attribution
git commit --trailer "Github-Issue:#<number>"  # Issue reference
```
