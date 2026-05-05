import re, os, base64, requests, time
from datetime import datetime, timezone
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from functions.box import (
    upload_to_box,
    upload_to_box_folder,
    get_or_create_file_link,
    get_or_create_folder_link,
)
from functions.helpers import clean_jsessionid

load_dotenv()

# ── Configs ────────────────────────────────────────────────────────────────────

TIME_DELAY = 4  # seconds between each entry page request

ROOT_URL = os.getenv("ROOT_URL")
ELEMENT_BASE_URL = os.getenv("ELEMENT_BASE_URL")

if not ROOT_URL or not ELEMENT_BASE_URL:
    raise RuntimeError("Variable not found")


# Analyze the website scraping the list of entries that are not in seen list
def scrape_entries(seen, session):
    """Scrape the main table and return only new entries."""
    response = session.get(ROOT_URL, timeout=30)  # Use session
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


def scrape_entries_with_retry(seen, session, max_retries=3, wait=30):
    for attempt in range(1, max_retries + 1):
        try:
            return scrape_entries(seen, session)
        except requests.exceptions.ConnectTimeout:
            print(f"Timeout on attempt {attempt}/{max_retries}. Waiting {wait}s...")
            if attempt < max_retries:
                time.sleep(wait)
        except requests.exceptions.RequestException as e:
            print(f"Request error on attempt {attempt}/{max_retries}: {e}")
            if attempt < max_retries:
                time.sleep(wait)

    print("Max retries reached. Skipping this run.")
    return None


def extract_url(row):
    """Extracts URL and metadata from a specific row."""
    try:
        anchors = row.select("td a[onclick*='atob']")

        for anchor in anchors:
            span = anchor.find("span")
            span_class = span.get("class", []) if span else []

            if "icon-download-locked" in span_class:
                continue

            onclick = anchor.get("onclick", "")

            # Extract the base64 string from atob('...')
            match = re.search(r"atob\('([^']+)'\)", onclick)
            if not match:
                continue

            # Decode base64 to get the real URL
            decoded_url = base64.b64decode(match.group(1)).decode("utf-8")

            if decoded_url.startswith("http"):
                return clean_jsessionid(decoded_url)

        return None

    except Exception as e:
        print(f"Fetching attachment error: {e}")
        return None


def fetch_attachments(url, session):

    try:
        resp = session.get(url, timeout=30)  # Use session
        soup = BeautifulSoup(resp.text, "html.parser")

        detail_div = soup.select_one(".dettaglio-pratica-rght.span6")
        if not detail_div or not detail_div.get_text(strip=True):
            return "non presente"

        # 1. Find all rows with the specific data attribute
        attachment_rows = soup.find_all("tr", attrs={"data-chiave-allegato": True})
        if not attachment_rows:
            return None

        # Return the main doc or the first one if documento principale is not present
        main_row = next(
            (
                row
                for row in attachment_rows
                if any(
                    "documento principale" in td.get_text(strip=True).lower()
                    for td in row.find_all("td")
                )
            ),
            attachment_rows[0],  # fallback to first row
        )

        main_url = extract_url(main_row)

        if not main_url:
            return None

        # others_urls = []
        # if len(attachment_rows) > 1:
        #     for row in attachment_rows:
        #         if row == main_row:
        #             continue
        #         valid_url = extract_url(row)
        #         if valid_url:
        #             others_urls.append(valid_url)
        #             time.sleep(TIME_DELAY)

        others_attachments = []
        for row in attachment_rows:
            if row == main_row:
                continue

            valid_url = extract_url(row)
            if not valid_url:
                continue

            chiave = row.get("data-chiave-allegato", "")
            mimetype = row.get("data-mimetype", "application/pdf")
            ext = "pdf" if mimetype == "application/pdf" else "p7m"

            title_td = row.find("td")
            title = title_td.get_text(strip=True) if title_td else chiave
            safe_title = re.sub(r"[^\w\s\-]", "", title).strip().replace(" ", "_")
            filename = f"{safe_title}.{ext}"

            others_attachments.append(
                {
                    "url": valid_url,
                    "filename": filename,
                }
            )

        return main_url, others_attachments

    except Exception as e:
        print(f"Fetching attachment error: {e}")
        return None


def process_single_entry(entry, box_client, box_items, session):

    if entry["registry"] in box_items:
        return "EXISTS"

    # Fetch ALL attachment URLs
    attachments_result = fetch_attachments(entry["entry_url"], session)

    if attachments_result is None:
        print(f"Skipping (attachment not ready): {entry['registry']}")
        return None
    if attachments_result == "non presente":
        # Update entry with "Non Presente" data case
        entry.update(
            {
                "attachment_url": None,
                "box_file_id": "",
                "box_file_link": "",
                "file_bytes": None,
            }
        )
        return entry

    main_attachment, others_attachments = attachments_result

    if not others_attachments:
        try:
            # main document
            entry["attachment_url"] = main_attachment
            box_file_id, file_downloaded = upload_to_box(
                box_client, main_attachment, entry["registry"]
            )
            if not box_file_id:
                return None

            file_link = get_or_create_file_link(box_client, box_file_id)

            # Update entry with Box and File data
            entry.update(
                {
                    "box_file_id": box_file_id,
                    "box_file_link": file_link,
                    "file_bytes": file_downloaded,  # will be re-fetched for Telegram
                }
            )

            return entry

        except Exception as e:
            print(f"Error processing on Box attachment {entry['registry']}: {e}")
            return None

    # Download and upload all to Box subfolder
    else:
        try:
            # main document
            entry["attachment_url"] = main_attachment
            box_file_id, file_downloaded = upload_to_box(
                box_client, main_attachment, entry["registry"]
            )
            if not box_file_id:
                return None

            file_link = get_or_create_file_link(box_client, box_file_id)

            # other documents
            box_folder_id, box_files_id = upload_to_box_folder(
                box_client, others_attachments, entry["registry"]
            )

            folder_link = get_or_create_folder_link(box_client, box_folder_id)

            # Update entry with Box and File data
            entry.update(
                {
                    "attachment_url": entry["attachment_url"],
                    "box_file_id": box_file_id,
                    "box_file_link": file_link,
                    "file_bytes": file_downloaded,
                    "box_folder_link": folder_link,
                    "box_files_id": box_files_id,
                }
            )

            return entry

        except Exception as e:
            print(f"Error processing on Box attachment {entry['registry']}: {e}")
            return None
