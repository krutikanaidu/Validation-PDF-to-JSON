# Form 93 Validator — Surya OCR Edition

Validates Form 93 JSON data against its source PDF with **high-accuracy OCR** powered by
[Surya](https://github.com/VikParuchuri/surya) — a transformer-based OCR engine that
outperforms Tesseract significantly on tabular government documents.

---

## Why Surya instead of Tesseract?

| | Tesseract | Surya |
|---|---|---|
| **Architecture** | Legacy LSTM | Transformer (SegFormer + Donut) |
| **Table accuracy** | Medium | High |
| **Column alignment** | Often breaks | Preserved via bbox sorting |
| **System dependency** | Yes (`tesseract-ocr` package) | No (pure Python + PyTorch) |
| **Indian forms / fonts** | Fair | Good |
| **GPU support** | No | Yes (automatic) |

---

## Files

| File | Purpose |
|------|---------|
| `form93_validator_surya.py` | Core validator — use directly for scripting or CI |
| `validate_uploads_surya.py` | Drop-and-run wrapper — auto-detects files in `./data/` |
| `requirements.txt` | Python dependencies |

---

## Installation

### 1. Python dependencies

```bash
pip install -r requirements.txt
```

This installs:
- `surya-ocr` — OCR engine (downloads ~1–2 GB of model weights on first run)
- `pdf2image` — PDF → PIL image conversion
- `Pillow` — image processing
- `tabulate` — formatted table output in the report

> **First run:** Surya automatically downloads its models to `~/.cache/huggingface/`.
> Subsequent runs reuse the cache — no internet needed.

### 2. System dependency (poppler)

Needed for `pdftotext` (fast digital extraction) and `pdf2image` (OCR path):

```bash
# Ubuntu / Debian
sudo apt install poppler-utils

# macOS
brew install poppler

# Windows — download from:
# https://github.com/oschwartz10612/poppler-windows/releases
# Add the bin/ folder to PATH.
```

### 3. GPU acceleration (optional but recommended for large PDFs)

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
```

Surya detects a CUDA GPU automatically and uses it. CPU works fine for small batches.

---

## Usage

### Quick start — auto-detect files

Place your `form93.pdf` and `data.json` in a `./data/` folder, then:

```bash
python validate_uploads_surya.py
```

The script finds the files, picks the best extraction method, and prints the report.

### Core validator — explicit paths

```bash
# Auto-detect: try digital extraction first, fall back to Surya OCR if needed
python form93_validator_surya.py --pdf form93.pdf --json data.json

# Force Surya OCR (good for complex column layouts even in digital PDFs)
python form93_validator_surya.py --pdf form93.pdf --json data.json --force-ocr

# JSON-only (no PDF required)
python form93_validator_surya.py --json data.json

# Export issues to CSV
python form93_validator_surya.py --pdf form93.pdf --json data.json --csv report.csv

# Higher DPI for small / dense text (default 200)
python form93_validator_surya.py --pdf form93.pdf --json data.json --dpi 300

# Skip name check + strict mode
python form93_validator_surya.py --pdf form93.pdf --json data.json \
    --skip-name-check --strict
```

---

## Validation checks

### JSON integrity (always runs)

| Rule | Severity |
|------|----------|
| Missing / empty required field | ERROR |
| Non-numeric `grossAmt` or `incomeTax` | ERROR |
| Duplicate voucher number | ERROR |
| Suspicious contractor name (e.g. `FORM 93`) | CRITICAL |
| `incomeTax` > `grossAmt` (possible swap) | CRITICAL |
| `grossAmt == 0` | WARNING |
| `incomeTax == 0` while gross is non-zero | WARNING |
| TDS rate outside \[0.5 %, 15 %\] | WARNING |

### PDF vs JSON comparison (when `--pdf` is supplied)

| Check | Severity |
|-------|----------|
| Gross amount mismatch (> ₹1,000 difference) | CRITICAL |
| Gross amount mismatch (≤ ₹1,000 difference) | WARNING |
| Income tax mismatch (> ₹500 difference) | CRITICAL |
| Income tax mismatch (≤ ₹500 difference) | WARNING |
| Contractor name mismatch | WARNING / CRITICAL |
| Voucher in PDF but missing from JSON | ERROR |
| Voucher in JSON but not found in PDF | WARNING |

---

## How extraction works

```
PDF supplied?
│
├─ --force-ocr flag?
│   └─ YES → Surya OCR  🤖
│
└─ NO → try pdftotext  ⚡
         │
         ├─ Sufficient text? → Done ✓
         │
         └─ Low yield (scanned PDF?) → Surya OCR  🤖
```

The `--force-ocr` flag is useful when `pdftotext` extracts text but column
alignment is poor (common with complex multi-column government layouts).

---

## Output example

```
════════════════════════════════════════════════════════════════════
  FORM 93 VALIDATION REPORT  (Surya OCR Edition)
════════════════════════════════════════════════════════════════════
  Generated   : 2026-05-13 10:45:00
  JSON file   : data.json
  PDF file    : form93.pdf
  OCR engine  : Surya OCR
  Records     : 150
════════════════════════════════════════════════════════════════════

━━  1. JSON INTEGRITY CHECKS  ━━
  ✅  All 150 records passed integrity checks.

━━  2. PDF vs JSON COMPARISON  ━━
  PDF records extracted  : 150
  ✅  Match          : 147
  ❌  Mismatch       : 2
  ⚠   Missing in JSON : 1
  ⚠   Extra in JSON   : 0

  Mismatched records (2):

  Voucher 4821 — ABC CONTRACTORS
   Field         JSON         PDF (expected)
  ─────────────────────────────────────────────
  🔴 grossAmt    1,023,000    1,033,000

════════════════════════════════════════════════════════════════════
  END OF REPORT
════════════════════════════════════════════════════════════════════

✅  Validation PASSED — no critical issues.
```

---

## Troubleshooting

### `surya-ocr` install fails
Make sure you have Python ≥ 3.10 and a recent `pip`:
```bash
pip install --upgrade pip
pip install surya-ocr
```

### Models not downloading
Surya downloads from Hugging Face Hub. If your machine is behind a proxy:
```bash
export HF_HUB_OFFLINE=0
export HTTPS_PROXY=http://your.proxy:port
pip install surya-ocr
```

### OCR accuracy is poor on a scanned PDF
Try a higher DPI (trade speed for quality):
```bash
python form93_validator_surya.py --pdf form.pdf --json data.json --force-ocr --dpi 300
```

### `pdf2image` fails — "Unable to get page count"
```bash
sudo apt install poppler-utils    # Ubuntu/Debian
brew install poppler              # macOS
```

### Very slow on CPU
Surya runs significantly faster on a GPU.  On CPU, expect ~10–30 s/page.
For a 5-page PDF on CPU that is roughly 1–2 minutes — acceptable for
one-off validation but consider a GPU machine for batch processing.

---

## License

Part of the Form 93 processing toolkit.
