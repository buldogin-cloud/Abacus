import unittest
from modules.telemetry.provider_usage import extract_usage

class ProviderUsageTests(unittest.TestCase):
    def test_openai_chat_completions(self):
        self.assertEqual(extract_usage("openai", {"usage":{"prompt_tokens":123,"completion_tokens":20}}),(123,20))
    def test_openai_responses(self):
        self.assertEqual(extract_usage("openai", {"usage":{"input_tokens":123,"output_tokens":20}}),(123,20))
    def test_anthropic(self):
        self.assertEqual(extract_usage("anthropic", {"usage":{"input_tokens":123,"output_tokens":20}}),(123,20))
    def test_incomplete_is_unknown(self):
        self.assertEqual(extract_usage("openai", {"usage":{"prompt_tokens":123}}),(None,None))
    def test_unsupported(self):
        self.assertEqual(extract_usage("abacus", {"usage":{"input_tokens":123,"output_tokens":20}}),(None,None))
    def test_bool_or_negative_rejected(self):
        self.assertEqual(extract_usage("anthropic", {"usage":{"input_tokens":True,"output_tokens":20}}),(None,None))
        self.assertEqual(extract_usage("anthropic", {"usage":{"input_tokens":-1,"output_tokens":20}}),(None,None))

if __name__ == "__main__":
    unittest.main()
