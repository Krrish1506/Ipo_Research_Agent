import os
import time
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials

# ----------------------------
# 1. Config / Auth
# ----------------------------
load_dotenv()
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")

try:
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
    gc = gspread.authorize(creds)
except Exception as e:
    print(f"CRITICAL: Failed to load credentials: {e}")
    exit()

def get_completeness_score(row_dict):
    """Rates how much data a row actually has."""
    score = 0
    for key, val in row_dict.items():
        v = str(val).strip().lower()
        if v and v not in ['tba', 'details pending.', '0']:
            score += 1
    return score

def organize_and_heal():
    print("Organizer Status: Connecting to Spreadsheet...")
    try:
        workbook = gc.open_by_url(GOOGLE_SHEET_URL)
        recent_sheet = workbook.worksheet("recent data")
        main_sheet = workbook.worksheet("ipolist")
        missing_sheet = workbook.worksheet("Missing Details")
    except Exception as e:
        print(f"Connection Error: {e}")
        return

    # ----------------------------
    # PHASE 1: Merge 'recent data' into 'ipolist'
    # ----------------------------
    print("Organizer Status: Checking 'recent data' for new scrapes...")
    new_data = recent_sheet.get_all_records()
    time.sleep(2) # Quota pause
    
    if new_data:
        headers = main_sheet.row_values(1)
        time.sleep(2)
        
        # Batch append everything from recent to the bottom of ipolist
        # We will let the deduplicator handle any duplicates later!
        rows_to_append = [[ipo.get(h, "") for h in headers] for ipo in new_data]
        main_sheet.append_rows(rows_to_append)
        print(f"Organizer Status: Appended {len(rows_to_append)} raw rows to ipolist.")
        time.sleep(2)
        
        # Wipe the recent data sheet clean safely
        recent_sheet.batch_clear(["A2:L1000"])
        print("Organizer Status: Wiped 'recent data' clean.")
        time.sleep(2)

    # ----------------------------
    # PHASE 2: Safe Deduplication (NO WIPING ALLOWED)
    # ----------------------------
    print("Organizer Status: Analyzing ipolist for duplicates...")
    all_ipos = main_sheet.get_all_records()
    time.sleep(2)
    
    best_rows = {} # Tracks the highest scoring row for each company
    
    # First pass: Figure out which row is the "best" for each company
    for i, ipo in enumerate(all_ipos):
        row_num = i + 2 # +2 because row 1 is header, and lists are 0-indexed
        company = str(ipo.get("company_name", "")).strip().lower()
        
        if not company:
            continue # Ignore blank rows
            
        score = get_completeness_score(ipo)
        
        if company not in best_rows or score > best_rows[company]['score']:
            best_rows[company] = {'row_num': row_num, 'score': score, 'data': ipo}

    best_row_numbers = set(info['row_num'] for info in best_rows.values())
    
    # Second pass: Mark any row that IS NOT the "best" row for deletion
    rows_to_delete = []
    for i, ipo in enumerate(all_ipos):
        row_num = i + 2
        if row_num not in best_row_numbers:
            rows_to_delete.append(row_num)
            
    # CRITICAL SAFETY: Delete duplicate rows one by one, BACKWARDS.
    # If you delete row 5, row 6 becomes row 5. Deleting backwards prevents this.
    if rows_to_delete:
        print(f"Organizer Status: Found {len(rows_to_delete)} duplicate/inferior rows. Safely deleting...")
        rows_to_delete.sort(reverse=True)
        for r_num in rows_to_delete:
            main_sheet.delete_rows(r_num)
            time.sleep(1.5) # Quota pause
        print("Organizer Status: Deduplication complete.")

    # ----------------------------
    # PHASE 3: Task Queue (Missing Details)
    # ----------------------------
    print("Organizer Status: Scanning for missing details...")
    missing_companies_to_queue = []
    
    for company_data in best_rows.values():
        ipo = company_data['data']
        notes = str(ipo.get("notes", "")).strip().lower()
        industry = str(ipo.get("industry", "")).strip().lower()
        
        # Figure out exactly what is missing
        missing_fields = []
        if notes in ['tba', '', 'details pending.']:
            missing_fields.append("notes")
        if industry in ['tba', '']:
            missing_fields.append("industry")
            
        if missing_fields:
            missing_companies_to_queue.append({
                "name": ipo.get("company_name", ""),
                "missing_str": ", ".join(missing_fields)
            })

    if missing_companies_to_queue:
        # Check what is already in the queue so we don't ask Gemini twice
        existing_missing = missing_sheet.col_values(1) # Gets column A
        time.sleep(2)
        
        existing_lower = [name.strip().lower() for name in existing_missing]
        
        new_to_ask = []
        for item in missing_companies_to_queue:
            if item["name"].strip().lower() not in existing_lower:
                # Appends [Column A (Name), Column B (What is missing)]
                new_to_ask.append([item["name"], item["missing_str"]]) 
                
        if new_to_ask:
            print(f"Organizer Status: Adding {len(new_to_ask)} new tasks to Missing Details queue...")
            missing_sheet.append_rows(new_to_ask)
            time.sleep(2)

    # ----------------------------
    # PHASE 4: Sort by Status then Date
    # ----------------------------
    print("Organizer Status: Sorting ipolist by Status and Date...")
    try:
        # Find which column number is 'status' and 'application_open'
        headers = main_sheet.row_values(1)
        
        # gspread uses 1-based indexing (A=1, B=2, etc.)
        date_col = headers.index('application_open') + 1 if 'application_open' in headers else 4
        status_col = headers.index('status') + 1 if 'status' in headers else 11
        
        # Sort Rule 1: Status A-Z (Closed -> Open -> Upcoming)
        # Sort Rule 2: Date Descending (Newest first)
        main_sheet.sort(
            (status_col, 'asc'), 
            (date_col, 'des'), 
            range='A2:L1000'
        )
        time.sleep(2)
    except Exception as e:
        print(f"Sort Note: {e}")

    print("Organizer Status: SUCCESS! Database is clean, deduplicated, and safely sorted.")

if __name__ == "__main__":
    organize_and_heal()