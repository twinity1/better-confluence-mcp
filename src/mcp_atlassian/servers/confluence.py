"""Confluence FastMCP server instance and tool definitions."""

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastmcp import Context, FastMCP
from pydantic import Field

# Per-space locks to prevent concurrent sync operations on the same space
_space_locks: dict[str, asyncio.Lock] = {}


def _get_space_lock(space_key: str) -> asyncio.Lock:
    """Get or create a lock for a specific space.

    This prevents race conditions when multiple tools try to sync
    the same space concurrently (e.g., parallel read_page calls).
    """
    if space_key not in _space_locks:
        _space_locks[space_key] = asyncio.Lock()
    return _space_locks[space_key]

from mcp_atlassian.local_storage import (
    SpaceMetadata,
    check_and_cleanup_moved_page,
    cleanup_deleted_pages,
    ensure_attachments_folder,
    get_page_info,
    load_space_metadata,
    merge_into_metadata,
    reformat_space_html,
    remove_pages_from_metadata,
    save_page_html,
    save_space_metadata,
)
from mcp_atlassian.servers.dependencies import get_confluence_fetcher
from mcp_atlassian.utils.decorators import check_write_access

logger = logging.getLogger(__name__)

confluence_mcp = FastMCP(
    name="Confluence MCP Service",
    description="Provides tools for syncing and editing Confluence spaces locally.",
)


# =============================================================================
# TOOLS - Local Space Sync & Update
# =============================================================================


# Auto full sync interval (3 days)
AUTO_FULL_SYNC_DAYS = 3


