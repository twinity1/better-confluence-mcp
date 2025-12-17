"""Local storage module for caching Confluence spaces on the filesystem as a tree."""

import json
import logging
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def sanitize_filename(title: str, max_length: int = 100) -> str:
    """Sanitize a page title for use as a filename.

    Args:
        title: The page title to sanitize
        max_length: Maximum length of the resulting filename (default 100)

    Returns:
        A safe filename string
    """
    # Normalize unicode characters (e.g., é -> e)
    normalized = unicodedata.normalize("NFKD", title)
    # Keep only ASCII characters (remove accents etc.)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")

    # Replace problematic characters with safe alternatives
    # These characters are not allowed in filenames on various systems
    replacements = {
        "/": "-",
        "\\": "-",
        ":": "-",
        "*": "",
        "?": "",
        '"': "",
        "<": "",
        ">": "",
        "|": "",
        "\n": " ",
        "\r": " ",
        "\t": " ",
    }
    for char, replacement in replacements.items():
        ascii_only = ascii_only.replace(char, replacement)

    # Replace multiple spaces/dashes with single ones
    sanitized = re.sub(r"[\s]+", " ", ascii_only)
    sanitized = re.sub(r"[-]+", "-", sanitized)

    # Remove leading/trailing spaces and dashes
    sanitized = sanitized.strip(" -")

    # Truncate if too long (leave room for .html extension)
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip(" -")

    # Fallback if empty after sanitization
    if not sanitized:
        sanitized = "untitled"

    return sanitized

# Base directory for local storage (relative to current working directory)
LOCAL_STORAGE_DIR = ".better-confluence-mcp"


