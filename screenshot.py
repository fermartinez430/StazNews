from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True); pg = b.new_page(viewport={"width":1280,"height":900})
    pg.goto("http://localhost:8080/StazNews/", wait_until="networkidle"); pg.screenshot(path="site-home.png", full_page=True); b.close()
