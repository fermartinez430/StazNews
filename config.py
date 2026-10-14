import os

# Public site origin, no trailing slash. Vercel uses the default.
# GitHub Pages sets BASE_URL in .github/workflows/pages.yml.
BASE_URL = os.environ.get("BASE_URL", "https://stazgames.com").rstrip("/")
SITE_NAME = "StazGames"
X_HANDLE = "StazNews"
OPERATOR = "Fernando Martinez"
OPERATOR_X = "stazmartinez"
TAGLINE = "Daily gaming news, free games, and the best deals, so you never miss a drop 🎮"
OUT_DIR = "public"
