import os
import json
import re
import time
import uvicorn
import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
from google import genai
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from organizer import organize_and_heal

# ---------------------------------------------------------
# 1. SETUP & AUTHENTICATION
# ---------------------------------------------------------
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_SHEET_URL = os.getenv("GOOGLE_SHEET_URL")

if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY not found in .env file")
client = genai.Client(api_key=GOOGLE_API_KEY)

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

app = FastAPI(title="Agentic AI IPO Analyst")

# ---------------------------------------------------------
# 2. CORE HELPER FUNCTIONS
# ---------------------------------------------------------
def clean_and_parse_json(text):
    try: return json.loads(text)
    except: pass
    try:
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match: return json.loads(match.group(0))
    except: pass
    try:
        clean_text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_text)
    except: return []

def generate_with_fallback(prompt):
    """Tries multiple Gemini models in sequence to bypass rate limits."""
    models_to_try = [
        'gemini-3-flash-preview',          
        'gemini-2.5-flash',        
        'gemini-3.1-flash-lite-preview',   
        'gemini-2.5-flash-lite',   
        'gemini-2.0-flash'         
    ]
    for model_name in models_to_try:
        try:
            print(f"Agent Status: Attempting AI task with {model_name}...")
            response = client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            return response.text
        except Exception as e:
            error_msg = str(e).lower()
            if "429" in error_msg or "quota" in error_msg:
                print(f"Rate limited on {model_name}. Switching to next model...")
                continue 
            else:
                print(f"Unexpected error with {model_name}: {e}")
                continue 
    print("CRITICAL: All fallback models exhausted! Try again tomorrow.")
    return ""

# ---------------------------------------------------------
# 3. SCRAPING & ETL PIPELINE (The Backend)
# ---------------------------------------------------------
def scrape_web_data():
    urls = [
        "https://ipowatch.in/upcoming-ipo-calendar-ipo-list/",
        "https://www.chittorgarh.com/report/ipo-in-india-list-of-mainboard-sme-ipos/82/"
    ]

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    combined_raw_text = ""
    for url in urls:
        try:
            print(f"Agent Status: Scraping {url}...")
            response = requests.get(url, headers=headers, timeout=30)
            soup = BeautifulSoup(response.text, 'html.parser')
            extracted_text = soup.get_text()[:8000] 
            combined_raw_text += f"\n\n--- SOURCE: {url} ---\n{extracted_text}"
            time.sleep(2)
        except Exception as e:
            print(f"Scraping Error on {url}: {e}")
            
    return combined_raw_text

def ai_extract_ipos(raw_text):
    prompt = f"""
    I have scraped this raw text from an IPO website:
    {raw_text}

    --- TASK ---
    Identify upcoming, open, and recently closed IPOs. Extract them into a JSON list.
    
    CRITICAL RULES:
    1. **GMP**: Look for "GMP" or "Premium". If not found, put 0.
    2. **Conflict Resolution**: If different sources provide conflicting data, act conservatively. Choose the lower GMP and wider price band.
    3. **Status**: 'upcoming', 'open', or 'closed'.
    4. **Price Band**: Extract High and Low. If fixed, Low=High.
    5. **Empty Fields**: Use "TBA". Do not leave blank.

    --- OUTPUT FORMAT (JSON ONLY) ---
    [
        {{
            "company_name": "Name",
            "symbol": "ShortCode",
            "ipo_date": "YYYY-MM-DD",
            "application_open": "YYYY-MM-DD",
            "application_close": "YYYY-MM-DD",
            "industry": "Industry Name",
            "lot_size": 100,
            "price_band_low": 100,
            "price_band_high": 120,
            "gmp": 50,
            "status": "open",
            "notes": "Short hype summary"
        }}
    ]
    """
    response_text = generate_with_fallback(prompt)
    if response_text:
        return clean_and_parse_json(response_text)
    return []