@dataclass
class PageNode:
    """A node in the page tree."""

    page_id: str
    title: str
    version: int | None
    url: str
    last_synced: str
    children: dict[str, "PageNode"] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "page_id": self.page_id,
            "title": self.title,
            "version": self.version,
            "url": self.url,
            "last_synced": self.last_synced,
            "children": {k: v.to_dict() for k, v in self.children.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PageNode":
        """Create from dictionary."""
        children = {k: cls.from_dict(v) for k, v in data.get("children", {}).items()}
        return cls(
            page_id=data["page_id"],
            title=data["title"],
            version=data.get("version"),
            url=data["url"],
            last_synced=data["last_synced"],
            children=children,
        )


@dataclass
class SpaceMetadata:
    """Metadata for a locally stored space with tree structure."""

    space_key: str
    space_name: str
    last_synced: str
    total_pages: int
    # Tree of pages - root level pages (pages without parents in this space)
    page_tree: dict[str, PageNode] = field(default_factory=dict)
    # Flat index for quick lookup by page_id -> {title, version, url, path, ancestors}
    page_index: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "space_key": self.space_key,
            "space_name": self.space_name,
            "last_synced": self.last_synced,
            "total_pages": self.total_pages,
            "page_tree": {k: v.to_dict() for k, v in self.page_tree.items()},
            "page_index": self.page_index,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SpaceMetadata":
        """Create from dictionary."""
        page_tree = {k: PageNode.from_dict(v) for k, v in data.get("page_tree", {}).items()}
        return cls(
            space_key=data["space_key"],
            space_name=data["space_name"],
            last_synced=data["last_synced"],
            total_pages=data["total_pages"],
            page_tree=page_tree,
            page_index=data.get("page_index", {}),
        )


def fix_html_spacing(html_content: str) -> str:
    """Fix spacing issues in HTML content.

    Ensures space exists between text and inline formatting tags like <strong>, <em>, etc.
    Example: 'Click on<strong>Button</strong>' -> 'Click on <strong>Button</strong>'
    Example: '</a>- text' -> '</a> - text'
    """
    # Inline formatting tags that typically need space before them
    inline_tags = r"(?:strong|em|b|i|u|code|span|a)"

    # Add space before opening tag if preceded by word character (not already spaced)
    # Match: word char + < + tag (no space between)
    html_content = re.sub(
        rf"(\w)(<{inline_tags}[\s>])",
        r"\1 \2",
        html_content,
        flags=re.IGNORECASE,
    )

    # Add space after closing tag if followed by word character (not already spaced)
    # Match: </tag> + word char (no space between)
    html_content = re.sub(
        rf"(</{inline_tags}>)(\w)",
        r"\1 \2",
        html_content,
        flags=re.IGNORECASE,
    )

    # Add space after closing tag if followed by dash (e.g., "</a>- text" -> "</a> - text")
    html_content = re.sub(
        rf"(</{inline_tags}>)(-)",
        r"\1 \2",
        html_content,
        flags=re.IGNORECASE,
    )

    # Add space before opening tag if preceded by dash (e.g., "-<strong>" -> "- <strong>")
    html_content = re.sub(
        rf"(-)(<{inline_tags}[\s>])",
        r"\1 \2",
        html_content,
        flags=re.IGNORECASE,
    )

    return html_content


def prettify_html(html_content: str) -> str:
    """Format HTML content for readability.

    Indents block elements but keeps text inline with its parent tag.
    For example: <p>Text here</p> instead of <p>\n Text here\n</p>
    """
    try:
        # Fix spacing issues first
        html_content = fix_html_spacing(html_content)
        soup = BeautifulSoup(html_content, "html.parser")
        return _prettify_element(soup, indent_level=0)
    except Exception as e:
        logger.warning(f"Failed to prettify HTML: {e}")
        return html_content


def _prettify_element(element, indent_level: int = 0) -> str:
    """Recursively prettify HTML elements with proper indentation.

    Block elements get their own lines with indentation.
    Inline text stays on the same line as its parent tag.
    CDATA sections in ac:plain-text-body are preserved for Confluence code blocks.
    """
    from bs4 import CData, NavigableString, Tag

    indent = "  " * indent_level
    result = []

    # Block-level elements that should be on their own line
    block_elements = {
        'html', 'head', 'body', 'div', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'ul', 'ol', 'li', 'table', 'tr', 'td', 'th', 'thead', 'tbody', 'tfoot',
        'form', 'fieldset', 'section', 'article', 'header', 'footer', 'nav',
        'aside', 'main', 'figure', 'figcaption', 'blockquote', 'pre', 'hr', 'br',
        'ac:layout', 'ac:layout-section', 'ac:layout-cell', 'ac:structured-macro',
        'ac:rich-text-body', 'ac:parameter', 'ac:plain-text-body'
    }

    # Elements that should preserve their raw content (including CDATA)
    preserve_content_elements = {'ac:plain-text-body'}

    for child in element.children:
        if isinstance(child, NavigableString):
            # Check if it's CDATA - preserve with wrapper
            if isinstance(child, CData):
                result.append(f"<![CDATA[{child}]]>")
            else:
                text = str(child)
                # Collapse whitespace but preserve meaningful spaces
                # First check if it's only whitespace (skip it)
                if not text.strip():
                    continue
                # Normalize internal whitespace: collapse runs of whitespace to single space
                import re
                text = re.sub(r'\s+', ' ', text)
                result.append(text)
        elif isinstance(child, Tag):
            tag_name = child.name.lower() if child.name else ''
            is_block = tag_name in block_elements

            # Build opening tag with attributes
            attrs = []
            for key, value in child.attrs.items():
                if isinstance(value, list):
                    value = ' '.join(value)
                attrs.append(f'{key}="{value}"')
            attrs_str = ' ' + ' '.join(attrs) if attrs else ''

            # Special handling for elements that need raw content preserved (code blocks)
            if tag_name in preserve_content_elements:
                # Preserve CDATA content exactly as-is for code blocks
                inner_content = ""
                for c in child.children:
                    if isinstance(c, CData):
                        inner_content += f"<![CDATA[{c}]]>"
                    elif isinstance(c, NavigableString):
                        # Wrap non-CDATA text in CDATA for safety
                        text = str(c)
                        if text.strip():
                            inner_content += f"<![CDATA[{text}]]>"
                    else:
                        inner_content += str(c)
                result.append(f"<{child.name}{attrs_str}>{inner_content}</{child.name}>")
                continue

            # Check if element has only text content (no nested tags)
            has_only_text = all(
                isinstance(c, NavigableString) for c in child.children
            )
            text_content = child.get_text().strip() if has_only_text else None

            # Check if element contains only inline content (text + inline tags, no block elements)
            def has_nested_blocks(el):
                for c in el.children:
                    if isinstance(c, Tag):
                        if c.name.lower() in block_elements:
                            return True
                return False

            has_block_children = has_nested_blocks(child)

            if is_block:
                if has_only_text and text_content:
                    # Block element with only text: <p>text</p>
                    result.append(f"\n{indent}<{child.name}{attrs_str}>{text_content}</{child.name}>")
                elif not list(child.children):
                    # Self-closing or empty block element
                    result.append(f"\n{indent}<{child.name}{attrs_str}></{child.name}>")
                elif not has_block_children:
                    # Block element with only inline content: <p><strong>text</strong>:</p>
                    inner = _prettify_element(child, indent_level)
                    result.append(f"\n{indent}<{child.name}{attrs_str}>{inner}</{child.name}>")
                else:
                    # Block element with nested block elements
                    inner = _prettify_element(child, indent_level + 1)
                    result.append(f"\n{indent}<{child.name}{attrs_str}>{inner}\n{indent}</{child.name}>")
            else:
                # Inline element - keep on same line
                if has_only_text:
                    result.append(f"<{child.name}{attrs_str}>{text_content or ''}</{child.name}>")
                else:
                    inner = _prettify_element(child, indent_level)
                    result.append(f"<{child.name}{attrs_str}>{inner}</{child.name}>")

    return ''.join(result)


def get_storage_path() -> Path:
    """Get the base storage path."""
    return Path.cwd() / LOCAL_STORAGE_DIR


def ensure_gitignore_entry(auto_add: bool = True) -> None:
    """Ensure the storage directory is in .gitignore.

    Args:
        auto_add: If True, automatically add the entry. If False, do nothing.
    """
    if not auto_add:
        return

    gitignore_path = Path.cwd() / ".gitignore"
    entry = f"/{LOCAL_STORAGE_DIR}/"

    # Check if .gitignore exists and already has our entry
    if gitignore_path.exists():
        content = gitignore_path.read_text()
        # Check for various formats of the entry
        if LOCAL_STORAGE_DIR in content:
            return  # Already has an entry for our directory

        # Append to existing file
        with open(gitignore_path, "a") as f:
            if not content.endswith("\n"):
                f.write("\n")
            f.write(f"\n# Better Confluence MCP local cache\n{entry}\n")
        logger.info(f"Added {entry} to .gitignore")
    else:
        # Create new .gitignore
        gitignore_path.write_text(f"# Better Confluence MCP local cache\n{entry}\n")
        logger.info(f"Created .gitignore with {entry}")


def get_all_synced_spaces() -> list[str]:
    """Get a list of all space keys that have been synced locally.

    Scans the storage directory for _metadata.json files and returns
    the space keys of all synced spaces.

    Returns:
        List of space keys that have local metadata files.
    """
    storage_path = get_storage_path()
    if not storage_path.exists():
        return []

    synced_spaces = []
    for space_dir in storage_path.iterdir():
        if space_dir.is_dir():
            metadata_file = space_dir / "_metadata.json"
            if metadata_file.exists():
                synced_spaces.append(space_dir.name)

    return sorted(synced_spaces)


def get_space_path(space_key: str) -> Path:
    """Get the path for a specific space."""
    return get_storage_path() / space_key


def get_metadata_path(space_key: str) -> Path:
    """Get the metadata file path for a space."""
    return get_space_path(space_key) / "_metadata.json"


def get_page_folder_path(space_key: str, ancestors: list[str], page_id: str) -> Path:
    """Get the folder path for a page based on its ancestors.

    Args:
        space_key: The space key
        ancestors: List of ancestor page IDs from root to immediate parent
        page_id: The page ID

    Returns:
        Path to the page folder
    """
    space_path = get_space_path(space_key)
    # Build path: space/ancestor1/ancestor2/.../page_id/
    path = space_path
    for ancestor_id in ancestors:
        path = path / ancestor_id
    path = path / page_id
    return path


def load_space_metadata(space_key: str) -> SpaceMetadata | None:
    """Load metadata for a space if it exists."""
    metadata_path = get_metadata_path(space_key)
    if not metadata_path.exists():
        return None
    try:
        with open(metadata_path, encoding="utf-8") as f:
            data = json.load(f)
        return SpaceMetadata.from_dict(data)
    except Exception as e:
        logger.error(f"Failed to load metadata for space {space_key}: {e}")
        return None


def save_space_metadata(metadata: SpaceMetadata) -> None:
    """Save metadata for a space."""
    metadata_path = get_metadata_path(metadata.space_key)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata.to_dict(), f, indent=2, ensure_ascii=False)


