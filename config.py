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

# Query params appended to direct store links once a program is approved.
# Leave a store as {} and nothing is added. CheapShark /redirect?dealID= URLs
# are never rewritten, so the deal redirect keeps working.
# Examples after approval:
#   "humble": {"partner": "stazgames"}
#   "fanatical": {"affiliate": "stazgames"}
#   "gmg": {"tap_a": "00000-000000"}
#   "amazon": {"tag": "stazgames-20"}
AFFILIATES = {
    "humble": {},
    "fanatical": {},
    "gmg": {},
    "amazon": {},
}
