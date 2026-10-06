"""Documentation images render safely without accepting arbitrary markup."""

import pytest

from utils.markdown import render_markdown


def test_documentation_image_escapes_alt_text_and_closes_surrounding_blocks() -> None:
    rendered = render_markdown(
        '- first\n![Tokens "<demo>" & map](/docs/images/stamp_manager_tray.png)\nAfter'
    )
    assert rendered.startswith('<ul><li>first</li></ul>\n<p><a ')
    assert 'src="/docs/images/stamp_manager_tray.png"' in rendered
    assert 'alt="Tokens &quot;&lt;demo&gt;&quot; &amp; map"' in rendered
    assert 'loading="lazy"' in rendered
    assert 'rel="noopener noreferrer"' in rendered
    assert rendered.endswith('</a></p>\n<p>After</p>')


@pytest.mark.parametrize("source", [
    '![bad](javascript:alert(1))',
    '![bad](data:image/svg+xml;base64,PHN2Zz4=)',
    '![bad](//example.test/tracker.png)',
    '![bad](/docs/images/test.png" onerror="alert)',
    '<img src=x onerror="alert(1)">',
])
def test_documentation_images_reject_untrusted_sources_and_markup(source: str) -> None:
    assert '<img ' not in render_markdown(source)


def test_image_syntax_inside_code_stays_literal() -> None:
    rendered = render_markdown('```\n![Map](/docs/images/map.png)\n```')
    assert '<img ' not in rendered
    assert '![Map](/docs/images/map.png)' in rendered
