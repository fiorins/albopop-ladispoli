import os, io, re, json, base64, requests, time, html
from zoneinfo import ZoneInfo

from datetime import datetime, timezone
from dotenv import load_dotenv

from bs4 import BeautifulSoup
from lxml import etree
from feedgen.feed import FeedGenerator

from box_sdk_gen import (
    BoxClient,
    BoxJWTAuth,
    JWTConfig,
    UploadFileAttributes,
    UploadFileAttributesParentField,
    AddShareLinkToFileSharedLink,
    AddShareLinkToFileSharedLinkAccessField,
)

import gspread
from oauth2client.service_account import ServiceAccountCredentials

load_dotenv()


# ── Variables ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BOX_CONFIG_JSON = ".secrets/config_box.json"
GOOGLE_CONFIG_JSON = ".secrets/config_google.json"

ROOT_URL = os.getenv("ROOT_URL")
ELEMENT_BASE_URL = os.getenv("ELEMENT_BASE_URL")

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID or not ROOT_URL or not ELEMENT_BASE_URL:
    raise RuntimeError("Variable not found")

# ── Configs ────────────────────────────────────────────────────────────────────
HEADERS = {"User-Agent": "Mozilla/5.0"}

SEEN_FILE = "seen.json"
FEED_FILE = "feed.xml"
FEED_URL = "https://fiorins.github.io/albopop-ladispoli/feed.xml"

TELEGRAM_DELAY = 4  # seconds between each message
SCRAPING_DELAY = 4  # seconds between each entry page request


# ── Helpers ───────────────────────────────────────────────────────────────────
# Loads the list of already processed entries from seen.json
def load_seen():
    try:
        with open(SEEN_FILE, "r") as f:
            data = json.load(f)
            return set(data)
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


# After processing new entries it saves the updated list back to seen.json
def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f, indent=4)  # indent makes it readable


# Strips it out the session token embedded in the attachment url
def clean_jsessionid(url):
    return re.sub(r";jsessionid=.*?(?=\?)", "", url)


# ── BOX cloud ─────────────────────────────────────────────────────────────────
def get_box_client():
    jwt_config = JWTConfig.from_config_file(config_file_path=BOX_CONFIG_JSON)
    auth = BoxJWTAuth(config=jwt_config)
    return BoxClient(auth=auth)


def get_box_items(client, folder_id="0"):
    result = client.folders.get_folder_items(folder_id)
    return [entry.name for entry in result.entries]


def upload_to_box(client, file_bytes, filename, folder_id="0"):
    register = re.search(r"\[(.*?)\]", filename)

    try:
        uploaded = client.uploads.upload_file(
            attributes=UploadFileAttributes(
                name=filename,
                parent=UploadFileAttributesParentField(id=folder_id),
            ),
            file=io.BytesIO(file_bytes),
        )

        file = uploaded.entries[0]
        print(f"Uploaded on Box item {register} with ID {file.id} and name {file.name}")

        return file

    except Exception as e:
        print("Box error: ", str(e))
        return None


def get_or_create_box_link(client, file_id):
    # Try to create the shared url
    try:
        file = client.shared_links_files.get_shared_link_for_file(
            file_id, "shared_link"
        )

        if not file.shared_link:
            file = client.shared_links_files.add_share_link_to_file(
                file_id,
                "shared_link",
                shared_link=AddShareLinkToFileSharedLink(
                    access=AddShareLinkToFileSharedLinkAccessField.OPEN
                ),
            )

        return file.shared_link.download_url

    # Fallback: recover
    except Exception as e:
        print("Error BOX: ", str(e))
        return None


