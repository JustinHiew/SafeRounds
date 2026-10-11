"""Real-browser check of the reviewer UI against tools/ui_check/server.py (fake model, no API spend).

    pip install playwright            # uses an installed Chromium; set CHROMIUM_PATH if needed
    PYTHONPATH=src python tools/ui_check/server.py . /tmp/ui-demo.db &
    python tools/ui_check/check.py /tmp/ui-shots   # prints PASS/FAIL lines and saves screenshots
"""
import json, os, sys
from playwright.sync_api import sync_playwright
OUT = sys.argv[1]; BASE = "http://127.0.0.1:8765"
issues, log = [], []
def check(cond, msg):
    log.append(("PASS " if cond else "FAIL ") + msg)
    if not cond: issues.append(msg)

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)
    ctx = b.new_context(viewport={"width": 1280, "height": 860}, accept_downloads=True)
    page = ctx.new_page()
    console = []
    page.on("console", lambda m: console.append(f"{m.type}: {m.text}") if m.type in ("error", "warning") else None)
    page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
    external = []
    page.on("request", lambda r: external.append(r.url) if not r.url.startswith(BASE) else None)

    page.goto(BASE + "/ui")
    check("/ui/login" in page.url, f"/ui redirects to login (at {page.url})")
    # bad password
    page.fill("#clinic", "harbourline"); page.fill("#email", "lead@clinic.example"); page.fill("#password", "wrong-password-1")
    page.click("#go"); page.wait_for_timeout(500)
    check(page.is_visible("#error") and page.inner_text("#error").strip() != "", f"bad login shows an error: {page.inner_text('#error')!r}")
    page.fill("#password", "correct-horse-battery"); page.click("#go")
    page.wait_for_url(BASE + "/ui", timeout=5000)
    page.wait_for_selector("button.item")
    state = ctx.storage_state()
    n = page.locator("button.item").count()
    check(n == 9, f"open queue lists 9 items (got {n})")
    check("Mia" not in page.inner_text("#listBody") and "0412" not in page.inner_text("#listBody"), "list shows no conversation text")
    page.screenshot(path=f"{OUT}/1-desktop-queue.png", full_page=True)

    # open each item and check highlights + no raw PII anywhere
    page.locator("button.item").first.click(); page.wait_for_selector(".crit")
    body = page.inner_text("#detailBody")
    check("0412 345 678" not in page.content() and "Mia" not in page.content(), "no raw name/phone in the page (de-identified)")
    check("peer support worker" in body or "AI assistant" in body or "intake" in body, "detail names the role as the clinic does")
    marks = page.locator("mark").count()
    log.append(f"INFO first item marks={marks} crit cards={page.locator('.crit').count()}")
    page.screenshot(path=f"{OUT}/2-desktop-detail.png", full_page=True)

    # find a flagged item (has a fail) and overturn one criterion by click
    flagged = None
    for i in range(n):
        page.locator("button.item").nth(i).click(); page.wait_for_timeout(250)
        if page.locator(".crit .chip.fail").count() and "needs review" not in page.inner_text("#detailBody").split("\n")[0:6].__str__():
            flagged = i; break
    check(flagged is not None, "found a flagged item with a fail verdict")
    if flagged is not None:
        check(page.locator("mark").count() > 0, "evidence quotes are highlighted in the transcript")
        card = page.locator(".crit").filter(has=page.locator(".chip.fail")).first
        card.locator("button.overturn").click(); page.wait_for_timeout(500)
        check("overturn" in card.inner_text().lower(), f"overturn recorded on the card")
        page.screenshot(path=f"{OUT}/3-after-overturn.png", full_page=True)

    # keyboard: j moves selection, c confirms all
    page.locator("button.item").first.click(); page.wait_for_timeout(300)
    page.locator("body").click(position={"x": 5, "y": 400}); page.keyboard.press("j"); page.wait_for_timeout(300)
    cur = page.locator('button.item[aria-current="true"]').count()
    check(cur == 1, "j moves the selection")
    before = page.locator("button.item").count()
    page.keyboard.press("c"); page.wait_for_timeout(700)
    after = page.locator("button.item").count()
    log.append(f"INFO open items before c={before} after c={after}")

    # unsure item: overturn disabled
    for i in range(page.locator("button.item").count()):
        page.locator("button.item").nth(i).click(); page.wait_for_timeout(250)
        u = page.locator(".crit").filter(has=page.locator(".chip.unsure"))
        if u.count():
            check(u.first.locator("button.overturn").is_disabled(), "unsure verdict cannot be overturned")
            break

    # reviewed tab
    page.click("#tabReviewed"); page.wait_for_timeout(500)
    r = page.locator("button.item").count()
    check(r >= 1, f"reviewed tab lists reviewed items ({r})")
    page.click("#tabOpen"); page.wait_for_timeout(400)

    # export (admin)
    with page.expect_download() as d:
        page.click("#exportLink")
    path = d.value.path(); lines = open(path).read().strip().splitlines()
    check(len(lines) >= 1 and all(json.loads(l).get("source") == "reviewer_feedback" for l in lines), f"export has {len(lines)} candidate(s) marked reviewer_feedback")
    check("0412" not in open(path).read(), "export contains no raw phone number")

    # phone width
    m = b.new_context(viewport={"width": 390, "height": 844}, storage_state=state, is_mobile=True, has_touch=True)
    mp = m.new_page(); mp.goto(BASE + "/ui"); mp.wait_for_selector("button.item")
    sw = mp.evaluate("document.documentElement.scrollWidth")
    check(sw <= 390, f"no horizontal scroll at 390px (scrollWidth={sw})")
    mp.screenshot(path=f"{OUT}/4-phone-list.png", full_page=True)
    mp.locator("button.item").first.click(); mp.wait_for_timeout(400)
    check(mp.is_visible("#back"), "phone: detail shows a Back to list button")
    sw = mp.evaluate("document.documentElement.scrollWidth")
    check(sw <= 390, f"phone detail: no horizontal scroll (scrollWidth={sw})")
    mp.screenshot(path=f"{OUT}/5-phone-detail.png", full_page=True)
    mp.click("#back"); mp.wait_for_timeout(300)
    check(mp.is_visible("button.item"), "phone: back returns to the list")

    # dark mode
    dk = b.new_context(viewport={"width": 1280, "height": 860}, storage_state=state, color_scheme="dark")
    dp = dk.new_page(); dp.goto(BASE + "/ui"); dp.wait_for_selector("button.item")
    dp.locator("button.item").first.click(); dp.wait_for_timeout(400)
    dp.screenshot(path=f"{OUT}/6-dark-detail.png", full_page=True)

    # dashboard (2F, admin): sparklines drawn by JS, no spend, phone width, dark mode
    page.goto(BASE + "/ui/dashboard"); page.wait_for_selector("#trend svg.spark")
    check(page.locator("#trend svg.spark path.line").count() >= 1, "dashboard draws sparklines (inline SVG)")
    check(page.locator("#daily tr").count() == 29, f"dashboard daily table has a header and 28 days ({page.locator('#daily tr').count()})")
    check(page.inner_text("#tiles").lower().find("evaluated") != -1 and page.inner_text("#alerts").strip() != "", "dashboard shows today's tiles and the alerts box")
    check("spend" not in page.inner_text("body").lower().replace("spend limit", "") and "usd" not in page.inner_text("body").lower(), "dashboard shows no spend or cost")
    page.screenshot(path=f"{OUT}/8-dashboard.png", full_page=True)
    dm = b.new_context(viewport={"width": 390, "height": 844}, storage_state=state, is_mobile=True, has_touch=True).new_page()
    dm.goto(BASE + "/ui/dashboard"); dm.wait_for_selector("#trend svg.spark")
    sw = dm.evaluate("document.documentElement.scrollWidth")
    check(sw <= 390, f"dashboard: no horizontal page scroll at 390px (scrollWidth={sw})")
    dm.screenshot(path=f"{OUT}/9-dashboard-phone.png", full_page=True)
    dd = b.new_context(viewport={"width": 1280, "height": 860}, storage_state=state, color_scheme="dark").new_page()
    dd.goto(BASE + "/ui/dashboard"); dd.wait_for_selector("#trend svg.spark")
    bg = dd.evaluate("getComputedStyle(document.body).backgroundColor")
    check(bg != "rgb(244, 246, 245)", f"dashboard dark mode applies the dark tokens (body background {bg})")
    dd.screenshot(path=f"{OUT}/10-dashboard-dark.png", full_page=True)

    # logout
    page.click("#logout"); page.wait_for_timeout(500)
    page.goto(BASE + "/ui"); check("/ui/login" in page.url, "after logout /ui goes back to login")

    check(not external, f"no external requests ({external[:3]})")
    real = [c for c in console if "401" not in c and "404" not in c]
    log.append(f"INFO ignored console lines: {[c for c in console if c not in real]}")
    check(not real, f"no console errors/warnings: {real[:5]}")
    # viewport (not full-page) screenshot after scrolling: is the header really sticky?
    sp = b.new_context(viewport={"width": 1280, "height": 860}, storage_state=state).new_page()
    sp.goto(BASE + "/ui"); sp.wait_for_selector("button.item"); sp.locator("button.item").first.click()
    sp.wait_for_timeout(300); sp.mouse.wheel(0, 900); sp.wait_for_timeout(300)
    sp.screenshot(path=f"{OUT}/7-scrolled-viewport.png")
    top = sp.evaluate("document.querySelector('.bar').getBoundingClientRect().top")
    check(abs(top) < 1, f"header stays at the top when scrolled (top={top})")
    lg = b.new_context(viewport={"width": 390, "height": 844}).new_page(); lg.goto(BASE + "/ui/login")
    lg.screenshot(path=f"{OUT}/0-login-phone.png")
    disp = lg.evaluate("getComputedStyle(document.getElementById('form')).display")
    check(disp == "grid", f"login form layout applied (display={disp})")
    b.close()
print("\n".join(log)); print("ISSUES:", issues)
sys.exit(1 if issues else 0)