@confluence_mcp.tool(tags={"confluence", "sync"})
async def sync_space(
    ctx: Context,
    space_key: Annotated[
        str,
        Field(
            description="The key of the Confluence space to sync (e.g., 'DEV', 'TEAM', 'IT')"
        ),
    ],
    full_sync: Annotated[
        bool,
        Field(
            description="Force full sync instead of incremental. Default is False (incremental).",
            default=False,
        ),
    ] = False,
) -> str:
    """Sync a Confluence space to local filesystem.

    ## How This MCP Works

    This MCP is designed around a simple principle: **coding agents work best
    with files**. Instead of fetching pages via API on every request, this MCP:

    1. **Syncs** Confluence spaces to local HTML files (this tool)
    2. **Agent edits** files directly using standard file tools (Read, Edit, Write)
    3. **Pushes** changes back to Confluence via push_page_update

    This approach keeps the context window clean and lets agents use their
    native file editing capabilities.

    ## Sync Behavior

    Downloads pages from the specified space and stores them as formatted HTML
    in a tree structure under .better-confluence-mcp/SPACE_KEY/.

    By default, performs incremental sync - only fetches pages modified since
    the last sync. Use full_sync=True to re-download everything.

    Auto full sync: If the last sync was more than 3 days ago, a full sync
    is automatically triggered to detect deleted pages.

    The folder structure mirrors the page hierarchy:
    - .better-confluence-mcp/SPACE_KEY/page_id/Page Title.html
    - .better-confluence-mcp/SPACE_KEY/page_id/child_page_id/Child Title.html

    IMPORTANT:
    - Do NOT call multiple sync_space or other sync tools in parallel.
    - Call them SEQUENTIALLY (one at a time).
    - Sync can take up to 15 minutes for large spaces - be patient.

    Args:
        ctx: The FastMCP context.
        space_key: The key of the space to sync.
        full_sync: If True, sync all pages. If False, only sync pages modified since last sync.

    Returns:
        JSON string with sync results.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    logger.info(f"Starting sync for space: {space_key} (full_sync={full_sync})")

    # Acquire lock for this space to prevent concurrent syncs
    space_lock = _get_space_lock(space_key)
    async with space_lock:
        return await _sync_space_impl(confluence_fetcher, space_key, full_sync)


async def _sync_space_impl(confluence_fetcher, space_key: str, full_sync: bool) -> str:
    """Internal implementation of space sync (called while holding lock)."""
    try:
        # Load existing metadata to get last sync time
        existing_metadata = load_space_metadata(space_key)
        last_sync_time = None
        auto_full_sync_triggered = False

        if not full_sync and existing_metadata:
            # Check if last sync was more than AUTO_FULL_SYNC_DAYS ago
            try:
                last_dt = datetime.fromisoformat(
                    existing_metadata.last_synced.replace("Z", "+00:00")
                )
                days_since_sync = (datetime.now(timezone.utc) - last_dt).days
                if days_since_sync >= AUTO_FULL_SYNC_DAYS:
                    logger.info(
                        f"Last sync was {days_since_sync} days ago, triggering auto full sync"
                    )
                    full_sync = True
                    auto_full_sync_triggered = True
            except (ValueError, AttributeError) as e:
                logger.warning(f"Could not parse last sync time: {e}")

        if not full_sync and existing_metadata:
            last_sync_time = existing_metadata.last_synced
            logger.info(f"Incremental sync from: {last_sync_time}")

        saved_pages: list[dict] = []
        moved_pages: list[str] = []
        errors: list[str] = []
        space_name = space_key
        all_page_ids: set[str] = set()

        # Use optimized bulk fetch for full sync (much faster!)
        if not last_sync_time:
            logger.info(f"Full sync: using optimized bulk fetch for space {space_key}")
            raw_pages = confluence_fetcher.get_all_space_pages_with_content(space_key)

            if not raw_pages and not existing_metadata:
                return json.dumps(
                    {"error": f"No pages found in space '{space_key}' or space does not exist."},
                    indent=2,
                    ensure_ascii=False,
                )

            # Process bulk results
            for page in raw_pages:
                page_id = page.get("id")
                all_page_ids.add(page_id)
                try:
                    title = page.get("title", "")
                    body = page.get("body", {}).get("storage", {}).get("value", "")
                    version = page.get("version", {}).get("number")
                    ancestors = page.get("ancestors", [])
                    ancestor_ids = [a.get("id") for a in ancestors]

                    # Build URL
                    page_links = page.get("_links", {})
                    web_ui = page_links.get("webui", "")
                    base_url = confluence_fetcher.config.url.rstrip("/")
                    url = f"{base_url}{web_ui}" if web_ui else ""

                    # Get space name from first page
                    if space_name == space_key:
                        page_space = page.get("space", {})
                        space_name = page_space.get("name", space_key)

                    # Check if page has moved
                    if check_and_cleanup_moved_page(
                        space_key, page_id, ancestor_ids, existing_metadata
                    ):
                        moved_pages.append(page_id)

                    # Save the page
                    file_path = save_page_html(
                        space_key=space_key,
                        page_id=page_id,
                        title=title,
                        html_content=body,
                        version=version,
                        url=url,
                        ancestors=ancestor_ids,
                    )

                    saved_pages.append({
                        "page_id": page_id,
                        "title": title,
                        "version": version,
                        "url": url,
                        "path": file_path,
                        "ancestors": ancestor_ids,
                        "last_synced": datetime.now(timezone.utc).isoformat(),
                    })

                    logger.debug(f"Saved page: {title} ({page_id})")

                except Exception as e:
                    error_msg = f"Failed to sync page {page_id}: {e}"
                    logger.error(error_msg)
                    errors.append(error_msg)

        else:
            # Incremental sync: use CQL to find modified pages, then fetch individually
            cql_base = f'type=page AND space.key="{space_key}"'
            try:
                last_dt = datetime.fromisoformat(last_sync_time.replace("Z", "+00:00"))
                last_sync_date_str = last_dt.strftime("%Y-%m-%d %H:%M")
                cql_query = f'{cql_base} AND lastModified >= "{last_sync_date_str}"'
            except ValueError:
                logger.warning(f"Could not parse last sync time: {last_sync_time}")
                cql_query = cql_base

            logger.info(f"Incremental sync using CQL: {cql_query}")
            search_results = confluence_fetcher.search_all(cql_query)

            if not search_results:
                return json.dumps(
                    {
                        "success": True,
                        "space_key": space_key,
                        "message": "No pages modified since last sync.",
                        "total_pages_in_cache": existing_metadata.total_pages if existing_metadata else 0,
                        "last_synced": existing_metadata.last_synced if existing_metadata else None,
                    },
                    indent=2,
                    ensure_ascii=False,
                )

            # Get space name
            if search_results and search_results[0].space:
                space_name = search_results[0].space.name or space_key

            # Process modified pages (need individual fetch for content)
            for search_page in search_results:
                page_id = search_page.id
                all_page_ids.add(page_id)
                try:
                    # Get full page content with expand (single API call per page)
                    full_page = confluence_fetcher.get_page_content(
                        page_id, convert_to_markdown=False
                    )
                    ancestors = confluence_fetcher.get_page_ancestors(page_id)
                    ancestor_ids = [a.id for a in ancestors]

                    if check_and_cleanup_moved_page(
                        space_key, page_id, ancestor_ids, existing_metadata
                    ):
                        moved_pages.append(page_id)

                    html_content = full_page.content or ""
                    version_num = full_page.version.number if full_page.version else None
                    file_path = save_page_html(
                        space_key=space_key,
                        page_id=page_id,
                        title=full_page.title,
                        html_content=html_content,
                        version=version_num,
                        url=full_page.url,
                        ancestors=ancestor_ids,
                    )

                    saved_pages.append({
                        "page_id": page_id,
                        "title": full_page.title,
                        "version": version_num,
                        "url": full_page.url,
                        "path": file_path,
                        "ancestors": ancestor_ids,
                        "last_synced": datetime.now(timezone.utc).isoformat(),
                    })

                    logger.debug(f"Saved page: {full_page.title} ({page_id})")

                except Exception as e:
                    error_msg = f"Failed to sync page {page_id}: {e}"
                    logger.error(error_msg)
                    errors.append(error_msg)

        # For full sync, cleanup pages that were deleted from Confluence
        deleted_pages: list[str] = []
        if full_sync and existing_metadata and all_page_ids:
            deleted_pages = cleanup_deleted_pages(
                space_key, all_page_ids, existing_metadata
            )

        # Merge into metadata
        new_metadata = merge_into_metadata(
            existing=existing_metadata,
            new_pages=saved_pages,
            space_key=space_key,
            space_name=space_name,
        )

        # Remove deleted pages from metadata
        if deleted_pages:
            new_metadata = remove_pages_from_metadata(new_metadata, deleted_pages)

        save_space_metadata(new_metadata)

        # Limit displayed pages to 50
        max_display = 50
        display_pages = [
            {"page_id": p["page_id"], "title": p["title"], "path": p["path"]}
            for p in saved_pages[:max_display]
        ]

        # Determine sync type for response
        if auto_full_sync_triggered:
            sync_type = "auto_full"
        elif full_sync or not existing_metadata:
            sync_type = "full"
        else:
            sync_type = "incremental"

        result = {
            "success": True,
            "space_key": space_key,
            "space_name": space_name,
            "sync_type": sync_type,
            "pages_synced": len(saved_pages),
            "total_pages_in_cache": new_metadata.total_pages,
            "last_synced": new_metadata.last_synced,
            "storage_path": f".better-confluence-mcp/{space_key}/",
            "synced_pages": display_pages,
        }

        if auto_full_sync_triggered:
            result["auto_full_sync_reason"] = (
                f"Last sync was more than {AUTO_FULL_SYNC_DAYS} days ago"
            )

        if len(saved_pages) > max_display:
            result["synced_pages_truncated"] = True
            result["synced_pages_message"] = f"Showing first {max_display} of {len(saved_pages)} synced pages"

        if moved_pages:
            result["pages_moved"] = len(moved_pages)
            result["moved_page_ids"] = moved_pages

        if deleted_pages:
            result["pages_deleted"] = len(deleted_pages)
            result["deleted_page_ids"] = deleted_pages

        if errors:
            result["errors"] = errors

        logger.info(
            f"Sync complete for space {space_key}: "
            f"{len(saved_pages)} synced, {len(moved_pages)} moved, {len(deleted_pages)} deleted"
        )
        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Sync failed for space {space_key}: {e}")
        return json.dumps(
            {"error": f"Failed to sync space '{space_key}': {str(e)}"},
            indent=2,
            ensure_ascii=False,
        )


@confluence_mcp.tool(tags={"confluence", "local"})
async def reformat_local_html(
    ctx: Context,
    space_key: Annotated[
        str,
        Field(description="The Confluence space key to reformat (e.g., 'DEV', 'IT')"),
    ],
) -> str:
    """Reformat all local HTML files in a space by re-applying prettification.

    This tool re-processes all locally cached HTML files to apply the latest
    formatting rules (e.g., spacing fixes around inline tags like <strong>).

    Use this after updating the MCP when new HTML formatting rules are added,
    instead of re-syncing from Confluence.

    Args:
        ctx: The FastMCP context.
        space_key: The key of the space to reformat.

    Returns:
        JSON string with reformat results.
    """
    result = reformat_space_html(space_key)
    return json.dumps(result, indent=2, ensure_ascii=False)


@confluence_mcp.tool(tags={"confluence", "read"})
async def read_page(
    ctx: Context,
    page_ids: Annotated[
        str,
        Field(description="Page ID(s) to read - single ID or comma-separated list (e.g., '123' or '123,456,789')"),
    ],
) -> str:
    """Read one or more Confluence pages by syncing their space(s) to local filesystem.

    ## How It Works

    1. **Fetches pages from Confluence** to determine their spaces
    2. **Syncs each space** to .better-confluence-mcp/<SPACE_KEY>/
    3. **Returns local paths** to the HTML files

    Supports bulk operations - pass multiple page IDs separated by commas.
    Pages are grouped by space and each space is synced once.

    ## Sync Behavior

    The sync is incremental by default - only pages modified since the last sync
    are downloaded. A full sync is triggered automatically every 3 days.

    After syncing, use standard file tools to read/edit the HTML files, then
    call push_page_update to push changes back to Confluence.

    ## Finding Pages by ID

    To find a page file by its ID, use glob: `.better-confluence-mcp/**/PAGE_ID/*.html`

    IMPORTANT:
    - Do NOT call multiple read_page or other sync tools in parallel.
    - Call them SEQUENTIALLY (one at a time).
    - Sync can take up to 15 minutes for large spaces - be patient.

    Args:
        ctx: The FastMCP context.
        page_ids: Single page ID or comma-separated list of page IDs.

    Returns:
        JSON with page metadata and local paths. For multiple pages, returns array of results.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    # Parse page IDs (comma-separated)
    id_list = [pid.strip() for pid in page_ids.split(",") if pid.strip()]

    if not id_list:
        return json.dumps({"error": "No page IDs provided"}, indent=2, ensure_ascii=False)

    # If single page, use original logic for backward compatibility
    if len(id_list) == 1:
        page_id = id_list[0]
        try:
            # First, try to get the page to find its space (before acquiring lock)
            target_page = None
            page_exists_in_confluence = True
            space_key = None
            space_name = None

            try:
                target_page = confluence_fetcher.get_page_content(
                    page_id, convert_to_markdown=False
                )
                if target_page:
                    space_key = target_page.space.key if target_page.space else None
                    space_name = target_page.space.name if target_page.space else space_key
            except Exception as e:
                error_str = str(e)
                if "404" in error_str or "not found" in error_str.lower():
                    page_exists_in_confluence = False
                else:
                    raise

            # If page not found in Confluence, check local storage to find the space
            if not target_page or not space_key:
                page_exists_in_confluence = False
                local_page_info = get_page_info(page_id)
                if local_page_info:
                    space_key = local_page_info.get("space_key")
                    logger.info(f"Page {page_id} not in Confluence, but found locally in space {space_key}")
                else:
                    return json.dumps(
                        {"error": f"Page '{page_id}' does not exist in Confluence or local storage."},
                        indent=2,
                        ensure_ascii=False,
                    )

            logger.info(f"Syncing space '{space_key}' to read page {page_id}")

            # Acquire lock for this space to prevent concurrent syncs
            space_lock = _get_space_lock(space_key)
            async with space_lock:
                return await _read_page_sync_impl(
                    confluence_fetcher, page_id, space_key, space_name, page_exists_in_confluence
                )
        except Exception as e:
            error_str = str(e)
            if "404" in error_str or "not found" in error_str.lower():
                return json.dumps(
                    {"error": f"Page '{page_id}' does not exist in Confluence."},
                    indent=2,
                    ensure_ascii=False,
                )
            logger.error(f"Failed to read page {page_id}: {e}")
            return json.dumps(
                {"error": f"Failed to read page: {error_str}"},
                indent=2,
                ensure_ascii=False,
            )

    # Multiple pages - group by space and sync each space once
    results = []
    pages_by_space: dict[str, list[dict]] = {}  # space_key -> [{page_id, exists_in_confluence}]

    # First pass: determine space for each page
    for page_id in id_list:
        try:
            target_page = confluence_fetcher.get_page_content(page_id, convert_to_markdown=False)
            if target_page and target_page.space:
                space_key = target_page.space.key
                if space_key not in pages_by_space:
                    pages_by_space[space_key] = []
                pages_by_space[space_key].append({
                    "page_id": page_id,
                    "exists": True,
                    "space_name": target_page.space.name,
                })
        except Exception as e:
            # Try local storage
            local_info = get_page_info(page_id)
            if local_info:
                space_key = local_info.get("space_key")
                if space_key not in pages_by_space:
                    pages_by_space[space_key] = []
                pages_by_space[space_key].append({
                    "page_id": page_id,
                    "exists": False,
                    "space_name": space_key,
                })
            else:
                results.append({
                    "page_id": page_id,
                    "error": f"Page not found in Confluence or local storage",
                })

    # Second pass: sync each space and collect results
    for space_key, pages in pages_by_space.items():
        space_name = pages[0].get("space_name", space_key)
        space_lock = _get_space_lock(space_key)
        async with space_lock:
            # Sync the space once
            await _sync_space_impl(confluence_fetcher, space_key, full_sync=False)

        # Get results for each page in this space
        new_metadata = load_space_metadata(space_key)
        for page_info in pages:
            page_id = page_info["page_id"]
            page_data = new_metadata.page_index.get(page_id) if new_metadata else None
            if page_data:
                results.append({
                    "success": True,
                    "page_id": page_id,
                    "title": page_data.get("title"),
                    "space_key": space_key,
                    "version": page_data.get("version"),
                    "url": page_data.get("url"),
                    "local_path": page_data.get("path"),
                    "absolute_path": str(Path.cwd() / page_data["path"]) if page_data.get("path") else None,
                })
            else:
                results.append({
                    "page_id": page_id,
                    "error": "Page not found after sync",
                })

    return json.dumps({"pages": results, "total": len(results)}, indent=2, ensure_ascii=False)