def save_page_html(
    space_key: str,
    page_id: str,
    title: str,
    html_content: str,
    version: int | None,
    url: str,
    ancestors: list[str],
) -> str:
    """Save a page as formatted HTML in tree structure.

    Args:
        space_key: The space key
        page_id: The page ID
        title: Page title
        html_content: Raw HTML content
        version: Page version
        url: Page URL
        ancestors: List of ancestor page IDs (from root to immediate parent)

    Returns:
        Relative file path
    """
    page_folder = get_page_folder_path(space_key, ancestors, page_id)
    page_folder.mkdir(parents=True, exist_ok=True)

    # Use sanitized title as filename
    safe_title = sanitize_filename(title)
    file_path = page_folder / f"{safe_title}.html"

    # Clean up any existing HTML files in the folder (handles title changes)
    for old_file in page_folder.glob("*.html"):
        if old_file != file_path:
            old_file.unlink()
            logger.debug(f"Removed old HTML file: {old_file.name}")

    # Prettify HTML (block elements indented, text inline)
    pretty_html = prettify_html(html_content)

    # Add metadata comment at top
    header_comment = f"""<!--
  Page ID: {page_id}
  Title: {title}
  Space: {space_key}
  Version: {version}
  URL: {url}
  Synced: {datetime.now(timezone.utc).isoformat()}
-->
"""
    full_content = header_comment + pretty_html

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(full_content)

    return str(file_path.relative_to(Path.cwd()))


