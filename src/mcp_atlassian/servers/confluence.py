"""Confluence FastMCP server instance and tool definitions."""

import asyncio
import json
import logging
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
    check_and_cleanup_moved_page,
    cleanup_deleted_pages,
    ensure_attachments_folder,
    get_page_info,
    load_space_metadata,
    merge_into_metadata,
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

    Downloads pages from the specified space and stores them as formatted HTML
    in a tree structure under .better-confluence-mcp/SPACE_KEY/.

    By default, performs incremental sync - only fetches pages modified since
    the last sync. Use full_sync=True to re-download everything.

    Auto full sync: If the last sync was more than 3 days ago, a full sync
    is automatically triggered to detect deleted pages.

    The folder structure mirrors the page hierarchy:
    - .better-confluence-mcp/SPACE_KEY/page_id/content.html
    - .better-confluence-mcp/SPACE_KEY/page_id/child_page_id/content.html

    After syncing, the agent can read and edit these HTML files directly using
    standard file tools, then use push_page_update to push changes.

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

        # Build CQL query
        cql_base = f'type=page AND space.key="{space_key}"'

        if last_sync_time:
            # Parse ISO format and convert to CQL date format
            try:
                last_dt = datetime.fromisoformat(last_sync_time.replace("Z", "+00:00"))
                last_sync_date_str = last_dt.strftime("%Y-%m-%d %H:%M")
                cql_query = f'{cql_base} AND lastModified >= "{last_sync_date_str}"'
            except ValueError:
                logger.warning(f"Could not parse last sync time: {last_sync_time}, doing full sync")
                cql_query = cql_base
        else:
            cql_query = cql_base

        logger.info(f"Using CQL: {cql_query}")

        # Search for pages (this returns page IDs and basic info)
        search_results = confluence_fetcher.search(cql_query, limit=500)

        if not search_results and not existing_metadata:
            return json.dumps(
                {"error": f"No pages found in space '{space_key}' or space does not exist."},
                indent=2,
                ensure_ascii=False,
            )

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
        space_name = space_key
        if search_results and search_results[0].space:
            space_name = search_results[0].space.name or space_key

        # Process each page: get full content and ancestors
        saved_pages: list[dict] = []
        moved_pages: list[str] = []
        errors: list[str] = []

        for search_page in search_results:
            page_id = search_page.id
            try:
                # Get full page content (as HTML)
                full_page = confluence_fetcher.get_page_content(
                    page_id, convert_to_markdown=False
                )

                # Get ancestors for tree structure
                ancestors = confluence_fetcher.get_page_ancestors(page_id)
                ancestor_ids = [a.id for a in ancestors]  # Root first, parent last

                # Check if page has moved and cleanup old location
                if check_and_cleanup_moved_page(
                    space_key, page_id, ancestor_ids, existing_metadata
                ):
                    moved_pages.append(page_id)

                # Save the page
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
        if full_sync and existing_metadata:
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


