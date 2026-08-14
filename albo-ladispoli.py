from zoneinfo import ZoneInfo
from datetime import datetime, timezone

from functions.scrape import scrape_entries_with_retry, process_single_entry
from functions.box import get_box_client, get_box_items
from functions.google import init_sheet, save_to_sheet
from functions.rss import generate_rss
from functions.telegram import (
    send_telegram_msg,
    send_with_rate_limit,
    send_missing_entries_alert,
)
from functions.helpers import (
    create_session,
    load_seen,
    save_seen,
    registry_sort_key,
)


def find_missing_entries(seen, entries):
    if not entries:
        return []

    missing = []

    # Group new registry numbers by year
    new_by_year = {}

    for entry in entries:
        registry = entry.get("registry", "")
        year, number = registry_sort_key(registry)

        if not year:
            continue

        new_by_year.setdefault(year, set()).add(number)

    # Convert seen registries once
    seen_by_year = {}

    for registry in seen:
        year, number = registry_sort_key(registry)

        if not year:
            continue

        seen_by_year.setdefault(year, set()).add(number)

    for year, new_numbers in new_by_year.items():
        new_numbers = sorted(new_numbers)
        old_numbers = seen_by_year.get(year, set())

        first_new = new_numbers[0]
        last_new = new_numbers[-1]

        # Find the latest already-seen entry immediately before
        # the range of newly discovered entries
        previous_numbers = [n for n in old_numbers if n < first_new]

        if previous_numbers:
            start = max(previous_numbers) + 1
        else:
            # No previous reference available, so only check gaps
            # within the new batch itself
            start = first_new

        known_numbers = old_numbers | set(new_numbers)

        for number in range(start, last_new + 1):
            if number not in known_numbers:
                missing.append(f"{year}-{number}")

    return sorted(missing, key=registry_sort_key)


