"""Confluence page tools - read_page, create_page, push_page_update."""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastmcp import Context
from pydantic import Field

from mcp_atlassian.local_storage import (
    SpaceMetadata,
    check_and_cleanup_moved_page,
    fix_html_spacing,
    get_page_info,
    load_space_metadata,
    merge_into_metadata,
    save_page_html,
    save_space_metadata,
)
from mcp_atlassian.servers.dependencies import get_confluence_fetcher
from mcp_atlassian.utils.decorators import check_write_access

from ._server import confluence_mcp, get_space_lock
from .sync import sync_space_impl

logger = logging.getLogger(__name__)


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

    ## Navigation Context

    Returns breadcrumb, siblings, and children for easy navigation:
    - **breadcrumb**: parent pages from root to immediate parent (with level, title, path)
    - **siblings**: pages at the same level (requested page marked with `requested: true`)
    - **children**: direct child pages under this page

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
        JSON with page metadata, breadcrumb (parent pages), siblings, and children.
    """
    confluence_fetcher = await get_confluence_fetcher(ctx)

    # Parse page IDs (comma-separated)
    id_list = [pid.strip() for pid in page_ids.split(",") if pid.strip()]

    if not id_list:
        return json.dumps({"error": "No page IDs provided"}, indent=2, ensure_ascii=False)

    # Use bulk CQL query to get page info for all pages at once
    pages_by_space: dict[str, list[dict]] = {}  # space_key -> [{page_id, space_name}]
    errors = []

    # Build CQL query with id in (...)
    cql_query = f'type=page AND id in ({",".join(id_list)})'
    logger.info(f"Fetching page info with CQL: {cql_query}")

    try:
        search_results = confluence_fetcher.search_all(cql_query)

        # Group pages by space
        found_ids = set()
        for page in search_results or []:
            found_ids.add(page.id)
            space_key = page.space.key if page.space else None
            space_name = page.space.name if page.space else space_key

            if not space_key:
                errors.append({"page_id": page.id, "error": "Could not determine space"})
                continue

            if space_key not in pages_by_space:
                pages_by_space[space_key] = []
            pages_by_space[space_key].append({
                "page_id": page.id,
                "space_name": space_name,
            })

        # Check for pages not found in Confluence - try local storage
        missing_ids = set(id_list) - found_ids
        for page_id in missing_ids:
            local_info = get_page_info(page_id)
            if local_info:
                space_key = local_info.get("space_key")
                if space_key:
                    if space_key not in pages_by_space:
                        pages_by_space[space_key] = []
                    pages_by_space[space_key].append({
                        "page_id": page_id,
                        "space_name": space_key,
                        "from_local": True,
                    })
                    logger.info(f"Page {page_id} found in local storage (space: {space_key})")
            else:
                errors.append({
                    "page_id": page_id,
                    "error": "Page not found in Confluence or local storage",
                })

    except Exception as e:
        logger.error(f"Failed to fetch pages with CQL: {e}")
        # Fallback: check local storage for all pages
        for page_id in id_list:
            local_info = get_page_info(page_id)
            if local_info:
                space_key = local_info.get("space_key")
                if space_key:
                    if space_key not in pages_by_space:
                        pages_by_space[space_key] = []
                    pages_by_space[space_key].append({
                        "page_id": page_id,
                        "space_name": space_key,
                        "from_local": True,
                    })
            else:
                errors.append({
                    "page_id": page_id,
                    "error": f"Page not found: {str(e)}",
                })

    # Sync each space and collect results
    results = list(errors)  # Start with errors

    for space_key, pages in pages_by_space.items():
        space_lock = get_space_lock(space_key)
        async with space_lock:
            # Sync the space once using the unified sync function
            await sync_space_impl(confluence_fetcher, space_key, full_sync=False)

        # Check if any requested pages have moved (ancestors changed)
        # This catches moves that incremental sync might miss
        existing_metadata = load_space_metadata(space_key)
        moved_pages = []
        for page_info in pages:
            page_id = page_info["page_id"]
            if page_info.get("from_local"):
                continue  # Skip pages only found locally

            try:
                # Fetch current ancestors from Confluence
                current_ancestors = confluence_fetcher.get_page_ancestors(page_id)
                current_ancestor_ids = [a.id for a in current_ancestors] if current_ancestors else []

                # Compare with local metadata
                local_data = existing_metadata.page_index.get(page_id) if existing_metadata else None
                local_ancestors = local_data.get("ancestors", []) if local_data else []

                if current_ancestor_ids != local_ancestors:
                    logger.info(f"Page {page_id} has moved: {local_ancestors} -> {current_ancestor_ids}")

                    # Cleanup old location
                    if check_and_cleanup_moved_page(space_key, page_id, current_ancestor_ids, existing_metadata):
                        moved_pages.append(page_id)

                    # Fetch full page and save to new location
                    full_page = confluence_fetcher.get_page_content(page_id)
                    if full_page:
                        base_url = confluence_fetcher.config.url.rstrip("/")
                        url = full_page.url or f"{base_url}/spaces/{space_key}/pages/{page_id}"
                        version_num = full_page.version.number if full_page.version else None

                        file_path = save_page_html(
                            space_key=space_key,
                            page_id=page_id,
                            title=full_page.title,
                            html_content=full_page.content or "",
                            version=version_num,
                            url=url,
                            ancestors=current_ancestor_ids,
                        )

                        # Update metadata
                        updated_page = {
                            "page_id": page_id,
                            "title": full_page.title,
                            "version": version_num,
                            "url": url,
                            "path": file_path,
                            "ancestors": current_ancestor_ids,
                            "last_synced": datetime.now(timezone.utc).isoformat(),
                        }
                        existing_metadata = merge_into_metadata(
                            existing_metadata, [updated_page], space_key,
                            existing_metadata.space_name if existing_metadata else space_key
                        )
                        save_space_metadata(existing_metadata)
                        logger.info(f"Page {page_id} moved and saved to new location: {file_path}")

            except Exception as e:
                logger.warning(f"Failed to check/move page {page_id}: {e}")

        # Get results for each page in this space from metadata
        new_metadata = load_space_metadata(space_key)
        for page_info in pages:
            page_id = page_info["page_id"]
            page_data = new_metadata.page_index.get(page_id) if new_metadata else None
            if page_data:
                ancestors = page_data.get("ancestors", [])

                # Build breadcrumb path with titles, paths, and level
                breadcrumb = []
                for level, ancestor_id in enumerate(ancestors, start=1):
                    ancestor_data = new_metadata.page_index.get(ancestor_id)
                    if ancestor_data:
                        breadcrumb.append({
                            "level": level,
                            "page_id": ancestor_id,
                            "title": ancestor_data.get("title"),
                            "local_path": ancestor_data.get("path"),
                        })

                # Find siblings (pages with same parent), including current page
                parent_id = ancestors[-1] if ancestors else None
                siblings = []
                for other_id, other_data in new_metadata.page_index.items():
                    other_ancestors = other_data.get("ancestors", [])
                    other_parent = other_ancestors[-1] if other_ancestors else None
                    if other_parent == parent_id:
                        sibling_entry = {
                            "page_id": other_id,
                            "title": other_data.get("title"),
                            "local_path": other_data.get("path"),
                        }
                        if other_id == page_id:
                            sibling_entry["requested"] = True
                        siblings.append(sibling_entry)

                # Find children (pages whose parent is the current page)
                children = []
                for other_id, other_data in new_metadata.page_index.items():
                    other_ancestors = other_data.get("ancestors", [])
                    other_parent = other_ancestors[-1] if other_ancestors else None
                    if other_parent == page_id:
                        children.append({
                            "page_id": other_id,
                            "title": other_data.get("title"),
                            "local_path": other_data.get("path"),
                        })

                results.append({
                    "success": True,
                    "page_id": page_id,
                    "title": page_data.get("title"),
                    "space_key": space_key,
                    "space_name": page_info.get("space_name", space_key),
                    "version": page_data.get("version"),
                    "url": page_data.get("url"),
                    "local_path": page_data.get("path"),
                    "absolute_path": str(Path.cwd() / page_data["path"]) if page_data.get("path") else None,
                    "breadcrumb": breadcrumb,
                    "siblings": siblings,
                    "children": children,
                    "last_synced": page_data.get("last_synced"),
                })
            else:
                results.append({
                    "page_id": page_id,
                    "error": "Page not found after sync",
                })

    # Return single result for single page (backward compatibility)
    if len(id_list) == 1 and len(results) == 1:
        result = results[0]
        if result.get("success"):
            result["space_synced"] = True
            result["total_pages_in_space"] = load_space_metadata(result["space_key"]).total_pages if load_space_metadata(result["space_key"]) else 0
        return json.dumps(result, indent=2, ensure_ascii=False)

    return json.dumps({"pages": results, "total": len(results)}, indent=2, ensure_ascii=False)


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

            # Fix spacing around inline tags (agents often write without proper spacing)
            content = fix_html_spacing(content)

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

            # Build diff URL for comparing versions
            base_url = confluence_fetcher.config.url.rstrip("/")
            diff_url = None
            if local_version and version_num:
                diff_url = (
                    f"{base_url}/pages/diffpagesbyversion.action"
                    f"?pageId={page_id}"
                    f"&selectedPageVersions={local_version}"
                    f"&selectedPageVersions={version_num}"
                )

            results.append({
                "success": True,
                "page_id": page_id,
                "title": updated_page.title,
                "previous_version": local_version,
                "new_version": version_num,
                "url": updated_page.url,
                "diff_url": diff_url,
            })

        except Exception as e:
            logger.error(f"Failed to update page {page_id}: {e}")
            results.append({
                "page_id": page_id,
                "error": str(e),
            })

    # Sync spaces that had pages moved
    for space_key in spaces_to_sync:
        space_lock = get_space_lock(space_key)
        async with space_lock:
            await sync_space_impl(confluence_fetcher, space_key, full_sync=False)

    return json.dumps({
        "pages": results,
        "total": len(results),
        "success_count": sum(1 for r in results if r.get("success")),
        "revision_message": revision_message,
    }, indent=2, ensure_ascii=False)
