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

# 1. Load Secrets
load_dotenv()
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
IPO_SHEET_API = os.getenv("IPO_SHEET_API")

# 2. Setup Gemini
if not GOOGLE_API_KEY:
    raise ValueError("GOOGLE_API_KEY not found in .env file")

genai.configure(api_key=GOOGLE_API_KEY)
model = genai.GenerativeModel('gemini-2.5-flash') 

app = FastAPI()

# --- HELPER FUNCTIONS ---

def get_sheet_data():
    if not IPO_SHEET_API: return []
    try:
        response = requests.get(IPO_SHEET_API)
        return response.json()
    except: return []

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

def scrape_web_data():
    url = "https://ipowatch.in/upcoming-ipo-calendar-ipo-list/" 
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        soup = BeautifulSoup(response.text, 'html.parser')
        return soup.get_text()[:10000]
    except Exception as e:
        print(f"Scraping Error: {e}")
        return ""

def ai_extract_ipos(raw_text):
    prompt = f"""
    I have scraped this raw text from an IPO website:
    {raw_text}

    Task: Identify the upcoming or open IPOs.
    Extract them into a JSON list matching these exact keys:
    - company_name
    - symbol (create a short 4-5 letter code if not found)
    - ipo_date (YYYY-MM-DD format, estimate if needed)
    - application_open (YYYY-MM-DD)
    - application_close (YYYY-MM-DD)
    - industry (guess based on name if not found)
    - lot_size (number)
    - price_band_low (number)
    - price_band_high (number)
    - gmp (number, 0 if not found)
    - issue_price (use price_band_high if not confirmed)
    - status (must be 'upcoming' or 'open')
    - notes (short 1 sentence summary)

    Return ONLY raw JSON. No markdown.
    """
    try:
        response = model.generate_content(prompt)
        return clean_and_parse_json(response.text)
    except Exception as e:
        print(f"Gemini Error: {e}")
        return []

def run_scraping_job():
    """This function runs in the background"""
    print("Background Job: Starting Scraping...")
    raw_text = scrape_web_data()
    if not raw_text: return

    print("Background Job: Extracting...")
    new_ipos = ai_extract_ipos(raw_text)
    if not new_ipos: return

    current_data = get_sheet_data()
    existing_names = [row.get('company_name', '').lower().strip() for row in current_data]
    
    rows_to_add = []
    for ipo in new_ipos:
        if ipo.get('company_name', '').lower().strip() not in existing_names:
            rows_to_add.append(ipo)
    
    if rows_to_add:
        print(f"Background Job: Adding {len(rows_to_add)} rows...")
        try:
            requests.post(IPO_SHEET_API, json=rows_to_add)
        except Exception as e:
            print(f"Sheet Error: {e}")
    else:
        print("Background Job: No new data.")

def analyze_ipo(ipo_data):
    data_str = json.dumps(ipo_data)
    prompt = f"""
    You are a strict Financial Analyst AI. 
    Data: {data_str}
    Output: JSON list with keys "company", "decision" (APPLY/AVOID/WATCH), "score", "reason".
    """
    try:
        response = model.generate_content(prompt)
        return clean_and_parse_json(response.text)
    except: return []

def generate_html_report(analysis_results):
    # (Same HTML/PDF Logic as before)
    html_rows = ""
    for item in analysis_results:
        color = "#e74c3c" if item.get('decision') == "AVOID" else "#27ae60" if item.get('decision') == "APPLY" else "#f39c12"
        bg_color = "#fdedec" if item.get('decision') == "AVOID" else "#eafaf1" if item.get('decision') == "APPLY" else "#fef9e7"
        
        html_rows += f"""
        <div class="ipo-card" style="border-left: 5px solid {color}; background-color: {bg_color}; padding: 15px; margin-bottom: 15px; border-radius: 5px;">
            <h3 style="margin: 0; color: #2c3e50;">{item.get('company', 'Unknown')}</h3>
            <p style="margin: 5px 0;">
                <strong style="color: {color}; font-size: 16px;">{item.get('decision', 'N/A')}</strong> 
                <span style="color: #555;">(Score: {item.get('score', 0)}/100)</span>
            </p>
            <p style="margin: 5px 0; font-size: 14px; color: #555;">{item.get('reason', '')}</p>
        </div>
        """
    return f"""
    <html>
    <head>
        <title>Daily IPO Report</title>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js"></script>
        <script>function downloadPDF() {{ const element = document.getElementById('report-content'); html2pdf().from(element).save('IPO_Report.pdf'); }}</script>
    </head>
    <body style="font-family: Arial, sans-serif; max-width: 700px; margin: auto; padding: 20px;">
        <button onclick="downloadPDF()" style="background-color: #e74c3c; color: white; border: none; padding: 10px; border-radius: 5px; cursor: pointer; float: right;">📄 PDF</button>
        <div id="report-content" style="background-color: white; padding: 30px;">
            <h2 style="color: #2c3e50; text-align: center;">🚀 Daily IPO Agent Report</h2>
            {html_rows}
        </div>
    </body>
    </html>
    """

# --- API ENDPOINTS ---

@app.get("/")
def home():
    return {"message": "IPO Agent is Running."}

@app.get("/update_sheet")
def trigger_background_update(background_tasks: BackgroundTasks):
    """Starts the scraping in the background and returns OK immediately."""
    background_tasks.add_task(run_scraping_job)
    return {"status": "success", "message": "Scraping started in background. Data will appear shortly."}

@app.get("/report", response_class=HTMLResponse)
def run_analysis_html():
    raw_data = get_sheet_data()
    results = analyze_ipo(raw_data)
    return generate_html_report(results)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)