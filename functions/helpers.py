import os, re, json, requests
from dotenv import load_dotenv
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Initialize environment variables once
load_dotenv()


# ── Request Settings ─────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "Mozilla/5.0"}
REQUEST_TIMEOUT = 30
TIME_DELAY = 4

# ── Project Paths ─────────────────────────────────────────────────────────────
FUNC_DIR = Path(__file__).resolve().parent
BASE_DIR = FUNC_DIR.parent
# BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SECRETS_DIR = BASE_DIR / ".secrets"
BOX_CONFIG_JSON = SECRETS_DIR / "config_box.json"
GOOGLE_CONFIG_JSON = SECRETS_DIR / "config_google.json"

# SEEN_FILE = os.path.join(BASE_DIR, "..", "seen.json")
SEEN_FILE = BASE_DIR / "seen.json"
FEED_FILE = BASE_DIR / "feed.xml"

# ── Environment Variables ────────────────────────────────────────────────────
ROOT_URL = os.getenv("ROOT_URL")
ELEMENT_BASE_URL = os.getenv("ELEMENT_BASE_URL")
ATTACHMENT_FOLDER_ID = os.getenv("ATTACHMENT_FOLDER_ID")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if (
    not ROOT_URL
    or not ELEMENT_BASE_URL
    or not ATTACHMENT_FOLDER_ID
    or not TELEGRAM_TOKEN
    or not TELEGRAM_CHAT_ID
):
    raise RuntimeError("Variable not found")


# ── Metadata Mappings ────────────────────────────────────────────────────────
TYPE_MAPPINGS = {
    "AVVISI": "Avvisi",
    "BANDI DI CONCORSO": "BandiDiConcorso",
    "DECRETI": "Decreti",
    "DELIBERE DI CONSIGLIO": "DelibereDiConsiglio",
    "DELIBERE DI GIUNTA": "DelibereDiGiunta",
    "DETERMINA": "Determine",
    "DETERMINE": "Determine",
    "ORDINANZE": "Ordinanze",
}

# ── RSS Metadata ─────────────────────────────────────────────────────────────
RSS_FEED_URL = "https://fiorins.github.io/albopop-ladispoli/feed.xml"
RSS_MUNICIPALITY_GEODATA = {
    "lat": "41.95326914",
    "long": "12.08091316",
    "city": "Ladispoli",
    "province": "Roma",
    "region": "Lazio",
    "istat": "istat:058116",
}


# ── Helpers ───────────────────────────────────────────────────────────────────
# Loads the list of already processed entries from seen.json
def load_seen():
    if not Path(SEEN_FILE).exists():
        # Create the file with an empty list []
        save_seen(set())
        return set()

    try:
        with Path(SEEN_FILE).open("r") as f:
            return set(json.load(f))
    except json.JSONDecodeError:
        return set()


# After processing new entries it saves the updated list back to seen.json
def save_seen(seen, limit=40):

    def safe_sort_key(x):
        try:
            parts = x.split("-")
            return (int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            return (0, 0)

    seen_list = sorted(seen, key=safe_sort_key)

    seen_list = seen_list[-limit:]
    seen_list.reverse()

    with Path(SEEN_FILE).open("w") as f:
        json.dump(seen_list, f, indent=4)


# Strips it out the session token embedded in the attachment url
def clean_jsessionid(url):
    return re.sub(r";jsessionid=.*?(?=\?)", "", url)


def create_session():
    session = requests.Session()
    session.headers.update(HEADERS)  # Set headers globally for this session

    retries = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=2,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )

    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    return session
