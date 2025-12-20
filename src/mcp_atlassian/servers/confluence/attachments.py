"""Confluence attachment tools - download_attachments, upload_attachment, create_mermaid_diagram."""

import json
import logging
import os
from pathlib import Path
from typing import Annotated

from fastmcp import Context
from pydantic import Field

from mcp_atlassian.local_storage import (
    ensure_attachments_folder,
    get_page_info,
)
from mcp_atlassian.servers.dependencies import get_confluence_fetcher
from mcp_atlassian.utils.decorators import check_write_access

from ._server import confluence_mcp

logger = logging.getLogger(__name__)


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

    Example image with full container width (recommended):
    <ac:image ac:align="center" ac:layout="center" ac:width="736">
      <ri:attachment ri:filename="screenshot.png"/>
    </ac:image>

    Note: Use ac:width="736" for images to display at 100% container width.

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

    This tool renders the mermaid source to a high-quality PNG image (8x scale
    for crisp text) and uploads it to Confluence.
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

    ## Line Breaks in Node Labels

    Use `<br>` for line breaks in mermaid node labels, NOT `\\n`:
    - Correct: `Node["Line 1<br>Line 2"]`
    - Wrong: `Node["Line 1\\nLine 2"]`

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

        # Render to PNG using mermaid-cli with high quality (8x scale for crisp text)
        # PNG is used because Confluence Cloud's API-uploaded SVGs don't render text
        # correctly (the UI uses a different Media Services flow not available via API)
        from mermaid_cli import render_mermaid

        # Scale viewport based on diagram complexity (number of lines)
        line_count = len(mermaid_source.strip().split("\n"))
        # Base: 1920x1080 for ~20 lines, scale up for larger diagrams
        scale_factor = max(1.0, line_count / 20)
        viewport_width = int(1920 * scale_factor)
        viewport_height = int(1080 * scale_factor)

        _, _, png_bytes = await render_mermaid(
            mermaid_source,
            output_format="png",
            viewport={"width": viewport_width, "height": viewport_height, "deviceScaleFactor": 8},
        )
        png_path.write_bytes(png_bytes)

        if not png_path.exists():
            return json.dumps(
                {"error": "Failed to render mermaid diagram - PNG not created."},
                indent=2,
                ensure_ascii=False,
            )

        # Upload PNG to Confluence
        confluence_fetcher.confluence.attach_file(
            filename=str(png_path),
            page_id=page_id,
            comment=f"Mermaid diagram: {base_name}",
        )

        # Build HTML snippet for inline embedding
        html_snippet = f'<ac:image ac:align="center" ac:alt="{png_filename}" ac:layout="center" ac:width="736"><ri:attachment ri:filename="{png_filename}"></ri:attachment></ac:image>'

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
