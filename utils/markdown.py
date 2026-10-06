"""Safe Markdown formatting shared by documentation and UI-help messages."""

import html
import re


def _format_inline(text: str, open_in_new_tab: bool = False) -> str:
    """Render the small, safe Markdown subset used by ABOUT.md and documentation."""
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)

    def link(match: re.Match[str]) -> str:
        label, url = match.groups()
        if re.match(r"^(https?://|mailto:|/|#)", url):
            target_attr = ' target="_blank" rel="noopener noreferrer"' if open_in_new_tab else ""
            return f'<a href="{html.escape(url, quote=True)}"{target_attr}>{label}</a>'
        return label

    return re.sub(r"\[([^]]+)\]\(([^)]+)\)", link, escaped)


def render_markdown(markdown_source: str, *, open_in_new_tab: bool = False) -> str:
    """Convert headings, code blocks, lists, and paragraphs in Markdown to page markup."""
    blocks: list[str] = []
    list_items: list[str] = []
    list_tag: str | None = None
    paragraph: list[str] = []
    in_code_block: bool = False
    code_block_lines: list[str] = []
    code_block_lang: str = ""

    def inline(text: str) -> str:
        return _format_inline(text, open_in_new_tab=open_in_new_tab)

    def flush_list() -> None:
        nonlocal list_items, list_tag
        if list_items and list_tag:
            blocks.append(f"<{list_tag}>" + "".join(list_items) + f"</{list_tag}>")
        list_items = []
        list_tag = None

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(f"<p>{inline(' '.join(paragraph))}</p>")
        paragraph = []

    def flush_code_block() -> None:
        nonlocal code_block_lines, code_block_lang, in_code_block
        if in_code_block:
            escaped = html.escape("\n".join(code_block_lines))
            lang_attr = f' class="language-{html.escape(code_block_lang)}"' if code_block_lang else ""
            blocks.append(f"<pre><code{lang_attr}>{escaped}</code></pre>")
        code_block_lines = []
        code_block_lang = ""
        in_code_block = False

    for raw_line in markdown_source.splitlines():
        trimmed = raw_line.strip()

        if trimmed.startswith("```"):
            if in_code_block:
                flush_code_block()
            else:
                flush_paragraph()
                flush_list()
                in_code_block = True
                code_block_lang = trimmed[3:].strip()
                code_block_lines = []
            continue

        if in_code_block:
            code_block_lines.append(raw_line)
            continue

        line = trimmed
        image = re.fullmatch(r"!\[([^]]*)\]\((/docs/images/[A-Za-z0-9_./-]+)\)", line)
        heading = re.match(r"^(#{1,4})\s+(.+)$", line)
        task_item = re.match(r"^[-*]\s+\[([ xX])\]\s+(.+)$", line)
        unordered_item = re.match(r"^[-*]\s+(.+)$", line)
        ordered_item = re.match(r"^\d+\.\s+(.+)$", line)

        if image:
            flush_paragraph()
            flush_list()
            alt, url = image.groups()
            safe_alt = html.escape(alt, quote=True)
            safe_url = html.escape(url, quote=True)
            blocks.append(
                f'<p><a href="{safe_url}" target="_blank" rel="noopener noreferrer">'
                f'<img src="{safe_url}" alt="{safe_alt}" loading="lazy"></a></p>'
            )
        elif heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            heading_text = heading.group(2)
            blocks.append(f"<h{level}>{inline(heading_text)}</h{level}>")
        elif task_item:
            flush_paragraph()
            if list_tag and list_tag != "ul":
                flush_list()
            list_tag = "ul"
            checked = " checked" if task_item.group(1).lower() == "x" else ""
            item_text = task_item.group(2)
            list_items.append(f'<li class="task-list-item"><input type="checkbox" disabled{checked}> {inline(item_text)}</li>')
        elif unordered_item or ordered_item:
            flush_paragraph()
            item_tag = "ul" if unordered_item else "ol"
            if list_tag and list_tag != item_tag:
                flush_list()
            list_tag = item_tag
            item_text = (unordered_item or ordered_item).group(1)
            list_items.append(f"<li>{inline(item_text)}</li>")
        elif line == "":
            flush_paragraph()
            flush_list()
        elif line in {"---", "***", "___"}:
            flush_paragraph()
            flush_list()
            blocks.append("<hr>")
        else:
            paragraph.append(line)

    flush_paragraph()
    flush_list()
    flush_code_block()
    return "\n".join(blocks)


