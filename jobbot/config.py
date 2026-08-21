import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "jobbot.db"

# Anthropic models — pin from docs.claude.com
GENERATION_MODEL = "claude-sonnet-4-6"   # resume / cover letter / answers
MATCHING_MODEL   = "claude-haiku-4-5-20251001"    # cheap normalization tasks

# Input data files
MASTER_RESUME_PATH = DATA_DIR / "master_resume.json"
PREFERENCES_PATH   = DATA_DIR / "preferences.json"
STAR_PATH          = DATA_DIR / "star.json"

# PDF output directory
PDF_DIR = DATA_DIR / "pdfs"
PDF_DIR.mkdir(parents=True, exist_ok=True)

# Flask
FLASK_HOST   = "127.0.0.1"
FLASK_PORT   = 5008
FLASK_DEBUG  = os.getenv("FLASK_DEBUG", "0") == "1"
SECRET_KEY   = os.getenv("JOBBOT_SECRET", "change-me-in-production")

# Selenium
CHROME_PROFILE_DIR = os.getenv("CHROME_PROFILE_DIR", "")   # empty = temp profile
USE_UNDETECTED_CD  = True   # use undetected-chromedriver

# Fuzzy matching threshold (rapidfuzz token_sort_ratio)
FUZZY_THRESHOLD = 85

# Character-limit default when maxlength not found on element
DEFAULT_CHAR_LIMIT = 4000

# ── Job discovery (jobspy) ─────────────────────────────────────────────────────
JOBSPY_SEARCH_TERMS   = os.getenv("JOBSPY_SEARCH_TERMS", "software engineer")
JOBSPY_LOCATION       = os.getenv("JOBSPY_LOCATION", "")
JOBSPY_RESULTS_WANTED = int(os.getenv("JOBSPY_RESULTS_WANTED", "50"))
JOBSPY_SITE_NAMES     = os.getenv("JOBSPY_SITES", "linkedin,indeed,glassdoor").split(",")
LEAD_SCORE_THRESHOLD  = int(os.getenv("LEAD_SCORE_THRESHOLD", "60"))
SCRAPE_HOUR           = int(os.getenv("SCRAPE_HOUR", "8"))   # daily scrape hour (24h)

# ── Gmail OAuth ────────────────────────────────────────────────────────────────
GMAIL_CREDENTIALS_PATH   = DATA_DIR / "gmail_credentials.json"
GMAIL_TOKEN_PERSONAL     = DATA_DIR / "gmail_token_personal.json"   # rluna727@gmail.com
GMAIL_TOKEN_BERKELEY     = DATA_DIR / "gmail_token_berkeley.json"   # rlunaorozco@berkeley.edu
GMAIL_SYNC_DAYS          = int(os.getenv("GMAIL_SYNC_DAYS", "14"))
