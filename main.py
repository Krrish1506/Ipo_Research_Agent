import os
import json
import requests
import re
import uvicorn
from bs4 import BeautifulSoup
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
import google.generativeai as genai
from datetime import datetime

# 1. Load Secrets
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
IPO_SHEET_API = os.getenv("IPO_SHEET_API")

# 2. Setup Gemini
if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY not found in .env file")

genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash') 

app = FastAPI(title="Agentic AI IPO Analyst")

# --- HELPER FUNCTIONS ---

def get_sheet_data():
    """Reads data from your Google Sheet via Sheet.best"""
    if not IPO_SHEET_API: return []
    try:
        response = requests.get(IPO_SHEET_API)
        return response.json()
    except: return []

def clean_and_parse_json(text):
    """Robust JSON extractor for AI responses"""
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

def scrape_web_data():
    """Fetches raw text from IPOWatch (or similar source)"""
    url = "https://ipowatch.in/upcoming-ipo-calendar-ipo-list/" 
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    
    try:
        print("Agent Status: Scraping Web Data...")
        response = requests.get(url, headers=headers, timeout=30)
        soup = BeautifulSoup(response.text, 'html.parser')
        # Get enough text to cover the main tables, but limit to prevent token overflow
        return soup.get_text()[:15000]
    except Exception as e:
        print(f"Scraping Error: {e}")
        return ""

def ai_extract_ipos(raw_text):
    """Uses Gemini to structure web data into precise financial data."""
    print("Agent Status: Extracting Data with Gemini...")
    prompt = f"""
    I have scraped this raw text from an IPO website:
    {raw_text}

    --- TASK ---
    Identify upcoming, open, and recently closed IPOs.
    Extract them into a JSON list.
    
    CRITICAL RULES:
    1. **GMP**: Look for "GMP", "Grey Market Premium", or "Premium". If not found, put 0.
    2. **Status**: precise status: 'upcoming', 'open', or 'closed'.
    3. **Price Band**: Extract the High and Low (e.g., 100-120). If fixed price, Low=High.
    4. **Empty Fields**: If data is missing (like dates), use "TBA". Do not leave blank.

    --- OUTPUT FORMAT (JSON ONLY) ---
    [
        {{
            "company_name": "Name",
            "symbol": "ShortCode",
            "ipo_date": "YYYY-MM-DD",
            "application_open": "YYYY-MM-DD",
            "application_close": "YYYY-MM-DD",
            "industry": "Industry Name",
            "lot_size": 100 (number),
            "price_band_low": 100 (number),
            "price_band_high": 120 (number),
            "gmp": 50 (number),
            "status": "open",
            "notes": "Short hype summary"
        }}
    ]
    """
    try:
        response = model.generate_content(prompt)
        return clean_and_parse_json(response.text)
    except Exception as e:
        print(f"Gemini Extraction Error: {e}")
        return []