def run_scraping_job():
    print("Agent Status: PHASE 1 - Processing Missing Details Queue...")
    try:
        workbook = gc.open_by_url(GOOGLE_SHEET_URL)
        missing_sheet = workbook.worksheet("Missing Details")
        raw_sheet = workbook.worksheet("recent data")
        
        missing_records = missing_sheet.get_all_records()
        time.sleep(3)
        
        if missing_records and len(missing_records) > 0:
            batch_string = ""
            for record in missing_records:
                company_name = str(record.get('company_name', '')).strip()
                missing_fields = str(record.get('missing_str', '')).strip() or 'industry, notes'
                if company_name:
                    batch_string += f"{company_name} (Missing: {missing_fields})\n"
            
            if batch_string.strip():
                batch_prompt = f"""
                I have a list of IPO companies that need specific missing fields filled in:
                {batch_string}
                
                CRITICAL INSTRUCTIONS:
                1. For each company, provide ONLY the missing fields listed.
                2. You MUST include the exact company_name in each JSON object.
                3. Provide realistic info. Otherwise, use "TBA".
                4. Return ONLY a JSON array.
                
                [
                    {{
                        "company_name": "Exact Company Name",
                        "industry": "Industry sector",
                        "notes": "Brief description"
                    }}
                ]
                """
                batch_response = generate_with_fallback(batch_prompt)
                if batch_response:
                    batch_data = clean_and_parse_json(batch_response)
                    if batch_data and len(batch_data) > 0:
                        raw_headers = raw_sheet.row_values(1)
                        time.sleep(3)
                        
                        # --- THE FIX: MERGE LOGIC ---
                        # Fetch the existing database so we don't overwrite prices/dates with blanks
                        main_sheet = workbook.worksheet("ipolist")
                        existing_ipos = main_sheet.get_all_records()
                        existing_map = {str(row.get('company_name', '')).strip().lower(): row for row in existing_ipos}
                        time.sleep(3)
                        
                        padded_rows = []
                        for item in batch_data:
                            comp_name = str(item.get("company_name", "")).strip().lower()
                            
                            # 1. Start with the existing data (prices, dates, etc.)
                            if comp_name in existing_map:
                                full_row = existing_map[comp_name].copy()
                            else:
                                full_row = {"company_name": item.get("company_name", "")}
                                
                            # 2. Overwrite only the missing cells with Gemini's new answers
                            for key, val in item.items():
                                if val and str(val).lower() not in ["tba", ""]:
                                    full_row[key] = val
                                    
                            # 3. Map it perfectly to headers
                            row_values = [str(full_row.get(h, "")).strip() for h in raw_headers]
                            padded_rows.append(row_values)
                        
                        if padded_rows:
                            raw_sheet.append_rows(padded_rows)
                            time.sleep(3)
                        
                        missing_sheet.batch_clear(["A2:Z1000"])
                        time.sleep(3)
    except Exception as e:
        print(f"Phase 1 Error: {e}")
    
    print("Agent Status: PHASE 2 - Normal Web Scraping...")
    raw_text = scrape_web_data()
    if not raw_text: return
    
    new_ipos = ai_extract_ipos(raw_text)
    if not new_ipos: return

    try:
        sheet = gc.open_by_url(GOOGLE_SHEET_URL).worksheet("recent data")
        headers = sheet.row_values(1)
        time.sleep(3)
        
        new_rows_values = [[str(ipo.get(h, "")).strip() if h in ipo else "" for h in headers] for ipo in new_ipos]
        sheet.append_rows(new_rows_values)
        time.sleep(3)
        print("Agent Status: Phase 2 complete.")
    except Exception as e:
        print(f"Failed to dump to Google Sheet: {e}")

def full_pipeline_job():
    print("🚀 MASTER PIPELINE: Starting Scraper Job...")
    run_scraping_job()
    print("✅ MASTER PIPELINE: Scraper finished. Starting Organizer Job...")
    organize_and_heal()
    print("🎉 MASTER PIPELINE: Full ETL sequence complete!")

