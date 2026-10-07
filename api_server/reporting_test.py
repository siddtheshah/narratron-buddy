from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import api_server.reporting as reporting
from storage.database import LocalDatabaseManager
from storage.theater_repository import TheaterRepository


@pytest.mark.asyncio
async def test_reports_link_shared_evidence_and_support_moderation(tmp_path: Path) -> None:
    database = LocalDatabaseManager(str(tmp_path / 'database.db'))
    try:
        owner = database.register_user('report_owner', 'owner@example.com', 'password')
        reporter = database.register_user('report_viewer', 'viewer@example.com', 'password')
        database.record_deployment('stage', owner['id'], 'KEY', name='Reported Theater')
        database.add_contributor('stage', owner['id'], reporter['id'])
        database.request_baton('stage', owner['id'], reporter['id'])
        database.accept_baton('stage', reporter['id'])
        repository = TheaterRepository(tmp_path / 'theaters')
        source = repository.theater_path('stage')
        source.mkdir()
        (source / 'theater.json').write_text('{"name":"Reported Theater"}')
        (source / 'theater.yaml').write_text('name: Reported Theater')
        manager = MagicMock()
        manager.theater.return_value.directory.return_value = source
        states = MagicMock()
        request = Request({'type': 'http', 'headers': []})
        body = reporting.ReportTheaterRequest(reason='harassment', details='Abusive messages')
        with patch.object(reporting, 'db', database), patch.object(reporting, 'theater_repository', repository), \
             patch.object(reporting, 'theater_manager', manager), patch.object(reporting, 'canvas_states', states), \
             patch.object(reporting, 'get_current_user_async', AsyncMock(return_value=reporter)), \
             patch.object(reporting, '_require_canvas_access_async', AsyncMock()):
            first = await reporting.report_theater('stage', body, request)
            second = await reporting.report_theater('stage', body, request)
        assert first['report_id'] != second['report_id']
        reports = database.list_theater_reports()
        assert len(reports) == 2
        assert reports[0]['snapshot_id'] == reports[1]['snapshot_id']
        assert reports[0]['owner_user_id'] == owner['id']
        assert reports[0]['active_orator_user_id'] == reporter['id']
        assert reports[0]['reporter_username'] == 'report_viewer'
        assert len(list((tmp_path / 'flagged_theaters').glob('*.zip'))) == 1
        assert database.review_theater_report(int(first['report_id']), reporter['id'], 'actioned', 'Confirmed')
        assert len(database.list_theater_reports('actioned')) == 1
        token = database.create_auth_session(owner['id'])
        assert database.validate_session_token(token)
        assert database.ban_user(owner['id'], 'Confirmed harassment')
        assert database.authenticate_user('report_owner', 'password') is None
        assert database.validate_session_token(token) is None
        # Evidence remains accessible after the theater and account are deleted.
        database.delete_deployment('stage')
        database.delete_user(owner['id'])
        assert len(database.list_theater_reports()) == 1
        assert Path(reports[0]['storage_path']).is_file()
    finally:
        database.close()


@pytest.mark.asyncio
async def test_report_access_validation_and_failed_capture(tmp_path: Path) -> None:
    request = Request({'type': 'http', 'headers': []})
    body = reporting.ReportTheaterRequest(reason='other', details='Problem')
    database = MagicMock()
    database.get_deployment.return_value = {'user_id': 1, 'name': 'Stage'}
    manager = MagicMock()
    manager.theater.return_value.directory.return_value = tmp_path / 'missing'
    repository = MagicMock()
    repository.reconstruct_theater.return_value = False
    with patch.object(reporting, 'get_current_user_async', AsyncMock(return_value=None)):
        with pytest.raises(HTTPException) as denied:
            await reporting.report_theater('stage', body, request)
        assert denied.value.status_code == 401
    access = AsyncMock(side_effect=HTTPException(403, 'Access denied'))
    with patch.object(reporting, 'get_current_user_async', AsyncMock(return_value={'id': 2})), \
         patch.object(reporting, '_require_canvas_access_async', access), patch.object(reporting, 'db', database), \
         patch.object(reporting, 'theater_manager', manager), patch.object(reporting, 'theater_repository', repository):
        with pytest.raises(HTTPException) as denied:
            await reporting.report_theater('stage', body, request)
        assert denied.value.status_code == 403
        database.get_deployment.assert_not_called()
        access.side_effect = None
        with pytest.raises(HTTPException) as invalid:
            await reporting.report_theater('stage', reporting.ReportTheaterRequest(reason='other', details='  '), request)
        assert invalid.value.status_code == 422
        with pytest.raises(HTTPException) as failed:
            await reporting.report_theater('stage', body, request)
        assert failed.value.status_code == 503
        database.record_theater_report.assert_not_called()


