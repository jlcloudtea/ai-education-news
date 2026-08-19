import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from rss_aggregator import (
    Article,
    canonical_url,
    compile_keyword,
    deduplicate,
    score_article,
    write_rss,
)


class AggregatorTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.ai = {value: compile_keyword(value) for value in ["AI", "machine learning", "ChatGPT"]}
        self.education = {
            value: compile_keyword(value) for value in ["education", "student", "teacher", "learning"]
        }
        self.strong = {value: compile_keyword(value) for value in ["education", "student", "teacher"]}
        self.negative = {value: compile_keyword(value) for value in ["stock", "GPU", "funding round"]}

    def article(self, title, description="", link="https://example.com/story", priority=5):
        return Article(
            title=title,
            link=link,
            description=description,
            source_name="Example",
            source_url="https://example.com/feed",
            source_group="Example",
            published=self.now - timedelta(hours=2),
            source_priority=priority,
        )

    def test_requires_ai_and_education(self):
        relevant = self.article("A teacher explores AI in the classroom", "A student uses ChatGPT responsibly")
        unrelated = self.article("New AI GPU launches", "Machine learning performance improves")
        self.assertIsNotNone(score_article(relevant, self.ai, self.education, self.strong, self.negative, {}, self.now))
        self.assertIsNone(score_article(unrelated, self.ai, self.education, self.strong, self.negative, {}, self.now))

    def test_machine_learning_is_not_education_by_itself(self):
        article = self.article("Machine learning model released", "New AI research")
        self.assertIsNone(score_article(article, self.ai, self.education, self.strong, self.negative, {}, self.now))

    def test_canonical_url_removes_tracking(self):
        self.assertEqual(
            canonical_url("https://Example.com/story/?utm_source=x&id=4#section"),
            "https://example.com/story?id=4",
        )

    def test_similar_title_dedup_prefers_authority(self):
        low = self.article("Schools adopt AI tutors in classrooms", link="https://low.example/a", priority=3)
        high = self.article("Schools adopt AI tutors in the classroom", link="https://high.example/b", priority=10)
        low.relevance = high.relevance = 20
        unique = deduplicate([low, high], 0.9)
        self.assertEqual(len(unique), 1)
        self.assertEqual(unique[0].link, high.link)

    def test_output_is_valid_rss(self):
        article = self.article("AI & education <together>", "Teachers & students")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "feed.xml"
            write_rss([article], output, "https://example.com", self.now)
            root = ET.parse(output).getroot()
            self.assertEqual(root.tag, "rss")
            self.assertEqual(root.findtext("channel/item/title"), article.title)


if __name__ == "__main__":
    unittest.main()