def get_page_info(page_id: str) -> dict | None:
    """Find a page by ID across all synced spaces.

    Returns:
        Page info dict with space_key, or None if not found.
    """
    storage_path = get_storage_path()
    if not storage_path.exists():
        return None

    for space_dir in storage_path.iterdir():
        if space_dir.is_dir() and not space_dir.name.startswith("_"):
            metadata = load_space_metadata(space_dir.name)
            if metadata and page_id in metadata.page_index:
                return {
                    "space_key": space_dir.name,
                    **metadata.page_index[page_id],
                }
    return None


def build_page_tree(
    pages: list[dict],
) -> tuple[dict[str, PageNode], dict[str, dict[str, Any]]]:
    """Build a tree structure and flat index from a list of pages.

    Args:
        pages: List of page dicts with keys: page_id, title, version, url, ancestors, path, last_synced

    Returns:
        Tuple of (page_tree, page_index)
    """
    # First, create all nodes and index
    nodes: dict[str, PageNode] = {}
    page_index: dict[str, dict[str, Any]] = {}

    for page in pages:
        page_id = page["page_id"]
        nodes[page_id] = PageNode(
            page_id=page_id,
            title=page["title"],
            version=page.get("version"),
            url=page["url"],
            last_synced=page["last_synced"],
        )
        page_index[page_id] = {
            "title": page["title"],
            "version": page.get("version"),
            "url": page["url"],
            "path": page["path"],
            "ancestors": page.get("ancestors", []),
            "last_synced": page["last_synced"],
        }

    # Build tree by assigning children to parents
    root_nodes: dict[str, PageNode] = {}

    for page in pages:
        page_id = page["page_id"]
        ancestors = page.get("ancestors", [])

        if not ancestors:
            # This is a root page
            root_nodes[page_id] = nodes[page_id]
        else:
            # Find the immediate parent
            parent_id = ancestors[-1]
            if parent_id in nodes:
                nodes[parent_id].children[page_id] = nodes[page_id]
            else:
                # Parent not in our set, treat as root
                root_nodes[page_id] = nodes[page_id]

    return root_nodes, page_index


def merge_into_metadata(
    existing: SpaceMetadata | None,
    new_pages: list[dict],
    space_key: str,
    space_name: str,
) -> SpaceMetadata:
    """Merge new/updated pages into existing metadata.

    Args:
        existing: Existing space metadata (or None for new space)
        new_pages: List of new/updated page dicts
        space_key: Space key
        space_name: Space name

    Returns:
        Merged SpaceMetadata
    """
    if existing:
        # Update existing index with new pages
        merged_index = existing.page_index.copy()
        for page in new_pages:
            page_id = page["page_id"]
            merged_index[page_id] = {
                "title": page["title"],
                "version": page.get("version"),
                "url": page["url"],
                "path": page["path"],
                "ancestors": page.get("ancestors", []),
                "last_synced": page["last_synced"],
            }
    else:
        merged_index = {}
        for page in new_pages:
            page_id = page["page_id"]
            merged_index[page_id] = {
                "title": page["title"],
                "version": page.get("version"),
                "url": page["url"],
                "path": page["path"],
                "ancestors": page.get("ancestors", []),
                "last_synced": page["last_synced"],
            }

    # Rebuild tree from merged index
    all_pages = [{"page_id": pid, **info} for pid, info in merged_index.items()]
    page_tree, page_index = build_page_tree(all_pages)

    return SpaceMetadata(
        space_key=space_key,
        space_name=space_name,
        last_synced=datetime.now(timezone.utc).isoformat(),
        total_pages=len(page_index),
        page_tree=page_tree,
        page_index=page_index,
    )


