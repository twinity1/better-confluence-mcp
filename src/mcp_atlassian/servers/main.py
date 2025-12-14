"""Main FastMCP server setup for Atlassian integration."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from fastmcp.tools import Tool as FastMCPTool
from mcp.types import Tool as MCPTool

from mcp_atlassian.confluence.config import ConfluenceConfig
from mcp_atlassian.local_storage import (
    ensure_gitignore_entry,
    get_all_synced_spaces,
    load_space_metadata,
    save_page_html,
    cleanup_deleted_pages,
    check_and_cleanup_moved_page,
)
from mcp_atlassian.utils.environment import get_available_services
from mcp_atlassian.utils.io import is_read_only_mode
from mcp_atlassian.utils.tools import get_enabled_tools, should_include_tool

from .confluence import confluence_mcp, AUTO_FULL_SYNC_DAYS
from .context import MainAppContext

logger = logging.getLogger("mcp-atlassian.server.main")


def is_auto_sync_enabled() -> bool:
    """Check if auto-sync on startup is enabled."""
    val = os.environ.get("AUTO_SYNC_ON_STARTUP", "true").lower()
    return val in ("true", "1", "yes")


def is_gitignore_auto_add_enabled() -> bool:
    """Check if auto-adding to .gitignore is enabled."""
    val = os.environ.get("AUTO_ADD_GITIGNORE", "true").lower()
    return val in ("true", "1", "yes")


async def auto_sync_spaces_background(confluence_config: ConfluenceConfig) -> None:
    """Sync all locally stored spaces in the background.

    This runs after server startup to keep local caches up to date.
    """
    from datetime import datetime, timezone

    from mcp_atlassian.confluence import ConfluenceFetcher

    synced_spaces = get_all_synced_spaces()
    if not synced_spaces:
        logger.debug("No locally synced spaces found, skipping auto-sync")
        return

    logger.info(f"Auto-syncing {len(synced_spaces)} spaces: {synced_spaces}")

    try:
        fetcher = ConfluenceFetcher(confluence_config)

        for space_key in synced_spaces:
            try:
                logger.info(f"Auto-syncing space: {space_key}")

                existing_metadata = load_space_metadata(space_key)
                if not existing_metadata:
                    logger.warning(f"No metadata for space {space_key}, skipping")
                    continue

                # Check if we need full sync
                full_sync = False
                try:
                    last_dt = datetime.fromisoformat(
                        existing_metadata.last_synced.replace("Z", "+00:00")
                    )
                    days_since_sync = (datetime.now(timezone.utc) - last_dt).days
                    if days_since_sync >= AUTO_FULL_SYNC_DAYS:
                        full_sync = True
                        logger.info(f"Space {space_key}: triggering full sync ({days_since_sync} days old)")
                except (ValueError, AttributeError):
                    pass

                # Build CQL query
                cql_base = f'type=page AND space.key="{space_key}"'
                if not full_sync:
                    last_dt = datetime.fromisoformat(
                        existing_metadata.last_synced.replace("Z", "+00:00")
                    )
                    last_sync_date_str = last_dt.strftime("%Y-%m-%d %H:%M")
                    cql_query = f'{cql_base} AND lastModified >= "{last_sync_date_str}"'
                else:
                    cql_query = cql_base

                # Search for pages
                search_results = fetcher.search(cql_query, limit=500)

                if not search_results:
                    logger.debug(f"Space {space_key}: no changes since last sync")
                    continue

                # Process pages
                saved_count = 0
                for search_page in search_results:
                    try:
                        full_page = fetcher.get_page_content(
                            search_page.id, convert_to_markdown=False
                        )
                        ancestors = fetcher.get_page_ancestors(search_page.id)
                        ancestor_ids = [a.id for a in ancestors]

                        check_and_cleanup_moved_page(
                            space_key, search_page.id, ancestor_ids, existing_metadata
                        )

                        html_content = full_page.content or ""
                        version_num = full_page.version.number if full_page.version else None
                        save_page_html(
                            space_key=space_key,
                            page_id=search_page.id,
                            title=full_page.title,
                            html_content=html_content,
                            version=version_num,
                            url=full_page.url,
                            ancestors=ancestor_ids,
                        )
                        saved_count += 1
                    except Exception as e:
                        logger.debug(f"Failed to sync page {search_page.id}: {e}")

                # Cleanup deleted pages on full sync
                if full_sync:
                    confluence_page_ids = {p.id for p in search_results}
                    deleted = cleanup_deleted_pages(space_key, confluence_page_ids, existing_metadata)
                    if deleted:
                        logger.debug(f"Space {space_key}: cleaned up {len(deleted)} deleted pages")

                logger.info(f"Auto-sync complete for {space_key}: {saved_count} pages updated")

            except Exception as e:
                logger.warning(f"Auto-sync failed for space {space_key}: {e}")

    except Exception as e:
        logger.error(f"Auto-sync initialization failed: {e}")


@asynccontextmanager
async def main_lifespan(app: FastMCP[MainAppContext]) -> AsyncIterator[dict]:
    logger.info("Main Atlassian MCP server lifespan starting...")
    services = get_available_services()
    read_only = is_read_only_mode()
    enabled_tools = get_enabled_tools()

    loaded_confluence_config: ConfluenceConfig | None = None

    if services.get("confluence"):
        try:
            confluence_config = ConfluenceConfig.from_env()
            if confluence_config.is_auth_configured():
                loaded_confluence_config = confluence_config
                logger.info(
                    "Confluence configuration loaded and authentication is configured."
                )
            else:
                logger.warning(
                    "Confluence URL found, but authentication is not fully configured. Confluence tools will be unavailable."
                )
        except Exception as e:
            logger.error(f"Failed to load Confluence configuration: {e}", exc_info=True)

    app_context = MainAppContext(
        full_confluence_config=loaded_confluence_config,
        read_only=read_only,
        enabled_tools=enabled_tools,
    )
    logger.info(f"Read-only mode: {'ENABLED' if read_only else 'DISABLED'}")
    logger.info(f"Enabled tools filter: {enabled_tools or 'All tools enabled'}")

    # Ensure .gitignore has our storage directory
    if is_gitignore_auto_add_enabled():
        try:
            ensure_gitignore_entry(auto_add=True)
        except Exception as e:
            logger.warning(f"Failed to update .gitignore: {e}")

    # Start auto-sync in background if enabled and configured
    auto_sync_task: asyncio.Task | None = None
    if is_auto_sync_enabled() and loaded_confluence_config:
        synced_spaces = get_all_synced_spaces()
        if synced_spaces:
            logger.info(f"Starting background auto-sync for spaces: {synced_spaces}")
            auto_sync_task = asyncio.create_task(
                auto_sync_spaces_background(loaded_confluence_config)
            )

    try:
        yield {"app_lifespan_context": app_context}
    except Exception as e:
        logger.error(f"Error during lifespan: {e}", exc_info=True)
        raise
    finally:
        logger.info("Main Atlassian MCP server lifespan shutting down...")
        # Cancel auto-sync task if running
        if auto_sync_task and not auto_sync_task.done():
            logger.debug("Cancelling background auto-sync task...")
            auto_sync_task.cancel()
            try:
                await auto_sync_task
            except asyncio.CancelledError:
                pass
        # Perform any necessary cleanup here
        try:
            if loaded_confluence_config:
                logger.debug("Cleaning up Confluence resources...")
        except Exception as e:
            logger.error(f"Error during cleanup: {e}", exc_info=True)
        logger.info("Main Atlassian MCP server lifespan shutdown complete.")


class AtlassianMCP(FastMCP[MainAppContext]):
    """Custom FastMCP server class for Atlassian integration with tool filtering."""

    async def _mcp_list_tools(self) -> list[MCPTool]:
        # Filter tools based on enabled_tools, read_only mode, and service configuration from the lifespan context.
        req_context = self._mcp_server.request_context
        if req_context is None or req_context.lifespan_context is None:
            logger.warning(
                "Lifespan context not available during _main_mcp_list_tools call."
            )
            return []

        lifespan_ctx_dict = req_context.lifespan_context
        app_lifespan_state: MainAppContext | None = (
            lifespan_ctx_dict.get("app_lifespan_context")
            if isinstance(lifespan_ctx_dict, dict)
            else None
        )
        read_only = (
            getattr(app_lifespan_state, "read_only", False)
            if app_lifespan_state
            else False
        )
        enabled_tools_filter = (
            getattr(app_lifespan_state, "enabled_tools", None)
            if app_lifespan_state
            else None
        )
        logger.debug(
            f"_main_mcp_list_tools: read_only={read_only}, enabled_tools_filter={enabled_tools_filter}"
        )

        all_tools: dict[str, FastMCPTool] = await self.get_tools()
        logger.debug(
            f"Aggregated {len(all_tools)} tools before filtering: {list(all_tools.keys())}"
        )

        filtered_tools: list[MCPTool] = []
        for registered_name, tool_obj in all_tools.items():
            tool_tags = tool_obj.tags

            if not should_include_tool(registered_name, enabled_tools_filter):
                logger.debug(f"Excluding tool '{registered_name}' (not enabled)")
                continue

            if tool_obj and read_only and "write" in tool_tags:
                logger.debug(
                    f"Excluding tool '{registered_name}' due to read-only mode and 'write' tag"
                )
                continue

            # Exclude Confluence tools if config is not fully authenticated
            is_confluence_tool = "confluence" in tool_tags
            service_configured_and_available = True
            if app_lifespan_state:
                if is_confluence_tool and not app_lifespan_state.full_confluence_config:
                    logger.debug(
                        f"Excluding Confluence tool '{registered_name}' as Confluence configuration/authentication is incomplete."
                    )
                    service_configured_and_available = False
            elif is_confluence_tool:
                logger.warning(
                    f"Excluding tool '{registered_name}' as application context is unavailable to verify service configuration."
                )
                service_configured_and_available = False

            if not service_configured_and_available:
                continue

            filtered_tools.append(tool_obj.to_mcp_tool(name=registered_name))

        logger.debug(
            f"_main_mcp_list_tools: Total tools after filtering: {len(filtered_tools)}"
        )
        return filtered_tools


main_mcp = AtlassianMCP(name="Atlassian MCP", lifespan=main_lifespan)
main_mcp.mount("confluence", confluence_mcp)
