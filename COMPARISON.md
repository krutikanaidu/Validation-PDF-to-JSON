# OCR Engine Comparison — Form 93 Validator

## Three versions at a glance

| | Original | Tesseract OCR | **Surya OCR** |
|---|---|---|---|
| **Script** | `form93_validator.py` | `form93_validator_ocr.py` | `form93_validator_surya.py` ✨ |
| **Digital PDFs** | ✅ Excellent | ✅ Excellent | ✅ Excellent |
| **Scanned PDFs** | ❌ Cannot read | ✅ Good | ✅ **Better** |
| **Table column alignment** | ✅ Great | ⚠️ Sometimes drifts | ✅ **Preserved via bbox** |
| **Auto-detection** | ❌ | ✅ | ✅ |
| **System OCR dependency** | ❌ (pdftotext only) | ✅ `tesseract-ocr` pkg | ❌ Pure Python |
| **Speed — digital PDF** | ⚡ < 1 s | ⚡ < 1 s | ⚡ < 1 s |
| **Speed — scanned PDF** | ❌ N/A | 🐌 5–30 s/page | 🐌 10–30 s/page (CPU) / ⚡ 2–5 s (GPU) |
| **GPU acceleration** | ❌ | ❌ | ✅ Automatic |
| **Accuracy on govt. forms** | —/— | ★★★ | ★★★★ |

---

## Which version should I use?

### Use **Original** (`form93_validator.py`) if:
- ✅ PDFs are always digital (text-selectable)
- ✅ You want the fewest dependencies
- ✅ Speed is critical and you never deal with scanned docs

### Use **Tesseract OCR** (`form93_validator_ocr.py`) if:
- ✅ You have scanned PDFs but no GPU available
- ✅ You're already set up with Tesseract
- ✅ Python-only install is not feasible

### Use **Surya OCR** (`form93_validator_surya.py`) ← **recommended** if:
- ✅ You have scanned PDFs or mixed batches
- ✅ Table column alignment accuracy matters (amounts / vouchers)
- ✅ You want pure-Python install (no `tesseract` system package)
- ✅ You have a GPU and want fast batch processing
- ✅ You're starting fresh and want the best accuracy

---

## Auto-detection flow (Surya and Tesseract versions)

```
pdftotext extraction
        │
   Enough text?
   ├─ YES ──────────────────────► use digital text  ⚡
   └─ NO  ──────────────────────► OCR fallback
                                      │
                    Tesseract version: pytesseract.image_to_string()
                    Surya version    : Surya transformer model  🤖
```

Both versions accept `--force-ocr` to skip the digital check entirely — useful
when `pdftotext` extracts *some* text but column alignment is poor.

---

## Installation comparison

### Original
```bash
pip install tabulate
sudo apt install poppler-utils
```

### Tesseract OCR
```bash
pip install tabulate pytesseract pdf2image pillow
sudo apt install poppler-utils tesseract-ocr
```

### Surya OCR (recommended)
```bash
pip install -r requirements.txt      # includes surya-ocr
sudo apt install poppler-utils       # still needed for pdftotext + pdf2image
# No tesseract-ocr package required!
```

---

## Command reference (identical interface across all three)

```bash
# Digital / auto-detect
python form93_validator_surya.py --pdf form.pdf --json data.json

# Force OCR
python form93_validator_surya.py --pdf form.pdf --json data.json --force-ocr

# JSON only
python form93_validator_surya.py --json data.json

# With CSV export
python form93_validator_surya.py --pdf form.pdf --json data.json --csv report.csv

# Strict mode
python form93_validator_surya.py --pdf form.pdf --json data.json --strict

# Higher DPI for dense scans (Surya only)
python form93_validator_surya.py --pdf form.pdf --json data.json --dpi 300
```

---

## Performance benchmarks (approximate)

### Digital PDF — 10 pages
| Method | Time |
|--------|------|
| pdftotext (all versions) | ~0.3 s |
| Surya forced OCR (CPU) | ~2–5 min |
| Surya forced OCR (GPU) | ~20–60 s |

### Scanned PDF — 10 pages
| Method | Time |
|--------|------|
| Original | ❌ fails |
| Tesseract auto | ~30–120 s |
| Surya auto (CPU) | ~3–6 min |
| Surya auto (GPU) | ~30–90 s |

> Surya is slower than Tesseract on CPU but significantly **more accurate**,
> especially on multi-column tabular layouts like Form 93.

---

## Migration from Tesseract → Surya

```bash
# 1. Install Surya
pip install surya-ocr pdf2image pillow

# 2. Replace the import in any existing scripts
#    Old: import form93_validator_ocr as validator
#    New: import form93_validator_surya as validator

# 3. Run — the CLI flags are identical
python form93_validator_surya.py --pdf form.pdf --json data.json
```

All flags (`--force-ocr`, `--skip-name-check`, `--strict`, `--csv`) work
identically in both versions.  The only addition in the Surya version is
`--dpi` (default 200).
