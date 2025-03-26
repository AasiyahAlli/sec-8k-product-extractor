# SEC 8-K New Product Extractor

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
import json
import time
import csv
import re
import os
import datetime
import openai
import spacy
import warnings
import ast
import logging
import sys

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Suppress XML warning
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Load spaCy model
try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    import subprocess
    subprocess.run(["python", "-m", "spacy", "download", "en_core_web_sm"])
    nlp = spacy.load("en_core_web_sm")

# Configure
openai.api_key = "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"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; MyProject/1.0; +aasiyah.inshanally@outlook.com)"}
DOC_COUNT = 100
BASE_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
BASE_SEARCH_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={CIK}&type=8-K&count={COUNT}&output=atom"
CIK_CACHE_FILE = "company_tickers.json"

# Get ticker from CIK
def get_ticker_cik_mapping():
    if os.path.exists(CIK_CACHE_FILE):
        with open(CIK_CACHE_FILE, "r") as f:
            data = json.load(f)
            return {entry['ticker'].upper(): str(entry['cik_str']).zfill(10) for entry in data.values()}
    try:
        response = requests.get(BASE_TICKER_URL, headers=HEADERS)
        response.raise_for_status()
        data = response.json()
        with open(CIK_CACHE_FILE, "w") as f:
            json.dump(data, f)
        return {entry['ticker'].upper(): str(entry['cik_str']).zfill(10) for entry in data.values()}
    except Exception as e:
        logging.error(f"Error fetching ticker to CIK mapping: {e}")
        return {}

def get_cik_from_ticker(ticker):
    mapping = get_ticker_cik_mapping()
    return mapping.get(ticker.upper(), None)

# Get filings from link
def get_8k_filings(cik, count=DOC_COUNT):
    try:
        url = BASE_SEARCH_URL.format(CIK=cik, COUNT=count)
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'xml')
        entries = soup.find_all('entry')
        filings = []
        for entry in entries:
            filing = {
                'title': entry.title.text,
                'link': entry.link['href'],
                'filing_time': entry.updated.text
            }
            filings.append(filing)
        return filings
    except Exception as e:
        logging.error(f"Error fetching 8-K filings: {e}")
        return []

# Extract Text
def extract_filing_text(filing_url):
    try:
        response = requests.get(filing_url, headers=HEADERS)
        soup = BeautifulSoup(response.text, 'html.parser')
        table = soup.find('table', class_='tableFile', summary="Document Format Files")
        if not table:
            logging.warning(f"No document table found in: {filing_url}")
            return ""
        for row in table.find_all('tr')[1:]:
            cols = row.find_all('td')
            if len(cols) < 3:
                continue
            doc_link = cols[2].find('a')
            if doc_link and ('.htm' in doc_link['href'] or '.html' in doc_link['href']):
                full_url = "https://www.sec.gov" + doc_link['href']
                filing_page = requests.get(full_url, headers=HEADERS)
                filing_page.raise_for_status()
                return BeautifulSoup(filing_page.text, 'html.parser').get_text()
    except Exception as e:
        logging.error(f"Error extracting filing text: {e}")
    return ""

# Use OpenAI for extraction
def safe_json_parse(content):
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(content)
        except Exception as e:
            logging.warning(f"Fallback JSON parse failed: {e}")
            return None

def extract_product_info(text):
    prompt = f"""
You are a helpful assistant. Extract only the new product announcement info from the SEC 8-K filing below.
Output only in JSON format with fields: company_name, new_product, product_description (<180 characters).

SEC Filing:
{text}
"""
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=500
        )
        content = response['choices'][0]['message']['content']
        logging.debug(f"LLM raw output: {content}")
        return safe_json_parse(content)
    except Exception as e:
        logging.error(f"LLM error: {e}")
        return None

# Combine
def run_pipeline(ticker_list):
    output = []
    for ticker in ticker_list:
        logging.info(f"Processing {ticker}...")
        cik = get_cik_from_ticker(ticker)
        if not cik:
            logging.warning(f"CIK not found for {ticker}. Skipping.")
            continue
        filings = get_8k_filings(cik)
        for filing in filings:
            text = extract_filing_text(filing['link'])
            if not text:
                logging.warning(f"Empty filing text for {filing['link']}")
                continue
            product_info = extract_product_info(text)
            if not product_info:
                logging.warning(f"No product info extracted from {filing['link']}")
                continue
            logging.info(f"Extracted: {product_info}")
            row = [
                product_info.get("company_name", ""),
                ticker.upper(),
                filing['filing_time'],
                product_info.get("new_product", ""),
                product_info.get("product_description", "")
            ]
            output.append(row)
            time.sleep(1)  # To avoid hammering the server
    return output

# Save to CSV
def save_to_csv(data, filename=None):
    if not filename:
        filename = f"product_announcements_{datetime.date.today()}.csv"
    with open(filename, mode='w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(["company_name", "stock_name", "filing_time", "new_product", "product_description"])
        writer.writerows(data)

# Main
if __name__ == '__main__':
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL", "MSFT", "GOOGL"]
    extracted_data = run_pipeline(tickers)
    save_to_csv(extracted_data)
    logging.info("Extraction complete. Saved to CSV.")