"""Credential handling tests: synthetic data, no network or real secrets."""
import os
import unittest
from unittest.mock import Mock, patch

from google.auth.exceptions import RefreshError
from integrations import google_auth as auth


class GoogleAuthTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.file = patch.object(auth, "TOKEN_FILE")
        self.token_file = self.file.start()
        self.addCleanup(self.file.stop)
        self.token_file.exists.return_value = False
        self.factory_patch = patch.object(auth, "Credentials")
        self.factory = self.factory_patch.start()
        self.addCleanup(self.factory_patch.stop)
        self.request = patch.object(auth, "Request", return_value=object())
        self.request.start()
        self.addCleanup(self.request.stop)

    def assert_code(self, code):
        with self.assertRaises(auth.GoogleAuthError) as ctx:
            auth.get_credentials()
        self.assertEqual(ctx.exception.code, code)
        self.assertNotIn("synthetic-secret", str(ctx.exception))
        self.assertTrue(ctx.exception.__suppress_context__ or ctx.exception.__context__ is None)
        return ctx.exception

    def env_creds(self, *, valid=False, refresh_token="synthetic-refresh"):
        os.environ["GOOGLE_TOKEN"] = '{}'
        creds = Mock(valid=valid, refresh_token=refresh_token)
        self.factory.from_authorized_user_info.return_value = creds
        return creds

    def test_missing_token_does_not_open_browser_or_write(self):
        self.assert_code("missing_token")
        self.factory.from_authorized_user_file.assert_not_called()
        self.assertFalse(hasattr(auth, "InstalledAppFlow"))
        self.token_file.write_text.assert_not_called()

    def test_blank_env_does_not_fall_back_to_file(self):
        os.environ["GOOGLE_TOKEN"] = ' '
        self.token_file.exists.return_value = True
        self.assert_code("missing_token")
        self.factory.from_authorized_user_file.assert_not_called()

    def test_malformed_json_is_safe(self):
        os.environ["GOOGLE_TOKEN"] = 'synthetic-secret'
        self.assert_code("malformed_token")

    def test_non_object_json_is_rejected(self):
        os.environ["GOOGLE_TOKEN"] = '[]'
        self.assert_code("malformed_token")

    def test_factory_error_is_safe_no_fallback(self):
        os.environ["GOOGLE_TOKEN"] = '{}'
        self.factory.from_authorized_user_info.side_effect = ValueError("synthetic-secret")
        self.assert_code("malformed_token")
        self.factory.from_authorized_user_file.assert_not_called()

    def test_valid_env_returned_without_refresh(self):
        creds = self.env_creds(valid=True)
        self.assertIs(auth.get_credentials(), creds)
        creds.refresh.assert_not_called()
        self.factory.from_authorized_user_info.assert_called_once_with({}, auth.DEFAULT_SCOPES)

    def test_refresh_success_checked(self):
        creds = self.env_creds()
        creds.refresh.side_effect = lambda _: setattr(creds, "valid", True)
        self.assertIs(auth.get_credentials(), creds)
        creds.refresh.assert_called_once()
        self.token_file.write_text.assert_not_called()

    def test_refresh_without_expiry_is_attempted_when_invalid(self):
        creds = self.env_creds()
        creds.expired = False
        creds.refresh.side_effect = lambda _: setattr(creds, "valid", True)
        self.assertIs(auth.get_credentials(), creds)

    def test_invalid_grant_sanitized_without_fallback(self):
        creds = self.env_creds()
        creds.refresh.side_effect = RefreshError("invalid_grant: synthetic-secret")
        self.assert_code("reauthorization_required")
        self.factory.from_authorized_user_file.assert_not_called()

    def test_other_refresh_rejection(self):
        creds = self.env_creds()
        creds.refresh.side_effect = RefreshError("synthetic-secret")
        self.assert_code("refresh_rejected")

    def test_network_failure_sanitized(self):
        creds = self.env_creds()
        creds.refresh.side_effect = OSError("synthetic-secret")
        self.assert_code("refresh_unavailable")

    def test_no_refresh_token(self):
        self.env_creds(refresh_token=None)
        self.assert_code("missing_refresh_token")

    def test_refresh_that_stays_invalid_fails(self):
        self.env_creds()
        self.assert_code("invalid_refreshed_token")

    def test_local_file_used_without_env(self):
        self.token_file.exists.return_value = True
        creds = Mock(valid=True)
        self.factory.from_authorized_user_file.return_value = creds
        self.assertIs(auth.get_credentials(), creds)

    def test_local_parse_error_sanitized(self):
        self.token_file.exists.return_value = True
        self.factory.from_authorized_user_file.side_effect = ValueError("synthetic-secret")
        self.assert_code("malformed_token")

    def test_custom_scopes_are_not_broadened(self):
        self.env_creds(valid=True)
        auth.get_credentials(["synthetic-readonly"])
        self.factory.from_authorized_user_info.assert_called_once_with({}, ["synthetic-readonly"])


class ExplicitAuthorizationTests(unittest.TestCase):
    def test_scopes_match_runtime_without_gmail_send(self):
        import authorize
        self.assertEqual(authorize.SCOPES, auth.DEFAULT_SCOPES)
        self.assertFalse(any("gmail" in scope for scope in authorize.SCOPES))

    def test_explicit_flag_required_before_flow(self):
        import authorize
        with patch.object(authorize.InstalledAppFlow, "from_client_secrets_file") as flow:
            with self.assertRaises(SystemExit):
                authorize.main([])
            flow.assert_not_called()

    def test_ci_cannot_open_auth_flow(self):
        import authorize
        with patch.dict(os.environ, {"CI": "true"}), patch.object(authorize.InstalledAppFlow, "from_client_secrets_file") as flow:
            with self.assertRaises(SystemExit):
                authorize.main(["--authorize"])
            flow.assert_not_called()


    def test_existing_output_refused_before_auth(self):
        import authorize
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "existing.json"
            output.write_text("synthetic-existing")
            with patch.dict(os.environ, {}, clear=True), patch.object(authorize.InstalledAppFlow, "from_client_secrets_file") as flow:
                with self.assertRaises(SystemExit):
                    authorize.main(["--authorize", "--token-output", str(output)])
                flow.assert_not_called()
                self.assertEqual(output.read_text(), "synthetic-existing")

    def test_success_saves_private_file_without_logging_secret(self):
        import authorize
        import tempfile
        import io
        from pathlib import Path
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as tmp:
            client = Path(tmp) / "client.json"
            client.write_text("{}")
            output = Path(tmp) / "new.json"
            creds = Mock(refresh_token="synthetic-refresh")
            creds.to_json.return_value = "synthetic-secret"
            flow = Mock()
            flow.run_local_server.return_value = creds
            out = io.StringIO()
            with patch.dict(os.environ, {}, clear=True), patch.object(authorize.InstalledAppFlow, "from_client_secrets_file", return_value=flow), redirect_stdout(out):
                rc = authorize.main(["--authorize", "--credentials", str(client), "--token-output", str(output)])
            self.assertEqual(rc, 0)
            self.assertEqual(output.read_text(), "synthetic-secret")
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("synthetic-secret", out.getvalue())


if __name__ == "__main__":
    unittest.main()