# ── RSS ───────────────────────────────────────────────────────────────────────
def add_channel_extras(channel):
    categories = [
        ("http://albopop.it/specs#channel-category-type", "Comune"),
        ("http://albopop.it/specs#channel-category-municipality", "Ladispoli"),
        ("http://albopop.it/specs#channel-category-province", "Roma"),
        ("http://albopop.it/specs#channel-category-region", "Lazio"),
        ("http://albopop.it/specs#channel-category-latitude", "41.95326914"),
        ("http://albopop.it/specs#channel-category-longitude", "12.08091316"),
        ("http://albopop.it/specs#channel-category-country", "Italia"),
        ("http://albopop.it/specs#channel-category-name", "Comune di Ladispoli"),
        ("http://albopop.it/specs#channel-category-uid", "istat:058116"),
    ]

    webmaster = channel.find("webMaster")
    insert_index = (
        list(channel).index(webmaster) + 1 if webmaster is not None else len(channel)
    )

    # Insert categories
    for i, (domain, value) in enumerate(categories):
        cat = etree.Element("category")
        cat.set("domain", domain)
        cat.text = value
        channel.insert(insert_index + i, cat)

    # Insert xhtml meta right after categories
    XHTML_NS = "http://www.w3.org/1999/xhtml"
    meta = etree.Element(
        f"{{{XHTML_NS}}}meta", attrib={"name": "robots", "content": "noindex"}
    )

    channel.insert(insert_index + len(categories), meta)


def add_item_categories(item, entry):
    categories = [
        (
            "http://albopop.it/specs#item-category-pubStart",
            str(entry.get("pub_start_alt", "")),
        ),
        (
            "http://albopop.it/specs#item-category-pubEnd",
            str(entry.get("pub_end_alt", "")),
        ),
        ("http://albopop.it/specs#item-category-uid", str(entry.get("registry", ""))),
        ("http://albopop.it/specs#item-category-type", str(entry.get("type", ""))),
        ("item-category-subType", str(entry.get("sub_type", ""))),
        ("item-category-entry", str(entry.get("entry_id", ""))),
        (
            "item-category-attachments",
            str(entry.get("att_count", "")),
        ),
        ("item-category-attachBoxUrl", str(entry.get("box_shared_link", ""))),
    ]

    guid = item.find("guid")
    insert_index = list(item).index(guid) + 1 if guid is not None else len(item)

    for i, (domain, value) in enumerate(categories):
        cat = etree.Element("category")
        cat.set("domain", domain)
        cat.text = value
        item.insert(insert_index + i, cat)


def fix_item(item, entry):
    desc = item.find("description")
    if desc is not None:
        # desc.text = etree.CDATA(f"📚 Allegati totali: {entry.get('att_count', '')}")
        desc.text = f"📚 Allegati totali: {entry['att_count']}"

    guid = item.find("guid")
    if guid is not None:
        guid.set("isPermaLink", "true")

    add_item_categories(item, entry)

    # Reorder pubDate before the guid
    pub_date = item.find("pubDate")
    guid = item.find("guid")

    if pub_date is not None and guid is not None:
        item.remove(pub_date)
        guid_index = list(item).index(guid)
        item.insert(guid_index, pub_date)


def generate_rss(all_entries):
    fg = FeedGenerator()
    fg.id(FEED_URL)
    fg.title("AlboPOP - Comune - Ladispoli")
    fg.link(href="https://fiorins.github.io/albopop-ladispoli/feed")
    fg.description("*non ufficiale* RSS feed dell'Albo Pretorio di Ladispoli")
    fg.language("it")

    fg.docs("http://albopop.it/comune/ladispoli/")
    fg.webMaster("davidefiorini@outlook.com (Davide Fiorini)")

    for e in all_entries:
        fe = fg.add_entry()
        fe.id(e["entry_id"])
        fe.title(e["title"])
        fe.link(href=e["entry_url"])
        fe.published(e["pub_start"])
        fe.description(f"📚 Allegati totali: {e['att_count']}")
        # if e.get("box_shared_link") and e["box_shared_link"] != "non presente":
        if e.get("box_shared_link"):
            fe.enclosure(e["box_shared_link"], 0, "application/pdf")
        else:
            fe.enclosure("", 0, "application/pdf")

    # Create the xml before save
    rss_xml = fg.rss_str(pretty=True)

    root = etree.fromstring(rss_xml)
    channel = root.find("channel")

    # Add custom categories
    add_channel_extras(channel)
    items = channel.findall("item")

    for item, entry in zip(items, all_entries):
        fix_item(item, entry)

    etree.indent(root, space="  ")

    # Save the final file
    tree = etree.ElementTree(root)
    tree.write(FEED_FILE, pretty_print=True, xml_declaration=True, encoding="utf-8")


