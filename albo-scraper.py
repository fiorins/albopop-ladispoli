import requests
import json
import os
import re
import base64
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
import openpyxl
from dotenv import load_dotenv
from lxml import etree
from zoneinfo import ZoneInfo

# from boxsdk import JWTAuth, Client

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("tg_token")
TELEGRAM_CHAT_ID = os.getenv("tg_chatid")
BOX_CONFIG_PATH = "box__config.json"

ROOT_URL = (
    "https://ladispoli.trasparenza-valutazione-merito.it/web/trasparenza/albo-pretorio"
)
ELEMENT_BASE_URL = "https://ladispoli.trasparenza-valutazione-merito.it/web/trasparenza/albo-pretorio/-/papca/display/"

SEEN_FILE = "seen.json"
FEED_FILE = "feed.xml"
EXCEL_FILE = "albo.xlsx"
FEED_URL = "https://fiorins.github.io/albopop-ladispoli/feed.xml"  # ← update this

HEADERS = {"User-Agent": "Mozilla/5.0"}


# ── Helpers ───────────────────────────────────────────────────────────────────
# Loads the list of already processed entries from seen.json
def load_seen():
    try:
        return set(json.load(open(SEEN_FILE)))
    except FileNotFoundError:
        return set()


# After processing new entries it saves the updated list back to seen.json
def save_seen(seen):
    json.dump(list(seen), open(SEEN_FILE, "w"))


# Strips it out the session token embedded in the attachment url
def clean_jsessionid(url):
    return re.sub(r";jsessionid=.*?(?=\?)", "", url)


# ── Scraper ───────────────────────────────────────────────────────────────────
def scrape_entries(seen):
    """Scrape the main table and return only new entries."""
    resp = requests.get(ROOT_URL, headers=HEADERS)
    soup = BeautifulSoup(resp.text, "html.parser")

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
        if not registry_raw or registry_raw in seen:
            continue

        parts = registry_raw.split("/")
        if len(parts) != 2:
            continue
        year, number = parts[0].strip(), parts[1].strip()

        type_raw = cells[1].get_text(strip=True)
        type_parts = type_raw.split(" /")
        main_type = type_parts[0].strip()
        sub_type = type_parts[1].strip() if len(type_parts) > 1 else ""

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
                "registry": registry_raw,
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


def fetch_attachment_url(entry_url):
    try:
        resp = requests.get(entry_url, headers=HEADERS)
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
        print(f"Error fetching attachment: {e}")
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

    # inserisci categorie
    for i, (domain, value) in enumerate(categories):
        cat = etree.Element("category")
        cat.set("domain", domain)
        cat.text = value
        channel.insert(insert_index + i, cat)

    # inserisci xhtml meta subito dopo le categorie
    XHTML_NS = "http://www.w3.org/1999/xhtml"
    meta = etree.Element(
        f"{{{XHTML_NS}}}meta", attrib={"name": "robots", "content": "noindex"}
    )

    channel.insert(insert_index + len(categories), meta)


def add_item_categories(item, entry):
    categories = [
        ("item-category-entry", str(entry.get("entry_id", ""))),
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
        (
            "http://albopop.it/specs#item-category-annotation",
            str(entry.get("att_count", "")),
        ),
        ("item-category-shortIdUrl", str(entry.get("entry_url_short", ""))),
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

    # Riordina pubDate prima del guid
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
        fe.id(e["entry_url"])
        fe.title(e["title"])
        fe.link(href=e["entry_url"])
        fe.published(e["pub_start"])
        fe.description(f"📚 Allegati totali: {e['att_count']}")
        if e.get("attachment_url") and e["attachment_url"] != "non presente":
            fe.enclosure(e["attachment_url"], 0, "application/pdf")
        else:
            fe.enclosure("", 0, "application/pdf")

    # crea XML prima di salvare
    rss_xml = fg.rss_str(pretty=True)

    root = etree.fromstring(rss_xml)
    channel = root.find("channel")

    # aggiungi categorie custom
    add_channel_extras(channel)
    items = channel.findall("item")

    for item, entry in zip(items, all_entries):
        fix_item(item, entry)

    etree.indent(root, space="  ")

    # salva il file finale
    tree = etree.ElementTree(root)
    tree.write(FEED_FILE, pretty_print=True, xml_declaration=True, encoding="utf-8")


# ── Excel ─────────────────────────────────────────────────────────────────────
EXCEL_HEADERS = [
    "Titolo",
    "Data inizio",
    "Data fine",
    "Anno",
    "Numero",
    "Tipo",
    "Sotto-tipo",
    "N. Allegati",
    "URL voce",
    "URL allegato",
]


def append_to_excel(entries):
    if os.path.exists(EXCEL_FILE):
        wb = openpyxl.load_workbook(EXCEL_FILE)
        ws = wb.active
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(EXCEL_HEADERS)

    for e in entries:
        ws.append(
            [
                e["title"],
                e["pub_start_alt"],
                e["pub_end_alt"],
                e["year"],
                e["number"],
                e["type"],
                e["sub_type"],
                e["att_count"],
                e["entry_url"],
                e.get("attachment_url", ""),
            ]
        )

    wb.save(EXCEL_FILE)


# ── Telegram ──────────────────────────────────────────────────────────────────
def send_telegram_text(message):
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
    )


