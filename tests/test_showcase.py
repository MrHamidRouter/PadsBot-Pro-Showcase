"""Static storefront acceptance tests; no external services needed."""
from html.parser import HTMLParser
from pathlib import Path
import json
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLANS = ("monthly", "quarterly", "semiannual", "annual")

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links=[]
        self.labels=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag == "a" and a.get("href"):
            self.links.append((a.get("class",""), a["href"]))
    def handle_data(self, data):
        self.labels.append(data)

class ShowcaseTests(unittest.TestCase):
    def test_language_navigation_and_plan_links(self):
        for language,file,other,checkout in (
            ("fa","index.html","en.html","checkout.html"),
            ("en","en.html","index.html","checkout-en.html")):
            with self.subTest(language=language):
                html=(ROOT/file).read_text(encoding="utf8")
                self.assertIn(f'<html lang="{language}"',html)
                self.assertNotIn("<script>",html,"Primary navigation must be JavaScript-independent")
                parser=Links()
                parser.feed(html)
                self.assertIn(("lang","./"+other),parser.links)
                for plan in PLANS:
                    self.assertIn(("plan-buy",f"./{checkout}?plan={plan}"),parser.links)
                self.assertEqual(sum("plan-buy" in a for a,_ in parser.links),4)

    def test_persian_is_visible_in_static_document(self):
        html=(ROOT/"index.html").read_text(encoding="utf8")
        self.assertIn("کسب‌وکار شما.",html)
        self.assertIn('class="faText"',html)
        self.assertIn('class="en hidden"',html)
        self.assertIn("انتخاب و ادامه خرید</a>",html)

    def test_checkout_pages(self):
        for path in ("checkout.html","checkout-en.html"):
            with self.subTest(path=path):
                html=(ROOT/path).read_text(encoding="utf8")
                self.assertIn("TJ6ys9jPCXGUEyHyXX9hodjeFxQ6zWGD6Q",html)
                self.assertIn("checkout-config.json",html)
                self.assertIn("/v1/payments/new",html)
                self.assertIn("/v1/payments/claim",html)
                scripts=re.findall(r"<script>([\s\S]*?)</script>",html)
                self.assertEqual(len(scripts),1)
                with tempfile.NamedTemporaryFile(mode="w",suffix=".js",encoding="utf8") as f:
                    f.write(scripts[0])
                    f.flush()
                    subprocess.run(["node","--check",f.name],check=True,capture_output=True,text=True)

    def test_checkout_disabled_until_backend(self):
        cfg=json.loads((ROOT/"checkout-config.json").read_text(encoding="utf8"))
        self.assertFalse(cfg["accept_payments"])
        self.assertFalse(cfg["api_base"])

if __name__=="__main__":
    unittest.main()
