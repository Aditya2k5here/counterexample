#!/usr/bin/env python3
"""Screenshot the counterexample report for the README.

The only thing in the repository that needs a browser. Everything that produces
a number runs with no dependencies at all.

    pip install playwright && playwright install chromium
    python tools/render_figures.py
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
REPORT = ROOT / "results" / "report.html"
DOCS = ROOT / "docs"


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("pip install playwright && playwright install chromium",
              file=sys.stderr)
        return 1
    if not REPORT.exists():
        print("no results/report.html -- run `make bench && make report` first",
              file=sys.stderr)
        return 1

    DOCS.mkdir(exist_ok=True)
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1300, "height": 1000},
                                device_scale_factor=2)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto("file://" + str(REPORT))
        page.wait_for_timeout(700)
        cards = page.locator(".card")
        n = cards.count()
        for i in range(min(n, 3)):
            cards.nth(i).screenshot(path=str(DOCS / ("counterexample-%d.png" % i)))
            print("wrote docs/counterexample-%d.png" % i)
        page.screenshot(path=str(DOCS / "report-top.png"),
                        clip={"x": 0, "y": 0, "width": 1300, "height": 1000})
        print("wrote docs/report-top.png")
        browser.close()
    if errors:
        print("page errors: %r" % errors[:3], file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
