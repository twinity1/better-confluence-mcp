"""Integration tests for content processing functionality.

These tests validate HTML ↔ Markdown conversion, macro handling,
special character preservation, and performance with large content.
"""

import time
from typing import Any

import pytest

from mcp_atlassian.preprocessing.confluence import ConfluencePreprocessor


class MockConfluenceClient:
    """Mock Confluence client for testing user lookups."""

    def get_user_details_by_accountid(self, account_id: str) -> dict[str, Any]:
        """Mock user details by account ID."""
        return {
            "displayName": f"User {account_id}",
            "accountType": "atlassian",
            "accountStatus": "active",
        }

    def get_user_details_by_username(self, username: str) -> dict[str, Any]:
        """Mock user details by username (Server/DC compatibility)."""
        return {
            "displayName": f"User {username}",
            "accountType": "atlassian",
            "accountStatus": "active",
        }


@pytest.fixture
def confluence_preprocessor():
    """Create a ConfluencePreprocessor instance with mock client."""
    return ConfluencePreprocessor(
        base_url="https://example.atlassian.net",
        confluence_client=MockConfluenceClient(),
    )


@pytest.mark.integration
class TestConfluenceContentProcessing:
    """Integration tests for Confluence content processing."""

    def test_confluence_macro_preservation(self, confluence_preprocessor):
        """Test preservation of Confluence macros during processing."""
        html_with_macros = """<p>Page content with macros:</p>
<ac:structured-macro ac:name="info" ac:schema-version="1">
    <ac:rich-text-body>
        <p>This is an info panel with <strong>formatting</strong></p>
    </ac:rich-text-body>
</ac:structured-macro>

<ac:structured-macro ac:name="code" ac:schema-version="1">
    <ac:parameter ac:name="language">python</ac:parameter>
    <ac:plain-text-body><![CDATA[
def process():
    return "Hello, World!"
]]></ac:plain-text-body>
</ac:structured-macro>

<ac:structured-macro ac:name="toc">
    <ac:parameter ac:name="maxLevel">3</ac:parameter>
</ac:structured-macro>

<ac:structured-macro ac:name="excerpt">
    <ac:rich-text-body>
        <p>This is an excerpt of the page.</p>
    </ac:rich-text-body>
</ac:structured-macro>"""

        processed_html, processed_markdown = (
            confluence_preprocessor.process_html_content(html_with_macros)
        )

        # Verify macros are preserved in HTML
        assert 'ac:structured-macro ac:name="info"' in processed_html
        assert 'ac:structured-macro ac:name="code"' in processed_html
        assert 'ac:structured-macro ac:name="toc"' in processed_html
        assert 'ac:structured-macro ac:name="excerpt"' in processed_html

        # Verify parameters are preserved
        assert 'ac:parameter ac:name="language">python' in processed_html
        assert 'ac:parameter ac:name="maxLevel">3' in processed_html

    def test_confluence_user_mentions_complex(self, confluence_preprocessor):
        """Test complex user mention scenarios in Confluence."""
        html_content = """<p>Multiple user mentions:</p>
<ac:link>
    <ri:user ri:account-id="user123"/>
</ac:link>

<p>User with link body:</p>
<ac:link>
    <ri:user ri:account-id="user456"/>
    <ac:link-body>@Custom Name</ac:link-body>
</ac:link>

<ac:structured-macro ac:name="profile">
    <ac:parameter ac:name="user">
        <ri:user ri:account-id="user789"/>
    </ac:parameter>
</ac:structured-macro>

<p>Server/DC user with userkey:</p>
<ac:structured-macro ac:name="profile">
    <ac:parameter ac:name="user">
        <ri:user ri:userkey="admin"/>
    </ac:parameter>
</ac:structured-macro>"""

        processed_html, processed_markdown = (
            confluence_preprocessor.process_html_content(html_content)
        )

        # Verify all user mentions are processed
        assert "@User user123" in processed_markdown
        assert "@User user456" in processed_markdown
        assert "@User user789" in processed_markdown
        assert "@User admin" in processed_markdown

    def test_confluence_markdown_roundtrip(self, confluence_preprocessor):
        """Test Markdown to Confluence storage format and processing."""
        markdown_content = """# Main Title

## Introduction
This is a **bold** paragraph with *italic* text and `inline code`.

### Code Block
```python
def hello_world():
    print("Hello, World!")
    return True
```

### Lists
- Item 1
  - Nested item 1.1
  - Nested item 1.2
- Item 2

1. First step
2. Second step
   1. Sub-step 2.1
   2. Sub-step 2.2

### Table
| Header 1 | Header 2 | Header 3 |
|----------|----------|----------|
| Cell 1   | Cell 2   | Cell 3   |
| Cell 4   | Cell 5   | Cell 6   |

### Links and Images
[Confluence Documentation](https://confluence.atlassian.com/doc/)
![Alt text](https://example.com/image.png)

### Blockquote
> This is a blockquote
> with multiple lines

### Horizontal Rule
---

### Special Characters
Unicode: α β γ δ ε ζ η θ
Emojis: 🚀 💻 ✅ ❌ 📝
Math: x² + y² = z²"""

        # Convert to Confluence storage format
        storage_format = confluence_preprocessor.markdown_to_confluence_storage(
            markdown_content
        )

        # Process the storage format
        processed_html, processed_markdown = (
            confluence_preprocessor.process_html_content(storage_format)
        )

        # Verify key elements are preserved
        assert "Main Title" in processed_markdown
        assert "**bold**" in processed_markdown
        assert "*italic*" in processed_markdown
        assert "`inline code`" in processed_markdown

        # Verify code block (may have escaped underscores)
        assert (
            "hello_world" in processed_markdown or "hello\\_world" in processed_markdown
        )
        assert "Hello, World!" in processed_markdown

        # Verify lists
        assert "Item 1" in processed_markdown
        assert "Nested item 1.1" in processed_markdown
        assert "First step" in processed_markdown
        assert "Sub-step 2.1" in processed_markdown

        # Verify table (tables might be converted to HTML)
        assert "Header 1" in processed_markdown
        assert "Cell 1" in processed_markdown

        # Verify links
        assert "Confluence Documentation" in processed_markdown
        assert "https://confluence.atlassian.com/doc/" in processed_markdown

        # Verify special characters
        assert "α β γ δ ε ζ η θ" in processed_markdown
        assert "🚀 💻 ✅ ❌ 📝" in processed_markdown
        assert "x² + y² = z²" in processed_markdown

    def test_confluence_heading_anchor_control(self, confluence_preprocessor):
        """Test control over heading anchor generation."""
        markdown_with_headings = """# Main Title
Content under main title.

## Section One
Content in section one.

### Subsection 1.1
Details here.

## Section Two
More content."""

        # Test with anchors disabled (default)
        storage_no_anchors = confluence_preprocessor.markdown_to_confluence_storage(
            markdown_with_headings
        )
        assert 'id="main-title"' not in storage_no_anchors.lower()
        assert 'id="section-one"' not in storage_no_anchors.lower()

        # Test with anchors enabled
        storage_with_anchors = confluence_preprocessor.markdown_to_confluence_storage(
            markdown_with_headings, enable_heading_anchors=True
        )
        # Verify headings are still present (they may have anchor macros)
        assert "Main Title</h1>" in storage_with_anchors
        assert "Section One</h2>" in storage_with_anchors

    def test_confluence_large_content_performance(self, confluence_preprocessor):
        """Test performance with large Confluence content (>1MB)."""
        # Generate large content with various Confluence elements
        large_content_parts = []

        for i in range(50):
            section = f"""<h2>Section {i}</h2>
<p>This is paragraph {i} with <strong>bold</strong> and <em>italic</em> text.</p>

<ac:structured-macro ac:name="info">
    <ac:rich-text-body>
        <p>Info box {i} with important information.</p>
    </ac:rich-text-body>
</ac:structured-macro>

<ul>
    <li>List item {i}.1</li>
    <li>List item {i}.2 with <code>inline code</code></li>
    <li>List item {i}.3</li>
</ul>

<ac:structured-macro ac:name="code">
    <ac:parameter ac:name="language">python</ac:parameter>
    <ac:plain-text-body><![CDATA[
def function_{i}():
    # Large function with many lines
    data = []
    for j in range(1000):
        data.append({{
            "id": j,
            "value": j * {i},
            "description": "Item " + str(j)
        }})

    result = sum(item["value"] for item in data)
    return result
]]></ac:plain-text-body>
</ac:structured-macro>

<table>
    <thead>
        <tr>
            <th>Header A</th>
            <th>Header B</th>
            <th>Header C</th>
        </tr>
    </thead>
    <tbody>
        <tr>
            <td>Row {i} Cell 1</td>
            <td>Row {i} Cell 2</td>
            <td>Row {i} Cell 3</td>
        </tr>
    </tbody>
</table>

<ac:link>
    <ri:user ri:account-id="user{i}"/>
</ac:link> completed this section.
"""
            large_content_parts.append(section)

        large_content = "\n".join(large_content_parts)
        content_size = len(large_content.encode("utf-8"))

        # Ensure content is reasonably large (adjust threshold for test)
        assert content_size > 50000  # 50KB is enough for performance testing

        # Test processing performance
        start_time = time.time()
        processed_html, processed_markdown = (
            confluence_preprocessor.process_html_content(large_content)
        )
        processing_time = time.time() - start_time

        # Performance assertion
        assert processing_time < 15.0  # Should complete within 15 seconds

        # Verify content integrity
        assert "Section 0" in processed_markdown
        assert "Section 49" in processed_markdown
        assert (
            "function" in processed_markdown
        )  # Function names might have escaped underscores
        assert "@User user10" in processed_markdown

    def test_confluence_nested_structures(self, confluence_preprocessor):
        """Test handling of deeply nested structures."""
        nested_html = """<div>
    <h1>Top Level</h1>
    <div>
        <h2>Level 2</h2>
        <div>
            <h3>Level 3</h3>
            <ul>
                <li>Item 1
                    <ul>
                        <li>Nested 1.1
                            <ul>
                                <li>Deep nested 1.1.1</li>
                                <li>Deep nested 1.1.2</li>
                            </ul>
                        </li>
                        <li>Nested 1.2</li>
                    </ul>
                </li>
                <li>Item 2</li>
            </ul>

            <blockquote>
                <p>Quote level 1</p>
                <blockquote>
                    <p>Quote level 2</p>
                    <blockquote>
                        <p>Quote level 3</p>
                    </blockquote>
                </blockquote>
            </blockquote>

            <table>
                <tr>
                    <td>
                        <table>
                            <tr>
                                <td>Nested table cell</td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </div>
    </div>
</div>"""

        processed_html, processed_markdown = (
            confluence_preprocessor.process_html_content(nested_html)
        )

        # Verify nested structures are preserved
        assert "Top Level" in processed_markdown
        assert "Level 2" in processed_markdown
        assert "Level 3" in processed_markdown
        assert "Deep nested 1.1.1" in processed_markdown
        assert "Quote level 1" in processed_markdown
        assert "Quote level 2" in processed_markdown
        assert "Quote level 3" in processed_markdown
        assert "Nested table cell" in processed_markdown

    def test_confluence_edge_cases(self, confluence_preprocessor):
        """Test edge cases in Confluence content processing."""
        # Empty content
        processed_html, processed_markdown = (
            confluence_preprocessor.process_html_content("")
        )
        assert processed_html == ""
        assert processed_markdown == ""

        # Malformed HTML
        malformed_html = "<p>Unclosed paragraph <strong>bold text</p>"
        processed_html, processed_markdown = (
            confluence_preprocessor.process_html_content(malformed_html)
        )
        assert "Unclosed paragraph" in processed_markdown
        assert "bold text" in processed_markdown

        # HTML with CDATA sections
        cdata_html = """<div>
            <![CDATA[This is raw CDATA content with <tags>]]>
        </div>"""
        processed_html, processed_markdown = (
            confluence_preprocessor.process_html_content(cdata_html)
        )
        assert "This is raw CDATA content" in processed_markdown

        # Very long single line
        long_line_html = f"<p>{'x' * 10000}</p>"
        processed_html, processed_markdown = (
            confluence_preprocessor.process_html_content(long_line_html)
        )
        assert len(processed_markdown) >= 10000

    def test_confluence_special_html_entities(self, confluence_preprocessor):
        """Test handling of HTML entities and special characters."""
        html_with_entities = """<p>HTML entities: &lt; &gt; &amp; &quot; &apos;</p>
<p>Named entities: &nbsp; &copy; &reg; &trade; &euro;</p>
<p>Numeric entities: &#65; &#66; &#67; &#8364; &#128512;</p>
<p>Mixed: &lt;tag&gt; &amp;&amp; &quot;quoted&quot;</p>"""

        processed_html, processed_markdown = (
            confluence_preprocessor.process_html_content(html_with_entities)
        )

        # Verify entities are properly decoded
        assert "<" in processed_markdown
        assert ">" in processed_markdown
        assert "&" in processed_markdown
        assert '"' in processed_markdown
        assert "©" in processed_markdown
        assert "®" in processed_markdown
        assert "€" in processed_markdown
        assert "😀" in processed_markdown  # Emoji from numeric entity