# ── TELEGRAM ──────────────────────────────────────────────────────────────────
def telegram_rate_wait():
    time.sleep(TELEGRAM_DELAY)


def escape(text):
    # The escape method is typically used to sanitize text so it doesn't break the format of the file or system
    return html.escape(str(text)) if text else ""


def send_with_rate_limit(send_func, *args, **kwargs):
    while True:
        resp = send_func(*args, **kwargs)

        if resp is None:
            return False

        if resp.status_code == 200:
            telegram_rate_wait()
            return resp

        if resp.status_code == 429:
            retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
            print(f"Rate limit Telegram. Attendo {retry_after} secondi...")
            time.sleep(retry_after + 1)
            continue

        print("Telegram error:", resp.status_code, resp.text)
        return None


def get_telegram_caption(meta: dict, include_header=False):

    title_edit = re.sub(r"(\.|\d|\/)", lambda x: x.group(0) + "\u200c", meta["title"])

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
    sub_type_edit = type_mappings.get(meta["category"], "Generico")

    header = "ℹ️ Allegato atto non presente\n\n" if include_header else ""

    return (
        f"{header}"
        f"{escape(title_edit)}\n\n"
        f"📒 <b>Registro:</b> <code>{escape(meta['register'])}</code>\n"
        f"🏷 <b>Categoria:</b> #{escape(sub_type_edit)}\n"
        f"🗓 <b>Pubblicazione:</b> <code>{escape(meta['date_start'])}</code>\n"
        f"⏳ <b>Scadenza:</b> <code>{escape(meta['date_end'])}</code>\n"
        f"🔗 <a href=\"{meta['url']}\">Pagina sull'albo ufficiale</a>\n\u200b"
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
            print(f"Sent on Telegram item: {meta['register']} ")
        else:
            print(
                f"Error with Telegram item: {meta['register']} failed ({response.status_code}): {response.text}"
            )

        return response

    except Exception as e:
        print(f"Telegram error for {meta['register']}: {e}")
        return None


# ── GOOGLE Sheet ──────────────────────────────────────────────────────────────
def init_sheet(year):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CONFIG_JSON, scope)
    client = gspread.authorize(creds)

    spreadsheet = client.open("AlboPOP-Ladispoli")
    sheet = spreadsheet.worksheet(f"voci {str(year)}")

    return sheet


def safe_int(value):
    if value in (None, ""):
        return ""
    return int(value)


def save_to_sheet(sheet, entry, existing_ids):

    if str(entry["entry_id"]) in existing_ids:
        print(f"Skipping Google Sheet step (already stored): {entry['entry_id']}")
        return False

    try:
        row = [
            entry["title"],
            entry["pub_start_alt"],
            entry["pub_end_alt"],
            safe_int(entry["entry_id"]),
            entry["entry_url"],
            safe_int(entry["year"]),
            safe_int(entry["number"]),
            entry["type"],
            entry["sub_type"],
            safe_int(entry["att_count"]),
            safe_int(entry.get("box_file_id", "")),
            entry.get("box_shared_link", ""),
            safe_int(entry.get("tg_message_id", "")),
        ]

        sheet.append_row(row, value_input_option="USER_ENTERED")
        print("Saved on Google Sheets:", entry["entry_id"])

        return True

    except Exception as e:
        print("Error Google Sheets:", e)
        return False