# ---------------------------------------------------------
# 4. REPORTING & ANALYSIS (The Frontend)
# ---------------------------------------------------------
def analyze_ipo(ipo_data):
    """The Financial Brain: Calculates Gains, Risk, and Verdict"""
    data_str = json.dumps(ipo_data)
    prompt = f"""
    You are a Senior Wealth Manager and IPO Specialist. 
    Analyze this actionable IPO data for a high-net-worth client: {data_str}

    --- OBJECTIVE ---
    Provide a verdict on whether the client should commit their financial resources.
    
    --- CRITICAL ANALYSIS RULES ---
    1. **Assurance Level**: Calculate a 'Confidence Score' (0-100%). High GMP (>25%) and strong Industry sectors increase this.
    2. **Resource Allocation**:
       - 'FULL APPLY': Strong GMP, high demand, low risk.
       - 'MODERATE': Good GMP but volatile industry.
       - 'AVOID': Listing price likely near or below cost.
    3. **Listing Day Assurance**: Based on the GMP, estimate the profit margin.

    --- OUTPUT FORMAT (JSON LIST) ---
    [
        {{
            "company": "Name",
            "decision": "FULL APPLY",
            "score": 92,
            "risk": "Low",
            "price_band": "100-110",
            "listing_price": "₹145",
            "gain_percent": "35%",
            "reason": "Exceptional GMP and heavy oversubscription potential. High assurance for resource commitment.",
            "status_category": "active"
        }}
    ]
    """
    try:
        # Reusing our safe fallback function for the report generation!
        response_text = generate_with_fallback(prompt)
        return clean_and_parse_json(response_text)
    except: return []

def generate_html_report(analysis_results):
    """Creates a Professional Financial Dashboard Report"""
    active_ipos = [i for i in analysis_results if i.get('status_category') != 'closed' and i.get('decision') != 'CLOSED']
    closed_ipos = [i for i in analysis_results if i.get('status_category') == 'closed' or i.get('decision') == 'CLOSED']

    def create_card(item):
        decision = item.get('decision', 'WATCH')
        score = item.get('score', 0)
        risk = item.get('risk', 'Unknown')
        
        color = "#f39c12" 
        bg_color = "#fef9e7"
        if decision == "APPLY":
            color = "#27ae60"
            bg_color = "#eafaf1"
        elif decision == "AVOID":
            color = "#e74c3c"
            bg_color = "#fdedec"
        elif decision == "CLOSED":
            color = "#7f8c8d"
            bg_color = "#f4f6f7"

        return f"""
        <div class="ipo-card" style="border-left: 5px solid {color}; background-color: {bg_color}; padding: 15px; margin-bottom: 15px; border-radius: 8px; box-shadow: 0 2px 5px rgba(0,0,0,0.05);">
            <div style="display: flex; justify-content: space-between; align-items: start;">
                <div>
                    <h3 style="margin: 0; color: #2c3e50; font-size: 18px;">{item.get('company', 'Unknown')}</h3>
                    <div style="margin-top: 5px; font-size: 12px; color: #555;">
                        <span style="background: #fff; padding: 2px 6px; border-radius: 4px; border: 1px solid #ddd;">Risk: {risk}</span>
                        <span style="background: #fff; padding: 2px 6px; border-radius: 4px; border: 1px solid #ddd; margin-left: 5px;">Gain: {item.get('gain_percent', 'N/A')}</span>
                    </div>
                </div>
                <div style="text-align: right;">
                    <span style="background-color: {color}; color: white; padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: bold;">{decision}</span>
                    <div style="font-size: 11px; color: #7f8c8d; margin-top: 4px;">Score: {score}/100</div>
                </div>
            </div>
            
            <hr style="border: 0; border-top: 1px solid rgba(0,0,0,0.05); margin: 10px 0;">
            
            <div style="display: flex; justify-content: space-between; font-size: 13px; color: #34495e; margin-bottom: 8px;">
                <span>Price Band: <strong>{item.get('price_band', 'N/A')}</strong></span>
                <span>Est. Listing: <strong>{item.get('listing_price', 'N/A')}</strong></span>
            </div>

            <div style="display: flex; justify-content: space-between; font-size: 13px; color: #34495e; margin-bottom: 8px;">
                <span>IPO Date: <strong>{item.get('ipo_date', 'TBA')}</strong></span>
                <span>Open-Close: <strong>{item.get('open_date', 'TBA')} to {item.get('close_date', 'TBA')}</strong></span>
            </div>

            <p style="margin: 0; font-size: 13px; color: #555; line-height: 1.5; margin-top: 10px;">
                {item.get('reason', '')}
            </p>
        </div>
        """

    active_html = "".join([create_card(i) for i in active_ipos])
    closed_html = "".join([create_card(i) for i in closed_ipos])
    live_link = "https://ipo-research-agent.onrender.com/report"

    return f"""
    <html>
    <head>
        <title>Daily IPO Report</title>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f0f2f5; margin: 0; padding: 20px; }}
            .container {{ max-width: 700px; margin: auto; background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); }}
            h2 {{ color: #2c3e50; text-align: center; border-bottom: 2px solid #eee; padding-bottom: 15px; }}
            .section-title {{ color: #7f8c8d; font-size: 14px; text-transform: uppercase; letter-spacing: 1px; margin: 25px 0 10px 0; font-weight: bold; }}
            .btn {{ display: inline-block; padding: 10px 20px; color: white; text-decoration: none; border-radius: 6px; font-weight: bold; font-size: 14px; }}
            .btn-blue {{ background-color: #3498db; }}
            .btn-red {{ background-color: #e74c3c; border: none; cursor: pointer; }}
        </style>
    </head>
    <body>
        <div style="text-align: center; margin-bottom: 20px;">
            <a href="{live_link}" target="_blank" class="btn btn-blue">
                View Interactive Report
            </a>
        </div>
        <div id="report-content" class="container">
            <h2 style="margin: 0; border: none;">IPO Agent Dashboard</h2>
            <p style="text-align: center; color: #95a5a6; font-size: 12px; margin-top: -10px; margin-bottom: 30px;">
                Generated on {datetime.now().strftime('%Y-%m-%d')} | Powered by Gemini 3.0 Flash
            </p>
            <div class="section-title">Actionable Opportunities</div>
            {active_html if active_html else "<p style='color:#999; text-align:center;'>No active IPOs found today.</p>"}
            <div class="section-title">Closed / Waiting for Listing</div>
            {closed_html if closed_html else "<p style='color:#999; text-align:center;'>No closed IPOs pending listing.</p>"}
        </div>
    </body>
    </html>
    """

