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

- **Tree-based storage** - Folder structure matches Confluence hierarchy
- **Incremental sync** - Only fetches pages modified since last sync (using CQL)
- **Auto full-sync** - Triggers full sync every 3 days to detect deletions
- **Version conflict detection** - Prevents overwriting external edits
- **Title-based filenames** - `Page Title.html` instead of `content.html`
- **Inline HTML formatting** - Pretty indentation without breaking Confluence

## Available Tools

### Local Sync Tools (Primary)

| Tool | Description |
|------|-------------|
| `confluence_sync_space` | Sync a Confluence space to local filesystem |
| `confluence_read_page` | Fetch a single page and save locally |
| `confluence_push_page_update` | Push local HTML changes back to Confluence |

### Attachment Tools

| Tool | Description |
|------|-------------|
| `confluence_download_attachments` | Download all attachments from a page to local storage |
| `confluence_upload_attachment` | Upload a local file as an attachment to a page |

### Direct API Tools

| Tool | Description |
|------|-------------|
| `confluence_search` | Search Confluence content using CQL |
| `confluence_get_spaces` | List available Confluence spaces |
| `confluence_get_comments` | Get comments for a page |
| `confluence_add_comment` | Add a comment to a page |
| `confluence_search_user` | Search Confluence users |

## Quick Start

### 1. Install

```bash
# Using pip
pip install better-confluence-mcp

# Or using uvx
uvx better-confluence-mcp
```

### 2. Configure

Set environment variables:

```bash
# For Confluence Cloud
export CONFLUENCE_URL="https://your-company.atlassian.net/wiki"
export CONFLUENCE_USERNAME="your.email@company.com"
export CONFLUENCE_API_TOKEN="your_api_token"

# For Confluence Server/Data Center
export CONFLUENCE_URL="https://confluence.your-company.com"
export CONFLUENCE_PERSONAL_TOKEN="your_pat_token"
```

### 3. Add to Your AI Assistant

**Claude Code** (`~/.claude.json`):
```json
{
  "mcpServers": {
    "confluence": {
      "command": "uvx",
      "args": ["better-confluence-mcp"],
      "env": {
        "CONFLUENCE_URL": "https://your-company.atlassian.net/wiki",
        "CONFLUENCE_USERNAME": "your.email@company.com",
        "CONFLUENCE_API_TOKEN": "your_api_token"
      }
    }
  }
}
```

**Cursor** (Settings → MCP):
```json
{
  "mcpServers": {
    "confluence": {
      "command": "uvx",
      "args": ["better-confluence-mcp"],
      "env": {
        "CONFLUENCE_URL": "https://your-company.atlassian.net/wiki",
        "CONFLUENCE_USERNAME": "your.email@company.com",
        "CONFLUENCE_API_TOKEN": "your_api_token"
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

## Why "Better"?

This is a fork of [mcp-atlassian](https://github.com/sooperset/mcp-atlassian) optimized for coding agents:

| Original | Better Confluence MCP |
|----------|----------------------|
| Fetches pages via API on demand | Syncs to local files once |
| Page content in context window | Files on filesystem |
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
