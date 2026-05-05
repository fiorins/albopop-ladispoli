import os, re, requests, time, html
from dotenv import load_dotenv

load_dotenv()

# ── Variables ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError("Variable not found")


# ── Configs ────────────────────────────────────────────────────────────────────
TIME_DELAY = 4  # seconds between each message


# ── Functions ──────────────────────────────────────────────────────────────────


def clean_href(url):
    if not url:
        return "#"

    url = str(url).strip()

    if not url or url == "#":
        return "#"

    return html.escape(url, quote=True)


def escape(text):
    # The escape method is typically used to sanitize text so it doesn't break the format of the file or system
    return html.escape(str(text)) if text else ""


def send_with_rate_limit(send_func, *args, **kwargs):
    while True:
        resp = send_func(*args, **kwargs)

        if resp is None:
            return False

        if resp.status_code == 200:
            time.sleep(TIME_DELAY)
            return resp

        if resp.status_code == 429:
            retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
            print(f"Rate limit Telegram. Wait {retry_after} seconds...")
            time.sleep(retry_after + 1)
            continue

        print("Telegram error: ", resp.status_code, resp.text)
        return None


def get_telegram_caption(meta: dict, include_header=False):
    raw_title = str(meta.get("title") or "Titolo non disponibile")
    title_edit = re.sub(r"(\.|\d|\/)", lambda x: x.group(0) + "\u200c", raw_title)

    type_mappings = {
        "AVVISI": "Avvisi",
        "BANDI DI CONCORSO": "BandiDiConcorso",
        "DECRETI": "Decreti",
        "DELIBERE DI CONSIGLIO": "DelibereDiConsiglio",
        "DELIBERE DI GIUNTA": "DelibereDiGiunta",
        "DETERMINA": "Determine",
        "DETERMINE": "Determine",
        "ORDINANZE": "Ordinanze",
    }
    category = str(meta.get("category", "")).strip().upper()
    sub_type_edit = type_mappings.get(category, "Generico")

    header = "ℹ️ Allegato atto: non presente\n\n" if include_header else ""

    # 1. Check if box_folder actually has content
    safe_official_url = clean_href(meta.get("url", "#"))
    safe_box_url = clean_href(meta.get("box_folder"))

    subfooter = f'🔗 <a href="{safe_official_url}">Pagina sull\'albo ufficiale</a>\n\n'

    if safe_box_url != "#":
        footer = f'📚 <a href="{safe_box_url}">Altri allegati atto</a>\n\u200b'
    else:
        footer = f"📚 Altri allegati atto: non presenti\n\u200b"

    return (
        f"{header}"
        f"{escape(title_edit)}\n\n"
        f"📒 <b>Registro:</b> <code>{escape(meta.get('register', 'N/A'))}</code>\n"
        f"🏷 <b>Categoria:</b> #{escape(sub_type_edit)}\n"
        f"🗓 <b>Pubblicazione:</b> <code>{escape(meta.get('date_start', 'N/A'))}</code>\n"
        f"⏳ <b>Scadenza:</b> <code>{escape(meta.get('date_end', 'N/A'))}</code>\n"
        f"{subfooter}"
        f"{footer}"
    )


# If file_bytes is provided, sends a document, otherwise, sends a text message.
def send_telegram_msg(meta: dict, file_bytes=None, filename=None):

    try:
        if file_bytes:
            # Send as Document
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": get_telegram_caption(meta),
                "parse_mode": "HTML",
            }
            files = {"document": (filename, file_bytes, "application/pdf")}
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
            response = requests.post(url, data=payload, files=files)
        else:
            # Send as Text only
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": get_telegram_caption(meta, include_header=True),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            response = requests.post(url, json=payload)

        if response.ok:
            print(f"Sent on Telegram item {meta['register']} ")
        else:
            print(
                f"Telegram error with item {meta['register']} failed ({response.status_code}): {response.text}"
            )

        return response

    except Exception as e:
        print(f"Telegram error for {meta['register']}: {e}")
        return None