# ---------------------------------------------------------
# 5. FASTAPI ENDPOINTS
# ---------------------------------------------------------
@app.get("/")
def home():
    return {"status": "Online", "agent": "IPO Scraper Engine", "version": "7.0 (Master Pipeline + Reports)"}

@app.get("/update_sheet")
def trigger_master_pipeline(background_tasks: BackgroundTasks):
    """Activepieces triggers this first. Runs scraping & organizing in background."""
    background_tasks.add_task(full_pipeline_job)
    return {"status": "success", "message": "Master pipeline started. Running in background."}

@app.get("/report", response_class=HTMLResponse)
def get_daily_report():
    """Fetches ONLY actionable IPOs for the analysis report."""
    try:
        workbook = gc.open_by_url(GOOGLE_SHEET_URL)
        main_sheet = workbook.worksheet("ipolist")
        all_data = main_sheet.get_all_records()

        # FILTER: Only keep IPOs that are 'open' or 'upcoming'
        # This ensures the client only sees actionable data for asset management.
        actionable_data = [
            ipo for ipo in all_data 
            if str(ipo.get('status', '')).lower() in ['open', 'upcoming']
        ]

        # Safety: If there are too many (e.g. 30 upcoming), take the top 15 
        # of the filtered list to stay within Gemini's quality window.
        final_list = actionable_data[:15] if len(actionable_data) > 15 else actionable_data

        if not final_list:
            return "<h1>No Active or Upcoming IPOs found in the database today.</h1>"

        # Analyze only the filtered, actionable items
        results = analyze_ipo(final_list)
        
        # DIAGNOSTIC CHECK: Stop silent failures
        if not results:
            return f"""
            <div style="font-family: sans-serif; padding: 20px; border: 2px solid red; background-color: #fce4e4;">
                <h2 style="color: #c0392b;">⚠️ AI Analysis Failed</h2>
                <p>The system found <b>{len(final_list)}</b> actionable IPOs in the database, but the Gemini API failed to analyze them.</p>
                <p><b>Possible Reasons:</b> Free-tier Rate Limit (429) exceeded, or Invalid API Key.</p>
                <p>Check your Render logs for: <i>'All fallback models exhausted'</i>.</p>
            </div>
            """
            
        # Merge dates from original data into results
        for r in results:
            comp_name = str(r.get('company', '')).strip().lower()
            orig_item = next((item for item in final_list if str(item.get('company_name', '')).strip().lower() == comp_name), {})
            r['ipo_date'] = orig_item.get('ipo_date', 'TBA')
            r['open_date'] = orig_item.get('application_open', 'TBA')
            r['close_date'] = orig_item.get('application_close', 'TBA')
            
        return generate_html_report(results)
        
    except Exception as e:
        print(f"Report Error: {e}")
        return f"<h1>Error generating report: {e}</h1>"

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)