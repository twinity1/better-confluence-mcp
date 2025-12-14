"""Module for Confluence search operations."""

import logging

from ..models.confluence import (
    ConfluencePage,
    ConfluenceSearchResult,
    ConfluenceUserSearchResult,
    ConfluenceUserSearchResults,
)
from ..utils.decorators import handle_atlassian_api_errors
from .client import ConfluenceClient
from .utils import quote_cql_identifier_if_needed

logger = logging.getLogger("mcp-atlassian")


class SearchMixin(ConfluenceClient):
    """Mixin for Confluence search operations."""

    # Confluence API max limit per request (Cloud has 250, Server/DC may vary)
    MAX_CQL_LIMIT = 250

    @handle_atlassian_api_errors("Confluence API")
    def search_all(
        self, cql: str, spaces_filter: str | None = None
    ) -> list[ConfluencePage]:
        """
        Search all content using CQL with automatic pagination.

        Fetches ALL matching results by paginating through the API.
        Use this for sync operations where you need all pages.

        Args:
            cql: Confluence Query Language string
            spaces_filter: Optional comma-separated list of space keys to filter by

        Returns:
            List of all ConfluencePage models matching the query
        """
        all_pages: list[ConfluencePage] = []
        start = 0

        # Apply spaces filter if present
        cql = self._apply_spaces_filter(cql, spaces_filter)

        while True:
            logger.debug(f"Fetching pages: start={start}, limit={self.MAX_CQL_LIMIT}")
            results = self.confluence.cql(cql=cql, start=start, limit=self.MAX_CQL_LIMIT)

            search_result = ConfluenceSearchResult.from_api_response(
                results,
                base_url=self.config.url,
                cql_query=cql,
                is_cloud=self.config.is_cloud,
            )

            all_pages.extend(search_result.results)

            # Check if there are more results
            total_size = results.get("totalSize", 0)
            fetched = start + len(search_result.results)

            logger.debug(f"Fetched {fetched}/{total_size} pages")

            if fetched >= total_size or len(search_result.results) == 0:
                break

            start = fetched

        logger.info(f"Total pages fetched: {len(all_pages)}")
        return all_pages

    def _apply_spaces_filter(
        self, cql: str, spaces_filter: str | None = None
    ) -> str:
        """Apply spaces filter to CQL query."""
        filter_to_use = spaces_filter or self.config.spaces_filter

        if filter_to_use:
            spaces = [s.strip() for s in filter_to_use.split(",")]
            space_query = " OR ".join(
                [f"space = {quote_cql_identifier_if_needed(space)}" for space in spaces]
            )

            if cql and space_query:
                if "space = " not in cql:
                    cql = f"({cql}) AND ({space_query})"
            else:
                cql = space_query

            logger.info(f"Applied spaces filter to query: {cql}")

        return cql

    @handle_atlassian_api_errors("Confluence API")
    def search(
        self, cql: str, limit: int = 10, spaces_filter: str | None = None
    ) -> list[ConfluencePage]:
        """
        Search content using Confluence Query Language (CQL).

        Args:
            cql: Confluence Query Language string
            limit: Maximum number of results to return (max 250 per API limit)
            spaces_filter: Optional comma-separated list of space keys to filter by,
                overrides config

        Returns:
            List of ConfluencePage models containing search results

        Raises:
            MCPAtlassianAuthenticationError: If authentication fails with the
                Confluence API (401/403)
        """
        # Apply spaces filter
        cql = self._apply_spaces_filter(cql, spaces_filter)

        # Execute the CQL search query (API max is 250)
        effective_limit = min(limit, self.MAX_CQL_LIMIT)
        results = self.confluence.cql(cql=cql, limit=effective_limit)

        # Convert the response to a search result model
        search_result = ConfluenceSearchResult.from_api_response(
            results,
            base_url=self.config.url,
            cql_query=cql,
            is_cloud=self.config.is_cloud,
        )

        # Process result excerpts as content
        processed_pages = []
        for page in search_result.results:
            # Get the excerpt from the original search results
            for result_item in results.get("results", []):
                if result_item.get("content", {}).get("id") == page.id:
                    excerpt = result_item.get("excerpt", "")
                    if excerpt:
                        # Process the excerpt as HTML content
                        space_key = page.space.key if page.space else ""
                        _, processed_markdown = self.preprocessor.process_html_content(
                            excerpt,
                            space_key=space_key,
                            confluence_client=self.confluence,
                        )
                        # Create a new page with processed content
                        page.content = processed_markdown
                    break

            processed_pages.append(page)

        # Return the list of result pages with processed content
        return processed_pages

    # Smaller batch size for bulk content fetch (full HTML is large)
    BULK_CONTENT_LIMIT = 50

    @handle_atlassian_api_errors("Confluence API")
    def get_all_space_pages_with_content(
        self, space_key: str
    ) -> list[dict]:
        """
        Get all pages from a space with content and ancestors in minimal API calls.

        Uses /rest/api/content with expand to get body.storage, ancestors, and version
        in a single paginated request. Much faster than fetching each page individually.

        Args:
            space_key: The space key to fetch pages from

        Returns:
            List of page dicts with id, title, body.storage, ancestors, version
        """
        all_pages: list[dict] = []
        start = 0

        while True:
            logger.debug(f"Fetching space pages: start={start}, limit={self.BULK_CONTENT_LIMIT}")
            result = self.confluence.get(
                "rest/api/content",
                params={
                    "spaceKey": space_key,
                    "type": "page",
                    "expand": "body.storage,ancestors,version",
                    "limit": self.BULK_CONTENT_LIMIT,
                    "start": start,
                },
            )

            pages = result.get("results", [])
            all_pages.extend(pages)
            logger.info(f"Fetched {len(all_pages)} pages so far...")

            # Check pagination
            if len(pages) == 0 or len(pages) < self.BULK_CONTENT_LIMIT:
                break

            start += len(pages)

        logger.info(f"Fetched {len(all_pages)} pages with content from space {space_key}")
        return all_pages

    @handle_atlassian_api_errors("Confluence API")
    def search_user(
        self, cql: str, limit: int = 10
    ) -> list[ConfluenceUserSearchResult]:
        """
        Search users using Confluence Query Language (CQL).

        Args:
            cql: Confluence Query Language string for user search
            limit: Maximum number of results to return

        Returns:
            List of ConfluenceUserSearchResult models containing user search results

        Raises:
            MCPAtlassianAuthenticationError: If authentication fails with the
                Confluence API (401/403)
        """
        # Execute the user search query using the direct API endpoint
        results = self.confluence.get(
            "rest/api/search/user", params={"cql": cql, "limit": limit}
        )

        # Convert the response to a user search result model
        search_result = ConfluenceUserSearchResults.from_api_response(results or {})

        # Return the list of user search results
        return search_result.results
