import gspread
from oauth2client.service_account import ServiceAccountCredentials
from .helpers import GOOGLE_CONFIG_JSON


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
    try:
        return int(value)
    except (TypeError, ValueError):
        return ""  # optionally: print(f"safe_int: could not convert {value!r}")


def save_to_sheet(sheet, entry, existing_ids):

    if str(entry["entry_id"]) in existing_ids:
        print(f"Skipping Google Sheet step, item already stored: {entry['entry_id']}")
        return False

    try:
        # columns of the rows ont the google sheet
        row = [
            entry.get("title", ""),
            entry.get("pub_start_alt", ""),
            entry.get("pub_end_alt", ""),
            safe_int(entry.get("entry_id", "")),
            entry.get("entry_url", ""),
            safe_int(entry.get("year", "")),
            safe_int(entry.get("number", "")),
            entry.get("type", ""),
            entry.get("sub_type", ""),
            safe_int(entry.get("att_count", "")),
            entry.get("box_file_id", ""),
            entry.get("box_file_link", ""),
            ", ".join(entry.get("box_folder_ids", [])) or "non presente",
            entry.get("box_folder_link", "non presente"),
            safe_int(entry.get("tg_message_id", "")),
        ]

        sheet.append_row(row, value_input_option="USER_ENTERED")
        print(f"Saved on Google Sheets item {entry['registry']} ")

        return True

    except Exception as e:
        print("Google Sheets error: ", e)
        return False
