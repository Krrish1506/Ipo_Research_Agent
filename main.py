import os
import json
import requests
import re
import uvicorn
from bs4 import BeautifulSoup
from fastapi import FastAPI
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
    """Reads data from your Google Sheet via Sheet.best"""
    if not IPO_SHEET_API:
        return []
    try:
        response = requests.get(IPO_SHEET_API)
        return response.json()
    except Exception:
        return []

def clean_and_parse_json(text):
    """Smartly extracts JSON from Gemini's response."""
    try:
        # 1. Try direct parse
        return json.loads(text)
    except:
        pass
    try:
        # 2. Extract between [ ]
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
    except:
        pass
    try:
        # 3. Remove markdown
        clean_text = text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_text)
    except:
        return []

def scrape_web_data():
    """Fetches raw text from a reliable IPO news source."""
    # We use a standard financial news page. You can change this URL to any IPO list page.
    url = "https://ipowatch.in/upcoming-ipo-calendar-ipo-list/" 
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # We grab the main table or content. 
        # Limiting to first 10,000 chars prevents token overflow.
        text_content = soup.get_text()[:10000]
        return text_content
    except Exception as e:
        print(f"Scraping Error: {e}")
        return ""

def ai_extract_ipos(raw_text):
    """Uses Gemini to convert messy web text into Sheet Rows."""
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
    - gmp (Grey Market Premium as number, put 0 if not found)
    - issue_price (use price_band_high if not confirmed)
    - status (must be 'upcoming' or 'open')
    - notes (short 1 sentence summary of hype/risk)

    Return ONLY raw JSON. No markdown.
    """
    try:
        response = model.generate_content(prompt)
        return clean_and_parse_json(response.text)
    except Exception as e:
        print(f"Gemini Extraction Error: {e}")
        return []

def analyze_ipo(ipo_data):
    """Analyzes the Sheet Data for Investment Decisions."""
    data_str = json.dumps(ipo_data)
    prompt = f"""
    You are a strict Financial Analyst AI. 
    Here is the data for upcoming IPOs: {data_str}
    
    Instructions:
    1. Analyze GMP, Price Band, and Subscription data.
    2. Decide if I should APPLY, AVOID, or WATCH.
    3. Give a Score (0-100).
    
    Output Format:
    Return ONLY a raw JSON list.
    Keys: "company", "decision", "score", "reason"
    """
    try:
        response = model.generate_content(prompt)
        return clean_and_parse_json(response.text)
    except Exception as e:
        return [{"company": "Error", "decision": "ERROR", "score": 0, "reason": str(e)}]

def generate_html_report(analysis_results):
    """Generates the HTML Report with PDF Download."""
    html_rows = ""
    for item in analysis_results:
        # Color coding
        color = "#e74c3c" # Red
        bg_color = "#fdedec"
        decision = item.get('decision', 'N/A')
        
        if decision == "APPLY":
            color = "#27ae60" # Green
            bg_color = "#eafaf1"
        elif decision == "WATCH":
            color = "#f39c12" # Orange
            bg_color = "#fef9e7"
            
        html_rows += f"""
        <div class="ipo-card" style="border-left: 5px solid {color}; background-color: {bg_color}; padding: 15px; margin-bottom: 15px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1);">
            <h3 style="margin: 0; color: #2c3e50;">{item.get('company', 'Unknown')}</h3>
            <p style="margin: 5px 0;">
                <strong style="color: {color}; font-size: 16px;">{decision}</strong> 
                <span style="color: #555; font-weight: bold;">(Score: {item.get('score', 0)}/100)</span>
            </p>
            <p style="margin: 5px 0; font-size: 14px; color: #555; line-height: 1.4;">{item.get('reason', '')}</p>
        </div>
        """

    full_html = f"""
    <html>
    <head>
        <title>Daily IPO Report</title>
        <script src="https://cdnjs.cloudflare.com/ajax/libs/html2pdf.js/0.10.1/html2pdf.bundle.min.js"></script>
        <script>
            function downloadPDF() {{
                const element = document.getElementById('report-content');
                const opt = {{
                    margin:       0.5,
                    filename:     'IPO_Analysis_Report.pdf',
                    image:        {{ type: 'jpeg', quality: 0.98 }},
                    html2canvas:  {{ scale: 2 }},
                    jsPDF:        {{ unit: 'in', format: 'letter', orientation: 'portrait' }}
                }};
                html2pdf().set(opt).from(element).save();
            }}
        </script>
    </head>
    <body style="font-family: Arial, sans-serif; max-width: 700px; margin: auto; padding: 20px; background-color: #f4f6f7;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px;">
            <h2 style="color: #2c3e50; margin: 0;"></h2> 
            <button onclick="downloadPDF()" style="background-color: #e74c3c; color: white; border: none; padding: 12px 20px; border-radius: 5px; cursor: pointer; font-weight: bold; font-size: 14px; box-shadow: 0 2px 5px rgba(0,0,0,0.2);">
                📄 Download PDF
            </button>
        </div>
        <div id="report-content" style="background-color: white; padding: 30px; border-radius: 10px;">
            <h2 style="color: #2c3e50; text-align: center; border-bottom: 2px solid #ecf0f1; padding-bottom: 15px; margin-top: 0;">🚀 Daily IPO Agent Report</h2>
            <p style="text-align: center; color: #7f8c8d; font-size: 12px; margin-bottom: 25px;">
                Strict Financial Analysis | Powered by Gemini AI
            </p>
            {html_rows}
            <p style="font-size: 10px; color: #bdc3c7; text-align: center; margin-top: 30px; border-top: 1px solid #ecf0f1; padding-top: 10px;">
                Disclaimer: This report is AI-generated for informational purposes only. Not financial advice.
            </p>
        </div>
    </body>
    </html>
    """
    return full_html

# --- API ENDPOINTS ---

@app.get("/")
def home():
    return {"message": "IPO Agent is Running. Use /update_sheet to scrape, /report to analyze."}

@app.get("/update_sheet")
def auto_update_sheet():
    """1. Scrape Web -> 2. Gemini Extracts -> 3. Save to Sheet"""
    
    # Step A: Scrape
    print("Scraping website...")
    raw_text = scrape_web_data()
    if not raw_text:
        return {"status": "error", "message": "Failed to scrape website"}

    # Step B: Gemini Extraction
    print("Extracting with AI...")
    new_ipos = ai_extract_ipos(raw_text)
    if not new_ipos or not isinstance(new_ipos, list):
         return {"status": "error", "message": "AI could not find structured data"}

    # Step C: Prevent Duplicates
    current_data = get_sheet_data()
    # Normalize names to lowercase for comparison
    existing_names = [row.get('company_name', '').lower().strip() for row in current_data]
    
    rows_to_add = []
    for ipo in new_ipos:
        # Only add if company name is not already in the sheet
        if ipo.get('company_name', '').lower().strip() not in existing_names:
            rows_to_add.append(ipo)
    
    # Step D: Save
    if rows_to_add:
        print(f"Adding {len(rows_to_add)} new IPOs...")
        try:
            requests.post(IPO_SHEET_API, json=rows_to_add)
            return {"status": "success", "added_count": len(rows_to_add), "new_data": rows_to_add}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    else:
        return {"status": "success", "message": "Sheet is already up to date (No new IPOs found)."}

@app.get("/analyze")
def run_analysis_json():
    raw_data = get_sheet_data()
    results = analyze_ipo(raw_data)
    return results

@app.get("/report", response_class=HTMLResponse)
def run_analysis_html():
    raw_data = get_sheet_data()
    results = analyze_ipo(raw_data)
    html_content = generate_html_report(results)
    return html_content

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)