def test_feedback_routes_persist_and_triage_without_flagging_theaters(tmp_path: Path) -> None:
    database = LocalDatabaseManager(str(tmp_path / 'feedback.db'))
    try:
        user = database.register_user('feedback_author', 'feedback@example.com', 'password')
        access = AsyncMock()
        with patch.object(reporting, 'db', database), \
             patch.object(reporting, 'get_current_user_async', AsyncMock(return_value=user)), \
             patch.object(reporting, '_require_canvas_access_async', access), \
             patch.object(reporting, 'snapshot_theater') as snapshot:
            client = TestClient(reporting.app)
            bug = client.post('/api/reports', json={
                'category': 'bug', 'details': '  Audio stops after reconnecting.  ', 'theater_id': 'stage',
            })
            suggestion = client.post('/api/reports', json={
                'category': 'suggestion', 'details': 'Add keyboard shortcuts for scene history.',
            })
            assert bug.status_code == suggestion.status_code == 201
            assert bug.json()['report_id'] != suggestion.json()['report_id']
            access.assert_awaited_once()
            snapshot.assert_not_called()
            bugs = database.list_user_feedback('bug')
            suggestions = database.list_user_feedback('suggestion')
            assert bugs[0]['details'] == 'Audio stops after reconnecting.'
            assert bugs[0]['theater_id'] == 'stage'
            assert bugs[0]['reporter_user_id'] == user['id']
            assert suggestions[0]['theater_id'] is None
            assert database.list_theater_reports() == []
            assert database.review_user_feedback(bug.json()['report_id'], user['id'], 'actioned', 'Fixed')
            assert database.list_user_feedback('bug') == []
            assert database.list_user_feedback('bug', status='actioned')[0]['review_notes'] == 'Fixed'
            for body in (
                {'category': 'unknown', 'details': 'Example'},
                {'category': 'bug', 'details': '  '},
                {'category': 'bug', 'details': 'x' * 4001},
            ):
                assert client.post('/api/reports', json=body).status_code == 422
            assert client.post('/api/reports', json={
                'category': 'bug', 'details': 'Problem', 'theater_id': '../escape',
            }).status_code == 400
            database.delete_user(user['id'])
            assert database.list_user_feedback('suggestion')[0]['reporter_user_id'] == user['id']
    finally:
        database.close()


@pytest.mark.asyncio
async def test_feedback_authentication_access_and_database_failures() -> None:
    request = Request({'type': 'http', 'headers': []})
    body = reporting.FeedbackRequest(category='bug', details='Audio stopped', theater_id='stage')
    database = MagicMock()
    with patch.object(reporting, 'get_current_user_async', AsyncMock(return_value=None)), \
         patch.object(reporting, 'db', database):
        with pytest.raises(HTTPException) as denied:
            await reporting.file_feedback(body, request)
        assert denied.value.status_code == 401
        database.record_user_feedback.assert_not_called()
    access = AsyncMock(side_effect=HTTPException(403, 'Access denied'))
    with patch.object(reporting, 'get_current_user_async', AsyncMock(return_value={'id': 2})), \
         patch.object(reporting, 'db', database), \
         patch.object(reporting, '_require_canvas_access_async', access):
        with pytest.raises(HTTPException) as denied:
            await reporting.file_feedback(body, request)
        assert denied.value.status_code == 403
        database.record_user_feedback.assert_not_called()
        access.side_effect = None
        database.record_user_feedback.side_effect = RuntimeError('Database unavailable')
        with pytest.raises(HTTPException) as failed:
            await reporting.file_feedback(body, request)
        assert failed.value.status_code == 503
