#!/usr/bin/env python3
"""
============================================================
  Auto-Detecting Form 93 Validator — Surya OCR Edition
============================================================
Drop your PDF and JSON into the ./data/ folder (or the
current directory) and run this script. It finds the files,
runs all validation checks, and prints a full report.

Usage:
  python validate_uploads_surya.py
  python validate_uploads_surya.py --csv report.csv
  python validate_uploads_surya.py --force-ocr
  python validate_uploads_surya.py --strict
  python validate_uploads_surya.py --pdf form.pdf --json data.json
============================================================
"""

import argparse
import sys
from pathlib import Path

import form93_validator_surya as validator


# ─────────────────────────────────────────────────────────────
# FILE AUTO-DETECTION
# ─────────────────────────────────────────────────────────────

def find_uploaded_files() -> tuple[list[Path], list[Path]]:
    """Scan ./data/ (then current dir) for PDF and JSON files."""
    search_dir = Path("./data")
    if not search_dir.exists():
        search_dir = Path(".")

    pdfs  = sorted(search_dir.glob("*.pdf"))
    jsons = sorted(search_dir.glob("*.json"))
    return pdfs, jsons


def _pick_file(files: list[Path], kind: str, required: bool = True) -> Path | None:
    """Interactively select one file from a list, or auto-select if only one."""
    if not files:
        if required:
            print(f"❌  No {kind} files found.  Please add one to the ./data/ folder.")
            sys.exit(1)
        print(f"⚠️   No {kind} files found — skipping {kind} validation.")
        return None

    if len(files) == 1:
        print(f"  ✓ Found {kind}: {files[0].name}")
        return files[0]

    print(f"⚠️   Found {len(files)} {kind} files:")
    for i, p in enumerate(files, 1):
        print(f"     {i}. {p.name}")

    choice = input(f"  Select {kind} number (or Enter to skip): ").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(files):
        return files[int(choice) - 1]

    if required:
        print(f"❌  Invalid selection.")
        sys.exit(1)

    print(f"  Skipping {kind} validation…")
    return None


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-detect and validate Form 93 files (Surya OCR edition).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--pdf",  metavar="PATH", help="Override: specific PDF file path")
    parser.add_argument("--json", metavar="PATH", help="Override: specific JSON file path")
    parser.add_argument("--csv",  metavar="PATH", help="Save issues to a CSV report")
    parser.add_argument("--force-ocr", action="store_true",
                        help="Force Surya OCR even for digital PDFs")
    parser.add_argument("--skip-name-check", action="store_true",
                        help="Skip contractor name comparison")
    parser.add_argument("--strict", action="store_true",
                        help="Treat warnings as errors")
    parser.add_argument("--dpi", type=int, default=200,
                        help="DPI for PDF→image conversion (default: 200)")
    args = parser.parse_args()

    # ── Resolve files ──────────────────────────────────────
    if args.pdf and args.json:
        pdf_file  = Path(args.pdf)
        json_file = Path(args.json)
    else:
        print("\n🔍  Scanning for uploaded files…")
        pdfs, jsons = find_uploaded_files()
        pdf_file  = _pick_file(pdfs,  "PDF",  required=False)
        json_file = _pick_file(jsons, "JSON", required=True)

    # Validate existence
    if json_file and not json_file.exists():
        print(f"❌  JSON file not found: {json_file}")
        sys.exit(1)
    if pdf_file and not pdf_file.exists():
        print(f"❌  PDF file not found: {pdf_file}")
        sys.exit(1)

    if args.force_ocr:
        print("🔧  Force-OCR mode enabled — Surya OCR will be used regardless of PDF type.")

    # ── Load JSON ──────────────────────────────────────────
    print(f"\n📂  Loading JSON: {json_file.name}")
    try:
        json_records = validator.load_json(str(json_file))
        print(f"   {validator.GREEN('✓')} {len(json_records)} records loaded.")
    except Exception as exc:
        print(validator.RED(f"   ✗ Failed to load JSON: {exc}"))
        sys.exit(1)

    # ── Integrity checks ───────────────────────────────────
    print("🔍  Running JSON integrity checks…")
    integrity_issues = validator.standalone_checks(json_records)
    n_issues = sum(len(r["issues"]) for r in integrity_issues)
    if n_issues:
        print(validator.YELLOW(f"   ⚠ {n_issues} integrity issue(s) found."))
    else:
        print(validator.GREEN(f"   ✓ All {len(json_records)} records passed integrity checks."))

    # ── PDF extraction & comparison ────────────────────────
    comparison = None
    ocr_used   = "digital (pdftotext)"

    if pdf_file:
        try:
            # Patch in the user-specified DPI
            _orig = validator.extract_pdf_text_surya
            def _patched(path, dpi=args.dpi):
                return _orig(path, dpi=dpi)
            validator.extract_pdf_text_surya = _patched

            pdf_text = validator.extract_pdf_text(str(pdf_file), force_ocr=args.force_ocr)

            # Determine which engine was actually used for the report header
            digital_ok = validator.is_text_extraction_valid(
                validator.extract_pdf_text_digital(str(pdf_file))
            )
            ocr_used = "digital (pdftotext)" if (not args.force_ocr and digital_ok) else "Surya OCR"

            pdf_records = validator.parse_pdf_records(pdf_text)
            print(f"   {validator.GREEN('✓')} {len(pdf_records)} PDF records parsed.")

            print("🔍  Comparing PDF with JSON…")
            comparison = validator.compare(pdf_records, json_records)

            if args.skip_name_check:
                for rec in comparison["mismatched"]:
                    rec["issues"] = [i for i in rec["issues"] if i["field"] != "contractorName"]
                comparison["mismatched"] = [r for r in comparison["mismatched"] if r["issues"]]

        except Exception as exc:
            print(validator.YELLOW(f"   ⚠ PDF extraction failed: {exc}"))
            print(validator.YELLOW("     Continuing with JSON-only checks."))

    # ── Report ─────────────────────────────────────────────
    validator.print_report(
        str(json_file),
        str(pdf_file) if pdf_file else None,
        json_records,
        integrity_issues,
        comparison,
        ocr_used,
    )

    # ── CSV export ─────────────────────────────────────────
    if args.csv:
        out = args.csv if args.csv.startswith("/") else f"/mnt/user-data/outputs/{args.csv}"
        validator.save_csv(out, json_records, integrity_issues, comparison)
        print(f"\n📊  Report saved → {out}")

    # ── Exit code ──────────────────────────────────────────
    has_critical = any(
        i["severity"] in ("CRITICAL", "ERROR")
        for rec in integrity_issues for i in rec["issues"]
    )
    if comparison:
        has_critical = has_critical or bool(
            comparison["mismatched"] or comparison["missing_in_json"]
        )
    if args.strict:
        has_critical = has_critical or any(
            i["severity"] == "WARNING"
            for rec in integrity_issues for i in rec["issues"]
        )

    if has_critical:
        print("\n❌  Validation FAILED — critical / error issues found.")
        sys.exit(1)
    else:
        print("\n✅  Validation PASSED — no critical issues.")
        sys.exit(0)


if __name__ == "__main__":
    main()