# ── Scraper ───────────────────────────────────────────────────────────────────
# Analyze the website scraping the list of entries that are not in seen list
def scrape_entries(seen, session):
    """Scrape the main table and return only new entries."""
    response = session.get(ROOT_URL, timeout=20)  # Use session
    soup = BeautifulSoup(response.text, "html.parser")

    entries = []
    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 5:
            continue
        if any(c.get_text(strip=True) == "" for c in cells):
            continue

        entry_id = row.get("data-id", "")
        if not entry_id:
            continue

        registry_raw = cells[0].get_text(strip=True)  # e.g. "2025/143"
        registry_edit = registry_raw.replace("/", "-")  # e.g. "2025-143"
        if not registry_edit or registry_edit in seen:
            continue
        parts = registry_edit.split("-")
        if len(parts) != 2:
            continue
        year, number = parts[0].strip(), parts[1].strip()

        main_el = cells[1].select_one(".categoria_categoria")
        sub_el = cells[1].select_one(".categoria_sottocategoria")
        main_type = main_el.get_text(strip=True) if main_el else ""
        sub_type = sub_el.get_text(strip=True) if sub_el else ""

        title = cells[2].get_text(strip=True)
        dates_raw = cells[3].get_text(strip=True)  # e.g. "01/01/2025 - 31/01/2025"
        att_count = cells[4].get_text(strip=True)

        pub_start_alt = dates_raw[:10]  # "01/01/2025"
        pub_end_alt = dates_raw[-10:]  # "31/01/2025"
        # Convert to RFC 822 for RSS pubDate
        try:
            pub_start = datetime.strptime(pub_start_alt, "%d/%m/%Y").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            pub_start = datetime.now(timezone.utc)

        entry_url = ELEMENT_BASE_URL + entry_id

        entries.append(
            {
                "registry": registry_edit,
                "year": year,
                "number": number,
                "title": title,
                "type": main_type,
                "sub_type": sub_type,
                "pub_start": pub_start,
                "pub_start_alt": pub_start_alt,
                "pub_end_alt": pub_end_alt,
                "att_count": att_count,
                "entry_id": entry_id,
                "entry_url": entry_url,
            }
        )

    return entries


def fetch_attachment_url(entry_url, session):
    try:
        resp = session.get(entry_url, timeout=20)  # Use session
        soup = BeautifulSoup(resp.text, "html.parser")

        detail_div = soup.select_one(".dettaglio-pratica-rght.span6")
        if not detail_div or not detail_div.get_text(strip=True):
            return "non presente"

        anchor = soup.select_one("tr[data-chiave-allegato] td a")
        if not anchor:
            return None

        onclick = anchor.get("onclick", "")
        # print(f"BREAKPOINT ATT 1: {onclick}", end="\n")

        # Extract the base64 string from atob('...')
        match = re.search(r"atob\('([^']+)'\)", onclick)
        # print(f"BREAKPOINT ATT 2: {match}", end="\n")
        if not match:
            return None

        # Decode base64 to get the real URL
        decoded_url = base64.b64decode(match.group(1)).decode("utf-8")
        # print(f"BREAKPOINT ATT 3: {decoded_url}", end="\n")

        if not decoded_url.startswith("http"):
            return None  # not ready yet, retry next run

        return clean_jsessionid(decoded_url)

    except Exception as e:
        print(f"Fetching attachment error: {e}")
        return None


