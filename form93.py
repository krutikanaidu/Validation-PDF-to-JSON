#!/usr/bin/env python3
"""Form 93 Validator — Surya OCR Edition (fixed + line-wrap handling)"""

import argparse, csv, json, re, subprocess, sys
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from tabulate import tabulate; HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False

try:
    from pdf2image import convert_from_path; HAS_PDF2IMAGE = True
except ImportError:
    HAS_PDF2IMAGE = False

# ── pdfplumber (NEW PRIMARY EXTRACTION ENGINE) ────────────────────────
try:
    import pdfplumber; HAS_PDFPLUMBER = True
except ImportError:
    HAS_PDFPLUMBER = False

HAS_SURYA = False; SURYA_API = None
RecognitionPredictor = None
DetectionPredictor = None

try:
    from surya.recognition import RecognitionPredictor  # type: ignore[no-redef]
    from surya.detection import DetectionPredictor  # type: ignore[no-redef]
    HAS_SURYA = True
    SURYA_API = "new"
except ImportError:
    pass

USE_COLOR = sys.stdout.isatty()
def _c(code, text): return f"\033[{code}m{text}\033[0m" if USE_COLOR else text
RED    = lambda t: _c("91", t)
YELLOW = lambda t: _c("93", t)
GREEN  = lambda t: _c("92", t)
CYAN   = lambda t: _c("96", t)
BOLD   = lambda t: _c("1",  t)
DIM    = lambda t: _c("2",  t)

# ══════════════════════════════════════════════════════════════════════
#  NEW: pdfplumber-based extraction  (replaces pdftotext + parse_pdf_records
#       as the primary path; pdftotext/Surya kept as fallback)
# ══════════════════════════════════════════════════════════════════════

def _pl_clean_line(line: str) -> str:
    """
    Replace OCR character substitutions that appear in Form 93 PDFs:
      '이'  Korean char misread as digit  → '0'
      'Ο'   Greek Omicron                 → '0'
      'О'   Cyrillic O (capital)          → '0'
      'о'   Cyrillic o (lowercase)        → '0'
    """
    return (line
            .replace('\uC774', '0')   # 이
            .replace('\u039F', '0')   # Ο  Greek Omicron
            .replace('\u041E', '0')   # О  Cyrillic capital O
            .replace('\u043E', '0')   # о  Cyrillic lowercase o
            )

# Core pattern: the integer immediately before DD/MM/YYYY is always
# the voucher number in Form 93. PAN and contractor name can appear
# anywhere on the same line (or an adjacent line) — we don't rely on them.
_PL_VOUCHER_RE = re.compile(
    r'(\d+)\s+(\d{2}/\d{2}/\d{3,4})\s+([\d,.]+)\s+([\d,.]+)'
)

# Lines that cannot contain a voucher record
_PL_SKIP_RE = re.compile(
    r'Report dated|FORM 93|Schedule of|Division:|Recovery For|'
    r'Sr\.|Designed|Referred to|between Central|with Railways|'
    r'Adjusting Account|Month\s*&\s*Year'
)

def extract_pdf_records_pdfplumber(pdf_path: str) -> dict:
    """
    Parse Form 93 PDF with pdfplumber and return a dict compatible with
    compare():

        { "voucher_str": {"nameOfAgency": str, "grossAmt": int|None,
                          "incomeTax": int|None}, ... }

    nameOfAgency is set to "" because pdfplumber's text layout doesn't
    reliably reconstruct the multi-line contractor name column; the
    comparison function already skips the name check when p_name is "".

    Handles all known Form 93 edge cases:
      • Cyrillic / Greek chars substituted for digits
      • PAN on a separate line from the amounts row
      • Scrambled OCR text mixed into the amounts line
      • Truncated dates (e.g. '05/01/202')
      • Periods used as thousands separators (e.g. '999.988')
      • Gross amounts wrapping to next line (e.g. '127,600,4' + '99' = '127,600,499')
    """
    records: dict = {}

    with pdfplumber.open(pdf_path) as pdf:
        n_pages = len(pdf.pages)
        print(f"  🔍 pdfplumber: {n_pages} pages …")

        for page_num, page in enumerate(pdf.pages, 1):
            text = page.extract_text()
            if not text:
                print(YELLOW(f"  ⚠  Page {page_num}: no text layer (image-based?)"))
                continue

            lines = text.split('\n')
            for i, line in enumerate(lines):
                if _PL_SKIP_RE.search(line):
                    continue

                cleaned = _pl_clean_line(line)
                m = _PL_VOUCHER_RE.search(cleaned)
                if not m:
                    continue

                voucher_no = int(m.group(1))
                # Sanity gate: Form 93 voucher numbers are in a reasonable range
                if not (1 <= voucher_no <= 999):
                    continue

                gross_raw = m.group(3)  # Keep commas for now to detect wrapping
                it_str    = m.group(4).replace(',', '').replace('.', '')

                # ── NEW: Handle gross amounts that wrap to next line ──────────
                # Pattern: comma-separated groups where last group has 1-2 digits
                # Example: "127,600,4" on line 1 + "99" on line 2 = "127,600,499"
                parts = gross_raw.split(',')
                if len(parts) > 1 and 0 < len(parts[-1]) < 3:
                    # Last group is incomplete (1-2 digits instead of 3)
                    needed_digits = 3 - len(parts[-1])
                    if i + 1 < len(lines):
                        next_line = _pl_clean_line(lines[i + 1]).strip()
                        # Look for continuation digits at start of next line
                        wrap_match = re.match(rf'^(\d{{{needed_digits}}})', next_line)
                        if wrap_match:
                            gross_raw += wrap_match.group(1)
                            print(f"    ✓ Voucher {voucher_no}: wrapped amount {m.group(3)} + {wrap_match.group(1)}")

                gross_str = gross_raw.replace(',', '').replace('.', '')

                voucher_key = str(voucher_no)
                if voucher_key in records:
                    continue  # keep first (earliest-page) occurrence

                records[voucher_key] = {
                    "nameOfAgency": "",          # not extracted — see docstring
                    "grossAmt":     int(gross_str) if gross_str.isdigit() else None,
                    "incomeTax":    int(it_str)    if it_str.isdigit()    else None,
                }

    return records