def run_scraping_job():
    """Background Task: Scrapes -> Extracts -> Smart Updates (TBA -> Real Data)"""
    raw_text = scrape_web_data()
    if not raw_text: return

    new_ipos = ai_extract_ipos(raw_text)
    if not new_ipos: return

    print("Agent Status: Checking for updates and duplicates...")
    current_data = get_sheet_data()
    
    # Map existing companies for fast lookup: {'name': full_row_data}
    existing_map = {str(row.get('company_name', '')).lower().strip(): row for row in current_data}
    
    rows_to_add = []
    
    for ipo in new_ipos:
        # Clean defaults before processing
        if not ipo.get('gmp'): ipo['gmp'] = 0
        if not ipo.get('price_band_high'): ipo['price_band_high'] = 0
        if not ipo.get('price_band_low'): ipo['price_band_low'] = 0
        if not ipo.get('lot_size'): ipo['lot_size'] = 0
        if not ipo.get('industry'): ipo['industry'] = "TBA"
        if not ipo.get('notes'): ipo['notes'] = "Details pending."

        name_key = str(ipo.get('company_name', '')).lower().strip()
        
        # SCENARIO 1: NEW DATA (Add it)
        if name_key not in existing_map:
            rows_to_add.append(ipo)
            existing_map[name_key] = ipo # Prevent double adding
            
        # SCENARIO 2: EXISTING DATA (Check for Updates)
        else:
            existing_row = existing_map[name_key]
            needs_update = False
            
            # Check if Sheet has "TBA" or "0" but we found REAL data
            # Check Price
            old_price = str(existing_row.get('price_band_high', '0'))
            new_price = str(ipo.get('price_band_high', '0'))
            if (old_price in ['0', 'TBA', '']) and (new_price not in ['0', 'TBA', '']):
                needs_update = True
                
            # Check Date
            old_date = str(existing_row.get('application_open', 'TBA'))
            new_date = str(ipo.get('application_open', 'TBA'))
            if (old_date in ['TBA', '']) and (new_date not in ['TBA', '']):
                needs_update = True

            # If we found new info, UPDATE the specific row
            if needs_update:
                print(f"Agent Status: Updating found for {ipo['company_name']}...")
                try:
                    # Sheet.best update endpoint: /tabs/0/company_name/{value}
                    # We encode the company name to handle spaces/symbols
                    sanitized_name = requests.utils.quote(ipo['company_name'])
                    update_url = f"{IPO_SHEET_API}/company_name/{sanitized_name}"
                    requests.patch(update_url, json=ipo)
                except Exception as e:
                    print(f"Update Error: {e}")

    # Batch add completely new rows
    if rows_to_add:
        print(f"Agent Status: Adding {len(rows_to_add)} NEW rows...")
        try:
            requests.post(IPO_SHEET_API, json=rows_to_add)
        except Exception as e:
            print(f"Sheet Error: {e}")
    else:
        print("Agent Status: No new companies found.")

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
            "score": 85 (0-100),
            "risk": "Medium",
            "price_band": "133-140",
            "listing_price": "₹150",
            "gain_percent": "25%",
            "reason": "Strong GMP indicates healthy demand despite market volatility.",
            "status_category": "active" (use 'active' for open/upcoming, 'closed' for closed)
        }}
    ]
    """
    try:
        response = model.generate_content(prompt)
        return clean_and_parse_json(response.text)
    except: return []

def generate_html_report(analysis_results):
    """Creates a Professional Financial Dashboard Report"""
    
    # Split into Active and Closed
    active_ipos = [i for i in analysis_results if i.get('status_category') != 'closed' and i.get('decision') != 'CLOSED']
    closed_ipos = [i for i in analysis_results if i.get('status_category') == 'closed' or i.get('decision') == 'CLOSED']

    def create_card(item):
        decision = item.get('decision', 'WATCH')
        score = item.get('score', 0)
        risk = item.get('risk', 'Unknown')
        
        # Color Coding
        color = "#f39c12" # Orange (Watch)
        bg_color = "#fef9e7"
        if decision == "APPLY":
            color = "#27ae60" # Green
            bg_color = "#eafaf1"
        elif decision == "AVOID":
            color = "#e74c3c" # Red
            bg_color = "#fdedec"
        elif decision == "CLOSED":
            color = "#7f8c8d" # Grey
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

    # Important: Replace this with your actual Render URL
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
        <script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js"></script>
        <script>
            function downloadPDF() {{
                const element = document.getElementById('report-content');
                const opt = {{ margin: 0.3, filename: 'IPO_Analysis_Report.pdf', image: {{ type: 'jpeg', quality: 0.98 }}, html2canvas: {{ scale: 2 }}, jsPDF: {{ unit: 'in', format: 'letter', orientation: 'portrait' }} }};
                html2pdf().set(opt).from(element).save();
            }}
        </script>
    </head>
    <body>
        
        <!-- View Online Button (For Email Clients) -->
        <div style="text-align: center; margin-bottom: 20px;">
            <a href="{live_link}" target="_blank" class="btn btn-blue">
                🌐 View Interactive Report & Download
            </a>
            <p style="font-size: 11px; color: #999; margin-top: 5px;">(Gmail blocks scripts. Click above to enable PDF download)</p>
        </div>

        <div id="report-content" class="container">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
                <h2 style="margin: 0; border: none;">🚀 IPO Agent Dashboard</h2>
                <button onclick="downloadPDF()" class="btn btn-red">📄 PDF</button>
            </div>
            
            <p style="text-align: center; color: #95a5a6; font-size: 12px; margin-top: -10px; margin-bottom: 30px;">
                Generated on {datetime.now().strftime('%Y-%m-%d')} | Powered by Gemini 2.5
            </p>

            <!-- Active Section -->
            <div class="section-title">🔥 Actionable Opportunities</div>
            {active_html if active_html else "<p style='color:#999; text-align:center;'>No active IPOs found today.</p>"}

            <!-- Closed Section -->
            <div class="section-title">🔒 Closed / Waiting for Listing</div>
            {closed_html if closed_html else "<p style='color:#999; text-align:center;'>No closed IPOs pending listing.</p>"}

            <div style="margin-top: 40px; padding-top: 15px; border-top: 1px solid #eee; text-align: center; font-size: 10px; color: #bdc3c7;">
                <strong>Disclaimer:</strong> This report is AI-generated for educational purposes only. <br>
                It is not financial advice. Please consult a certified financial advisor before investing.
            </div>
        </div>
    </body>
    </html>
    """

# --- API ENDPOINTS ---

@app.get("/")
def home():
    return {"status": "Online", "agent": "IPO Analyst", "version": "2.0 (Pro)"}

@app.get("/update_sheet")
def trigger_background_update(background_tasks: BackgroundTasks):
    """Starts the scraping in the background."""
    background_tasks.add_task(run_scraping_job)
    return {"status": "success", "message": "Background Agent Started: Scraping & Analyzing..."}

@app.get("/report", response_class=HTMLResponse)
def run_analysis_html(background_tasks: BackgroundTasks):
    """Generates the report AND triggers a new scrape in the background."""
    # This line ensures your data is refreshed every time you (or anyone) views the report
    background_tasks.add_task(run_scraping_job)
    
    raw_data = get_sheet_data()
    results = analyze_ipo(raw_data)
    return generate_html_report(results)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)