def main():

    print("----- Start log -----\n")
    current_year = datetime.now(ZoneInfo("Europe/Rome")).year

    # 1. Initialize Session and Global Headers
    session = create_session()

    # 2. Load already seen entries (as a Set for fast lookups)
    seen = load_seen()
    seen_list = sorted(seen, key=registry_sort_key)

    print(f"Previous run, old items list ({len(seen_list)} tot):\n" f"{seen_list}\n")

    # 2. Scrape new entries (Passing the session)
    # TO TEST COMMENT HERE:
    entries = scrape_entries_with_retry(seen, session)
    if entries is None:
        print("Website unreachable. Will retry next scheduled run.")
        print("----- End log -----")
        return
    # TO TEST UNCOMMENT HERE:
    # entries = [
    #     {
    #         "registry": "2026-1159",
    #         "year": "2026",
    #         "number": "1159",
    #         "title": "APPROVAZIONE ELENCO DEGLI UTENTI AMMESSI E DI QUELLI NON AMMESSI ALL’AGEVOLAZIONE TARIFFARIA PER L’ABBATTIMENTO DELLE SPESE SOSTENUTE PER IL PAGAMENTO DEL SERVIZIO DI MENSA SCOLASTICA ANNO 2025/2026 ",
    #         "type": "ATTI AMMINISTRATIVI",
    #         "sub_type": "DETERMINE",
    #         "pub_start": datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc),
    #         "pub_start_alt": "05/05/2026",
    #         "pub_end_alt": "20/05/2026",
    #         "att_count": "2",
    #         "entry_id": "1666628",
    #         "entry_url": "https://ladispoli.trasparenza-valutazione-merito.it/web/trasparenza/albo-pretorio/-/papca/display/1666628",
    #     }
    # ]

    if not entries:
        print("No new entries.")
        print("----- End log -----")
        return

    entries.sort(key=lambda entry: registry_sort_key(entry.get("registry", "")))
    entries_list = [entry.get("registry", "") for entry in entries]
    print(
        f"Actual run, new items list ({len(entries_list)} tot):\n" f"{entries_list}\n"
    )

    missing_entries = find_missing_entries(seen, entries)

    if missing_entries:
        print(
            f"WARNING: Missing registry entries detected "
            f"({len(missing_entries)} tot):\n"
            f"{missing_entries}\n"
        )

        send_with_rate_limit(
            send_missing_entries_alert,
            missing_entries,
        )

    # 3. Initialize external services
    box_client = get_box_client()
    sheet = init_sheet(current_year)

    # 4. Fetch Google Sheet IDs once to avoid "Quota Exceeded" errors
    existing_ids = set(sheet.col_values(4))

    # 5. Fetch current Box inventory
    box_items = get_box_items(box_client)
    # box_items_names = [entry.name for entry in box_items]
    box_items_names = {item.name for item in box_items}
    # print(f"Last 10 uploaded Box items ({len(box_items_names)} tot):\n{box_items_names[:10]}\n")

    valid_entries = []
    skipped_box = []
    uploaded_box = []

    # 6. Process each entry (Download/Upload logic)
    # Fetch attachment from url and upload it on Box, process in reverse to safely skip entries
    for entry in entries:

        result = process_single_entry(
            entry, box_client, box_items, box_items_names, session
        )
        # If the function returns "EXISTS", it means this entry has been handled before.
        # The code adds the entry's registry to a seen set and skips the rest of the loop for this item

        if result == "EXISTS":
            skipped_box.append(entry["registry"])
            continue

        if result is not None:
            if (
                result.get("box_file_id")
                and result.get("box_file_id") != "non presente"
            ):
                uploaded_box.append(entry["registry"])

            # Only add to the final queue if it's a valid dictionary
            valid_entries.append(result)

    print(
        f"\nSkipping Box step, items already stored ({len(skipped_box)} tot):\n{skipped_box}\n"
    )
    print(f"Uploaded on Box items ({len(uploaded_box)} tot):\n{uploaded_box}\n")

    if not valid_entries:
        print("\nNo valid new entries after attachment check.\n")
        print("----- End log -----")
        return

    # 7. Rebuild RSS Feed
    # Update RSS (pass all entries for full feed rebuild if needed)
    generate_rss(valid_entries)

    # 8. Final Processing: Telegram and Google Sheets
    # Send Telegram messages
    for entry in valid_entries:
        meta = {
            "title": entry.get("title", ""),
            "register": entry.get("registry", ""),
            "category": entry.get("sub_type", ""),
            "date_start": entry.get("pub_start_alt", ""),
            "date_end": entry.get("pub_end_alt", ""),
            "url": entry.get("entry_url", ""),
            "box_folder": entry.get("box_folder_link", ""),
        }

        # Send to Telegram (Auto-detects if file_bytes exists)
        sent_ok = send_with_rate_limit(
            send_telegram_msg,
            meta,
            file_bytes=entry.get("file_bytes"),  # entry.get("box_file_link"),
            filename=entry.get("filename")
            or f"allegato_atto_[{entry['registry']}].pdf",
        )

        # 9. Mark as processed and save to Sheet
        # Mark as seen only after successful processing
        if sent_ok:
            # Store the Telegram message ID for reference
            entry["tg_message_id"] = (
                sent_ok.json().get("result", {}).get("message_id", "")
            )

            # Update Google Sheets AND our local cache of IDs
            if save_to_sheet(sheet, entry, existing_ids):
                existing_ids.add(
                    str(entry["entry_id"])
                )  # We add it here so the NEXT entry in the loop knows this ID is now taken
                seen.add(entry["registry"])  # Add the entry to the completed items list

        # 10. Memory Management: Clear PDF data from RAM after use
        entry.pop("file_bytes", None)

    save_seen(seen)
    print(f"\nProcessed {len(valid_entries)} new entries.")

    print("----- End log -----")


if __name__ == "__main__":
    main()
