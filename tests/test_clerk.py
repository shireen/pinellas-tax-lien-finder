"""Synthetic, offline regressions for fail-closed Clerk searches."""
import unittest
from unittest.mock import Mock, patch

import requests

from taxlien import liens

FORM = '<form id="schfrm" action="/Search/SearchTypeName"></form>'
DISCLAIMER = '<input name="disclaimer" value="true">'
NAMES = '<div id="NameListTreeView"><input name="names[0].Value" value="SYNTHETIC OWNER"></div>'
GRID = '<div id="RsltsGrid"></div>'
NO_NAMES = 'No names found. Please try your search again.'


def response(text='', data=None):
    r = Mock(text=text)
    r.json.return_value = data
    return r


def record(number=1, **changes):
    row = dict(Name='SYNTHETIC OWNER', CrossPartyName='SYNTHETIC BANK',
               DocTypeDescription='MORTGAGE', RecordDate='/Date(1704067200000)/',
               Comments='SYNTHETIC SUBDIVISION', InstrumentNumber=str(number))
    row.update(changes)
    return row


class ClerkTests(unittest.TestCase):
    def setUp(self):
        self.sessions = patch.object(liens, 'session', side_effect=lambda: Mock())
        self.sessions.start()
        self.addCleanup(self.sessions.stop)
        self.clerk = liens.Clerk()
        self.get = patch.object(liens, 'get').start()
        self.post = patch.object(liens, 'post').start()
        self.addCleanup(patch.stopall)
        self.get.side_effect = [response(FORM), response(GRID)]

    def search(self, pages):
        self.post.side_effect = [response(NAMES), response(GRID)] + [response(data=p) for p in pages]
        return self.clerk.search_name('SYNTHETIC OWNER')

    def test_session_get_precedes_disclaimer(self):
        calls = Mock()
        calls.attach_mock(self.get, 'get')
        calls.attach_mock(self.post, 'post')
        self.get.side_effect = [response(DISCLAIMER)]
        self.post.side_effect = [response(FORM), response(NO_NAMES)]
        self.assertEqual(self.clerk.search_name('SYNTHETIC OWNER'), [])
        self.assertEqual([c[0] for c in calls.mock_calls], ['get', 'post', 'post'])
        self.assertTrue(calls.mock_calls[0].args[1].endswith('/search/SearchTypeName'))
        self.assertIn('/search/Disclaimer?', calls.mock_calls[1].args[1])

    def test_no_names_never_fetches_previous_owner_grid(self):
        self.assertEqual(len(self.search([dict(data=[record()], total=1)])), 1)
        self.get.reset_mock(side_effect=True)
        self.post.reset_mock(side_effect=True)
        self.post.return_value = response(NO_NAMES)
        self.assertEqual(self.clerk.search_name('ANOTHER SYNTHETIC OWNER'), [])
        self.get.assert_not_called()
        self.assertEqual(self.post.call_count, 1)

    def test_malformed_name_and_selection_html_fail_closed(self):
        for replies in ([response('<html>Access error</html>')],
                        [response('<div id="NameListTreeView"></div>')],
                        [response(NAMES), response('<html>Login</html>')]):
            with self.subTest(replies=replies):
                self.clerk.ready = True
                self.post.side_effect = replies
                with self.assertRaises(liens.ClerkSearchError):
                    self.clerk.search_name('SYNTHETIC OWNER')
                self.assertFalse(self.clerk.ready)

    def test_invalid_initial_form_fails_closed(self):
        self.get.side_effect = [response('<html>Access denied</html>')]
        with self.assertRaises(liens.ClerkSearchError):
            self.clerk.search_name('SYNTHETIC OWNER')
        self.post.assert_not_called()

    def test_malformed_grid_json_and_errors_fail_closed(self):
        invalid = [None, [], {}, dict(data=[]), dict(data=[], total=True),
                   dict(data=[], total=-1), dict(data=[], total='0'),
                   dict(data={}, total=0), dict(data=[], total=0, errors='failed'),
                   dict(data=[], total=0, Errors=['failed'])]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.clerk.ready = True
                self.get.side_effect = None
                with self.assertRaises(liens.ClerkSearchError):
                    self.search([payload])
        bad = response()
        bad.json.side_effect = ValueError('Synthetic invalid JSON')
        self.clerk.ready = True
        self.post.side_effect = [response(NAMES), response(GRID), bad]
        with self.assertRaises(liens.ClerkSearchError):
            self.clerk.search_name('SYNTHETIC OWNER')

    def test_multiple_pages_stop_at_exact_total(self):
        rows = self.search([dict(data=[record(1)], total=2), dict(data=[record(2)], total=2)])
        self.assertEqual([r['instrument'] for r in rows], ['1', '2'])
        self.assertEqual(rows[0]['date'], '2024-01-01')
        self.assertEqual([c.kwargs['data']['page'] for c in self.post.call_args_list[2:]], [1, 2])

    def test_explicit_empty_grid_is_valid(self):
        self.assertEqual(self.search([dict(data=[], total=0)]), [])

    def test_pagination_repeated_truncated_excess_or_changed_total_fails(self):
        scenarios = [
            [dict(data=[record()], total=2), dict(data=[record()], total=2)],
            [dict(data=[record()], total=2), dict(data=[], total=2)],
            [dict(data=[record(), record(2)], total=1)],
            [dict(data=[record()], total=2), dict(data=[record(2)], total=3)],
        ]
        for pages in scenarios:
            with self.subTest(pages=pages):
                self.clerk.ready = True
                self.get.side_effect = None
                with self.assertRaisesRegex(liens.ClerkSearchError, 'incomplete_results'):
                    self.search(pages)

    def test_overlapping_pages_with_changed_display_guid_fail_closed(self):
        with self.assertRaisesRegex(liens.ClerkSearchError, 'incomplete_results'):
            self.search([dict(data=[record(1, GUID=1), record(2, GUID=2)], total=4),
                         dict(data=[record(2, GUID=3), record(3, GUID=4)], total=4)])

    def test_pagination_page_limit_fails_closed(self):
        self.clerk.MAX_PAGES = 1
        with self.assertRaisesRegex(liens.ClerkSearchError, 'incomplete_results'):
            self.search([dict(data=[record()], total=2)])

    def test_invalid_record_dates_fail_closed(self):
        for value in ('not a date', '/Date(NaN)/', '/Date(9999999999999999999999999)/', None, 0):
            with self.subTest(value=value):
                with self.assertRaisesRegex(liens.ClerkSearchError, 'invalid_record'):
                    self.clerk._parse_record(record(RecordDate=value))
        self.assertEqual(self.clerk._parse_record(record(RecordDate='/Date(1704067200000-0500)/'))['date'], '2024-01-01')

    def test_bad_record_shapes_fail_closed(self):
        for row in (None, [], {}, record(Name=123), record(CrossPartyName=[]), record(Comments={})):
            with self.subTest(row=row):
                with self.assertRaisesRegex(liens.ClerkSearchError, 'invalid_record'):
                    self.clerk._parse_record(row)

    def test_403_opens_circuit_for_remaining_owners(self):
        denied = requests.Response()
        denied.status_code = 403
        self.get.side_effect = requests.HTTPError('secret response body', response=denied)
        original_session = self.clerk.s
        for name in ('FIRST SYNTHETIC OWNER', 'SECOND SYNTHETIC OWNER'):
            with self.assertRaises(liens.ClerkSearchError) as caught:
                self.clerk.search_name(name)
            self.assertEqual(caught.exception.code, 'access_denied')
            self.assertNotIn('secret', str(caught.exception))
        self.assertEqual(self.get.call_count, 1)
        self.post.assert_not_called()
        original_session.close.assert_called_once()


class ScreeningTests(unittest.TestCase):
    def test_unlinked_releases_never_cancel_government_liens(self):
        rows = [liens.Clerk._parse_record(record(1, DocTypeDescription='LIEN', CrossPartyName='CITY OF SYNTHETIC'))]
        rows += [liens.Clerk._parse_record(record(n, DocTypeDescription='RELEASE OF LIEN',
                  CrossPartyName='CITY OF SYNTHETIC', Comments='UNRELATED PARCEL')) for n in (2, 3)]
        result = liens.screen(rows, 'SYNTHETIC SUBDIVISION')
        self.assertEqual(result['active_gov_liens'], 1)
        self.assertIn('unlinked release', result['notes'])
        self.assertNotIn('No liens', result['notes'])

    def test_company_names_are_not_truncated(self):
        for name in ('SYNTHETIC LONG COMPANY HOLDINGS LLC', 'SYNTHETIC LONG COMPANY HOLDINGS INC',
                     'SYNTHETIC LONG COMPANY HOLDINGS CORPORATION', 'SYNTHETIC LONG COMPANY HOLDINGS LP'):
            self.assertEqual(liens.owner_search_name(name), name)


if __name__ == '__main__':
    unittest.main()
