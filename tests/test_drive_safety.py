"""Load real Drive implementation independently of legacy test module stubs."""
import contextlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import MagicMock

spec = importlib.util.spec_from_file_location(
    'drive_client_under_test', Path(__file__).resolve().parents[1] / 'integrations/drive_client.py')
drive_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drive_module)
DriveClient = drive_module.DriveClient
PRIVATE = 'private-body-token-and-url'


def client():
    instance = DriveClient.__new__(DriveClient)
    instance.service = MagicMock()
    return instance


class DriveSafetyTests(unittest.TestCase):
    def test_empty_lookup_is_absent(self):
        drive = client()
        drive.service.files().list().execute.return_value = {'files': []}
        self.assertIsNone(drive.find_file('fixture'))

    def test_single_file_lookup(self):
        drive = client()
        drive.service.files().list().execute.return_value = {'files': [{'id': 'one'}]}
        self.assertEqual({'id': 'one'}, drive.find_file('fixture'))

    def test_ambiguous_or_paginated_lookup_rejected(self):
        for result in ({'files': [{'id': 'one'}, {'id': 'two'}]},
                       {'files': [{'id': 'one'}], 'nextPageToken': 'more'}):
            drive = client()
            drive.service.files().list().execute.return_value = result
            with self.assertRaisesRegex(RuntimeError, 'Ambiguous'):
                drive.find_file('fixture')

    def test_lookup_error_is_not_absence_and_does_not_leak(self):
        drive = client()
        drive.service.files().list().execute.side_effect = RuntimeError(PRIVATE)
        with self.assertRaisesRegex(RuntimeError, 'lookup failed') as error:
            drive.find_file('fixture')
        self.assertNotIn(PRIVATE, str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)

    def test_failed_lookup_never_creates_or_updates(self):
        drive = client()
        drive.service.files().list().execute.side_effect = RuntimeError(PRIVATE)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertIsNone(drive.write_file('body', 'fixture', 'parent'))
        drive.service.files().create.assert_not_called()
        drive.service.files().update.assert_not_called()
        self.assertNotIn(PRIVATE, output.getvalue())

    def test_operation_errors_do_not_print_payload_or_file_id(self):
        for operation, args in (
            ('read_file', (PRIVATE,)), ('update_file_by_id', (PRIVATE, 'body')),
            ('trash_file', (PRIVATE,)), ('delete_file', (PRIVATE,)),
        ):
            with self.subTest(operation=operation):
                drive = client()
                drive.service.files.side_effect = RuntimeError(PRIVATE)
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertFalse(getattr(drive, operation)(*args))
                self.assertNotIn(PRIVATE, output.getvalue())
                self.assertIn('RuntimeError', output.getvalue())


if __name__ == '__main__':
    unittest.main()