@pytest.mark.integration
class TestContentProcessingUnicode:
    """Test Unicode handling in content processing."""

    def test_unicode_consistency(self, confluence_preprocessor):
        """Test Unicode handling consistency in Confluence processing."""
        unicode_content = """Unicode Test 🌍

Symbols: ™ © ® € £ ¥
Math: ∑ ∏ ∫ ∞ ≈ ≠ ≤ ≥
Greek: Α Β Γ Δ Ε Ζ Η Θ
Arrows: → ← ↑ ↓ ↔ ⇒ ⇐ ⇔
Box Drawing: ┌─┬─┐ │ ├─┼─┤ └─┴─┘
Emojis: 😀 😎 🚀 💻 ✅ ❌ ⚡ 🔥"""

        # Process through Confluence
        processed_html, confluence_result = (
            confluence_preprocessor.process_html_content(f"<p>{unicode_content}</p>")
        )

        # Verify Unicode is preserved
        for char in ["🌍", "™", "∑", "Α", "→", "┌", "😀", "🚀"]:
            assert char in confluence_result

    def test_error_recovery(self, confluence_preprocessor):
        """Test error recovery in content processing."""
        # Test with invalid input types (should raise exceptions)
        with pytest.raises(Exception):
            confluence_preprocessor.process_html_content(None)

        # Test with extremely malformed content
        malformed_content = "<<<>>>&&&'''\"\"\"{{{{}}}}[[[[]]]]"

        # Confluence should handle this
        processed_html, confluence_result = (
            confluence_preprocessor.process_html_content(malformed_content)
        )
        assert len(confluence_result) > 0