# ══════════════════════════════════════════════════════════════════════
#  LEGACY: pdftotext digital extraction  (kept as fallback)
# ══════════════════════════════════════════════════════════════════════

def extract_pdf_text_digital(pdf_path):
    try:
        r = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                           capture_output=True, text=True, timeout=60,
                           encoding="utf-8", errors="replace")
        return r.stdout if r.returncode == 0 else None
    except FileNotFoundError:
        print(YELLOW("  ⚠ pdftotext not found.")); return None
    except Exception:
        return None

def is_text_extraction_valid(text, min_chars=500):
    return bool(text) and len(re.findall(r"[a-zA-Z0-9]", text)) >= min_chars

# ── Surya OCR ─────────────────────────────────────────────

_surya_models = {}

def _load_surya_models():
    global _surya_models
    if _surya_models:
        return _surya_models
    if not HAS_SURYA:
        raise RuntimeError("Surya OCR is not installed")
    print("  🤖 Loading Surya OCR models…")
    _surya_models["det"] = DetectionPredictor()  # type: ignore[misc]
    _surya_models["rec"] = RecognitionPredictor()  # type: ignore[misc]
    print(f"  {GREEN('✓')} Surya models loaded.")
    return _surya_models

def _run_surya_on_images(images):
    models = _load_surya_models()
    det = models["det"]
    rec = models["rec"]
    return rec(images, [["en"]] * len(images), det)

def _surya_result_to_text(page_result):
    lines = page_result.text_lines
    def _top(l):
        if hasattr(l, "bbox") and l.bbox:
            b = l.bbox; return b[1] if isinstance(b, (list, tuple)) else float("inf")
        if hasattr(l, "polygon") and l.polygon:
            return min(pt[1] for pt in l.polygon)
        return float("inf")
    def _left(l):
        if hasattr(l, "bbox") and l.bbox:
            b = l.bbox; return b[0] if isinstance(b, (list, tuple)) else float("inf")
        if hasattr(l, "polygon") and l.polygon:
            return min(pt[0] for pt in l.polygon)
        return float("inf")
    return "\n".join(ln.text for ln in sorted(lines, key=lambda l: (_top(l), _left(l))) if ln.text.strip())

def extract_pdf_text_surya(pdf_path, dpi=200):
    if not HAS_SURYA:
        print(RED("  ✗ Surya OCR not installed.")); return None
    if not HAS_PDF2IMAGE:
        print(RED("  ✗ pdf2image not installed.")); return None
    try:
        print(f"  📸 Converting PDF to images (DPI={dpi})…")
        images = convert_from_path(pdf_path, dpi=dpi)
        total  = len(images)
        print(f"  🔬 Running Surya OCR on {total} page(s)…")
        results = _run_surya_on_images(images)
        full_text = []
        for i, pr in enumerate(results, 1):
            print(f"     ✓ Page {i}/{total}", end="\r")
            full_text.append(_surya_result_to_text(pr))
        print(f"  {GREEN('✓')} Surya OCR complete.     ")
        return "\n\n".join(full_text)
    except Exception as exc:
        print(RED(f"  ✗ Surya OCR failed: {exc}")); return None

