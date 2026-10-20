# StazGames

Static site for [StazGames](https://stazgames.com): gaming news, free games, and deals. `build.py` pulls public feeds and writes HTML, JSON, RSS (`feed.xml` and `free-games/feed.xml`), `sitemap.xml`, `robots.txt`, `llms.txt`, and `og.png` into `public/`.

The primary site is `https://stazgames.com` on Vercel. GitHub Pages is a secondary copy at `https://fermartinez430.github.io/StazNews`. Set `BASE_URL` (no trailing slash) to change the public URL. It defaults to `https://stazgames.com`.

## Build locally

```bash
python -m pip install -r requirements.txt
python3 build.py
python3 test_site.py
```

`python3 build.py --offline` skips the live feeds. A feed that fails is recorded in `public/build-report.json` and does not fail the build. `python3 test_site.py --external` also checks outbound links.

Preview the apex site:

```bash
python3 -m http.server 8080 -d public
```

Then open http://localhost:8080/. For the GitHub Pages subpath, build with `BASE_URL=https://fermartinez430.github.io/StazNews` and serve `public/` under `/StazNews/`.

## Vercel

`vercel.json` installs dependencies, runs `python3 build.py && python3 test_site.py`, and publishes `public/`. `api/subscribe.py` is the newsletter function. It needs:

- `RESEND_API_KEY` — Resend API key that can create contacts
- `RESEND_SEGMENT_ID` — optional segment id; omit it to use the global contacts list

## GitHub Pages

`.github/workflows/pages.yml` builds and deploys `public/` on every push to `main`, on manual `workflow_dispatch`, and every 3 hours. The build and test steps set `BASE_URL` to the project site. The signup form there posts to `https://stazgames.com/api/subscribe`. In the repository, set **Settings → Pages → Build and deployment → Source** to **GitHub Actions**.
