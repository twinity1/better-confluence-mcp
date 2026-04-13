# Better Confluence MCP

MCP server for Confluence with **local filesystem caching** - designed specifically for coding agents like Claude Code, Cursor, Windsurf, and similar AI assistants.

## Why This Approach?

**Coding agents work best with files.** They excel at reading, editing, and managing files on your filesystem. This MCP leverages that strength:

1. **Sync** Confluence spaces to local `.html` files
2. **Edit** files directly using the agent's native file tools
3. **Push** changes back to Confluence

No complex API calls mid-conversation. No context window bloat from fetching pages repeatedly. Just simple file operations that agents already do well.

## How It Works

```
Confluence Space                    Local Filesystem
    ├── Page A          sync_space      .better-confluence-mcp/
    │   ├── Page B      ─────────►          └── SPACE_KEY/
    │   └── Page C                              ├── page_id_A/
    └── Page D                                  │   ├── Page A.html
                                                │   ├── page_id_B/
                        push_page_update        │   │   └── Page B.html
                        ◄───────────────        │   └── page_id_C/
                                                │       └── Page C.html
                                                └── page_id_D/
                                                    └── Page D.html
```

The folder structure mirrors the Confluence page hierarchy. Each page is stored as an HTML file named after the page title.

## Features

- **Fast & token-efficient editing** - Agents make surgical edits to local files instead of regenerating entire pages, saving tokens
- **Large page support** - Edit pages of any size without context window limits
- **Mermaid diagrams** - Render mermaid diagrams to PNG and embed them in pages
- **Tree-based storage** - Folder structure matches Confluence hierarchy
- **Incremental sync** - Only fetches pages modified since last sync (using CQL)
- **Version conflict detection** - Prevents overwriting external edits
- **Two transport modes** - stdio for local agents, streamable HTTP for remote/shared deployments

## Transport Modes

The server supports two transport modes:

| Mode | Use case | Auth | Write tools |
|------|----------|------|-------------|
| **stdio** (default) | Local agents (Claude Code, Cursor) | Environment variables | All tools available |
| **streamable-http** | Remote/shared deployment (Docker) | Per-request HTTP headers | Read-only + search |

### stdio Mode (Default)

The standard mode for local AI assistants. Authentication is configured via environment variables and all tools are available.

```bash
better-confluence-mcp
```

### Streamable HTTP Mode (Read-Only)

Runs as a read-only HTTP server for remote access. Designed for shared deployments where multiple users connect to the same server. **All write operations are disabled** - no creating, updating, or uploading pages.

```bash
better-confluence-mcp --transport streamable-http --host 0.0.0.0 --port 8000
```

Key differences from stdio mode:

- **Auth via HTTP headers** - credentials are sent per-request, not stored in env vars
- **Read-only** - all write tools are disabled (no create, update, upload)
- **Search tool** - `confluence_confluence_search` is enabled (only available in HTTP mode)
- **Per-user isolation** - each user's synced data is stored in a separate directory

## Available Tools

### stdio Mode

| Tool | Description |
|------|-------------|
| `confluence_sync_space` | Sync a Confluence space to local filesystem |
| `confluence_read_page` | Read page(s) - supports bulk via comma-separated IDs |
| `confluence_create_page` | Create page(s) - supports bulk via comma-separated titles |
| `confluence_push_page_update` | Push changes to page(s) - supports bulk via comma-separated IDs |
| `confluence_download_attachments` | Download all attachments from a page |
| `confluence_upload_attachment` | Upload a file as attachment |
| `confluence_create_mermaid_diagram` | Create mermaid diagram (.png) and upload to page |
| `confluence_get_spaces` | List available spaces |
| `confluence_get_comments` | Get comments for a page |
| `confluence_add_comment` | Add a comment to a page |
| `confluence_search_user` | Search Confluence users |

### HTTP Mode (Read-Only + Search)

| Tool | Description |
|------|-------------|
| `confluence_confluence_search` | **Full-text search** with relevance ranking (HTTP-only) |
| `confluence_sync_space` | Sync a Confluence space |
| `confluence_read_page` | Read page(s) - returns **full content** in response |
| `confluence_download_attachments` | Download attachments from a page |
| `confluence_get_spaces` | List available spaces |
| `confluence_get_comments` | Get comments for a page |
| `confluence_search_user` | Search Confluence users |