def extract_pdf_text(pdf_path, force_ocr=False):
    """Legacy text extractor used only when pdfplumber is unavailable."""
    print(f"\n  📄 Extracting PDF: {Path(pdf_path).name}")
    if force_ocr:
        print("  🔧 OCR mode forced")
        text = extract_pdf_text_surya(pdf_path)
        if not text: raise RuntimeError("Surya OCR failed.")
        return text
    print("  🔍 Trying digital extraction (pdftotext)…")
    text = extract_pdf_text_digital(pdf_path)
    if is_text_extraction_valid(text):
        print(f"  {GREEN('✓')} Digital extraction successful.")
        return text
    print(YELLOW("  ⚠ Low text yield — switching to Surya OCR…"))
    text = extract_pdf_text_surya(pdf_path)
    if not text:
        raise RuntimeError("Both pdftotext and Surya OCR failed.")
    return text

# ── Amount cleaning ───────────────────────────────────────

def clean_amount(raw):
    cleaned = re.sub(r"[^\d]", "", raw)
    return int(cleaned) if cleaned else None

# ══════════════════════════════════════════════════════════════════════
#  LEGACY: pdftotext-based record parser  (kept as fallback)
# ══════════════════════════════════════════════════════════════════════

_TRAILING_JUNK = re.compile(
    r"\s+(?:[A-Z0-9]{1,3}|Page\s+\d+|\d{1,3})\s*$", re.IGNORECASE
)

def parse_pdf_records(pdf_text):
    """
    Legacy parser — used only when pdfplumber is unavailable.
    Parses the raw text produced by pdftotext -layout.
    """
    PAN_RE    = re.compile(r"\b([A-Z]{3}[A-Z0-9]{5,9})\b")
    AMOUNT_RE = re.compile(
        r"\b(\d{1,3})\s{2,}(\d{2}/\d{2}/\d{0,4})\s+([\d,.]+(?:[,.]\d+)*)\s+([\d,.]+)"
    )
    AMOUNT_PARTIAL_RE = re.compile(
        r"\b(\d{1,3})\s{2,}(\d{2}/\d{2}/\d{0,4})\s+([\d,.]+(?:[,.]\d+)*)\s*$"
    )
    IT_DISPLACED_RE = re.compile(r"^\s*([\d,.]+)(?:\s|$)")
    SR_NAME_RE   = re.compile(
        r"^\s*(\d{1,3})\s{2,}([A-Z][A-Za-z0-9\s\.,&'\-\/\(\)]+?)\s*$"
    )
    NAME_CONT_RE = re.compile(
        r"^\s{3,}([A-Z][A-Za-z0-9\s\.,&'\-\/\(\)]{3,}?)\s*$"
    )

    lines   = pdf_text.splitlines()
    records = {}

    for i, line in enumerate(lines):
        amt_match = AMOUNT_RE.search(line)
        if not amt_match:
            continue

        pan_line = line if PAN_RE.search(line) else ""
        if not pan_line:
            for _delta in range(1, 4):
                for _sign in (1, -1):
                    _idx = i + _sign * _delta
                    if 0 <= _idx < len(lines) and PAN_RE.search(lines[_idx]):
                        pan_line = lines[_idx]
                        break
                if pan_line:
                    break
        if not pan_line:
            continue

        voucher   = amt_match.group(1)
        gross_raw = amt_match.group(3)
        it_raw    = amt_match.group(4)

        _parts = gross_raw.split(",")
        if len(_parts) > 1 and 0 < len(_parts[-1]) < 3:
            _need = 3 - len(_parts[-1])
            _pat  = re.compile(r"(\d{" + str(_need) + r"})\s*$")
            for _la in range(1, 4):
                if i + _la >= len(lines):
                    break
                _m = _pat.search(lines[i + _la])
                if _m:
                    gross_raw += _m.group(1)
                    break

        gross_amt  = clean_amount(gross_raw)
        income_tax = clean_amount(it_raw)

        name_parts = []
        for delta in range(-3, 5):
            idx = i + delta
            if not (0 <= idx < len(lines)):
                continue
            m = SR_NAME_RE.match(lines[idx])
            if m:
                name_parts.append(m.group(2).strip())
                if idx + 1 < len(lines):
                    cont = NAME_CONT_RE.match(lines[idx + 1])
                    if cont:
                        name_parts.append(cont.group(1).strip())
                break

        if not name_parts:
            _pan_src   = pan_line if pan_line else line
            pan_match  = PAN_RE.search(_pan_src)
            if pan_match:
                pan_start  = pan_match.start()
                before_pan = _pan_src[:pan_start].strip()
                before_pan = _TRAILING_JUNK.sub("", before_pan).strip()
                if len(before_pan) > 3:
                    name_parts.append(before_pan)

        records[voucher] = {
            "nameOfAgency": " ".join(name_parts).strip(),
            "grossAmt":     gross_amt,
            "incomeTax":    income_tax,
        }

    # Second pass for partial / displaced-IT lines
    for i, line in enumerate(lines):
        m = AMOUNT_PARTIAL_RE.search(line)
        if not m: continue
        if AMOUNT_RE.search(line): continue

        voucher = m.group(1)
        if voucher in records: continue

        pan_line = line if PAN_RE.search(line) else ""
        if not pan_line:
            for _delta in range(1, 4):
                for _sign in (1, -1):
                    _idx = i + _sign * _delta
                    if 0 <= _idx < len(lines) and PAN_RE.search(lines[_idx]):
                        pan_line = lines[_idx]; break
                if pan_line: break
        if not pan_line: continue

        gross_raw = m.group(3)

        income_tax = None
        for _la in range(1, 4):
            _prev = lines[i - _la] if i - _la >= 0 else ""
            _im   = IT_DISPLACED_RE.match(_prev)
            if _im:
                candidate = clean_amount(_im.group(1))
                if candidate and candidate > 0:
                    income_tax = candidate
                    break

        _parts = gross_raw.split(",")
        if len(_parts) > 1 and 0 < len(_parts[-1]) < 3:
            _need = 3 - len(_parts[-1])
            _pat  = re.compile(r"(\d{" + str(_need) + r"})\s*$")
            for _la in range(1, 4):
                if i + _la >= len(lines): break
                _mm = _pat.search(lines[i + _la])
                if _mm: gross_raw += _mm.group(1); break

        gross_amt = clean_amount(gross_raw)

        name_parts = []
        for delta in range(-3, 5):
            idx = i + delta
            if not (0 <= idx < len(lines)): continue
            nm = SR_NAME_RE.match(lines[idx])
            if nm:
                name_parts.append(nm.group(2).strip())
                if idx + 1 < len(lines):
                    cont = NAME_CONT_RE.match(lines[idx + 1])
                    if cont: name_parts.append(cont.group(1).strip())
                break
        if not name_parts:
            _pan_src   = pan_line
            pan_match  = PAN_RE.search(_pan_src)
            if pan_match:
                pan_start  = pan_match.start()
                before_pan = _pan_src[:pan_start].strip()
                before_pan = _TRAILING_JUNK.sub("", before_pan).strip()
                if len(before_pan) > 3: name_parts.append(before_pan)

        records[voucher] = {
            "nameOfAgency": " ".join(name_parts).strip(),
            "grossAmt":     gross_amt,
            "incomeTax":    income_tax,
        }

    return records