def process_single_entry(entry, box_client, box_items, session):
    """
    Handles the attachment fetching and Box upload logic for a single entry.
    Returns the updated entry if successful, or None if it should be skipped.
    """
    filename = f"allegato_atto_[{entry['registry']}].pdf"

    # 1. Check if already in Box
    if filename in box_items:
        print(f"Skipping Box step (already stored): {entry['registry']}")
        return "SEEN"  # Special flag to mark as seen without processing

    # 2. Fetch the attachment URL
    att_url = fetch_attachment_url(entry["entry_url"], session)  # Pass session
    time.sleep(SCRAPING_DELAY)

    if att_url is None:
        print(f"Skipping (attachment not ready): {entry['registry']}")
        return None

    # 3. Handle "Non Presente" case
    if att_url == "non presente":
        entry.update(
            {
                "attachment_url": None,
                "box_file_id": "",
                "box_shared_link": "",
                "file_bytes": None,
            }
        )
        return entry

    # 4. Download and Upload
    try:
        entry["attachment_url"] = att_url

        # The attachment URL after base64 decoding sometimes points to a different domain (CDN or storage server). 
        # Sending session cookies from ladispoli.trasparenza-valutazione-merito.it to a different domain could cause issues or get rejected. 
        # Use plain requests.get() for the attachment download
        file_resp = requests.get(att_url, headers=HEADERS, timeout=30)
        file_resp.raise_for_status()

        box_file = upload_to_box(box_client, file_resp.content, filename)
        if not box_file:
            return None

        box_link = get_or_create_box_link(box_client, box_file.id)

        # Update entry with Box and File data
        entry.update(
            {
                "box_file_id": box_file.id,
                "box_shared_link": box_link,
                "file_bytes": file_resp.content,
                "filename": filename,
            }
        )

        return entry

    except Exception as e:
        print(f"Error processing on Box attachment {entry['registry']}: {e}")
        return None


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    current_year = datetime.now(ZoneInfo("Europe/Rome")).year

    # 1. Initialize Session and Global Headers
    session = requests.Session()
    session.headers.update(HEADERS)  # Set headers globally for this session

    # 2. Load already seen entries (as a Set for fast lookups)
    seen = load_seen()
    seen_list = sorted(
        list(seen), key=lambda x: int(x.split("-")[-1]) if "-" in x else 0
    )
    print(f"Previous run items list {len(seen_list)}:\n{seen_list}")

    # 2. Scrape new entries (Passing the session)
    entries = scrape_entries(seen, session)
    entries_list = [f"{entry['year']}-{entry['number']}" for entry in entries]
    entries_list.sort(key=lambda x: int(x.split("-")[-1]))
    print(f"Actual run items list {len(entries_list)}:\n{entries_list}")

    if not entries:
        print("No new entries.")
        return

    # 3. Initialize external services
    box_client = get_box_client()
    sheet = init_sheet(current_year)

    # 4. Fetch Google Sheet IDs once to avoid "Quota Exceeded" errors
    existing_ids = set(sheet.col_values(4))

    # 5. Fetch current Box inventory
    box_items = get_box_items(box_client)
    print(f"Box items ({len(box_items)} tot), first 20 items:\n{box_items[:20]}")
    print(f"Box items ({len(box_items)} tot), last 20 items:\n{box_items[-20:]}")

    valid_entries = []

    # 6. Process each entry (Download/Upload logic)
    # Fetch attachment from url and upload it on Box, process in reverse to safely skip entries
    for entry in reversed(entries):
        result = process_single_entry(entry, box_client, box_items, session)
        if result == "SEEN":
            seen.add(entry["registry"])
            continue

        if result is not None:
            # Only add to the final queue if it's a valid dictionary
            valid_entries.insert(0, result)

    if not valid_entries:
        print("No valid new entries after attachment check.")
        return

    # 7. Rebuild RSS Feed
    # Update RSS (pass all entries for full feed rebuild if needed)
    generate_rss(valid_entries)

    # 8. Final Processing: Telegram and Google Sheets
    # Send Telegram messages
    for entry in valid_entries:

        meta = {
            "title": f"{entry['title']}",
            "register": f"{entry['registry']}",
            "category": f"{entry['sub_type']}",
            "date_start": f"{entry['pub_start_alt']}",
            "date_end": f"{entry['pub_end_alt']}",
            "url": f"{entry['entry_url']}",
        }

        # Send to Telegram (Auto-detects if file_bytes exists)
        sent_ok = send_with_rate_limit(
            send_telegram_msg,
            meta,
            file_bytes=entry.get("file_bytes"),
            # file_bytes=entry.get("box_shared_link"),
            filename=entry.get("filename"),
        )

        # 9. Mark as processed and save to Sheet
        # Mark as seen only after successful processing
        if sent_ok:
            # Store the Telegram message ID for reference
            entry["tg_message_id"] = (
                sent_ok.json().get("result", {}).get("message_id", "")
            )

            seen.add(entry["registry"])

            # Update Google Sheets AND our local cache of IDs
            if save_to_sheet(sheet, entry, existing_ids):
                # We add it here so the NEXT entry in the loop knows this ID is now taken
                existing_ids.add(str(entry["entry_id"]))

        # 10. Memory Management: Clear PDF data from RAM after use
        entry.pop("file_bytes", None)

    save_seen(seen)
    print(f"Processed {len(valid_entries)} new entries.")


if __name__ == "__main__":
    main()
