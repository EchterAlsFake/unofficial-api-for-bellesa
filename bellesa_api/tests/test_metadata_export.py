import unittest

from bellesa_api.modules.consts import video_fields


class MetadataExportTests(unittest.TestCase):
    def test_keeps_each_credited_provider(self):
        fields = video_fields({"id": "123", "title": "Example", "content_provider": [
            {"name": "First", "handle": "first"}, {"name": "Second", "handle": "second"}]}, "123")
        self.assertEqual([item["name"] for item in fields["content_providers"]], ["First", "Second"])
        self.assertEqual(fields["author"], "First")