# ── JSON loading ──────────────────────────────────────────

def load_json(json_path):
    with open(json_path, encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "records", "rows", "entries"):
            if key in payload and isinstance(payload[key], list):
                return payload[key]
    raise ValueError(
        f"Cannot locate record list in JSON. "
        f"Keys: {list(payload.keys()) if isinstance(payload, dict) else type(payload)}"
    )

# ── Name matching ─────────────────────────────────────────

def names_match(json_name, pdf_name):
    if not json_name or not pdf_name:
        return False
    def _norm(s):
        return re.sub(r"\s+", " ", s).strip().lower()
    j = _norm(json_name)
    p = _norm(pdf_name)
    if j in p or p in j:
        return True
    j_words = set(re.findall(r"[a-z0-9]+", j))
    p_words = set(re.findall(r"[a-z0-9]+", p))
    stopwords = {"ltd", "mss", "the", "and", "pvt", "of"}
    j_words -= stopwords
    p_words -= stopwords
    if not j_words or not p_words:
        return j in p or p in j
    shorter = j_words if len(j_words) <= len(p_words) else p_words
    overlap = len(shorter & (j_words | p_words))
    return overlap / len(shorter) >= 0.60

# ── Standalone integrity checks ───────────────────────────

SUSPICIOUS_NAMES = {"FORM 93", "FORM93", "PAGE", "REPORT", "SCHEDULE"}
MIN_TDS_RATE     = 0.005
MAX_TDS_RATE     = 0.15

