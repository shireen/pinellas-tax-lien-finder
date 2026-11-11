"""Offline HTTP retry tests. Never make a network request."""
import unittest
from unittest.mock import Mock, patch

import requests

from taxlien import http


def error(status, retry_after=None):
    r = requests.Response()
    r.status_code = status
    if retry_after is not None:
        r.headers['Retry-After'] = retry_after
    return requests.HTTPError(response=r)


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.sleep = patch.object(http.time, 'sleep').start()
        self.addCleanup(patch.stopall)
        self.session = Mock()

    def test_transient_status_retries_preserve_timeout_and_arguments(self):
        for wrapper, method_name in ((http.get, 'get'), (http.post, 'post')):
            for status in (408, 429, 500, 502, 503, 504):
                with self.subTest(method=method_name, status=status):
                    method = getattr(self.session, method_name)
                    method.reset_mock()
                    good = Mock()
                    method.side_effect = [error(status), good]
                    self.assertIs(wrapper(self.session, 'https://example.invalid', timeout=17,
                                          headers={'X-Test': 'synthetic'}, data={'a': 'b'}), good)
                    self.assertEqual(method.call_count, 2)
                    for call in method.call_args_list:
                        self.assertEqual(call.kwargs['timeout'], 17)
                        self.assertEqual(call.kwargs['data'], {'a': 'b'})
                        self.assertEqual(call.kwargs['headers'], {'X-Test': 'synthetic'})

    def test_status_errors_raised_by_response_are_retried(self):
        bad, good = Mock(), Mock()
        bad.raise_for_status.side_effect = error(503)
        self.session.get.side_effect = [bad, good]
        self.assertIs(http.get(self.session, 'https://example.invalid'), good)
        self.assertEqual(self.session.get.call_count, 2)

    def test_403_and_other_permanent_errors_never_retry(self):
        for status in (400, 401, 403, 404, 422):
            with self.subTest(status=status):
                self.session.get.reset_mock()
                self.sleep.reset_mock()
                self.session.get.side_effect = error(status)
                with self.assertRaises(requests.HTTPError):
                    http.get(self.session, 'https://example.invalid')
                self.assertEqual(self.session.get.call_count, 1)
                self.sleep.assert_not_called()

    def test_network_timeouts_retry_with_same_timeout(self):
        self.session.post.side_effect = requests.Timeout('synthetic timeout')
        with self.assertRaises(requests.Timeout):
            http.post(self.session, 'https://example.invalid', timeout=(5, 20))
        self.assertEqual(self.session.post.call_count, 3)
        self.assertEqual([c.kwargs['timeout'] for c in self.session.post.call_args_list], [(5, 20)] * 3)
        self.assertEqual(self.sleep.call_count, 2)

    def test_retry_after_respected_or_deferred(self):
        self.session.get.side_effect = [error(429, '12'), Mock()]
        http.get(self.session, 'https://example.invalid')
        self.sleep.assert_called_once_with(12)
        for delay in ('61', 'Wed, 01 Jan 2031 00:00:00 GMT'):
            self.session.get.reset_mock()
            self.sleep.reset_mock()
            self.session.get.side_effect = error(429, delay)
            with self.assertRaises(requests.HTTPError):
                http.get(self.session, 'https://example.invalid')
            self.assertEqual(self.session.get.call_count, 1)
            self.sleep.assert_not_called()

    def test_zero_attempts_rejected(self):
        with self.assertRaises(ValueError):
            http.get(self.session, 'https://example.invalid', retries=0)
        self.session.get.assert_not_called()


if __name__ == '__main__':
    unittest.main()
