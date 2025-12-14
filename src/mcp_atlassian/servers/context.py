from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp_atlassian.confluence.config import ConfluenceConfig


@dataclass(frozen=True)
class MainAppContext:
    """
    Context holding fully configured Confluence configuration
    loaded from environment variables at server startup.
    This configuration includes any global/default authentication details.
    """

    full_confluence_config: ConfluenceConfig | None = None
    read_only: bool = False
    enabled_tools: list[str] | None = None
