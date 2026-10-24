import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class IdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        self.ids.update(value for name, value in attrs if name == "id" and value)


class FrontendIdTests(unittest.TestCase):
    def test_javascript_id_lookups_exist_in_dashboard_html(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        javascript = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        parser = IdParser()
        parser.feed(html)
        used_ids = {
            match.group(1) or match.group(2)
            for match in re.finditer(
                r"\$\(\s*['\"]([^'\"]+)['\"]\s*\)|getElementById\(\s*['\"]([^'\"]+)['\"]\s*\)",
                javascript,
            )
        }
        missing = sorted(used_ids - parser.ids)
        self.assertEqual(missing, [], f"JavaScript references missing HTML IDs: {missing}")


if __name__ == "__main__":
    unittest.main()
