from zoneinfo import ZoneInfo
from datetime import datetime, timezone
from dotenv import load_dotenv

from functions.scrape import *
from functions.box import get_box_client, get_box_items
from functions.google import init_sheet, save_to_sheet
from functions.helpers import create_session, load_seen, save_seen
from functions.rss import generate_rss
from functions.telegram import send_telegram_msg, send_with_rate_limit

load_dotenv()


def main():

    print("----- Start log -----\n")
    current_year = datetime.now(ZoneInfo("Europe/Rome")).year

    # 1. Initialize Session and Global Headers
    session = create_session()

    # 2. Load already seen entries (as a Set for fast lookups)
    seen = load_seen()
    print(f"Previous run, old items list ({len(seen)} tot):\n{list(seen)}\n")

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
    #         "registry": "2026-1039",
    #         "year": "2026",
    #         "number": "1039",
    #         "title": "LIQUIDAZIONE SPESA PER LA FORNITURA DI BUONI PASTO ELETTRONICI FATTURA N. VO-67619 DEL 16/04/2026 DAY RISTOSERVICE S.P.A. ",
    #         "type": "ATTI AMMINISTRATIVI",
    #         "sub_type": "DETERMINE",
    #         "pub_start": datetime(2026, 4, 21, 0, 0, tzinfo=timezone.utc),
    #         "pub_start_alt": "21/04/2026",
    #         "pub_end_alt": "06/05/2026",
    #         "att_count": "2",
    #         "entry_id": "1656948",
    #         "entry_url": "https://ladispoli.trasparenza-valutazione-merito.it/web/trasparenza/albo-pretorio/-/papca/display/1656948",
    #     }
    # ]

    entries_list = [entry.get("registry", "") for entry in entries]
    entries_list.sort(key=lambda x: int(x.split("-")[-1]))
    print(f"Actual run, new items list ({len(entries_list)} tot):\n{entries_list}\n")

    if not entries:
        print("No new entries.")
        print("----- End log -----")
        return

    # 3. Initialize external services
    box_client = get_box_client()
    sheet = init_sheet(current_year)

    # 4. Fetch Google Sheet IDs once to avoid "Quota Exceeded" errors
    existing_ids = set(sheet.col_values(4))

    # 5. Fetch current Box inventory
    box_items = get_box_items(box_client)
    # print(f"Last 10 uploaded Box items ({len(box_items)} tot):\n{box_items[:10]}\n")

    valid_entries = []
    skipped_box = []
    uploaded_box = []

    # 6. Process each entry (Download/Upload logic)
    # Fetch attachment from url and upload it on Box, process in reverse to safely skip entries
    for entry in reversed(entries):
        # Entry at this point
        # entry: {
        #     'registry': '2026-1037',
        #     'year': '2026',
        #     'number': '1037',
        #     'title': 'APPROVAZIONE DEL PROGETTO DI FATTIBILITÀ TECNICO ECONOMICA PER IL “COMPLETAMENTO DEL RESTAURO CONSERVATIVO DEL COMPLESSO MONUMENTALE TORRE FLAVIA E MUSEALIZZAZIONE” VOLTO ALLA PARTECIPAZIONE ALL’ AVVISO PUBBLICO INDETTO DALLA REGIONE LAZIO CON DETERMINAZIONE DIRIGENZIALE N. G00823 DEL 27/01/2026, FINALIZZATO ALLA PRESENTAZIONE DI ISTANZE PER IL "PIANO DI INTERVENTI STRAORDINARI PER LA VALORIZZAZIONE DEI TEATRI, DELLE SALE CINEMATOGRAFICHE, DEI PALAZZI STORICI, DEI LUOGHI DI CULTO, DEGLI SPAZI ARCHEOLOGICI E RICREATIVI DEL LAZIO". ',
        #     'type': 'ATTI AMMINISTRATIVI',
        #     'sub_type': 'DELIBERE DI GIUNTA',
        #     'pub_start': datetime.datetime(2026, 5, 4, 0, 0, tzinfo=datetime.timezone.utc),
        #     'pub_start_alt': '21/04/2026',
        #     'pub_end_alt': '06/05/2026',
        #     'att_count': '31',
        #     'entry_id': '1656920',
        #     'entry_url': 'https://ladispoli.trasparenza-valutazione-merito.it/web/trasparenza/albo-pretorio/-/papca/display/1656920'
        # }
        # """

        result = process_single_entry(entry, box_client, box_items, session)
        # If the function returns "EXISTS", it means this entry has been handled before.
        # The code adds the entry's registry to a seen set and skips the rest of the loop for this item

        if result == "EXISTS":
            skipped_box.append(entry["registry"])
            continue

        if result is not None:
            if result.get("box_file_id"):
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