async def _read_page_sync_impl(
    confluence_fetcher,
    page_id: str,
    space_key: str,
    space_name: str | None,
    page_exists_in_confluence: bool,
) -> str:
    """Internal implementation of read_page sync (called while holding lock)."""
    # Load existing metadata to check for incremental sync
    existing_metadata = load_space_metadata(space_key)
    full_sync = False

    # Check if we need full sync (more than AUTO_FULL_SYNC_DAYS old)
    if existing_metadata:
        try:
            last_dt = datetime.fromisoformat(
                existing_metadata.last_synced.replace("Z", "+00:00")
            )
            days_since_sync = (datetime.now(timezone.utc) - last_dt).days
            if days_since_sync >= AUTO_FULL_SYNC_DAYS:
                full_sync = True
                logger.info(f"Last sync was {days_since_sync} days ago, triggering full sync")
        except (ValueError, AttributeError):
            pass

    # Build CQL query for the space
    cql_base = f'type=page AND space.key="{space_key}"'

    if not full_sync and existing_metadata:
        try:
            last_dt = datetime.fromisoformat(
                existing_metadata.last_synced.replace("Z", "+00:00")
            )
            last_sync_date_str = last_dt.strftime("%Y-%m-%d %H:%M")
            cql_query = f'{cql_base} AND lastModified >= "{last_sync_date_str}"'
        except ValueError:
            cql_query = cql_base
    else:
        cql_query = cql_base

    # Search for ALL pages in the space with pagination
    search_results = confluence_fetcher.search_all(cql_query)

    # Process pages
    saved_pages: list[dict] = []
    target_page_data: dict | None = None

    for search_page in search_results or []:
        try:
            full_page = confluence_fetcher.get_page_content(
                search_page.id, convert_to_markdown=False
            )
            ancestors = confluence_fetcher.get_page_ancestors(search_page.id)
            ancestor_ids = [a.id for a in ancestors]

            # Check for moved pages
            check_and_cleanup_moved_page(
                space_key, search_page.id, ancestor_ids, existing_metadata
            )

            html_content = full_page.content or ""
            version_num = full_page.version.number if full_page.version else None
            file_path = save_page_html(
                space_key=space_key,
                page_id=search_page.id,
                title=full_page.title,
                html_content=html_content,
                version=version_num,
                url=full_page.url,
                ancestors=ancestor_ids,
            )

            page_data = {
                "page_id": search_page.id,
                "title": full_page.title,
                "version": version_num,
                "url": full_page.url,
                "path": file_path,
                "ancestors": ancestor_ids,
                "last_synced": datetime.now(timezone.utc).isoformat(),
            }
            saved_pages.append(page_data)

            # Track the target page
            if search_page.id == page_id:
                target_page_data = page_data

        except Exception as e:
            logger.debug(f"Failed to sync page {search_page.id}: {e}")

    # Cleanup deleted pages on full sync
    deleted_pages: list[str] = []
    if full_sync and existing_metadata and search_results:
        confluence_page_ids = {p.id for p in search_results}
        deleted_pages = cleanup_deleted_pages(
            space_key, confluence_page_ids, existing_metadata
        )

    # Merge into metadata
    new_metadata = merge_into_metadata(
        existing=existing_metadata,
        new_pages=saved_pages,
        space_key=space_key,
        space_name=space_name,
    )

    if deleted_pages:
        new_metadata = remove_pages_from_metadata(new_metadata, deleted_pages)

    save_space_metadata(new_metadata)

    # If target page wasn't in search results (not modified), get from metadata
    if not target_page_data and page_id in new_metadata.page_index:
        idx = new_metadata.page_index[page_id]
        target_page_data = {
            "page_id": page_id,
            "title": idx["title"],
            "version": idx["version"],
            "url": idx["url"],
            "path": idx["path"],
            "ancestors": idx.get("ancestors", []),
            "last_synced": idx["last_synced"],
        }

    # Handle case where page was deleted from Confluence
    if not page_exists_in_confluence or not target_page_data:
        return json.dumps(
            {
                "success": False,
                "error": f"Page '{page_id}' does not exist in Confluence.",
                "page_id": page_id,
                "space_key": space_key,
                "space_synced": True,
                "pages_synced": len(saved_pages),
                "total_pages_in_space": new_metadata.total_pages,
                "message": "The space was synced successfully, but the requested page was not found.",
            },
            indent=2,
            ensure_ascii=False,
        )

    result = {
        "success": True,
        "page_id": page_id,
        "title": target_page_data["title"],
        "space_key": space_key,
        "space_name": space_name,
        "version": target_page_data["version"],
        "url": target_page_data["url"],
        "local_path": target_page_data["path"],
        "absolute_path": str(Path.cwd() / target_page_data["path"]),
        "ancestors": target_page_data["ancestors"],
        "last_synced": target_page_data["last_synced"],
        "space_synced": True,
        "pages_synced": len(saved_pages),
        "total_pages_in_space": new_metadata.total_pages,
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


@confluence_mcp.tool(tags={"confluence", "write"})
@check_write_access
async def create_page(
    ctx: Context,
    titles: Annotated[
        str,
        Field(description="Title(s) of new page(s) - single title or comma-separated list (e.g., 'My Page' or 'Page 1,Page 2,Page 3')"),
    ],
    parent_id: Annotated[
        str | None,
        Field(description="ID of the parent page (pages will be created as children). Provide either parent_id OR sibling_id, not both."),
    ] = None,
    sibling_id: Annotated[
        str | None,
        Field(description="ID of a sibling page (pages will be created next to this page, under the same parent). Provide either parent_id OR sibling_id, not both."),
    ] = None,
) -> str:
    """Create one or more new empty pages in Confluence.

    Creates pages with the given titles and saves them locally. Specify either
    parent_id OR sibling_id to control placement. All pages are created under
    the same parent.

    Supports bulk operations - pass multiple titles separated by commas.

    Args:
        ctx: The FastMCP context.
        titles: Single title or comma-separated list of titles.
        parent_id: Optional ID of the parent page (creates as children).
        sibling_id: Optional ID of a sibling page (creates under same parent).

    Returns:
        JSON string with the new page info and local paths.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    # Parse titles (comma-separated)
    title_list = [t.strip() for t in titles.split(",") if t.strip()]
    if not title_list:
        return json.dumps({"error": "No titles provided"}, indent=2, ensure_ascii=False)

    # Validate params - need exactly one of parent_id or sibling_id
    if not parent_id and not sibling_id:
        return json.dumps(
            {"error": "Must provide either parent_id or sibling_id"},
            indent=2,
            ensure_ascii=False,
        )
    if parent_id and sibling_id:
        return json.dumps(
            {"error": "Provide either parent_id OR sibling_id, not both"},
            indent=2,
            ensure_ascii=False,
        )

    try:
        # Determine actual parent and space
        actual_parent_id = parent_id
        space_key = None

        if sibling_id:
            sibling_page = confluence_fetcher.get_page_content(sibling_id, convert_to_markdown=False)
            if not sibling_page:
                return json.dumps(
                    {"error": f"Sibling page '{sibling_id}' not found"},
                    indent=2,
                    ensure_ascii=False,
                )
            space_key = sibling_page.space.key if sibling_page.space else None
            ancestors = confluence_fetcher.get_page_ancestors(sibling_id)
            if ancestors:
                actual_parent_id = ancestors[-1].id
            else:
                actual_parent_id = None
        else:
            parent_page = confluence_fetcher.get_page_content(parent_id, convert_to_markdown=False)
            if not parent_page:
                return json.dumps(
                    {"error": f"Parent page '{parent_id}' not found"},
                    indent=2,
                    ensure_ascii=False,
                )
            space_key = parent_page.space.key if parent_page.space else None

        if not space_key:
            return json.dumps(
                {"error": "Could not determine space key from parent/sibling page"},
                indent=2,
                ensure_ascii=False,
            )

        # Get ancestors once (shared by all new pages)
        ancestor_ids = []
        if actual_parent_id:
            parent_ancestors = confluence_fetcher.get_page_ancestors(actual_parent_id)
            ancestor_ids = [a.id for a in parent_ancestors] + [actual_parent_id]

        # Load/create metadata once
        existing_metadata = load_space_metadata(space_key)
        if not existing_metadata:
            existing_metadata = SpaceMetadata(
                space_key=space_key,
                space_name=space_key,
                last_synced=datetime.now(timezone.utc).isoformat(),
                total_pages=0,
            )

        results = []
        for title in title_list:
            try:
                logger.info(f"Creating page '{title}' in space {space_key} under parent {actual_parent_id}")

                new_page = confluence_fetcher.create_page(
                    space_key=space_key,
                    title=title,
                    body="",
                    parent_id=actual_parent_id,
                    is_markdown=False,
                    content_representation="storage",
                )

                page_id_str = str(new_page.id)
                version_num = new_page.version.number if new_page.version else 1
                file_path = save_page_html(
                    space_key=space_key,
                    page_id=page_id_str,
                    title=new_page.title,
                    html_content="",
                    version=version_num,
                    url=new_page.url,
                    ancestors=ancestor_ids,
                )

                existing_metadata.page_index[page_id_str] = {
                    "title": new_page.title,
                    "version": version_num,
                    "url": new_page.url,
                    "path": file_path,
                    "ancestors": ancestor_ids,
                    "last_synced": datetime.now(timezone.utc).isoformat(),
                }

                results.append({
                    "success": True,
                    "page_id": page_id_str,
                    "title": new_page.title,
                    "url": new_page.url,
                    "local_path": file_path,
                    "absolute_path": str(Path.cwd() / file_path),
                })
            except Exception as e:
                logger.error(f"Failed to create page '{title}': {e}")
                results.append({
                    "title": title,
                    "error": str(e),
                })

        # Save metadata once after all pages created
        existing_metadata.total_pages = len(existing_metadata.page_index)
        save_space_metadata(existing_metadata)

        # Return single result for single page (backward compatibility)
        if len(title_list) == 1:
            result = results[0]
            if result.get("success"):
                result["message"] = f"Page '{result['title']}' created successfully"
                result["space_key"] = space_key
                result["parent_id"] = actual_parent_id
            return json.dumps(result, indent=2, ensure_ascii=False)

        return json.dumps({
            "pages": results,
            "total": len(results),
            "space_key": space_key,
            "parent_id": actual_parent_id,
        }, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Failed to create pages: {e}")
        return json.dumps(
            {"error": f"Failed to create pages: {str(e)}"},
            indent=2,
            ensure_ascii=False,
        )


@confluence_mcp.tool(tags={"confluence", "write"})
@check_write_access
async def push_page_update(
    ctx: Context,
    page_ids: Annotated[
        str,
        Field(description="Page ID(s) to update - single ID or comma-separated list (e.g., '123' or '123,456,789')"),
    ],
    revision_message: Annotated[
        str,
        Field(description="A message describing the changes (like a commit message)"),
    ],
    move_to_parent_id: Annotated[
        str | None,
        Field(description="Optional: Move page to be a child of this page (single page only)."),
    ] = None,
    before_page_id: Annotated[
        str | None,
        Field(description="Optional: Position page right BEFORE this sibling (single page only)."),
    ] = None,
    after_page_id: Annotated[
        str | None,
        Field(description="Optional: Position page right AFTER this sibling (single page only)."),
    ] = None,
) -> str:
    """Push local HTML changes to Confluence for one or more pages.

    Supports bulk operations - pass multiple page IDs separated by commas.
    Each page's content is read from its local HTML file and pushed to Confluence.

    The agent should:
    1. Read/edit local HTML files (find paths in _metadata.json or tree structure)
    2. To RENAME: Edit the "Title:" line in the HTML comment header
    3. Call this tool with the page_ids and revision_message

    CODE BLOCKS in Confluence must use this format:
    <ac:structured-macro ac:name="code" ac:schema-version="1">
      <ac:parameter ac:name="language">python</ac:parameter>
      <ac:plain-text-body><![CDATA[your code here]]></ac:plain-text-body>
    </ac:structured-macro>

    ## Diagrams with Mermaid

    Use `confluence_create_mermaid_diagram` tool to create diagrams - it handles
    rendering and uploading the .png image. Write the mermaid source directly
    into the page content using an expand/code block (see tool docs for format).

    Before updating, it verifies each page's local version matches Confluence.

    ## Moving and Reordering Pages (single page only)

    Use these optional parameters to move or reorder a SINGLE page:
    - `move_to_parent_id`: Move page as a child of another page (appended at end)
    - `before_page_id`: Position page right before a sibling
    - `after_page_id`: Position page right after a sibling

    Move parameters are ignored for bulk operations.

    Args:
        ctx: The FastMCP context.
        page_ids: Single page ID or comma-separated list of page IDs.
        revision_message: Description of the changes (shared across all pages).
        move_to_parent_id: Optional new parent page ID (single page only).
        before_page_id: Optional page ID to position before (single page only).
        after_page_id: Optional page ID to position after (single page only).

    Returns:
        JSON string indicating success or failure for each page.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    # Parse page IDs
    id_list = [pid.strip() for pid in page_ids.split(",") if pid.strip()]
    if not id_list:
        return json.dumps({"error": "No page IDs provided"}, indent=2, ensure_ascii=False)

    # Validate move params - only one can be provided, and only for single page
    move_params = [move_to_parent_id, before_page_id, after_page_id]
    has_move = any(p is not None for p in move_params)
    if has_move and len(id_list) > 1:
        return json.dumps(
            {"error": "Move parameters only work with single page, not bulk operations"},
            indent=2,
            ensure_ascii=False,
        )
    if sum(1 for p in move_params if p is not None) > 1:
        return json.dumps(
            {"error": "Provide only ONE of: move_to_parent_id, before_page_id, or after_page_id"},
            indent=2,
            ensure_ascii=False,
        )

    # Determine move operation type
    move_target_id = None
    move_position = None
    if move_to_parent_id:
        move_target_id = move_to_parent_id
        move_position = "append"
    elif before_page_id:
        move_target_id = before_page_id
        move_position = "before"
    elif after_page_id:
        move_target_id = after_page_id
        move_position = "after"

    results = []
    spaces_to_sync = set()  # Track spaces that need syncing after moves

    for page_id in id_list:
        try:
            # Find the page in local storage
            page_info = get_page_info(page_id)
            if not page_info:
                results.append({
                    "page_id": page_id,
                    "error": "Page not found in local storage",
                })
                continue

            # Read the local HTML file
            file_path = Path.cwd() / page_info["path"]
            if not file_path.exists():
                results.append({
                    "page_id": page_id,
                    "error": f"Local file not found: {page_info['path']}",
                })
                continue

            space_key = page_info["space_key"]
            local_version = page_info.get("version")

            # Check version mismatch
            try:
                current_page = confluence_fetcher.get_page_content(page_id, convert_to_markdown=False)
                confluence_version = current_page.version.number if current_page.version else None

                if local_version and confluence_version and local_version != confluence_version:
                    results.append({
                        "page_id": page_id,
                        "error": f"Version mismatch (local={local_version}, confluence={confluence_version})",
                    })
                    continue
            except Exception as e:
                logger.warning(f"Could not verify version for {page_id}: {e}")

            # Read and parse content
            with open(file_path, encoding="utf-8") as f:
                content = f.read()

            page_title = page_info["title"]
            if content.startswith("<!--"):
                end_comment = content.find("-->")
                if end_comment != -1:
                    header = content[:end_comment]
                    title_match = re.search(r"^\s*Title:\s*(.+)$", header, re.MULTILINE)
                    if title_match:
                        page_title = title_match.group(1).strip()
                    content = content[end_comment + 3:].lstrip("\n")

            # Update page in Confluence
            updated_page = confluence_fetcher.update_page(
                page_id=page_id,
                title=page_title,
                body=content,
                is_minor_edit=False,
                version_comment=revision_message,
                is_markdown=False,
                content_representation="storage",
            )

            # Handle move (single page only)
            if move_target_id and move_position:
                confluence_fetcher.move_page(
                    page_id=page_id,
                    target_id=move_target_id,
                    position=move_position,
                )
                spaces_to_sync.add(space_key)

            # Update local storage
            ancestors = page_info.get("ancestors", [])
            version_num = updated_page.version.number if updated_page.version else None
            new_path = save_page_html(
                space_key=space_key,
                page_id=page_id,
                title=updated_page.title,
                html_content=content,
                version=version_num,
                url=updated_page.url,
                ancestors=ancestors,
            )

            # Update metadata
            existing_metadata = load_space_metadata(space_key)
            if existing_metadata:
                existing_metadata.page_index[page_id] = {
                    "title": updated_page.title,
                    "version": version_num,
                    "url": updated_page.url,
                    "path": new_path,
                    "ancestors": ancestors,
                    "last_synced": datetime.now(timezone.utc).isoformat(),
                }
                save_space_metadata(existing_metadata)

            results.append({
                "success": True,
                "page_id": page_id,
                "title": updated_page.title,
                "new_version": version_num,
                "url": updated_page.url,
            })

        except Exception as e:
            logger.error(f"Failed to update page {page_id}: {e}")
            results.append({
                "page_id": page_id,
                "error": str(e),
            })

    # Sync spaces that had pages moved
    for space_key in spaces_to_sync:
        space_lock = _get_space_lock(space_key)
        async with space_lock:
            await _sync_space_impl(confluence_fetcher, space_key, full_sync=False)

    return json.dumps({
        "pages": results,
        "total": len(results),
        "success_count": sum(1 for r in results if r.get("success")),
        "revision_message": revision_message,
    }, indent=2, ensure_ascii=False)


# =============================================================================
# TOOLS - Spaces, Comments, Users
# =============================================================================


@confluence_mcp.tool(tags={"confluence", "read"})
async def get_spaces(
    ctx: Context,
    limit: Annotated[
        int,
        Field(description="Maximum number of spaces to return", ge=1, le=100, default=25),
    ] = 25,
) -> str:
    """List available Confluence spaces.

    Args:
        ctx: The FastMCP context.
        limit: Maximum number of spaces to return.

    Returns:
        JSON string with list of spaces.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    try:
        result = confluence_fetcher.get_spaces(start=0, limit=limit)
        spaces = []
        for space in result.get("results", []):
            spaces.append({
                "key": space.get("key"),
                "name": space.get("name"),
                "type": space.get("type"),
            })

        return json.dumps(
            {"success": True, "total": len(spaces), "spaces": spaces},
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        logger.error(f"Failed to get spaces: {e}")
        return json.dumps(
            {"error": f"Failed to get spaces: {str(e)}"},
            indent=2,
            ensure_ascii=False,
        )


@confluence_mcp.tool(tags={"confluence", "read"})
async def get_comments(
    ctx: Context,
    page_id: Annotated[
        str,
        Field(description="Confluence page ID"),
    ],
) -> str:
    """Get comments for a specific Confluence page.

    Args:
        ctx: The FastMCP context.
        page_id: Confluence page ID.

    Returns:
        JSON string with list of comments.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    try:
        comments = confluence_fetcher.get_page_comments(page_id, return_markdown=True)

        comment_list = []
        for comment in comments:
            comment_list.append({
                "id": comment.id,
                "author": comment.author.display_name if comment.author else None,
                "created": comment.created.isoformat() if comment.created else None,
                "content": comment.body,
            })

        return json.dumps(
            {"success": True, "page_id": page_id, "total": len(comment_list), "comments": comment_list},
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        logger.error(f"Failed to get comments for page {page_id}: {e}")
        return json.dumps(
            {"error": f"Failed to get comments: {str(e)}"},
            indent=2,
            ensure_ascii=False,
        )


@confluence_mcp.tool(tags={"confluence", "write"})
@check_write_access
async def add_comment(
    ctx: Context,
    page_id: Annotated[
        str,
        Field(description="Confluence page ID"),
    ],
    content: Annotated[
        str,
        Field(description="Comment content in Markdown format"),
    ],
) -> str:
    """Add a comment to a Confluence page.

    Args:
        ctx: The FastMCP context.
        page_id: The ID of the page to add a comment to.
        content: The comment content in Markdown format.

    Returns:
        JSON string with the created comment.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    try:
        comment = confluence_fetcher.add_comment(page_id, content)

        if not comment:
            return json.dumps(
                {"error": "Failed to add comment"},
                indent=2,
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "success": True,
                "comment": {
                    "id": comment.id,
                    "author": comment.author.display_name if comment.author else None,
                    "created": comment.created.isoformat() if comment.created else None,
                    "content": comment.body,
                },
            },
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        logger.error(f"Failed to add comment to page {page_id}: {e}")
        return json.dumps(
            {"error": f"Failed to add comment: {str(e)}"},
            indent=2,
            ensure_ascii=False,
        )


@confluence_mcp.tool(tags={"confluence", "read"})
async def search_user(
    ctx: Context,
    query: Annotated[
        str,
        Field(
            description="CQL query for user search. Example: 'user.fullname ~ \"John\"'"
        ),
    ],
    limit: Annotated[
        int,
        Field(description="Maximum number of results (1-50)", ge=1, le=50, default=10),
    ] = 10,
) -> str:
    """Search Confluence users using CQL.

    Args:
        ctx: The FastMCP context.
        query: CQL query string for user search.
        limit: Maximum number of results (1-50).

    Returns:
        JSON string with list of matching users.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    try:
        results = confluence_fetcher.search_user(cql=query, limit=limit)

        users = []
        for user in results:
            users.append({
                "account_id": user.account_id,
                "display_name": user.display_name,
                "email": user.email,
            })

        return json.dumps(
            {"success": True, "total": len(users), "users": users},
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        logger.error(f"User search failed: {e}")
        return json.dumps(
            {"error": f"User search failed: {str(e)}"},
            indent=2,
            ensure_ascii=False,
        )


# =============================================================================
# TOOLS - Attachments
# =============================================================================


@confluence_mcp.tool(tags={"confluence", "read"})
async def download_attachments(
    ctx: Context,
    page_id: Annotated[
        str,
        Field(description="The ID of the page to download attachments from"),
    ],
) -> str:
    """Download all attachments from a Confluence page to local storage.

    Downloads all attachments (including inline images/screenshots) to an
    'attachments' folder next to the page's HTML file.

    The page must be synced locally first (use sync_space or read_page).

    Attachments are referenced in Confluence HTML using these macros:
    - Images: <ac:image><ri:attachment ri:filename="image.png"/></ac:image>
    - Links: <ac:link><ri:attachment ri:filename="file.pdf"/></ac:link>

    Returns local paths to each file for easy reading/editing by the agent.

    Args:
        ctx: The FastMCP context.
        page_id: The ID of the page to download attachments from.

    Returns:
        JSON with attachments_folder and list of downloaded files with local_path.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    # Find the page in local storage
    page_info = get_page_info(page_id)

    if not page_info:
        return json.dumps(
            {
                "error": f"Page '{page_id}' not found in local storage.",
                "hint": "Use sync_space or read_page to sync the page first.",
            },
            indent=2,
            ensure_ascii=False,
        )

    space_key = page_info["space_key"]
    ancestors = page_info.get("ancestors", [])

    try:
        # Get attachments metadata from Confluence
        attachments_response = confluence_fetcher.confluence.get_attachments_from_content(
            page_id, start=0, limit=500
        )
        attachments = attachments_response.get("results", [])

        if not attachments:
            return json.dumps(
                {
                    "success": True,
                    "page_id": page_id,
                    "message": "No attachments found on this page.",
                    "downloaded": [],
                },
                indent=2,
                ensure_ascii=False,
            )

        # Ensure attachments folder exists
        attachments_folder = ensure_attachments_folder(space_key, ancestors, page_id)

        # Use the built-in download method (handles authentication properly)
        download_result = confluence_fetcher.confluence.download_attachments_from_page(
            page_id, path=str(attachments_folder)
        )

        downloaded_count = download_result.get("attachments_downloaded", 0)

        # Build list of downloaded files with metadata
        downloaded = []
        for attachment in attachments:
            filename = attachment.get("title", "")
            file_path = attachments_folder / filename
            if file_path.exists():
                downloaded.append({
                    "filename": filename,
                    "size": attachment.get("extensions", {}).get("fileSize"),
                    "media_type": attachment.get("extensions", {}).get("mediaType"),
                    "local_path": str(file_path.absolute()),
                })

        result = {
            "success": True,
            "page_id": page_id,
            "attachments_folder": str(attachments_folder.absolute()),
            "total_attachments": len(attachments),
            "downloaded_count": downloaded_count,
            "downloaded": downloaded,
        }

        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Failed to download attachments for page {page_id}: {e}")
        return json.dumps(
            {"error": f"Failed to download attachments: {str(e)}"},
            indent=2,
            ensure_ascii=False,
        )


@confluence_mcp.tool(tags={"confluence", "write"})
@check_write_access
async def upload_attachment(
    ctx: Context,
    page_id: Annotated[
        str,
        Field(description="The ID of the page to upload the attachment to"),
    ],
    file_path: Annotated[
        str,
        Field(description="Absolute or relative path to the local file to upload"),
    ],
    comment: Annotated[
        str | None,
        Field(description="Optional comment for the attachment"),
    ] = None,
) -> str:
    """Upload a file as an attachment to a Confluence page.

    After uploading, the attachment is available but NOT displayed inline.
    To display the attachment inline on the page, follow these steps:

    1. Upload the file using this tool
    2. Edit the local HTML file to add the appropriate macro:
       - For images: <ac:image><ri:attachment ri:filename="image.png"/></ac:image>
       - For file links: <ac:link><ri:attachment ri:filename="file.pdf"/></ac:link>
    3. Push the changes using push_page_update

    Example image with alignment:
    <ac:image ac:align="center" ac:layout="center">
      <ri:attachment ri:filename="screenshot.png"/>
    </ac:image>

    Args:
        ctx: The FastMCP context.
        page_id: The ID of the page to upload the attachment to.
        file_path: Path to the local file to upload.
        comment: Optional comment for the attachment.

    Returns:
        JSON string with upload result or error.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    # Resolve the file path
    local_file = Path(file_path)
    if not local_file.is_absolute():
        local_file = Path.cwd() / file_path

    if not local_file.exists():
        return json.dumps(
            {"error": f"File not found: {file_path}"},
            indent=2,
            ensure_ascii=False,
        )

    if not local_file.is_file():
        return json.dumps(
            {"error": f"Path is not a file: {file_path}"},
            indent=2,
            ensure_ascii=False,
        )

    try:
        # Upload the attachment
        result = confluence_fetcher.confluence.attach_file(
            filename=str(local_file),
            page_id=page_id,
            comment=comment,
        )

        if not result:
            return json.dumps(
                {"error": "Upload failed - no response from server"},
                indent=2,
                ensure_ascii=False,
            )

        # Extract attachment info from result
        attachment_info = result.get("results", [result])[0] if isinstance(result, dict) else result

        return json.dumps(
            {
                "success": True,
                "page_id": page_id,
                "filename": local_file.name,
                "attachment_id": attachment_info.get("id") if isinstance(attachment_info, dict) else None,
                "message": f"Successfully uploaded {local_file.name}",
            },
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        logger.error(f"Failed to upload attachment to page {page_id}: {e}")
        return json.dumps(
            {"error": f"Failed to upload attachment: {str(e)}"},
            indent=2,
            ensure_ascii=False,
        )


def _is_mermaid_enabled() -> bool:
    """Check if mermaid diagram rendering is enabled via env var."""
    return os.environ.get("MERMAID_ENABLED", "").lower() in ("true", "1", "yes")


@confluence_mcp.tool(tags={"confluence", "write"})
@check_write_access
async def create_mermaid_diagram(
    ctx: Context,
    page_id: Annotated[
        str,
        Field(description="The ID of the page to attach the diagram to"),
    ],
    mermaid_source: Annotated[
        str,
        Field(description="The mermaid diagram source code (e.g., 'graph TD; A-->B')"),
    ],
    filename: Annotated[
        str,
        Field(description="Base filename without extension (e.g., 'architecture' creates architecture.png)"),
    ],
) -> str:
    """Render a mermaid diagram to PNG and upload it as an attachment.

    Requires `MERMAID_ENABLED=true` env var and `playwright install chromium`.

    This tool renders the mermaid source to a PNG image and uploads it to Confluence.
    The mermaid source should be embedded directly in the page content using an
    expand/code block, NOT as a separate attachment.

    ## Recommended Workflow

    1. Call this tool with the mermaid source to create and upload the PNG
    2. Add the image to the page using the HTML snippet from the response
    3. Add the source below the image in an expand/code block for easy editing:

    <ac:structured-macro ac:name="expand" ac:schema-version="1" data-layout="wide">
      <ac:parameter ac:name="title">diagram-name.mmd</ac:parameter>
      <ac:rich-text-body>
        <ac:structured-macro ac:name="code" ac:schema-version="1">
          <ac:plain-text-body><![CDATA[graph LR
        A --> B]]></ac:plain-text-body>
        </ac:structured-macro>
      </ac:rich-text-body>
    </ac:structured-macro>

    ## Updating Existing Diagrams

    To update an existing diagram:
    1. Find and read the mermaid source from the expand/code block in the page HTML
    2. Call this tool with the updated mermaid_source (same filename to overwrite PNG)
    3. Update the source in the expand/code block in the page HTML

    Args:
        ctx: The FastMCP context.
        page_id: The ID of the page to attach the diagram to.
        mermaid_source: The mermaid diagram source code.
        filename: Base filename without extension.

    Returns:
        JSON with success status and HTML snippet for inline embedding.
    """
    # Check if mermaid is enabled
    if not _is_mermaid_enabled():
        return json.dumps(
            {
                "error": "Mermaid diagram rendering is disabled.",
                "hint": "Set MERMAID_ENABLED=true and run 'playwright install chromium' to enable.",
            },
            indent=2,
            ensure_ascii=False,
        )

    confluence_fetcher = await get_confluence_fetcher(ctx)

    # Find the page in local storage
    page_info = get_page_info(page_id)

    if not page_info:
        return json.dumps(
            {
                "error": f"Page '{page_id}' not found in local storage.",
                "hint": "Use sync_space or read_page to sync the page first.",
            },
            indent=2,
            ensure_ascii=False,
        )

    space_key = page_info["space_key"]
    ancestors = page_info.get("ancestors", [])

    # Ensure attachments folder exists
    attachments_folder = ensure_attachments_folder(space_key, ancestors, page_id)

    # Clean filename (remove extensions if provided)
    base_name = filename.rsplit(".", 1)[0] if "." in filename else filename
    mmd_filename = f"{base_name}.mmd"
    png_filename = f"{base_name}.png"
    mmd_path = attachments_folder / mmd_filename
    png_path = attachments_folder / png_filename

    try:
        # Save mermaid source to .mmd file
        mmd_path.write_text(mermaid_source, encoding="utf-8")

        # Render to PNG using mermaid-cli (async version) with 2x scale for better quality
        from mermaid_cli import render_mermaid_file

        await render_mermaid_file(
            str(mmd_path),
            str(png_path),
            "png",
            viewport={"width": 800, "height": 600, "deviceScaleFactor": 2},
        )

        if not png_path.exists():
            return json.dumps(
                {"error": "Failed to render mermaid diagram - PNG not created."},
                indent=2,
                ensure_ascii=False,
            )

        # Upload PNG to Confluence
        png_result = confluence_fetcher.confluence.attach_file(
            filename=str(png_path),
            page_id=page_id,
            comment=f"Mermaid diagram: {base_name}",
        )

        # Build HTML snippet for inline embedding
        html_snippet = f'<ac:image ac:align="center" ac:layout="center"><ri:attachment ri:filename="{png_filename}"/></ac:image>'

        # Build expand/code block snippet for mermaid source
        expand_snippet = f"""<ac:structured-macro ac:name="expand" ac:schema-version="1" data-layout="wide">
  <ac:parameter ac:name="title">{base_name}.mmd</ac:parameter>
  <ac:rich-text-body>
    <ac:structured-macro ac:name="code" ac:schema-version="1">
      <ac:plain-text-body><![CDATA[{mermaid_source}]]></ac:plain-text-body>
    </ac:structured-macro>
  </ac:rich-text-body>
</ac:structured-macro>"""

        return json.dumps(
            {
                "success": True,
                "page_id": page_id,
                "png_file": str(png_path),
                "html_snippet": html_snippet,
                "expand_snippet": expand_snippet,
                "message": f"Successfully created and uploaded diagram '{base_name}.png'. Add html_snippet for the image and expand_snippet for the editable source.",
            },
            indent=2,
            ensure_ascii=False,
        )

    except ImportError:
        return json.dumps(
            {
                "error": "mermaid-cli not available.",
                "hint": "Run 'playwright install chromium' to enable mermaid rendering.",
            },
            indent=2,
            ensure_ascii=False,
        )
    except Exception as e:
        logger.error(f"Failed to create mermaid diagram for page {page_id}: {e}")
        return json.dumps(
            {"error": f"Failed to create mermaid diagram: {str(e)}"},
            indent=2,
            ensure_ascii=False,
        )
