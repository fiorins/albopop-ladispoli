import re, json, requests
from dotenv import load_dotenv
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()


# ── Configs ────────────────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "Mozilla/5.0"}

SEEN_FILE = "../seen.json"


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
    seen_list = sorted(seen, key=lambda x: (int(x.split("-")[0]), int(x.split("-")[1])))

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