def standalone_checks(json_records):
    issues = []
    seen_vouchers = {}
    for idx, rec in enumerate(json_records):
        row_issues = []
        voucher = str(rec.get("voucher",        "")).strip()
        name    = str(rec.get("nameOfAgency", "")).strip()
        gross_s = str(rec.get("grossAmt",       "")).strip()
        it_s    = str(rec.get("incomeTax",      "")).strip()

        for fld in ("voucher", "nameOfAgency", "grossAmt", "incomeTax"):
            if not str(rec.get(fld, "")).strip():
                row_issues.append({"rule": "MISSING_FIELD", "field": fld,
                                   "json": "", "severity": "ERROR",
                                   "detail": f"Field '{fld}' is empty or missing"})

        gross = it = None
        try: gross = int(gross_s)
        except ValueError:
            row_issues.append({"rule": "NON_NUMERIC", "field": "grossAmt",
                               "json": gross_s, "severity": "ERROR",
                               "detail": "grossAmt is not a valid integer"})
        try: it = int(it_s)
        except ValueError:
            row_issues.append({"rule": "NON_NUMERIC", "field": "incomeTax",
                               "json": it_s, "severity": "ERROR",
                               "detail": "incomeTax is not a valid integer"})

        if name and name.upper() in SUSPICIOUS_NAMES:
            row_issues.append({"rule": "SUSPICIOUS_NAME", "field": "nameOfAgency",
                               "json": name, "severity": "CRITICAL",
                               "detail": f"Name '{name}' looks like a header artefact"})

        if gross is not None and it is not None:
            if gross == 0:
                row_issues.append({"rule": "ZERO_AMOUNT", "field": "grossAmt",
                                   "json": gross_s, "severity": "WARNING",
                                   "detail": "grossAmt is zero"})
            if it == 0 and gross > 0:
                row_issues.append({"rule": "ZERO_INCOME_TAX", "field": "incomeTax",
                                   "json": it_s, "severity": "WARNING",
                                   "detail": "incomeTax is zero while grossAmt is non-zero"})
            if gross > 0 and it > gross:
                row_issues.append({"rule": "POSSIBLE_SWAP",
                                   "field": "grossAmt / incomeTax",
                                   "json": f"grossAmt={gross:,}  incomeTax={it:,}",
                                   "severity": "CRITICAL",
                                   "detail": "incomeTax exceeds grossAmt — values may be swapped"})
            if gross > 0 and it >= 0:
                rate = it / gross
                if rate < MIN_TDS_RATE or rate > MAX_TDS_RATE:
                    row_issues.append({"rule": "TDS_RATE_ANOMALY", "field": "incomeTax",
                                       "json": f"rate={rate:.4%}", "severity": "WARNING",
                                       "detail": (f"TDS rate {rate:.4%} outside "
                                                  f"[{MIN_TDS_RATE:.1%}–{MAX_TDS_RATE:.1%}]")})

        if voucher:
            if voucher in seen_vouchers:
                row_issues.append({"rule": "DUPLICATE_VOUCHER", "field": "voucher",
                                   "json": voucher, "severity": "ERROR",
                                   "detail": f"Voucher {voucher} already seen at record #{seen_vouchers[voucher]+1}"})
            else:
                seen_vouchers[voucher] = idx

        if row_issues:
            issues.append({"record_index": idx+1, "voucher": voucher,
                           "name": name, "issues": row_issues})
    return issues

# ── PDF vs JSON comparison ────────────────────────────────

def compare(pdf_records, json_records):
    def _int_key(x): return int(x) if str(x).isdigit() else 0

    pdf_vouchers    = set(pdf_records.keys())
    json_by_voucher = {str(r.get("voucher","")).strip(): r for r in json_records}
    json_vouchers   = set(json_by_voucher.keys())

    missing_in_json = sorted(pdf_vouchers - json_vouchers,  key=_int_key)
    extra_in_json   = sorted(json_vouchers - pdf_vouchers,  key=_int_key)
    matched = []; mismatched = []

    for voucher in sorted(pdf_vouchers & json_vouchers, key=_int_key):
        pdf = pdf_records[voucher]
        jsn = json_by_voucher[voucher]
        row_issues = []

        j_gross = int(jsn.get("grossAmt", 0) or 0)
        p_gross = pdf["grossAmt"]
        if p_gross is not None and j_gross != p_gross:
            row_issues.append({"field": "grossAmt",
                               "json": f"{j_gross:,}", "pdf": f"{p_gross:,}",
                               "severity": "CRITICAL" if abs(j_gross-p_gross)>1_000 else "WARNING"})

        j_it = int(jsn.get("incomeTax", 0) or 0)
        p_it = pdf["incomeTax"]
        if p_it is not None and j_it != p_it:
            row_issues.append({"field": "incomeTax",
                               "json": f"{j_it:,}", "pdf": f"{p_it:,}",
                               "severity": "CRITICAL" if abs(j_it-p_it)>500 else "WARNING"})

        j_name = str(jsn.get("nameOfAgency","")).strip()
        p_name = pdf["nameOfAgency"]
        if p_name and not names_match(j_name, p_name):
            row_issues.append({"field": "nameOfAgency",
                               "json": j_name, "pdf": p_name,
                               "severity": "CRITICAL" if j_name.upper() in SUSPICIOUS_NAMES else "WARNING"})

        if row_issues:
            mismatched.append({"voucher": voucher, "pdf_name": pdf["nameOfAgency"], "issues": row_issues})
        else:
            matched.append({"voucher": voucher})

    return {"matched": matched, "mismatched": mismatched,
            "missing_in_json": missing_in_json, "extra_in_json": extra_in_json}

