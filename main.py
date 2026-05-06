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
import google.generativeai as genai
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
genai.configure(api_key=GOOGLE_API_KEY)

try:
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
    gc = gspread.authorize(creds)
    print("Google Sheets Authenticated Successfully!")
except Exception as e:
    print(f"CRITICAL: Failed to load credentials.json: {e}")

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
        'gemini-3.0-flash',          
        'gemini-2.5-flash',        
        'gemini-3.1-flash-lite',   
        'gemini-2.5-flash-lite',   
        'gemini-1.5-flash'         
    ]
    for model_name in models_to_try:
        try:
            print(f"Agent Status: Attempting AI task with {model_name}...")
            current_model = genai.GenerativeModel(model_name)
            response = current_model.generate_content(prompt)
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
    url = "https://ipowatch.in/upcoming-ipo-calendar-ipo-list/" 
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        soup = BeautifulSoup(response.text, 'html.parser')
        return soup.get_text()[:15000]
    except Exception as e:
        print(f"Scraping Error: {e}")
        return ""

def ai_extract_ipos(raw_text):
    prompt = f"""
    I have scraped this raw text from an IPO website:
    {raw_text}

    --- TASK ---
    Identify upcoming, open, and recently closed IPOs. Extract them into a JSON list.
    
    CRITICAL RULES:
    1. **GMP**: Look for "GMP" or "Premium". If not found, put 0.
    2. **Status**: 'upcoming', 'open', or 'closed'.
    3. **Price Band**: Extract High and Low. If fixed, Low=High.
    4. **Empty Fields**: Use "TBA". Do not leave blank.

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
                    if batch_data:
                        raw_headers = raw_sheet.row_values(1)
                        time.sleep(3)
                        
                        padded_rows = []
                        for item in batch_data:
                            row_values = [str(item.get(h, "")).strip() if h in item else "" for h in raw_headers]
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
    You are a Senior Financial Analyst AI.
    Here is the latest IPO data: {data_str}

    --- YOUR JOB ---
    For each IPO, provide a deep analysis JSON object.
    
    1. **Calculate Gain**: If GMP & Price exist, calculate % Gain = (GMP / Price High) * 100.
    2. **Risk Analysis**: Assess risk (Low/Medium/High) based on Industry & GMP.
    3. **Decision**: 
       - APPLY (If Gain > 15% and Risk is Low/Med)
       - AVOID (If Gain < 5% or Risk is High)
       - WATCH (If uncertain or data missing)
       - CLOSED (If status is 'closed')
    4. **Estimated Listing**: Price High + GMP.

    --- OUTPUT FORMAT (JSON LIST) ---
    [
        {{
            "company": "Name",
            "decision": "APPLY",
            "score": 85,
            "risk": "Medium",
            "price_band": "133-140",
            "listing_price": "₹150",
            "gain_percent": "25%",
            "reason": "Strong GMP indicates healthy demand.",
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
    """Activepieces triggers this 4 minutes later to grab the email HTML."""
    try:
        # 1. Pull the fresh, organized data from Google Sheets
        workbook = gc.open_by_url(GOOGLE_SHEET_URL)
        main_sheet = workbook.worksheet("ipolist")
        raw_data = main_sheet.get_all_records()
        
        # 2. Ask the AI Analyst to evaluate the IPOs
        results = analyze_ipo(raw_data)
        
        # 3. Generate and return the HTML template
        return generate_html_report(results)
    except Exception as e:
        return f"<h1>Error generating report: {e}</h1>"

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)