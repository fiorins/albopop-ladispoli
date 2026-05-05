import os
from dotenv import load_dotenv
import gspread
from oauth2client.service_account import ServiceAccountCredentials

load_dotenv()


# ── Variables ──────────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
GOOGLE_CONFIG_JSON = os.path.join(BASE_DIR, "..", ".secrets", "config_google.json")


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
        print(f"Skipping Google Sheet step, item already stored: {entry['entry_id']}")
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
            entry.get("box_file_id", ""),
            entry.get("box_file_link", ""),
            entry.get("box_folder_ids", ""),
            entry.get("box_folder_link", ""),
            safe_int(entry.get("tg_message_id", "")),
        ]

        sheet.append_row(row, value_input_option="USER_ENTERED")
        print(f"Saved on Google Sheets item {entry['registry']} ")

        return True

    except Exception as e:
        print("Google Sheets error: ", e)
        return False
