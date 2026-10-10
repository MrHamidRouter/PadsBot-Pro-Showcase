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

    def test_four_price_cards_are_direct_grid_children(self):
        """Regression: accidentally duplicated <div> nested the plan cards."""
        class PricingStructure(HTMLParser):
            def __init__(self):
                super().__init__()
                self.stack=[]
                self.in_grid=False
                self.plan_ids=[]
                self.plan_depths=[]
                self.links=0
            def handle_starttag(self,tag,attrs):
                values=dict(attrs)
                if tag=="div":
                    classes=values.get("class","").split()
                    if "price-grid" in classes:
                        if self.in_grid:
                            raise AssertionError("Nested price grids")
                        self.in_grid=True
                        self.stack.append(("grid",values.get("class")))
                    elif self.in_grid:
                        if "price" in classes:
                            self.plan_ids.append(values.get("data-plan"))
                            self.plan_depths.append(len(self.stack))
                        self.stack.append(("div",values.get("class")))
                elif self.in_grid and tag=="a" and "plan-buy" in values.get("class","").split():
                    self.links+=1
            def handle_endtag(self,tag):
                if tag=="div" and self.in_grid:
                    if not self.stack:
                        raise AssertionError("Unmatched closing div in pricing grid")
                    popped=self.stack.pop()
                    if popped[0]=="grid":
                        self.in_grid=False
        for file in ("index.html","en.html"):
            with self.subTest(file=file):
                html=(ROOT/file).read_text(encoding="utf8")
                parser=PricingStructure()
                parser.feed(html)
                self.assertFalse(parser.in_grid)
                self.assertEqual(parser.stack,[])
                self.assertEqual(parser.plan_ids,list(PLANS))
                self.assertEqual(parser.plan_depths,[1,1,1,1])
                self.assertEqual(parser.links,4)

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

    def test_managed_hosting_prices_match_checkout(self):
        prices={"monthly":12,"quarterly":33,"semiannual":66,"annual":123}
        for home,checkout in (("index.html","checkout.html"),
                              ("en.html","checkout-en.html")):
            with self.subTest(home=home):
                site=(ROOT/home).read_text(encoding="utf8")
                form=(ROOT/checkout).read_text(encoding="utf8")
                for plan,price in prices.items():
                    block=re.search(r'<div data-plan="'+plan+r'" class="price(?: primary)?">([\s\S]*?)<a class="plan-buy"',site)
                    self.assertIsNotNone(block)
                    self.assertIn(f'<div class="amount">${price}</div>',block.group(1))
                    self.assertIn('class="hosting-included"',block.group(1))
                    option=re.search(r'<option value="'+plan+r'">([^<]+)</option>',form)
                    self.assertIsNotNone(option)
                    self.assertIn(str(price) if home=="en.html" else
                                  str(price).translate(str.maketrans("0123456789","۰۱۲۳۴۵۶۷۸۹")),
                                  option.group(1))
                self.assertIn('monthly:12,quarterly:33,semiannual:66,annual:123',form)
                self.assertIn('hosting',site.lower()) if home=="en.html" else self.assertIn("میزبانی",site)

    def test_checkout_disabled_until_backend(self):
        cfg=json.loads((ROOT/"checkout-config.json").read_text(encoding="utf8"))
        self.assertFalse(cfg["accept_payments"])
        self.assertFalse(cfg["api_base"])

if __name__=="__main__":
    unittest.main()
