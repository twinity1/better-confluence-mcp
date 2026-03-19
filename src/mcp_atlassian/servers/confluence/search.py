"""Confluence search tool - local file search with sync (HTTP-only)."""

import json
import logging
import re
from pathlib import Path
from typing import Annotated

from fastmcp import Context
from pydantic import Field

from mcp_atlassian.local_storage import (
    get_space_path,
    load_space_metadata,
)
from mcp_atlassian.servers.dependencies import get_confluence_fetcher

from ._server import confluence_mcp, get_space_lock
from .sync import sync_space_impl

logger = logging.getLogger(__name__)

# Maximum matches per page to avoid huge responses
MAX_MATCHES_PER_PAGE = 5
# Maximum total matches across all pages
MAX_TOTAL_MATCHES = 50

# Location weights for relevance scoring
_WEIGHT_TITLE = 100
_WEIGHT_HEADING = 10
_WEIGHT_BODY = 1


def _strip_html_tags(html: str) -> str:
    """Strip HTML tags to get plain text, preserving line breaks."""
    text = re.sub(r"<(?:br|p|div|h[1-6]|li|tr|table)[^>]*>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&nbsp;", " ").replace("&#39;", "'")
    return text


def _extract_metadata_from_html(content: str) -> dict:
    """Extract page metadata from the HTML comment header."""
    metadata = {}
    if content.startswith("<!--"):
        end_comment = content.find("-->")
        if end_comment != -1:
            header = content[:end_comment]
            for field_name in ("Page ID", "Title", "Space", "Version", "URL"):
                match = re.search(rf"^\s*{field_name}:\s*(.+)$", header, re.MULTILINE)
                if match:
                    key = field_name.lower().replace(" ", "_")
                    metadata[key] = match.group(1).strip()
    return metadata


def _detect_heading_lines(html_body: str) -> set[int]:
    """Detect which plain-text line numbers correspond to headings.

    Parses the HTML to find <h1>-<h6> content, then maps those
    text fragments back to line numbers in the stripped plain text.
    """
    heading_texts: list[str] = []
    for m in re.finditer(r"<h[1-6][^>]*>(.*?)</h[1-6]>", html_body, re.IGNORECASE | re.DOTALL):
        inner = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if inner:
            heading_texts.append(inner)

    if not heading_texts:
        return set()

    plain_text = _strip_html_tags(html_body)
    lines = plain_text.splitlines()

    heading_line_nums: set[int] = set()
    for line_num, line in enumerate(lines):
        stripped = line.strip()
        if stripped and any(stripped == ht or stripped.startswith(ht) for ht in heading_texts):
            heading_line_nums.add(line_num)

    return heading_line_nums


def _compile_pattern(query: str, case_sensitive: bool, is_regex: bool) -> re.Pattern:  # type: ignore[type-arg]
    """Compile a search pattern from the query string."""
    flags = 0 if case_sensitive else re.IGNORECASE
    if is_regex:
        return re.compile(query, flags)
    return re.compile(re.escape(query), flags)


def _search_file(
    file_path: Path,
    pattern: re.Pattern,  # type: ignore[type-arg]
    context_lines: int,
    heading_lines: set[int] | None = None,
) -> tuple[list[dict], int]:
    """Search a single HTML file with a compiled pattern.

    Returns (matches_list, relevance_score).
    """
    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception:
        return [], 0

    # Skip the metadata comment header
    body = content
    if content.startswith("<!--"):
        end_comment = content.find("-->")
        if end_comment != -1:
            body = content[end_comment + 3:]

    # Detect heading lines if not provided
    if heading_lines is None:
        heading_lines = _detect_heading_lines(body)

    plain_text = _strip_html_tags(body)
    lines = plain_text.splitlines()

    matches = []
    score = 0

    for line_num, line in enumerate(lines):
        if pattern.search(line):
            # Determine location type for relevance
            is_heading = line_num in heading_lines
            location = "heading" if is_heading else "body"
            score += _WEIGHT_HEADING if is_heading else _WEIGHT_BODY

            # Extract context around the match
            start = max(0, line_num - context_lines)
            end = min(len(lines), line_num + context_lines + 1)

            ctx = []
            for i in range(start, end):
                ctx_line = lines[i].strip()
                if ctx_line:
                    ctx.append({
                        "line": i + 1,
                        "text": ctx_line,
                        "is_match": i == line_num,
                    })

            matches.append({
                "line": line_num + 1,
                "match_text": line.strip(),
                "location": location,
                "context": ctx,
            })

            if len(matches) >= MAX_MATCHES_PER_PAGE:
                break

    return matches, score


