import unittest

from modules.daily_briefing.evidence_quality import assess


class EvidenceQualityTests(unittest.TestCase):
    def test_unverified_news_requires_review(self):
        item = {"source": "moz_ukraine", "source_id": "a",
                "title": "Новий наказ", "factual_summary": "Заголовок",
                "url": "https://news.example.org/post", "source_verified": False}
        result = assess(item)
        self.assertTrue(result["needs_human_review"])
        self.assertIn("no_official_primary_link", result["quality_issues"])

    def test_official_link_does_not_equal_verified(self):
        item = {"source": "moz_ukraine", "source_id": "a",
                "title": "Наказ", "factual_summary": "Документ про медичну допомогу та стандарти",
                "url": "https://moz.gov.ua/test", "published_at": "2026-10-18",
                "source_verified": False}
        result = assess(item)
        self.assertEqual(result["evidence_status"], "unverified")

    def test_mail_without_context_is_flagged(self):
        item = {"source": "gmail", "source_id": "1", "subject": "Завдання",
                "snippet": "ОК"}
        self.assertIn("insufficient_context", assess(item)["quality_issues"])


if __name__ == "__main__":
    unittest.main()