# ── Reporting ─────────────────────────────────────────────

def _sev_icon(sev):
    return {"CRITICAL":"🔴","ERROR":"🟠","WARNING":"🟡"}.get(sev,"⚪")

def print_report(json_path, pdf_path, json_records, integrity_issues, comparison, ocr_engine="pdfplumber"):
    now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(json_records)
    print(); print(BOLD("="*68))
    print(BOLD("  FORM 93 VALIDATION REPORT  (Surya OCR Edition — fixed)"))
    print(BOLD("="*68))
    print(f"  Generated   : {now}")
    print(f"  JSON file   : {json_path}")
    if pdf_path: print(f"  PDF file    : {pdf_path}"); print(f"  OCR engine  : {ocr_engine}")
    print(f"  Records     : {total}")
    print(BOLD("="*68))

    _empty_name  = sum(1 for r in json_records if not str(r.get("nameOfAgency","")).strip())
    _empty_gross = sum(1 for r in json_records if not str(r.get("grossAmt","")).strip())
    _empty_it    = sum(1 for r in json_records if not str(r.get("incomeTax","")).strip())
    if _empty_name or _empty_gross or _empty_it:
        print(); print(BOLD("━━  ⚠  DATA QUALITY SUMMARY  ━━"))
        if _empty_name:  print(RED(f"  nameOfAgency  empty : {_empty_name:>4} / {total}  ({_empty_name/total*100:.0f}%)"))
        if _empty_gross: print(RED(f"  grossAmt      empty : {_empty_gross:>4} / {total}  ({_empty_gross/total*100:.0f}%)"))
        if _empty_it:    print(RED(f"  incomeTax     empty : {_empty_it:>4} / {total}  ({_empty_it/total*100:.0f}%)"))
        print(); print(YELLOW("  ► Fix the source JSON before re-running."))

    print(); print(BOLD("━━  1. JSON INTEGRITY CHECKS  ━━"))
    if not integrity_issues:
        print(GREEN(f"  ✅  All {total} records passed integrity checks."))
    else:
        crit = sum(1 for r in integrity_issues for i in r["issues"] if i["severity"]=="CRITICAL")
        err  = sum(1 for r in integrity_issues for i in r["issues"] if i["severity"]=="ERROR")
        warn = sum(1 for r in integrity_issues for i in r["issues"] if i["severity"]=="WARNING")
        print(f"  Records with issues : {RED(str(len(integrity_issues)))} of {total}")
        print(f"  {_sev_icon('CRITICAL')} Critical:{crit}  {_sev_icon('ERROR')} Error:{err}  {_sev_icon('WARNING')} Warning:{warn}")
        print()
        rows = [[_sev_icon(i["severity"])+" "+i["severity"], rec["voucher"] or "—",
                 rec["name"][:30] or "—", i["rule"], i["field"],
                 str(i.get("json",""))[:40], i["detail"][:60]]
                for rec in integrity_issues for i in rec["issues"]]
        headers = ["Sev","Voucher","Name","Rule","Field","JSON Value","Detail"]
        if HAS_TABULATE: print(tabulate(rows, headers=headers, tablefmt="simple"))
        else:
            print("  "+"  |  ".join(headers))
            for r in rows: print("  "+"  |  ".join(str(c) for c in r))

    if comparison is None:
        print(); print(DIM("  (PDF comparison skipped)"))
    else:
        print(); print(BOLD("━━  2. PDF vs JSON COMPARISON  ━━"))
        matched    = comparison["matched"]
        mismatched = comparison["mismatched"]
        miss_json  = comparison["missing_in_json"]
        extra_json = comparison["extra_in_json"]
        pdf_total  = len(matched)+len(mismatched)+len(miss_json)
        print(f"  PDF records extracted  : {pdf_total}")
        print(f"  {GREEN('✅  Match')}          : {len(matched)}")
        print(f"  {RED('❌  Mismatch')}       : {len(mismatched)}")
        print(f"  {YELLOW('⚠   Missing in JSON')} : {len(miss_json)}")
        print(f"  {YELLOW('⚠   Extra in JSON')}   : {len(extra_json)}")
        if miss_json:  print(); print(YELLOW("  Vouchers in PDF but missing in JSON:")); print(f"    {', '.join(miss_json)}")
        if extra_json: print(); print(YELLOW("  Vouchers in JSON but not found in PDF:")); print(f"    {', '.join(extra_json)}")
        if mismatched:
            print(); print(RED(f"  Mismatched records ({len(mismatched)}):"))
            for rec in mismatched:
                print(); print(f"  {BOLD('Voucher '+rec['voucher'])} — {rec['pdf_name']}")
                rows = [[_sev_icon(i["severity"]), i["field"],
                         str(i.get("json",""))[:45], str(i.get("pdf",""))[:45]]
                        for i in rec["issues"]]
                headers2 = ["","Field","JSON","PDF (expected)"]
                if HAS_TABULATE:
                    print(tabulate(rows, headers=headers2, tablefmt="simple",
                                   colalign=("center","left","left","left")))
                else:
                    for r in rows: print(f"    {r[0]}  {r[1]:<30}  JSON: {r[2]}  →  PDF: {r[3]}")

    print(); print(BOLD("="*68)); print(BOLD("  END OF REPORT")); print(BOLD("="*68)); print()

