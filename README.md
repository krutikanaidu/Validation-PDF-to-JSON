# Validation-PDF-to-JSON
Validation of gross amount from pdf to json

These two scripts together form a Form 93 validation pipeline — a tool used in Indian Railways/government accounting to verify that TDS (Tax Deducted at Source) data extracted into JSON matches the original Form 93 PDF document.

The Two-File Architecture
The design follows a clean library + runner pattern:

form93_validator_surya.py — the core engine: all extraction, parsing, comparison, and reporting logic lives here.
validate_uploads_surya.py — the entry point: auto-detects files, wires up arguments, and calls the engine.

This separation means you can import the validator as a module or run it directly via CLI.

Inside the Core Engine (form93_validator_surya.py)
1. Dependency Detection
At the top, the script probes for optional libraries (pdfplumber, surya, pdf2image, tabulate) using try/except blocks and sets boolean flags like HAS_PDFPLUMBER. This lets it gracefully degrade — if a heavy OCR library isn't installed, it falls back to simpler methods.
2. PDF Extraction — Three-Tier Fallback
The script has three ways to get text out of a PDF, tried in order:
Tier 1 — pdfplumber (primary, extract_pdf_records_pdfplumber)
Opens the PDF natively, extracts text page by page, cleans OCR artefacts (Cyrillic/Greek characters misread as digits like О → 0), then uses a regex _PL_VOUCHER_RE to find rows matching the pattern: voucher_number  DD/MM/YYYY  gross_amount  income_tax. It also handles line-wrapped amounts — e.g. if 127,600,4 appears at end of one line and 99 starts the next, it stitches them into 127,600,499.
Tier 2 — pdftotext (fallback digital extraction)
Shells out to the system pdftotext -layout command. If the text yield is too low (less than 500 alphanumeric chars), it escalates to Tier 3.
Tier 3 — Surya OCR (fallback for scanned/image PDFs)
Converts PDF pages to images via pdf2image, then runs the Surya deep-learning OCR model to read them. Models are lazy-loaded and cached in _surya_models so they're only instantiated once.
The legacy parse_pdf_records() function handles Tier 2/3 output — it's more complex because pdftotext text needs more heuristic parsing (PAN number detection, contractor name reconstruction across nearby lines, displaced income tax values).
3. JSON Loading (load_json)
Handles both a bare JSON array [...] and wrapped formats like {"data": [...]}, checking common envelope keys (data, records, rows, entries).
4. Standalone Integrity Checks (standalone_checks)
Runs entirely on the JSON — no PDF needed. For each record it checks:

Missing fields — any of voucher, nameOfAgency, grossAmt, incomeTax being empty
Non-numeric amounts — values that can't be parsed as integers
Suspicious names — values like "FORM 93" or "PAGE" that are clearly OCR header noise
Zero amounts — gross or tax being zero
Possible swap — income tax exceeding gross amount (likely the columns got swapped)
TDS rate anomaly — the ratio incomeTax / grossAmt should fall between 0.5% and 15%; anything outside that range is flagged
Duplicate vouchers — same voucher number appearing more than once

5. PDF vs JSON Comparison (compare)
Matches records by voucher number and checks three fields:

grossAmt — exact integer match; severity is CRITICAL if difference > ₹1,000, else WARNING
incomeTax — exact match; CRITICAL if difference > ₹500
nameOfAgency — fuzzy match via names_match(), which uses substring containment plus a 60% word-overlap threshold (ignoring stopwords like "ltd", "pvt")

It also reports vouchers present in PDF but absent from JSON (possible omissions) and vice versa (possible phantom entries).
6. Reporting & CSV Export
print_report() produces a terminal report with ANSI color coding (auto-disabled when not in a TTY). save_csv() writes two files: a full row-by-row comparison sheet and a focused issues log, both UTF-8 with BOM (for Excel compatibility).

The Runner (validate_uploads_surya.py)
This script adds auto-detection on top of the engine. It scans ./data/ (falling back to the current directory) for .pdf and .json files. If multiple files of a type are found, it prompts the user to choose. It also monkey-patches extract_pdf_text_surya to inject the --dpi argument, which is a pragmatic (if slightly hacky) way to thread a CLI parameter into a library function without changing its signature.
<img width="402" height="500" alt="Screenshot 2026-05-18 111321" src="https://github.com/user-attachments/assets/0319dd68-c20c-46f5-9eb7-5e03f30e33f5" />