@confluence_mcp.tool(tags={"confluence", "read"})
async def read_page(
    ctx: Context,
    page_id: Annotated[
        str,
        Field(description="The ID of the page to read"),
    ],
) -> str:
    """Read a Confluence page by syncing its entire space to the local filesystem.

    IMPORTANT: This tool syncs ALL pages from the space containing the requested
    page to .better-confluence-mcp/<SPACE_KEY>/ in the current working directory.
    This ensures the full context of the space is available locally for the agent
    to browse and edit.

    The sync is incremental by default - only pages modified since the last sync
    are downloaded. A full sync is triggered automatically every 3 days.

    After syncing, use standard file tools to read/edit the HTML files, then
    call push_page_update to push changes back to Confluence.

    IMPORTANT:
    - Do NOT call multiple read_page or other sync tools in parallel.
    - Call them SEQUENTIALLY (one at a time).
    - Sync can take up to 15 minutes for large spaces - be patient.

    Returns:
        - Page metadata (title, version, URL)
        - local_path: Relative path to the HTML file
        - absolute_path: Full path to the HTML file
        - Space sync summary (pages_synced, total_pages_in_space)

    Args:
        ctx: The FastMCP context.
        page_id: The ID of the page to read.

    Returns:
        JSON string with page metadata and local path, or error if not found.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

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

    # Search for pages in the space
    search_results = confluence_fetcher.search(cql_query, limit=500)

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
    title: Annotated[
        str,
        Field(description="The title of the new page"),
    ],
    parent_id: Annotated[
        str | None,
        Field(description="ID of the parent page (page will be created as a child). Provide either parent_id OR sibling_id, not both."),
    ] = None,
    sibling_id: Annotated[
        str | None,
        Field(description="ID of a sibling page (page will be created next to this page, under the same parent). Provide either parent_id OR sibling_id, not both."),
    ] = None,
) -> str:
    """Create a new empty page in Confluence.

    Creates a page with the given title and syncs the entire space to make it
    available locally. Specify either parent_id OR sibling_id to control placement.

    IMPORTANT:
    - This tool syncs the space after creating the page.
    - Do NOT call multiple create_page or other sync tools in parallel.
    - Call them SEQUENTIALLY (one at a time).
    - Sync can take up to 15 minutes for large spaces - be patient.

    Args:
        ctx: The FastMCP context.
        title: The title of the new page.
        parent_id: Optional ID of the parent page (creates as child).
        sibling_id: Optional ID of a sibling page (creates under same parent).

    Returns:
        JSON string with the new page info and local path.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

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
        # If sibling_id provided, get its parent
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
            # Get sibling's ancestors to find its parent
            ancestors = confluence_fetcher.get_page_ancestors(sibling_id)
            if ancestors:
                actual_parent_id = ancestors[-1].id  # Last ancestor is immediate parent
            else:
                # Sibling is a root page, so new page will also be root
                actual_parent_id = None
        else:
            # Get space from parent
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

        logger.info(f"Creating page '{title}' in space {space_key} under parent {actual_parent_id}")

        # Create the page with empty content
        new_page = confluence_fetcher.create_page(
            space_key=space_key,
            title=title,
            body="",
            parent_id=actual_parent_id,
            is_markdown=False,
            content_representation="storage",
        )

        # Sync the space to get the new page locally (full sync to ensure new page is included)
        space_lock = _get_space_lock(space_key)
        async with space_lock:
            await _sync_space_impl(confluence_fetcher, space_key, full_sync=True)

        # Get the new page info from metadata
        new_metadata = load_space_metadata(space_key)
        page_data = new_metadata.page_index.get(new_page.id) if new_metadata else None

        result = {
            "success": True,
            "message": f"Page '{title}' created successfully",
            "page_id": new_page.id,
            "title": new_page.title,
            "space_key": space_key,
            "url": new_page.url,
            "local_path": page_data["path"] if page_data else None,
            "absolute_path": str(Path.cwd() / page_data["path"]) if page_data else None,
            "parent_id": actual_parent_id,
        }
        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Failed to create page '{title}': {e}")
        return json.dumps(
            {"error": f"Failed to create page: {str(e)}"},
            indent=2,
            ensure_ascii=False,
        )