def send_telegram_document(title, registry, file_bytes, filename):
    caption = f"🆕 <b>{title}</b>\n" f"📋 Registro: {registry}"
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
        data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
        files={"document": (filename, file_bytes)},
    )


# ── Box ───────────────────────────────────────────────────────────────────────
# def upload_to_box(file_bytes, filename, folder_id="0"):
#     try:
#         auth = JWTAuth.from_settings_file(BOX_CONFIG_PATH)
#         client = Client(auth)
#         import io

#         client.folder(folder_id).upload_stream(io.BytesIO(file_bytes), filename)
#         print(f"Uploaded to Box: {filename}")
#     except Exception as e:
#         print(f"Box upload error: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    seen = load_seen()
    entries = scrape_entries(seen)
    # print(f"seen list: {seen}")
    # print(f"entries list: {entries}")

    if not entries:
        print("No new entries.")
        return

    valid_entries = []

    # print(f"valid_entries list start: {valid_entries}")

    # Process in reverse to safely skip entries
    for entry in reversed(entries):
        att_url = fetch_attachment_url(entry["entry_url"])

        if att_url is None:
            # Attachment not ready yet — skip, will retry next run
            print(f"Skipping (attachment not ready): {entry['registry']}")
            # continue

        entry["attachment_url"] = att_url
        valid_entries.insert(0, entry)
        # print(f"valid_entries list end: {valid_entries}")

    if not valid_entries:
        print("No valid new entries after attachment check.")
        return

    # Update Excel
    append_to_excel(valid_entries)

    # Update RSS (pass all entries for full feed rebuild if needed)
    generate_rss(valid_entries)

    # Per-entry: Telegram + Box
    for entry in valid_entries:
        att_url = entry.get("attachment_url", "non presente")

        if att_url and att_url != "non presente":
            try:
                file_resp = requests.get(att_url, headers=HEADERS)
                filename = f"{entry['year']}_{entry['number']}.pdf"
                file_bytes = file_resp.content

                # Telegram with file attached
                send_telegram_document(
                    entry["title"], entry["registry"], file_bytes, filename
                )

                # Box upload
                # upload_to_box(file_bytes, filename)

            except Exception as e:
                print(f"File handling error for {entry['registry']}: {e}")
                # Fallback: send text only
                send_telegram_text(
                    f"🆕 <b>{entry['title']}</b>\n"
                    f"📋 {entry['registry']}\n"
                    f'🔗 <a href="{entry["entry_url"]}">Apri voce</a>'
                )
        else:
            # No attachment — send text with hyperlink
            send_telegram_text(
                f"🆕 <b>{entry['title']}</b>\n"
                f"📋 {entry['registry']}\n"
                f"📎 Nessun allegato\n"
                f'🔗 <a href="{entry["entry_url"]}">Apri voce</a>'
            )

        # Mark as seen only after successful processing
        seen.add(entry["registry"])

    save_seen(seen)
    print(f"Processed {len(valid_entries)} new entries.")


if __name__ == "__main__":
    main()
