import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from integrations.gmail_client import GmailClient
from integrations.calendar_client import CalendarClient
from modules.daily_briefing.collector import collect_gmail, CollectedData

RAW = b'From: sender@example.org\r\nTo: owner@example.org\r\nSubject: Long message\r\nMessage-ID: <a@example.org>\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n' + b'x' * 500


def client(gmail=True):
    with patch.dict('os.environ', {'GMAIL_APP_PASSWORD': 'fixture'}):
        c = GmailClient()
    c.imap = MagicMock()
    c.imap.capabilities = (b'IMAP4rev1', b'X-GM-EXT-1') if gmail else (b'IMAP4rev1',)
    c.imap.select.return_value = ('OK', [b'3'])
    c.imap.response.return_value = ('UIDVALIDITY', [b'123'])
    return c


class MailTests(unittest.TestCase):
    def test_empty_source_set_is_not_success(self):
        self.assertEqual(CollectedData("test", "2026-10-23T04:00:00Z").overall_status(), "failed")

    def test_uid_readonly_peek_and_gmail_identity(self):
        c = client()
        c.imap.uid.side_effect = [('OK', [b'9']), ('OK', [(b'1 (UID 9 FLAGS (\\Seen) X-GM-MSGID 255 X-GM-THRID 256)', RAW)])]
        rows = c.get_recent_messages('2026-10-23T04:00:00Z', 20)
        c.imap.select.assert_called_with('INBOX', readonly=True)
        self.assertEqual(c.imap.uid.call_args_list[0].args[0], 'search')
        fetch = c.imap.uid.call_args_list[1].args
        self.assertEqual(fetch[0:2], ('fetch', '9'))
        self.assertIn('BODY.PEEK[]', fetch[2])
        self.assertNotIn('RFC822', fetch[2])
        self.assertEqual(rows[0]['message_id'], 'ff')
        self.assertEqual(rows[0]['threadId'], '100')
        self.assertEqual(rows[0]['url'], 'https://mail.google.com/mail/#all/ff')
        self.assertEqual(len(rows[0]['body']), 500)
        self.assertFalse(rows[0]['is_unread'])
        self.assertNotIn('UNSEEN', c.last_collection['query'])

    def test_gmail_metadata_after_body_literal(self):
        c = client()
        c.uidvalidity = '123'
        c.imap.uid.return_value = ('OK', [(b'1 (UID 9 BODY[] {500}', RAW),
                                         b' X-GM-MSGID 255 X-GM-THRID 256 FLAGS (\\Seen))'])
        row = c._get_message_summary('9')
        self.assertEqual(row['message_id'], 'ff')
        self.assertEqual(row['threadId'], '100')
        self.assertFalse(row['is_unread'])

    def test_fallback_is_stable_and_not_gmail_link(self):
        c = client(False)
        c.verify_connection()
        c.imap.uid.return_value = ('OK', [(b'1 (UID 9 FLAGS ())', RAW)])
        row = c._get_message_summary('9')
        self.assertEqual(row['id'], 'imap:andrewbelin6@gmail.com:INBOX:123:9')
        self.assertEqual(row['message_id'], '')
        self.assertEqual(row['url'], '')
        self.assertEqual(row['id_kind'], 'imap_uid')

    def test_unstable_identity_fails(self):
        c = client(False)
        c.imap.uid.return_value = ('OK', [(b'1 (UID 9 FLAGS ())', RAW)])
        with self.assertRaisesRegex(RuntimeError, 'stable'):
            c._get_message_summary('9')

    def test_search_failure_is_not_empty_success(self):
        c = client()
        c.imap.uid.return_value = ('NO', [b'failure'])
        with self.assertRaisesRegex(RuntimeError, 'search failed'):
            c.get_unread_messages()

    def test_fetch_failure_propagates(self):
        c = client()
        c.imap.uid.side_effect = [('OK', [b'9']), ('NO', [b'failure'])]
        with self.assertRaisesRegex(RuntimeError, 'fetch failed'):
            c.get_recent_messages('2026-10-23T04:00:00Z')

    def test_empty_search_is_verified_empty(self):
        c = client()
        c.imap.uid.return_value = ('OK', [b''])
        self.assertEqual(c.get_recent_messages('2026-10-23T04:00:00Z'), [])
        self.assertFalse(c.last_collection['truncated'])

    def test_cap_is_explicit_and_collector_partial(self):
        c = client()
        c.imap.uid.side_effect = [('OK', [b'9 10']), ('OK', [(b'1 (UID 9 FLAGS () X-GM-MSGID 255)', RAW)])]
        with patch('integrations.gmail_client.GmailClient', return_value=c):
            result = collect_gmail({'max_messages': 1}, '2026-10-23T04:00:00Z')
        self.assertEqual(result.status, 'partial')
        self.assertTrue(result.connection_verified)
        self.assertEqual(result.meta['matched'], 2)
        self.assertEqual(len(result.records[0]['body']), 500)
        self.assertEqual(result.records[0]['message_id'], 'ff')

    def test_read_messages_included_and_drafts_excluded(self):
        c = MagicMock()
        c.last_collection = {'truncated': False}
        c.get_recent_messages.return_value = [
            {'id': '1', 'message_id': '1', 'is_unread': False, 'body': 'read message'},
            {'id': '2', 'is_draft': True}, {'id': '3', 'is_sent': True}]
        with patch('integrations.gmail_client.GmailClient', return_value=c):
            result = collect_gmail({}, '2026-10-23T04:00:00Z')
        self.assertEqual(result.status, 'success')
        self.assertEqual(len(result.records), 1)
        self.assertFalse(result.records[0]['is_unread'])
        self.assertEqual(result.records[0]['body'], 'read message')

    def test_attachment_metadata_not_analysis(self):
        c = client()
        c.uidvalidity = '123'
        raw = (b'Content-Type: multipart/mixed; boundary=x\r\n\r\n--x\r\n'
               b'Content-Type: text/plain\r\n\r\nHello\r\n--x\r\n'
               b'Content-Type: application/pdf\r\nContent-Disposition: attachment; filename="offer.pdf"\r\n'
               b'Content-Transfer-Encoding: base64\r\n\r\nYWJj\r\n--x--\r\n')
        c.imap.uid.return_value = ('OK', [(b'1 (UID 9 FLAGS () X-GM-MSGID 255)', raw)])
        row = c._get_message_summary('9')
        self.assertEqual(row['attachments'], [{'filename': 'offer.pdf', 'type': 'application/pdf',
                                               'size_bytes': 3, 'analysis_status': 'not_analyzed'}])

    def test_collector_fetch_error_is_failed(self):
        c = MagicMock()
        c.get_recent_messages.side_effect = RuntimeError('incomplete')
        with patch('integrations.gmail_client.GmailClient', return_value=c):
            result = collect_gmail({}, '2026-10-23T04:00:00Z')
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.records, [])


class CalendarTests(unittest.TestCase):
    def test_all_pages_and_description(self):
        c = CalendarClient.__new__(CalendarClient)
        c.calendar_id = 'primary'
        c.service = MagicMock()
        c.service.events().list().execute.side_effect = [
            {'items': [{'id': '1', 'description': 'context'}], 'nextPageToken': 'p2'},
            {'items': [{'id': '2'}]}]
        now = datetime.now(timezone.utc)
        rows = c._fetch_events(now, now)
        self.assertEqual([r['id'] for r in rows], ['1', '2'])
        self.assertEqual(rows[0]['description'], 'context')
        self.assertEqual(c.service.events().list.call_args.kwargs['pageToken'], 'p2')

    def test_repeated_token_fails_instead_of_partial_success(self):
        c = CalendarClient.__new__(CalendarClient)
        c.calendar_id = 'primary'
        c.service = MagicMock()
        c.service.events().list().execute.return_value = {'items': [], 'nextPageToken': 'p2'}
        now = datetime.now(timezone.utc)
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            c._fetch_events(now, now)


if __name__ == '__main__':
    unittest.main()