def save_csv(output_path, json_records, integrity_issues, comparison):
    base   = Path(output_path)
    stem   = base.with_suffix("")
    full_path   = Path(str(stem) + "_full_comparison.csv")
    issues_path = Path(str(stem) + "_issues_log.csv")

    def _int_key(x):
        return int(x) if str(x).isdigit() else 0

    pdf_by_voucher      = {}
    mismatch_by_voucher = {}
    matched_vouchers    = set()

    if comparison:
        for rec in comparison["matched"]:
            matched_vouchers.add(rec["voucher"])
        for rec in comparison["mismatched"]:
            mismatch_by_voucher[rec["voucher"]] = rec["issues"]
            pdf_by_voucher[rec["voucher"]] = {
                "pdf_name": rec["pdf_name"],
                **{iss["field"]: iss.get("pdf","") for iss in rec["issues"]
                   if iss["field"] in ("grossAmt","incomeTax","nameOfAgency")},
            }

    integrity_by_voucher = {}
    for rec in integrity_issues:
        integrity_by_voucher[rec["voucher"]] = rec["issues"]

    with open(full_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow([
            "Voucher",
            "JSON_AgencyName", "JSON_GrossAmt", "JSON_IncomeTax",
            "PDF_AgencyName",  "PDF_GrossAmt",  "PDF_IncomeTax",
            "PDF_Match_Status",
            "Integrity_Status",
            "Issues_Summary",
        ])

        for rec in sorted(json_records, key=lambda r: _int_key(r.get("voucher",""))):
            voucher   = str(rec.get("voucher","")).strip()
            j_name    = str(rec.get("nameOfAgency","")).strip()
            j_gross   = str(rec.get("grossAmt","")).strip()
            j_it      = str(rec.get("incomeTax","")).strip()

            if comparison is None:
                pdf_status = "PDF_NOT_RUN"
                p_name = p_gross = p_it = ""
            elif voucher in matched_vouchers:
                pdf_status = "MATCH"
                p_name  = j_name
                p_gross = j_gross
                p_it    = j_it
            elif voucher in mismatch_by_voucher:
                pdf_status = "MISMATCH"
                issues = mismatch_by_voucher[voucher]
                def _pdf_val(field, json_val):
                    for iss in issues:
                        if iss["field"] == field:
                            return iss.get("pdf", "")
                    return json_val
                p_name  = _pdf_val("nameOfAgency", j_name)
                p_gross = _pdf_val("grossAmt",      j_gross)
                p_it    = _pdf_val("incomeTax",     j_it)
            elif voucher in (comparison.get("missing_in_json") or []):
                pdf_status = "IN_PDF_ONLY"
                p_name = p_gross = p_it = ""
            else:
                pdf_status = "NOT_IN_PDF"
                p_name = p_gross = p_it = ""

            int_issues = integrity_by_voucher.get(voucher, [])
            int_status = "OK" if not int_issues else (
                "CRITICAL" if any(i["severity"]=="CRITICAL" for i in int_issues) else
                "ERROR"    if any(i["severity"]=="ERROR"    for i in int_issues) else
                "WARNING"
            )

            all_issues = []
            for iss in int_issues:
                all_issues.append(f"[INTEGRITY/{iss['severity']}] {iss['rule']}: {iss['detail']}")
            if voucher in mismatch_by_voucher:
                for iss in mismatch_by_voucher[voucher]:
                    all_issues.append(
                        f"[PDF/{iss['severity']}] {iss['field']}: "
                        f"JSON={iss.get('json','')} PDF={iss.get('pdf','')}"
                    )
            issues_summary = " | ".join(all_issues) if all_issues else ""

            w.writerow([
                voucher,
                j_name, j_gross, j_it,
                p_name, p_gross, p_it,
                pdf_status, int_status,
                issues_summary,
            ])

    with open(issues_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["Voucher","AgencyName","CheckType","Severity",
                    "Rule/Field","JSON_Value","PDF_Value","Detail"])
        for rec in integrity_issues:
            for iss in rec["issues"]:
                w.writerow([rec["voucher"], rec["name"], "INTEGRITY", iss["severity"],
                            iss["rule"]+"/"+iss["field"], iss.get("json",""), "", iss["detail"]])
        if comparison:
            for rec in comparison["mismatched"]:
                for iss in rec["issues"]:
                    w.writerow([rec["voucher"], rec["pdf_name"], "PDF_COMPARISON", iss["severity"],
                                iss["field"], iss.get("json",""), iss.get("pdf",""), ""])
            for v in comparison["missing_in_json"]:
                w.writerow([v,"","PDF_COMPARISON","ERROR","MISSING_IN_JSON","","",
                            "Voucher in PDF but absent from JSON"])
            for v in comparison["extra_in_json"]:
                w.writerow([v,"","PDF_COMPARISON","WARNING","EXTRA_IN_JSON","","",
                            "Voucher in JSON but not found in PDF"])

    print(f"  📄  Full comparison CSV → {full_path}")
    print(f"  📄  Issues log CSV      → {issues_path}")

# ── CLI ───────────────────────────────────────────────────

def build_arg_parser():
    p = argparse.ArgumentParser(description="Validate Form 93 JSON against its source PDF.")
    p.add_argument("--pdf",  metavar="PATH")
    p.add_argument("--json", metavar="PATH", required=True)
    p.add_argument("--csv",  metavar="PATH")
    p.add_argument("--force-ocr", action="store_true",
                   help="Skip pdfplumber and force Surya OCR")
    p.add_argument("--skip-name-check", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--dpi", type=int, default=200)
    return p

def main():
    parser = build_arg_parser(); args = parser.parse_args()
    print(f"\n  Loading JSON  …  {args.json}")
    try:
        json_records = load_json(args.json)
        print(f"  {GREEN('✓')} {len(json_records)} records loaded.")
    except Exception as exc:
        print(RED(f"  ✗ Failed to load JSON: {exc}")); sys.exit(1)

    print("\n🔍  Running JSON integrity checks…")
    integrity_issues = standalone_checks(json_records)

    comparison = None
    ocr_used   = "pdfplumber (text-line parser)"

    if args.pdf:
        print(f"\n  📄 Extracting PDF: {Path(args.pdf).name}")
        try:
            # ── PRIMARY PATH: pdfplumber ──────────────────────────────
            if HAS_PDFPLUMBER and not args.force_ocr:
                pdf_records = extract_pdf_records_pdfplumber(args.pdf)
                ocr_used    = "pdfplumber (text-line parser)"

            # ── FALLBACK: pdftotext → Surya OCR ──────────────────────
            else:
                if not HAS_PDFPLUMBER:
                    print(YELLOW("  ⚠ pdfplumber not installed — falling back to pdftotext/Surya."))
                    print(YELLOW("    Install it with:  pip install pdfplumber"))
                pdf_text    = extract_pdf_text(args.pdf, force_ocr=args.force_ocr)
                digital_ok  = is_text_extraction_valid(extract_pdf_text_digital(args.pdf))
                ocr_used    = "digital (pdftotext)" if (not args.force_ocr and digital_ok) else "Surya OCR"
                pdf_records = parse_pdf_records(pdf_text)

            print(f"  {GREEN('✓')} {len(pdf_records)} PDF records parsed.")
            print("\n🔍  Comparing PDF with JSON…")
            comparison = compare(pdf_records, json_records)

            if args.skip_name_check:
                for rec in comparison["mismatched"]:
                    rec["issues"] = [i for i in rec["issues"] if i["field"] != "nameOfAgency"]
                comparison["mismatched"] = [r for r in comparison["mismatched"] if r["issues"]]

        except Exception as exc:
            print(YELLOW(f"  ⚠ PDF extraction failed: {exc}"))

    print_report(args.json, args.pdf, json_records, integrity_issues, comparison, ocr_used)

    if args.csv:
        save_csv(args.csv, json_records, integrity_issues, comparison)

    has_critical = any(i["severity"] in ("CRITICAL","ERROR")
                       for rec in integrity_issues for i in rec["issues"])
    if comparison:
        has_critical = has_critical or bool(comparison["mismatched"] or comparison["missing_in_json"])
    if args.strict:
        has_critical = has_critical or any(i["severity"]=="WARNING"
                                           for rec in integrity_issues for i in rec["issues"])
    sys.exit(1 if has_critical else 0)

if __name__ == "__main__":
    main()
    