> In HTTP mode, `read_page` includes the full HTML content in the JSON response (since the client can't access local files on the server).

## Search Tool (HTTP Mode)

The `confluence_confluence_search` tool provides local full-text search through synced Confluence pages. It syncs the space before every search to ensure content is up to date.

### Features

- **Title + body search** - matches in page titles are ranked higher
- **Relevance ranking** - title match (100) > heading match (10) > body match (1)
- **Regex support** - set `is_regex: true` to use regex patterns
- **Context lines** - shows surrounding lines around each match (like `grep -C`)
- **Multi-space** - search across multiple spaces in one call

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `space_keys` | string | *required* | Space key(s) to search - single or comma-separated (e.g., `"IT"` or `"IT,DEV,TEAM"`) |
| `query` | string | *required* | Search text or regex pattern |
| `context_lines` | int | 3 | Lines of context around each match (0-10) |
| `case_sensitive` | bool | false | Case-sensitive matching |
| `is_regex` | bool | false | Treat query as a regex pattern |

### Examples

**Plain text search:**
```json
{"space_keys": "IT", "query": "kubernetes"}
```

**Regex search for IP addresses:**
```json
{"space_keys": "IT,DEV", "query": "\\b\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\b", "is_regex": true}
```

**Case-sensitive search with more context:**
```json
{"space_keys": "IT", "query": "PostgreSQL", "case_sensitive": true, "context_lines": 5}
```

### Response

Results are sorted by relevance score:

```json
{
  "success": true,
  "space_keys": ["IT"],
  "pages_searched": 251,
  "pages_matched": 4,
  "results": [
    {
      "page_id": "1506803713",
      "title": "NetBird dokumentace",
      "space_key": "IT",
      "url": "https://...",
      "relevance_score": 105,
      "title_matched": true,
      "match_count": 3,
      "matches": [
        {
          "line": 4,
          "match_text": "Netbird.io VPN allows access to...",
          "location": "body",
          "context": [
            {"line": 3, "text": "...", "is_match": false},
            {"line": 4, "text": "Netbird.io VPN allows...", "is_match": true},
            {"line": 5, "text": "...", "is_match": false}
          ]
        }
      ]
    }
  ]
}
```

## Quick Start

### stdio Mode (Local Agent)

Add to **Claude Code** (`~/.claude.json`) or **Cursor** (Settings → MCP):

```json
{
  "mcpServers": {
    "confluence": {
      "command": "uvx",
      "args": ["better-confluence-mcp"],
      "env": {
        "CONFLUENCE_URL": "https://your-company.atlassian.net/wiki",
        "CONFLUENCE_USERNAME": "your.email@company.com",
        "CONFLUENCE_API_TOKEN": "your_api_token",
      }
    }
  }
}
```

### HTTP Mode (Docker)

```bash
docker run -p 8000:8000 \
  -e CONFLUENCE_URL=https://your-company.atlassian.net/wiki \
  twinity1/better-confluence-mcp
```

Or build from source:

```bash
docker build -t better-confluence-mcp .
```

The only required environment variable is `CONFLUENCE_URL`. User credentials are provided per-request via HTTP headers.

#### Authentication Headers

Two formats are supported:

**Basic Auth:**
```
Authorization: Basic base64(username:api_token)
```

**Custom headers:**
```
X-Confluence-Username: your.email@company.com
X-Confluence-Token: your_api_token
```

Requests without valid authentication receive a `401` response.

## Per-User Isolation (HTTP Mode)

In HTTP mode, each user's synced data is stored in a separate directory namespaced by their username. This ensures:

- **User A** cannot see pages synced by **User B**
- Confluence API calls use the requesting user's credentials, so access controls are enforced
- If a user doesn't have access to a space, the sync fails with a 403 from Confluence

```
.better-confluence-mcp/
└── _users/
    ├── a1b2c3d4_alice/
    │   └── IT/
    │       ├── _metadata.json
    │       └── ...
    └── e5f6a7b8_bob/
        └── DEV/
            └── ...
```

In stdio mode, storage uses the shared root directory (no user namespace) since there's only one user.

## Usage Examples

### Sync a space

```
"Sync the DEV space from Confluence"
```

The agent calls `confluence_sync_space` with `space_key: "DEV"`. Pages are saved to `.better-confluence-mcp/DEV/`.

### Edit a page

```
"Update the Getting Started guide in our docs"
```

The agent:
1. Reads the HTML file from `.better-confluence-mcp/...`
2. Edits the content using its file editing tools
3. Calls `confluence_push_page_update` to push changes

### Working with attachments

```
"Download all attachments from the Getting Started page"
```

The agent calls `confluence_download_attachments` with the page ID. Attachments are saved to `.better-confluence-mcp/SPACE/page_id/attachments/`.

```
"Upload this screenshot to the deployment docs"
```

The agent calls `confluence_upload_attachment` with the page ID and file path.

### Version conflict handling

If someone else edited the page in Confluence:

```json
{
  "error": "Page was modified externally",
  "message": "The page was edited in Confluence (version 5) since your last sync (version 4). The page has been re-synced. Please review the changes and try again.",
  "local_version": 4,
  "confluence_version": 5
}
```

The page is automatically re-synced with the latest content.

## File Structure

```
.better-confluence-mcp/
└── SPACE_KEY/
    ├── _metadata.json          # Space metadata with page tree
    └── page_id/
        ├── Page Title.html     # Page content
        ├── attachments/        # Downloaded attachments (optional)
        │   ├── screenshot.png
        │   └── document.pdf
        └── child_page_id/
            └── Child Page.html
```

Each HTML file includes metadata in a comment header:

```html
<!--
  Page ID: 123456
  Title: Getting Started
  Space: DEV
  Version: 3
  URL: https://your-company.atlassian.net/wiki/spaces/DEV/pages/123456
  Synced: 2025-01-15T10:30:00+00:00
-->
<h1>Getting Started</h1>
<p>Welcome to our documentation...</p>
```

## Configuration Options

### Environment Variables

| Environment Variable | Description |
|---------------------|-------------|
| `CONFLUENCE_URL` | Base URL of your Confluence instance |
| `CONFLUENCE_USERNAME` | Username/email (Cloud) - stdio mode only |
| `CONFLUENCE_API_TOKEN` | API token (Cloud) - stdio mode only |
| `CONFLUENCE_PERSONAL_TOKEN` | Personal Access Token (Server/DC) - stdio mode only |
| `CONFLUENCE_SSL_VERIFY` | Verify SSL certificates (default: true) |
| `CONFLUENCE_SPACES_FILTER` | Comma-separated space keys to filter searches |
| `READ_ONLY_MODE` | Disable write operations (default: false; always true in HTTP mode) |
| `AUTO_SYNC_ON_STARTUP` | Auto-sync locally cached spaces on startup (default: true) |
| `AUTO_ADD_GITIGNORE` | Auto-add storage directory to .gitignore (default: true) |

### CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--transport` | `stdio` | Transport mode: `stdio` or `streamable-http` |
| `--host` | `0.0.0.0` | Host to bind (HTTP mode only) |
| `--port` | `8000` | Port to listen on (HTTP mode only) |
| `--read-only` | off | Disable write operations |
| `--enabled-tools` | all | Comma-separated list of tools to enable |
| `--confluence-url` | - | Override `CONFLUENCE_URL` env var |
| `--confluence-spaces-filter` | - | Override `CONFLUENCE_SPACES_FILTER` env var |
| `-v` / `-vv` | - | Increase logging verbosity |

## Why "Better"?

This is a fork of [mcp-atlassian](https://github.com/sooperset/mcp-atlassian) optimized for coding agents:

| Original | Better Confluence MCP |
|----------|----------------------|
| Fetches pages via API on demand | Syncs to local files once |
| Page content in context window | Files on filesystem |
| Must regenerate entire page for any change | Surgical edits - fewer tokens burned |
| Complex tool parameters | Simple read/edit/push workflow |
| General purpose | Optimized for agents |
| stdio only | stdio + streamable HTTP with Docker |

## Compatibility

| Product | Deployment | Status |
|---------|------------|--------|
| Confluence | Cloud | Supported |
| Confluence | Server/Data Center 6.0+ | Supported |

## Development

```bash
# Clone the repo
git clone https://github.com/twinity1/better-confluence-mcp
cd better-confluence-mcp

# Install dependencies
uv sync

# Run tests
uv run pytest tests/

# Run the server locally (stdio)
uv run better-confluence-mcp

# Run the server locally (HTTP)
uv run better-confluence-mcp --transport streamable-http --port 8000
```

## License

MIT License - see [LICENSE](LICENSE) file.

Based on [mcp-atlassian](https://github.com/sooperset/mcp-atlassian) by sooperset.
