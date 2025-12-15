"""Confluence sync tools - sync_space."""

import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastmcp import Context
from pydantic import Field

from mcp_atlassian.local_storage import (
    check_and_cleanup_moved_page,
    cleanup_deleted_pages,
    load_space_metadata,
    merge_into_metadata,
    remove_pages_from_metadata,
    save_page_html,
    save_space_metadata,
)
from mcp_atlassian.servers.dependencies import get_confluence_fetcher

from ._server import AUTO_FULL_SYNC_DAYS, confluence_mcp, get_space_lock

logger = logging.getLogger(__name__)


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
    space_lock = get_space_lock(space_key)
    async with space_lock:
        return await sync_space_impl(confluence_fetcher, space_key, full_sync)


async def sync_space_impl(confluence_fetcher, space_key: str, full_sync: bool) -> str:
    """Internal implementation of space sync (called while holding lock).

    This is the unified sync function used by both sync_space and read_page.
    """
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
