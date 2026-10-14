# StazGames

Static site for [StazGames](https://fermartinez430.github.io/StazNews): gaming news, free games, and deals. `build.py` pulls public feeds and writes HTML, JSON, `sitemap.xml`, `robots.txt`, and `llms.txt` into `public/`.

The published site lives at `https://fermartinez430.github.io/StazNews`, so every internal link, canonical URL, asset path, sitemap entry, and robots/llms reference includes the `/StazNews/` project path. Change the public URL in one place: `BASE_URL` in `config.py` (no trailing slash).

## Build locally

```bash
python -m pip install -r requirements.txt
python build.py
python test_site.py
```

`python build.py --offline` skips the live feeds. A feed that fails is recorded in `public/build-report.json` and does not fail the build. `test_site.py --external` also checks outbound links; the Pages workflow does not pass that flag.

Preview under the same subpath Pages uses:

```bash
mkdir -p /tmp/staz-preview
ln -sfn "$PWD/public" /tmp/staz-preview/StazNews
python -m http.server 8080 -d /tmp/staz-preview
```

Then open http://localhost:8080/StazNews/.

## GitHub Pages

`.github/workflows/pages.yml` builds and deploys `public/` on every push to `main`, on manual `workflow_dispatch`, and every 3 hours. In the repository, set **Settings → Pages → Build and deployment → Source** to **GitHub Actions**.
