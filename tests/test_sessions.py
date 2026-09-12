"""Real HTTP sessions with a persistent database and a controllable expiry clock."""
import hashlib
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.cookies import SimpleCookie
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.app import Server, Store


class AdminSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'water.db')
        self.now = 1000
        self.admin_key = 'admin-test-key'
        self.start()

    def start(self):
        self.store = Store(self.path, lambda: self.now)
        self.server = Server(('127.0.0.1', 0), self.store, self.admin_key,
                             'device' * 10, 'https://example.test')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:{}/water/api/'.format(self.server.server_port)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.store.db.close()

    def restart(self):
        self.stop()
        self.start()

    def tearDown(self):
        self.stop()
        self.temp.cleanup()

    def request(self, path, data=None, cookie=None):
        headers = {'Origin': 'https://example.test'}
        if cookie is not None:
            headers['Cookie'] = cookie
        request = urllib.request.Request(self.url + path,
                                         None if data is None else json.dumps(data).encode(), headers)
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            response.read()
            return response.status, response.headers

    def login(self):
        code, headers = self.request('login', {'key': self.admin_key})
        self.assertEqual(code, 200)
        cookie = SimpleCookie(headers['Set-Cookie'])['water_session']
        self.assertEqual(int(cookie['max-age']), 86313600)
        self.assertTrue(cookie['secure'])
        self.assertTrue(cookie['httponly'])
        self.assertEqual(cookie['samesite'], 'Strict')
        self.assertEqual(cookie['path'], '/water/api/')
        return 'water_session=' + cookie.value

    def status(self, cookie):
        return self.request('status', cookie=cookie)[0]

    def test_session_expires_exactly_999_days_after_login(self):
        cookie = self.login()
        self.now += 999 * 86400 - 1
        self.assertEqual(self.status(cookie), 200)
        self.now += 1
        # Reading status must not extend the fixed server expiry.
        self.assertEqual(self.status(cookie), 401)
        self.now += 1
        self.assertEqual(self.status(cookie), 401)

    def test_login_survives_server_and_database_reopen(self):
        cookie = self.login()
        self.now += 86400
        self.restart()
        self.assertEqual(self.status(cookie), 200)
        self.now = 1000 + 999 * 86400
        self.assertEqual(self.status(cookie), 401)

    def test_logout_stays_revoked_after_restart_without_revoking_other_login(self):
        first, second = self.login(), self.login()
        code, headers = self.request('logout', {}, first)
        self.assertEqual(code, 200)
        self.assertEqual(SimpleCookie(headers['Set-Cookie'])['water_session']['max-age'], '0')
        self.assertEqual(self.status(first), 401)
        self.restart()
        self.assertEqual(self.status(first), 401)
        self.assertEqual(self.status(second), 200)

    def test_database_digest_cannot_be_used_as_token(self):
        cookie = self.login()
        token = cookie.split('=', 1)[1]
        row = dict(self.store.db.execute('SELECT * FROM admin_sessions').fetchone())
        self.assertNotIn(token, row.values())
        self.assertNotIn(self.admin_key, row.values())
        self.assertEqual(row['token_hash'], hashlib.sha256(token.encode()).hexdigest())
        self.assertEqual(self.status('water_session=' + row['token_hash']), 401)
        self.assertEqual(self.status('water_session=unknown'), 401)
        self.assertEqual(self.status(cookie), 200)

    def test_changing_admin_key_permanently_revokes_old_sessions(self):
        cookie = self.login()
        old_key = self.admin_key
        self.admin_key = 'replacement-admin-key'
        self.restart()
        self.assertEqual(self.status(cookie), 401)
        self.assertEqual(self.status(self.login()), 200)
        self.admin_key = old_key
        self.restart()
        self.assertEqual(self.status(cookie), 401)

    def test_login_removes_expired_sessions(self):
        expired_cookie = self.login()
        self.now += 999 * 86400
        valid_cookie = self.login()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM admin_sessions').fetchone()[0], 1)
        self.assertEqual(self.status(expired_cookie), 401)
        self.assertEqual(self.status(valid_cookie), 200)


if __name__ == '__main__':
    unittest.main()