def delete_page_folder(space_key: str, page_id: str, ancestors: list[str]) -> bool:
    """Delete a page folder from local storage.

    Args:
        space_key: The space key
        page_id: The page ID
        ancestors: List of ancestor page IDs

    Returns:
        True if deleted, False if not found
    """
    import shutil

    page_folder = get_page_folder_path(space_key, ancestors, page_id)
    if page_folder.exists():
        shutil.rmtree(page_folder)
        logger.debug(f"Deleted page folder: {page_folder}")
        return True
    return False


def check_and_cleanup_moved_page(
    space_key: str,
    page_id: str,
    new_ancestors: list[str],
    existing_metadata: SpaceMetadata | None,
) -> bool:
    """Check if a page has moved and clean up old location.

    Args:
        space_key: The space key
        page_id: The page ID
        new_ancestors: New ancestor list from Confluence
        existing_metadata: Existing space metadata

    Returns:
        True if page was moved and old location deleted
    """
    if not existing_metadata or page_id not in existing_metadata.page_index:
        return False

    old_info = existing_metadata.page_index[page_id]
    old_ancestors = old_info.get("ancestors", [])

    if old_ancestors != new_ancestors:
        # Page has moved - delete old folder
        deleted = delete_page_folder(space_key, page_id, old_ancestors)
        if deleted:
            logger.info(f"Page {page_id} moved: cleaned up old location")
        return deleted
    return False


def cleanup_deleted_pages(
    space_key: str,
    confluence_page_ids: set[str],
    existing_metadata: SpaceMetadata,
) -> list[str]:
    """Remove local pages that no longer exist in Confluence.

    Args:
        space_key: The space key
        confluence_page_ids: Set of page IDs currently in Confluence
        existing_metadata: Existing space metadata

    Returns:
        List of deleted page IDs
    """
    deleted_pages = []
    local_page_ids = set(existing_metadata.page_index.keys())

    # Find pages that exist locally but not in Confluence
    orphaned_ids = local_page_ids - confluence_page_ids

    for page_id in orphaned_ids:
        page_info = existing_metadata.page_index.get(page_id)
        if page_info:
            ancestors = page_info.get("ancestors", [])
            if delete_page_folder(space_key, page_id, ancestors):
                deleted_pages.append(page_id)
                logger.info(f"Deleted orphaned page {page_id}: {page_info.get('title')}")

    return deleted_pages


def remove_pages_from_metadata(
    metadata: SpaceMetadata,
    page_ids_to_remove: list[str],
) -> SpaceMetadata:
    """Remove pages from metadata and rebuild tree.

    Args:
        metadata: The space metadata
        page_ids_to_remove: List of page IDs to remove

    Returns:
        Updated SpaceMetadata
    """
    # Remove from index
    for page_id in page_ids_to_remove:
        if page_id in metadata.page_index:
            del metadata.page_index[page_id]

    # Rebuild tree
    all_pages = [{"page_id": pid, **info} for pid, info in metadata.page_index.items()]
    page_tree, page_index = build_page_tree(all_pages)

    return SpaceMetadata(
        space_key=metadata.space_key,
        space_name=metadata.space_name,
        last_synced=metadata.last_synced,
        total_pages=len(page_index),
        page_tree=page_tree,
        page_index=page_index,
    )


def get_attachments_folder_path(space_key: str, ancestors: list[str], page_id: str) -> Path:
    """Get the attachments folder path for a page.

    Args:
        space_key: The space key
        ancestors: List of ancestor page IDs from root to immediate parent
        page_id: The page ID

    Returns:
        Path to the attachments folder within the page folder
    """
    page_folder = get_page_folder_path(space_key, ancestors, page_id)
    return page_folder / "attachments"


def ensure_attachments_folder(space_key: str, ancestors: list[str], page_id: str) -> Path:
    """Ensure the attachments folder exists for a page.

    Args:
        space_key: The space key
        ancestors: List of ancestor page IDs from root to immediate parent
        page_id: The page ID

    Returns:
        Path to the attachments folder (created if needed)
    """
    attachments_folder = get_attachments_folder_path(space_key, ancestors, page_id)
    attachments_folder.mkdir(parents=True, exist_ok=True)
    return attachments_folder
