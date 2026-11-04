import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import requests

from services.glados_service import GLaDOSService
from services.workbuddy_service import WorkBuddyService


def response(status, payload):
    result = requests.Response()
    result.status_code = status
    result.url = 'https://example.invalid/api'
    result._content = json.dumps(payload).encode('utf-8')
    return result


class GladosRequestTests(unittest.TestCase):
    def test_domain_token_and_full_cookie(self):
        cookie = 'gld:accts=fake; gld:accts.sig=a; gld:sess=fake; gld:sess.sig=b'
        for configured, expected in [
            (None, 'https://glados.rocks'),
            ('', 'https://glados.rocks'),
            (' https://glados.rocks/ ', 'https://glados.rocks'),
            ('https://glados.cloud', 'https://glados.cloud'),
        ]:
            with self.subTest(configured=configured), patch.dict(os.environ, {}, clear=True):
                os.environ['GR_COOKIE'] = cookie
                if configured is not None:
                    os.environ['GLADOS_BASE_URL'] = configured
                service = GLaDOSService()
                config = service.get_account_configs()[0]
                with patch.object(service.session, 'request', return_value=response(200, {'code': 0, 'data': {}})) as request, redirect_stdout(io.StringIO()):
                    self.assertTrue(service.do_checkin(config)['success'])
                    service.get_usage_info(config)
                checkin, status = request.call_args_list
                self.assertEqual(checkin.args, ('POST', expected + '/api/user/checkin'))
                self.assertEqual(json.loads(checkin.kwargs['data']), {'token': expected.split('://')[1]})
                self.assertEqual(status.args, ('GET', expected + '/api/user/status'))
                for call in (checkin, status):
                    self.assertEqual(call.kwargs['headers']['cookie'], cookie)
                    self.assertEqual(call.kwargs['headers']['origin'], expected)


class WorkBuddyResponseTests(unittest.TestCase):
    def setUp(self):
        self.service = WorkBuddyService()
        self.config = dict(account_id='test-account', access_token='fake', user_id='fake', domain='', expires_at=9999999999999)

    def test_duplicate_http_400_succeeds_without_retry(self):
        replies = [response(200, {'code': 0, 'data': {'today_checked_in': False}}),
                   response(400, {'code': 10001, 'msg': '今天已签到，请明天再来'})]
        with patch.object(self.service.session, 'request', side_effect=replies) as request, patch('services.base_service.time.sleep') as sleep, redirect_stdout(io.StringIO()):
            result = self.service.process_single_account(self.config)
        self.assertTrue(result.success)
        self.assertTrue(result.data['already_checked_in'])
        self.assertEqual(request.call_count, 2)
        sleep.assert_not_called()

    def test_other_http_errors_are_not_success(self):
        for path, status, payload in [
            ('daily-checkin', 400, {'code': 10002}),
            ('daily-checkin', 401, {'code': 10001}),
            ('daily-checkin', 403, {'code': 10001}),
            ('daily-checkin', 500, {'code': 10001}),
            ('checkin-status', 400, {'code': 10001}),
            ('daily-checkin', 400, []),
        ]:
            with self.subTest(path=path, status=status, payload=payload), patch.object(self.service.session, 'request', return_value=response(status, payload)):
                with self.assertRaises(requests.exceptions.HTTPError):
                    self.service._api(path, self.config)

    def test_non_json_http_error_is_preserved(self):
        reply = response(400, {})
        reply._content = b'<html>Bad Request</html>'
        with patch.object(self.service.session, 'request', return_value=reply):
            with self.assertRaises(requests.exceptions.HTTPError):
                self.service._api('daily-checkin', self.config)

    def test_existing_success_paths(self):
        for replies, already in [
            ([response(200, {'code': 0, 'data': {'today_checked_in': True}})], True),
            ([response(200, {'code': 0}), response(200, {'code': 0, 'data': {'credit': 10}})], False),
            ([response(200, {'code': 0}), response(200, {'code': 10001})], True),
        ]:
            with self.subTest(already=already), patch.object(self.service.session, 'request', side_effect=replies):
                result = self.service.do_checkin(self.config)
                self.assertTrue(result['success'])
                self.assertEqual(bool(result.get('already_checked_in')), already)
                if not already:
                    self.assertEqual(result['credit'], 10)


if __name__ == '__main__':
    unittest.main()
