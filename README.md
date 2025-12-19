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
                                                │   │   └── Page B.html
                        push_page_update        │   └── page_id_C/
                        ◄───────────────        │       └── Page C.html
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

## Available Tools

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

## Quick Start

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
        "MERMAID_ENABLED": "true"
      }
    }
  }
}
```

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

| Environment Variable | Description |
|---------------------|-------------|
| `CONFLUENCE_URL` | Base URL of your Confluence instance |
| `CONFLUENCE_USERNAME` | Username/email (Cloud) |
| `CONFLUENCE_API_TOKEN` | API token (Cloud) |
| `CONFLUENCE_PERSONAL_TOKEN` | Personal Access Token (Server/DC) |
| `CONFLUENCE_SSL_VERIFY` | Verify SSL certificates (default: true) |
| `READ_ONLY_MODE` | Disable write operations (default: false) |
| `AUTO_SYNC_ON_STARTUP` | Auto-sync locally cached spaces on startup (default: true) |
| `AUTO_ADD_GITIGNORE` | Auto-add storage directory to .gitignore (default: true) |
| `MERMAID_ENABLED` | Enable mermaid diagram rendering (default: false). Requires `playwright install chromium` |

## Why "Better"?

This is a fork of [mcp-atlassian](https://github.com/sooperset/mcp-atlassian) optimized for coding agents:

| Original | Better Confluence MCP |
|----------|----------------------|
| Fetches pages via API on demand | Syncs to local files once |
| Page content in context window | Files on filesystem |
| Must regenerate entire page for any change | Surgical edits - fewer tokens burned |
| Complex tool parameters | Simple read/edit/push workflow |
| General purpose | Optimized for agents |

## Compatibility

| Product | Deployment | Status |
|---------|------------|--------|
| Confluence | Cloud | ✅ Supported |
| Confluence | Server/Data Center 6.0+ | ✅ Supported |

## Development

```bash
# Clone the repo
git clone https://github.com/twinity1/better-confluence-mcp
cd better-confluence-mcp

# Install dependencies
uv sync

# Run tests
uv run pytest tests/

# Run the server locally
uv run better-confluence-mcp
```

## License

MIT License - see [LICENSE](LICENSE) file.

Based on [mcp-atlassian](https://github.com/sooperset/mcp-atlassian) by sooperset.