def _search_space(
    space_key: str,
    pattern: re.Pattern,  # type: ignore[type-arg]
    context_lines: int,
    title_pattern: re.Pattern | None,  # type: ignore[type-arg]
) -> tuple[list[dict], int, int]:
    """Search all pages in a single space.

    Returns (results, pages_searched, total_matches).
    """
    metadata = load_space_metadata(space_key)
    if not metadata:
        return [], 0, 0

    space_path = get_space_path(space_key)
    if not space_path.exists():
        return [], 0, 0

    results = []
    total_matches = 0
    pages_searched = 0

    for html_file in sorted(space_path.rglob("*.html")):
        pages_searched += 1

        file_content = html_file.read_text(encoding="utf-8")
        file_meta = _extract_metadata_from_html(file_content)
        page_id = file_meta.get("page_id", "")
        title = file_meta.get("title", "")
        index_data = metadata.page_index.get(page_id, {})

        # Title match scoring
        title_score = 0
        title_matched = False
        if title_pattern and title and title_pattern.search(title):
            title_score = _WEIGHT_TITLE
            title_matched = True

        # Body search
        body = file_content
        if file_content.startswith("<!--"):
            end_comment = file_content.find("-->")
            if end_comment != -1:
                body = file_content[end_comment + 3:]

        heading_lines = _detect_heading_lines(body)
        body_matches, body_score = _search_file(
            html_file, pattern, context_lines, heading_lines
        )

        # Skip pages with no matches at all
        if not body_matches and not title_matched:
            continue

        page_score = title_score + body_score

        result_entry = {
            "page_id": page_id,
            "title": title or index_data.get("title", html_file.stem),
            "space_key": space_key,
            "url": file_meta.get("url", index_data.get("url", "")),
            "local_path": str(html_file.relative_to(Path.cwd())),
            "relevance_score": page_score,
            "title_matched": title_matched,
            "match_count": len(body_matches),
            "matches": body_matches,
        }

        results.append(result_entry)
        total_matches += len(body_matches) + (1 if title_matched else 0)

        if total_matches >= MAX_TOTAL_MATCHES:
            break

    # Sort by relevance score descending
    results.sort(key=lambda r: r["relevance_score"], reverse=True)

    return results, pages_searched, total_matches


@confluence_mcp.tool(tags={"confluence", "read", "http_only"})
async def confluence_search(
    ctx: Context,
    space_keys: Annotated[
        str,
        Field(description="Space key(s) to search in - single key or comma-separated list (e.g., 'IT' or 'IT,DEV,TEAM')"),
    ],
    query: Annotated[
        str,
        Field(description="Search query - plain text or regex pattern (if is_regex=true). Searches page titles and body content."),
    ],
    context_lines: Annotated[
        int,
        Field(
            description="Number of lines of context to show around each match (default 3)",
            ge=0,
            le=10,
            default=3,
        ),
    ] = 3,
    case_sensitive: Annotated[
        bool,
        Field(description="Whether the search should be case-sensitive (default false)"),
    ] = False,
    is_regex: Annotated[
        bool,
        Field(description="Treat query as a regex pattern (default false)"),
    ] = False,
) -> str:
    """Search through Confluence pages by title and body content.

    Searches one or more spaces. Each space is synced (incremental) before
    searching to ensure content is up to date.

    ## Features

    - **Title + body search**: matches in page titles are ranked higher
    - **Relevance ranking**: title match (100) > heading match (10) > body match (1)
    - **Regex support**: set is_regex=true to use regex patterns
    - **Context lines**: shows surrounding lines around each match (like grep -C)
    - **Multi-space**: search across multiple spaces in one call

    This tool is only available in HTTP transport mode.

    Args:
        ctx: The FastMCP context.
        space_keys: Comma-separated space keys to search in.
        query: Search text or regex pattern.
        context_lines: Lines of context around each match (0-10, default 3).
        case_sensitive: Case-sensitive search (default false).
        is_regex: Treat query as regex (default false).

    Returns:
        JSON with matching pages ranked by relevance, each with match locations and context.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    # Parse space keys
    keys = [k.strip() for k in space_keys.split(",") if k.strip()]
    if not keys:
        return json.dumps({"error": "No space keys provided"}, indent=2, ensure_ascii=False)

    # Compile pattern once
    try:
        pattern = _compile_pattern(query, case_sensitive, is_regex)
    except re.error as e:
        return json.dumps(
            {"error": f"Invalid regex pattern: {e}"},
            indent=2,
            ensure_ascii=False,
        )

    # Title pattern is always the same as body pattern
    title_pattern = pattern

    # Sync all spaces first
    sync_errors = []
    for space_key in keys:
        logger.info(f"Syncing space {space_key} before search...")
        space_lock = get_space_lock(space_key)
        async with space_lock:
            sync_result = await sync_space_impl(confluence_fetcher, space_key, full_sync=False)
        try:
            sync_data = json.loads(sync_result)
            if "error" in sync_data:
                sync_errors.append({"space_key": space_key, "error": sync_data["error"]})
        except (json.JSONDecodeError, TypeError):
            pass

    # Search all spaces
    all_results = []
    total_pages_searched = 0
    total_matches = 0

    for space_key in keys:
        results, pages_searched, matches = _search_space(
            space_key, pattern, context_lines, title_pattern
        )
        all_results.extend(results)
        total_pages_searched += pages_searched
        total_matches += matches

    # Sort combined results by relevance
    all_results.sort(key=lambda r: r["relevance_score"], reverse=True)

    # Trim to max
    truncated = len(all_results) > MAX_TOTAL_MATCHES
    all_results = all_results[:MAX_TOTAL_MATCHES]

    response: dict = {
        "success": True,
        "space_keys": keys,
        "query": query,
        "is_regex": is_regex,
        "pages_searched": total_pages_searched,
        "pages_matched": len(all_results),
        "total_matches": total_matches,
        "truncated": truncated,
        "results": all_results,
    }

    if sync_errors:
        response["sync_errors"] = sync_errors

    return json.dumps(response, indent=2, ensure_ascii=False)
