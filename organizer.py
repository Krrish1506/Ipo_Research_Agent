import os
import json
import time
from datetime import datetime
from dotenv import load_dotenv
import gspread
import gspread.utils
import yfinance as yf
from google.oauth2.service_account import Credentials

def parse_date(date_str):
    """Safely parses YYYY-MM-DD strings to date objects."""
    if not date_str or str(date_str).lower() in ['tba', 'details pending.', '0', '']:
        return None
    try:
        return datetime.strptime(str(date_str).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None

def fetch_live_stock_data(symbol):
    """Fetches live stock data using yfinance for listed companies."""
    if not symbol or not (symbol.endswith(".NS") or symbol.endswith(".BO")):
        return None
        
    try:
        stock = yf.Ticker(symbol)
        hist = stock.history(period="max")
        if hist.empty:
            return None
            
        listing_price = round(float(hist.iloc[0]['Open']), 2)
        current_open = round(float(hist.iloc[-1]['Open']), 2)
        current_close = round(float(hist.iloc[-1]['Close']), 2)
        
        diff = current_close - listing_price
        pct = (diff / listing_price) * 100 if listing_price > 0 else 0
        gain_loss = f"{diff:+.2f} ({pct:+.2f}%)"
        
        return {
            "listing_price": listing_price,
            "current_open": current_open,
            "current_close": current_close,
            "gain_loss": gain_loss
        }
    except Exception as e:
        print(f"Organizer Status: Error fetching yfinance for {symbol}: {e}")
        return None

# ----------------------------
# 1. Config / Auth
# ----------------------------
load_dotenv()
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")

try:
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    
    google_creds_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
    
    if google_creds_str:
        creds_dict = json.loads(google_creds_str)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        print("Google Sheets Authenticated Successfully! (from environment variable)")
    else:
        creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
        print("Google Sheets Authenticated Successfully! (from local file)")
        
    gc = gspread.authorize(creds)
except Exception as e:
    print(f"CRITICAL: Failed to load credentials: {e}")
    exit()

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

    # Get actual row counts from each sheet
    recent_row_count = len(recent_sheet.get_all_records())
    main_row_count = len(main_sheet.get_all_records())
    missing_row_count = len(missing_sheet.get_all_records())
    
    print(f"Organizer Status: Sheet row counts - recent: {recent_row_count}, ipolist: {main_row_count}, missing_details: {missing_row_count}")
    
    # Calculate actual ranges (header is row 1, data starts at row 2)
    recent_end_row = recent_row_count + 10  # Buffer for safety
    main_end_row = main_row_count + 100  # Larger buffer for main sheet
    missing_end_row = missing_row_count + 50  # Buffer for missing sheet
    
    print(f"Organizer Status: Dynamic ranges - recent: A2:L{recent_end_row}, ipolist: A2:L{main_end_row}, missing: A2:B{missing_end_row}")

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
        rows_to_append = [[ipo.get(h, "") for h in headers] for ipo in new_data]

        if rows_to_append:
            main_sheet.append_rows(rows_to_append)
            print(f"Organizer Status: Appended {len(rows_to_append)} rows to ipolist.")
            time.sleep(2)
        
        # Wipe the recent data sheet clean safely
        recent_sheet.batch_clear([f"A2:L{recent_end_row}"])
        print("Organizer Status: Wiped 'recent data' clean.")
        time.sleep(2)

    # ----------------------------
    # PHASE 2: Safe Cleanup & Deduplication (NO WIPING ALLOWED)
    # ----------------------------
    print("Organizer Status: Analyzing ipolist for cleanup...")
    all_ipos = main_sheet.get_all_records()
    time.sleep(2)
    headers = main_sheet.row_values(1)
    time.sleep(2)
    
    # Ensure Live Tracking Headers Exist
    new_cols = ["listing_price", "current_open", "current_close", "gain_loss"]
    added_new_cols = False
    for col in new_cols:
        if col not in headers:
            headers.append(col)
            added_new_cols = True
            
    if added_new_cols:
        print("Organizer Status: Adding new live tracking columns to ipolist...")
        end_col_a1 = gspread.utils.rowcol_to_a1(1, len(headers))
        end_col_letter = ''.join([c for c in end_col_a1 if c.isalpha()])
        main_sheet.update([headers], f"A1:{end_col_letter}1")
        time.sleep(2)
    
    rows_to_delete = []
    seen = {} # map (company, symbol) -> {'row_num': row_num, 'data': ipo}
    updates_map = {} # map old_row_num -> [updated_row_list]
    today = datetime.now().date()
    
    for i, ipo in enumerate(all_ipos):
        row_num = i + 2 # +2 because row 1 is header, and lists are 0-indexed
        
        # Check if more than 5 core columns are blank/incomplete (TBA is NOT blank)
        core_headers = [h for h in headers if h not in ["listing_price", "current_open", "current_close", "gain_loss"]]
        blank_count = 0
        for h in core_headers:
            val = str(ipo.get(h, "")).strip()
            if val == "":
                blank_count += 1
                
        if blank_count > 5:
            rows_to_delete.append(row_num)
            continue
            
        # Automatic Status Update based on Date
        status_changed = False
        ipo_d = parse_date(ipo.get("ipo_date", ""))
        close_d = parse_date(ipo.get("application_close", ""))
        open_d = parse_date(ipo.get("application_open", ""))
        
        current_status = str(ipo.get("status", "")).strip().lower()
        new_status = current_status
        
        if ipo_d and today >= ipo_d:
            new_status = "listed"
        elif close_d and today > close_d:
            new_status = "closed"
        elif open_d and close_d and open_d <= today <= close_d:
            new_status = "open"
        elif open_d and today < open_d:
            new_status = "upcoming"
            
        # Do not downgrade from listed
        if new_status != current_status and not (current_status == "listed" and new_status != "listed"):
            ipo["status"] = new_status
            status_changed = True
            
        # Deduplication: Check by company_name and symbol
        company_name = str(ipo.get("company_name", "")).strip().lower()
        symbol = str(ipo.get("symbol", "")).strip()
        
        # Live Market Tracking Update for Listed IPOs
        if new_status == "listed":
            live_data = fetch_live_stock_data(symbol)
            if live_data:
                for key, val in live_data.items():
                    if str(ipo.get(key, "")) != str(val):
                        ipo[key] = val
                        status_changed = True
        
        if company_name or symbol.lower():
            dup_key = (company_name, symbol.lower())
            if dup_key in seen:
                # Found a newer duplicate. We merge its valid data into the OLD row.
                old_info = seen[dup_key]
                old_ipo = old_info['data']
                old_row_num = old_info['row_num']
                
                changed = False
                for h in headers:
                    old_val = str(old_ipo.get(h, "")).strip()
                    new_val = str(ipo.get(h, "")).strip()
                    
                    # Update if new_val is not blank, not TBA, and different from old_val
                    if new_val and new_val.lower() not in ["tba", "details pending.", "0"]:
                        if new_val != old_val:
                            old_ipo[h] = new_val # Update our tracking copy
                            changed = True
                            
                if changed or status_changed:
                    # Queue an update request for the old row
                    updated_row_list = [old_ipo.get(h, "") for h in headers]
                    updates_map[old_row_num] = updated_row_list
                
                # The newer row is now redundant since its good data is merged. Delete it.
                rows_to_delete.append(row_num)
            else:
                # First time seeing this IPO (keep track of it)
                seen[dup_key] = {'row_num': row_num, 'data': ipo}
                if status_changed:
                    updated_row_list = [ipo.get(h, "") for h in headers]
                    updates_map[row_num] = updated_row_list

    # Push updates safely using batch_update
    if updates_map:
        # Dynamically calculate the end column letter to avoid IncorrectRange errors
        end_col_a1 = gspread.utils.rowcol_to_a1(1, len(headers))
        end_col_letter = ''.join([c for c in end_col_a1 if c.isalpha()])
        
        updates_to_push = [
            {'range': f'A{r_num}:{end_col_letter}{r_num}', 'values': [row_data]} 
            for r_num, row_data in updates_map.items()
        ]
        print(f"Organizer Status: Updating {len(updates_to_push)} existing IPOs with fresh data...")
        try:
            main_sheet.batch_update(updates_to_push)
            time.sleep(2)
        except Exception as e:
            print(f"Organizer Status: Failed to batch update rows: {e}")
            
    # CRITICAL SAFETY: Delete duplicate rows in batches (backwards).
    if rows_to_delete:
        print(f"Organizer Status: Found {len(rows_to_delete)} duplicate/incomplete rows. Safely deleting backwards...")
        rows_to_delete.sort(reverse=True)
        
        # Delete in SMALL batches of 10 to avoid quota overload
        batch_size = 10
        max_retries = 3
        
        for batch_start in range(0, len(rows_to_delete), batch_size):
            batch_end = min(batch_start + batch_size, len(rows_to_delete))
            batch = rows_to_delete[batch_start:batch_end]
            
            retry_count = 0
            while retry_count < max_retries:
                try:
                    for r_num in batch:
                        main_sheet.delete_rows(r_num)
                    print(f"Organizer Status: Deleted rows {batch_start}-{batch_end}/{len(rows_to_delete)}...")
                    time.sleep(6)
                    break
                except Exception as e:
                    if "429" in str(e) or "quota" in str(e).lower():
                        retry_count += 1
                        wait_time = (2 ** retry_count) * 5
                        print(f"Organizer Status: Quota hit. Waiting {wait_time}s before retry {retry_count}/{max_retries}...")
                        time.sleep(wait_time)
                    else:
                        print(f"Organizer Status: Unexpected error during deletion: {e}")
                        raise
        
        print("Organizer Status: Cleanup complete.")

    # ----------------------------
    # PHASE 3: Priority Task Queue (Missing Details)
    # ----------------------------
    print("Organizer Status: Rebuilding Missing Details queue with priority...")
    missing_companies_to_queue = []
    
    for company_data in seen.values():
        ipo = company_data['data']
        notes = str(ipo.get("notes", "")).strip().lower()
        industry = str(ipo.get("industry", "")).strip().lower()
        status = str(ipo.get("status", "")).strip().lower()
        symbol = str(ipo.get("symbol", "")).strip()
        
        # Figure out exactly what is missing
        missing_fields = []
        if notes in ['tba', '', 'details pending.']:
            missing_fields.append("notes")
        if industry in ['tba', '']:
            missing_fields.append("industry")
        if status == "listed" and not symbol.endswith(".NS") and not symbol.endswith(".BO"):
            missing_fields.append("symbol")
            
        if missing_fields:
            priority = 99 # Default low priority
            ipo_d = parse_date(ipo.get("ipo_date", ""))
            open_d = parse_date(ipo.get("application_open", ""))
            
            if status == "open" or (open_d and 0 <= (open_d - today).days <= 7):
                priority = 1
            elif status == "listed" or (ipo_d and 0 <= (ipo_d - today).days <= 14):
                priority = 2
                
            missing_companies_to_queue.append({
                "name": ipo.get("company_name", ""),
                "missing_str": ", ".join(missing_fields),
                "priority": priority
            })

    if missing_companies_to_queue:
        # Sort by priority (Lowest number = Highest Priority)
        missing_companies_to_queue.sort(key=lambda x: x["priority"])
        
        # Rebuild the queue completely so the highest priorities are always on top
        new_queue = [["company_name", "missing_str"]] # Headers
        for item in missing_companies_to_queue:
            new_queue.append([item["name"], item["missing_str"]]) 
            
        print(f"Organizer Status: Writing {len(new_queue)-1} prioritized tasks to Missing Details queue...")
        try:
            missing_sheet.batch_clear(["A1:Z1000"])
            time.sleep(2)
            missing_sheet.update(new_queue, "A1")
            time.sleep(2)
        except Exception as e:
            print(f"Organizer Status: Failed to rewrite Missing Details queue: {e}")

    # ----------------------------
    # PHASE 4: Sort by Dates
    # ----------------------------
    print("Organizer Status: Sorting ipolist by IPO Dates...")
    try:
        headers = main_sheet.row_values(1)
        
        # gspread uses 1-based indexing (A=1, B=2, etc.)
        ipo_date_col = headers.index('ipo_date') + 1 if 'ipo_date' in headers else 3
        open_col = headers.index('application_open') + 1 if 'application_open' in headers else 4
        close_col = headers.index('application_close') + 1 if 'application_close' in headers else 5
        
        # Sort Rule: IPO Date -> Open Date -> Close Date (Descending - Newest first)
        main_sheet.sort(
            (ipo_date_col, 'des'), 
            (open_col, 'des'), 
            (close_col, 'des'), 
            range=f'A2:L{main_end_row}'
        )
        time.sleep(2)
    except Exception as e:
        print(f"Sort Note: {e}")

    print("Organizer Status: SUCCESS! Database is clean, deduplicated, and safely sorted.")

if __name__ == "__main__":
    organize_and_heal()