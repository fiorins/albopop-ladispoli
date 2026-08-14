import re, requests, time, html
from .helpers import (
    TELEGRAM_CHAT_ID,
    TELEGRAM_CHAT_ID_UPDATES,
    TELEGRAM_TOKEN,
    TYPE_MAPPINGS,
)

TIME_DELAY = 4  # delay seconds
MAX_LENGTH = 1024  # caption max chars


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


def send_with_rate_limit(send_func, *args, max_retries=5, **kwargs):
    for attempt in range(max_retries):
        resp = send_func(*args, **kwargs)

        if resp is None:
            print(f"Attempt {attempt}: Function returned None.")
            return False

        if resp.status_code == 200:
            time.sleep(TIME_DELAY)
            return resp

        if resp.status_code == 429:
            retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
            print(
                f"Rate limit (Attempt {attempt}/{max_retries}). Wait {retry_after}s..."
            )
            time.sleep(retry_after + 1)
            continue

        print(f"Telegram error on attempt {attempt}: {resp.status_code} {resp.text}")
        return None

    print(f"Max retries ({max_retries}) reached. Giving up.")
    return None


def get_telegram_caption(meta: dict, include_header=False):

    def build(title):
        title_edit = re.sub(r"(\.|\d|\/)", lambda x: x.group(0) + "\u200c", title)
        category = str(meta.get("category", "")).strip().upper()
        sub_type_edit = TYPE_MAPPINGS.get(category, "Generico")

        pub_start = meta.get("date_start") or "N/A"
        pub_end = meta.get("date_end") or "N/A"

        header = "ℹ️ Allegato atto: non presente\n\n" if include_header else ""
        safe_official_url = clean_href(meta.get("url", "#"))
        safe_box_url = clean_href(meta.get("box_folder"))

        subfooter = (
            f'🔗 <a href="{safe_official_url}">Pagina sull\'albo ufficiale</a>\n\n'
        )
        footer = (
            f'📚 <a href="{safe_box_url}">Altri allegati atto</a>\n\u200b'
            if safe_box_url != "#"
            else "📚 Altri allegati atto: non presenti\n\u200b"
        )

        return (
            f"{header}"
            f"{escape(title_edit)}\n\n"
            f"📒 <b>Registro:</b> <code>{escape(meta.get('register', 'N/A'))}</code>\n"
            f"🏷 <b>Categoria:</b> #{escape(sub_type_edit)}\n"
            f"🗓 <b>Pubblicazione:</b> <code>{escape(pub_start)}</code>\n"
            f"⏳ <b>Scadenza:</b> <code>{escape(pub_end)}</code>\n"
            f"{subfooter}"
            f"{footer}"
        )

    raw_title = str(meta.get("title") or "Titolo non disponibile")
    caption = build(raw_title)

    if len(caption) <= MAX_LENGTH:
        return caption

    # Binary search for the longest title that fits
    lo, hi = 0, len(raw_title)

    while lo < hi:
        mid = (lo + hi + 1) // 2
        test_caption = build(raw_title[:mid].rstrip() + "…")

        if len(test_caption) <= MAX_LENGTH:
            lo = mid
        else:
            hi = mid - 1

    caption = build(raw_title[:lo].rstrip() + "…")

    if len(caption) > MAX_LENGTH:
        caption = build("…")

    return caption


def send_telegram_msg(meta: dict, file_bytes=None, filename=None):
    """
    Send a Telegram message based on situation, if file_bytes is provided,
    sends a document, otherwise, sends a text message
    """

    try:
        if file_bytes:
            # Send as Document
            caption = get_telegram_caption(meta)

            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML",
            }
            files = {"document": (filename, file_bytes, "application/pdf")}
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
            response = requests.post(url, data=payload, files=files)
        else:
            # Send as Text only
            caption = get_telegram_caption(meta, include_header=True)

            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": caption,
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


def send_missing_entries_alert(missing_entries):
    if not missing_entries:
        return None

    text = (
        "⚠️ <b>Missing registry entries detected</b>\n\n"
        f"Missing: <code>{', '.join(missing_entries)}</code>"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID_UPDATES,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    return requests.post(url, json=payload)
