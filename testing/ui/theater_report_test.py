"""Browser validation of the report modal using the actual canvas markup."""

from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from playwright.sync_api import Route, expect, sync_playwright


def test_report_modal_settings_submission_and_retry() -> None:
    template = Path('templates/canvas.html').read_text(encoding='utf-8')
    css = re.search(r'<style>(.*?)</style>', template, re.DOTALL).group(1)
    start = template.index('    <!-- Pullout Navigation & Help Menu Drawer -->')
    end = template.index('    <div id="clip-share-modal"', start)
    markup = template[start:end]
    page_html = f'''<html><head><meta charset="utf-8"><style>{css}</style></head><body>
    {markup}<script type="module">
    import {{setupTheaterReport}} from '/static/js/theater-report.js';
    document.getElementById('pullout-overlay').classList.add('active');
    document.getElementById('pullout-overlay').setAttribute('aria-hidden', 'false');
    setupTheaterReport('stage', () => {{}});
    </script></body></html>'''
    submissions: list[str] = []

    def serve(route: Route) -> None:
        path = urlsplit(route.request.url).path
        if path == '/static/js/theater-report.js':
            route.fulfill(body=Path('static/js/theater-report.js').read_text(encoding='utf-8'), content_type='text/javascript')
        elif path in ('/api/theaters/stage/reports', '/api/reports'):
            submissions.append(route.request.post_data or '')
            if len(submissions) == 1:
                route.fulfill(status=503, json={'detail': 'Could not save your report. Please try again.'})
            else:
                route.fulfill(status=201, json={'report_id': 2, 'status': 'ok'})
        else:
            route.fulfill(body=page_html, content_type='text/html')

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge' if sys.platform == 'win32' else None)
        page = browser.new_page(viewport={'width': 1000, 'height': 850})
        page.route('**/*', serve)
        page.goto('http://narratron.test/')
        settings = page.locator('.pullout-body > div').filter(has=page.get_by_text('Settings', exact=True))
        expect(settings).to_have_count(1)
        expect(settings.get_by_text('Minimal Mode')).to_be_visible()
        expect(settings.get_by_text('Input Settings')).to_be_visible()
        expect(settings.get_by_text('Edit theater.yaml')).to_be_visible()
        page.get_by_role('button', name='Report Theater').click()
        modal = page.locator('#report-theater-modal')
        expect(modal).to_be_visible()
        expect(page.get_by_label('Report type', exact=True)).to_be_focused()
        page.get_by_label('Report type', exact=True).select_option('harassment')
        page.get_by_label('What happened?').fill('Abusive messages')
        page.get_by_role('button', name='Send Report').click()
        expect(page.get_by_role('status')).to_contain_text('Please try again')
        expect(page.get_by_label('What happened?')).to_have_value('Abusive messages')
        page.get_by_role('button', name='Send Report').click()
        expect(page.get_by_role('status')).to_contain_text('Report sent')
        assert len(submissions) == 2
        expect(page.get_by_role('button', name='Done')).to_be_focused()
        page.keyboard.press('Escape')
        expect(modal).to_have_attribute('aria-hidden', 'true')
        page.get_by_role('button', name='Report Theater').click()
        expect(page.get_by_label('What happened?')).to_have_value('')
        page.get_by_role('button', name='Cancel').click()
        expect(modal).to_have_attribute('aria-hidden', 'true')
        for category in ('bug', 'suggestion'):
            page.get_by_role('button', name='Report Theater').click()
            page.get_by_label('Report type', exact=True).select_option(category)
            expect(page.locator('#report-theater-description')).to_contain_text('feedback')
            page.get_by_label('What happened?').fill('Please improve audio reconnect behavior.')
            page.get_by_role('button', name='Send Report').click()
            expect(page.get_by_role('status')).to_contain_text('Feedback sent')
            assert f'"category":"{category}"' in submissions[-1]
            assert '"theater_id":"stage"' in submissions[-1]
            page.get_by_role('button', name='Done').click()
        browser.close()