@confluence_mcp.tool(tags={"confluence", "write"})
@check_write_access
async def push_page_update(
    ctx: Context,
    page_id: Annotated[
        str,
        Field(description="The ID of the page to update"),
    ],
    revision_message: Annotated[
        str,
        Field(description="A message describing the changes (like a commit message)"),
    ],
    move_to_parent_id: Annotated[
        str | None,
        Field(description="Optional: Move page to be a child of this page. Provide either move_to_parent_id OR move_to_sibling_id, not both."),
    ] = None,
    move_to_sibling_id: Annotated[
        str | None,
        Field(description="Optional: Move page to be a sibling of this page (under the same parent). Provide either move_to_parent_id OR move_to_sibling_id, not both."),
    ] = None,
) -> str:
    """Push local HTML changes to Confluence, optionally moving or renaming the page.

    The agent should:
    1. Read the local HTML file (find path in _metadata.json or use the tree structure)
    2. Make edits to the HTML content
    3. To RENAME: Edit the "Title:" line in the HTML comment header
    4. Call this tool with the page_id and revision_message

    The tool will read the current content AND title from the local file and push
    to Confluence. If you changed the Title in the header comment, the page will
    be renamed in Confluence.

    CODE BLOCKS in Confluence must use this format:
    <ac:structured-macro ac:name="code" ac:schema-version="1">
      <ac:parameter ac:name="language">python</ac:parameter>
      <ac:plain-text-body><![CDATA[your code here]]></ac:plain-text-body>
    </ac:structured-macro>

    Before updating, it verifies the local version matches Confluence. If someone
    else edited the page, the local copy is re-synced and an error is returned.

    Optionally, specify move_to_parent_id or move_to_sibling_id to move the page
    to a new location in the page hierarchy.

    IMPORTANT:
    - If moving the page, this tool syncs the space afterward.
    - Do NOT call multiple push_page_update (with move) or sync tools in parallel.
    - Call them SEQUENTIALLY (one at a time).
    - Sync can take up to 15 minutes for large spaces - be patient.

    Args:
        ctx: The FastMCP context.
        page_id: The ID of the page to update.
        revision_message: Description of the changes (shown in Confluence page history).
        move_to_parent_id: Optional new parent page ID (moves page as child).
        move_to_sibling_id: Optional sibling page ID (moves page under same parent).

    Returns:
        JSON string indicating success or failure.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    # Validate move params
    if move_to_parent_id and move_to_sibling_id:
        return json.dumps(
            {"error": "Provide either move_to_parent_id OR move_to_sibling_id, not both"},
            indent=2,
            ensure_ascii=False,
        )

    # Resolve move target
    new_parent_id = None
    if move_to_parent_id:
        new_parent_id = move_to_parent_id
    elif move_to_sibling_id:
        # Get sibling's parent to use as our new parent
        try:
            ancestors = confluence_fetcher.get_page_ancestors(move_to_sibling_id)
            if ancestors:
                new_parent_id = ancestors[-1].id  # Last ancestor is immediate parent
            # If no ancestors, sibling is root - we'd become root too (parent_id=None)
        except Exception as e:
            return json.dumps(
                {"error": f"Failed to get sibling page info: {str(e)}"},
                indent=2,
                ensure_ascii=False,
            )

    # Find the page in local storage
    page_info = get_page_info(page_id)

    if not page_info:
        return json.dumps(
            {
                "error": f"Page '{page_id}' not found in local storage.",
                "hint": "Use sync_space to sync the space first, then edit the HTML file and call this tool.",
            },
            indent=2,
            ensure_ascii=False,
        )

    # Read the local HTML file
    file_path = Path.cwd() / page_info["path"]
    if not file_path.exists():
        return json.dumps(
            {
                "error": f"Local file not found: {page_info['path']}",
                "hint": "The file may have been deleted. Re-sync the space.",
            },
            indent=2,
            ensure_ascii=False,
        )

    space_key = page_info["space_key"]
    local_version = page_info.get("version")

    # Check if local version matches Confluence version before updating
    try:
        current_page = confluence_fetcher.get_page_content(
            page_id, convert_to_markdown=False
        )
        confluence_version = current_page.version.number if current_page.version else None

        if local_version is not None and confluence_version is not None:
            if local_version != confluence_version:
                # Version mismatch - re-sync the page and return error
                logger.warning(
                    f"Version mismatch for page {page_id}: local={local_version}, confluence={confluence_version}"
                )

                # Get ancestors for tree structure
                ancestors = confluence_fetcher.get_page_ancestors(page_id)
                ancestor_ids = [a.id for a in ancestors]

                # Re-sync the page with latest content
                html_content = current_page.content or ""
                new_path = save_page_html(
                    space_key=space_key,
                    page_id=page_id,
                    title=current_page.title,
                    html_content=html_content,
                    version=confluence_version,
                    url=current_page.url,
                    ancestors=ancestor_ids,
                )

                # Update metadata
                existing_metadata = load_space_metadata(space_key)
                if existing_metadata:
                    existing_metadata.page_index[page_id] = {
                        "title": current_page.title,
                        "version": confluence_version,
                        "url": current_page.url,
                        "path": new_path,
                        "ancestors": ancestor_ids,
                        "last_synced": datetime.now(timezone.utc).isoformat(),
                    }
                    save_space_metadata(existing_metadata)

                return json.dumps(
                    {
                        "error": "Page was modified externally",
                        "message": (
                            f"The page was edited in Confluence (version {confluence_version}) "
                            f"since your last sync (version {local_version}). "
                            "The page has been re-synced with the latest content. "
                            "Please review the changes, make your edits again, and call push_page_update."
                        ),
                        "local_path": new_path,
                        "local_version": local_version,
                        "confluence_version": confluence_version,
                        "page_id": page_id,
                        "title": current_page.title,
                    },
                    indent=2,
                    ensure_ascii=False,
                )

    except Exception as e:
        logger.warning(f"Could not verify page version before update: {e}")
        # Continue with update attempt - let it fail naturally if there's a real issue

    with open(file_path, encoding="utf-8") as f:
        content = f.read()

    # Parse title from the metadata comment header (allows renaming)
    page_title = page_info["title"]  # Default to metadata title
    if content.startswith("<!--"):
        end_comment = content.find("-->")
        if end_comment != -1:
            header = content[:end_comment]
            # Extract title from header: "  Title: Page Name"
            title_match = re.search(r"^\s*Title:\s*(.+)$", header, re.MULTILINE)
            if title_match:
                page_title = title_match.group(1).strip()
            # Strip the header from content
            content = content[end_comment + 3:].lstrip("\n")

    try:
        # Determine if we're moving the page
        is_moving = new_parent_id is not None or move_to_sibling_id is not None

        # Update the page in Confluence using storage format (HTML)
        updated_page = confluence_fetcher.update_page(
            page_id=page_id,
            title=page_title,  # Use title from HTML header (enables renaming)
            body=content,
            is_minor_edit=False,
            version_comment=revision_message,
            is_markdown=False,
            content_representation="storage",
            parent_id=new_parent_id,  # This moves the page if provided
        )

        # If page was moved, sync the space to update local file structure
        if is_moving:
            space_lock = _get_space_lock(space_key)
            async with space_lock:
                await _sync_space_impl(confluence_fetcher, space_key, full_sync=False)

            # Get updated page info from metadata
            new_metadata = load_space_metadata(space_key)
            page_data = new_metadata.page_index.get(page_id) if new_metadata else None
            version_num = updated_page.version.number if updated_page.version else None

            result = {
                "success": True,
                "message": "Page updated and moved successfully",
                "page_id": page_id,
                "title": updated_page.title,
                "new_version": version_num,
                "url": updated_page.url,
                "revision_message": revision_message,
                "moved_to_parent": new_parent_id,
                "local_path": page_data["path"] if page_data else None,
                "absolute_path": str(Path.cwd() / page_data["path"]) if page_data else None,
            }
            return json.dumps(result, indent=2, ensure_ascii=False)

        # Not moving - just update local storage with new version
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
            existing_metadata.last_synced = datetime.now(timezone.utc).isoformat()
            save_space_metadata(existing_metadata)

        result = {
            "success": True,
            "message": "Page updated successfully",
            "page_id": page_id,
            "title": updated_page.title,
            "new_version": version_num,
            "url": updated_page.url,
            "revision_message": revision_message,
        }
        return json.dumps(result, indent=2, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Failed to update page {page_id}: {e}")
        return json.dumps(
            {
                "success": False,
                "error": f"Failed to update page: {str(e)}",
                "page_id": page_id,
            },
            indent=2,
            ensure_ascii=False,
        )


# =============================================================================
# TOOLS - Search, Spaces, Comments, Users
# =============================================================================


@confluence_mcp.tool(tags={"confluence", "read"})
async def search(
    ctx: Context,
    query: Annotated[
        str,
        Field(
            description="Search query - can be simple text or CQL. Examples: 'project documentation', 'type=page AND space=DEV', 'title~\"Meeting Notes\"'"
        ),
    ],
    limit: Annotated[
        int,
        Field(description="Maximum number of results (1-50)", ge=1, le=50, default=10),
    ] = 10,
    spaces_filter: Annotated[
        str | None,
        Field(
            description="Comma-separated list of space keys to filter by (e.g., 'DEV,QA')"
        ),
    ] = None,
) -> str:
    """Search Confluence content using simple terms or CQL.

    Args:
        ctx: The FastMCP context.
        query: Search query - can be simple text or a CQL query string.
        limit: Maximum number of results (1-50).
        spaces_filter: Optional comma-separated list of space keys to filter by.

    Returns:
        JSON string with search results.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    try:
        # If query doesn't look like CQL, wrap it in a text search
        if not any(op in query for op in ["=", "~", ">", "<", " AND ", " OR "]):
            cql_query = f'text ~ "{query}"'
        else:
            cql_query = query

        results = confluence_fetcher.search(
            cql=cql_query,
            limit=limit,
            spaces_filter=spaces_filter,
        )

        pages = []
        for page in results:
            pages.append({
                "id": page.id,
                "title": page.title,
                "space_key": page.space.key if page.space else None,
                "url": page.url,
                "excerpt": page.content[:200] if page.content else None,
            })

        return json.dumps(
            {"success": True, "total": len(pages), "results": pages},
            indent=2,
            ensure_ascii=False,
        )

    except Exception as e:
        logger.error(f"Search failed: {e}")
        return json.dumps(
            {"error": f"Search failed: {str(e)}"},
            indent=2,
            ensure_ascii=False,
        )


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

    Args:
        ctx: The FastMCP context.
        page_id: The ID of the page to download attachments from.

    Returns:
        JSON string with list of downloaded files or error.
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
                    "local_path": str(file_path.relative_to(Path.cwd())),
                })

        result = {
            "success": True,
            "page_id": page_id,
            "attachments_folder": str(attachments_folder.relative_to(Path.cwd())),
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
