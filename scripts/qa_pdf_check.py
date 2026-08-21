#!/usr/bin/env python3
"""qa_pdf_check.py - deterministic PDF QA (no model-side visual inspection).

Per lesson_visual-qa-task-routing_20260723: frontier-model sessions never open,
render, page through, or visually read PDFs. This script answers the two
questions that matter without any rendering:

  1. Page count == expected?         (--pages N)
  2. Selectable text layer present?  (--min-chars, default 200)

Usage:
  python qa_pdf_check.py FILE.pdf [--pages N] [--min-chars 200]

Exit codes: 0 = PASS, 1 = FAIL, 2 = usage/read error.
Output is one PASS/FAIL verdict line plus one line per check (ASCII only,
Windows-safe). Visual QA (clipping, overflow, table drift, fonts) is NOT this
script's job - route it to Max's eyeball or a Sonnet-class subagent per the
lesson.
"""
import argparse
import sys


def main():
    ap = argparse.ArgumentParser(description="Deterministic PDF QA: page count + text-layer probe.")
    ap.add_argument("pdf", help="path to the PDF file")
    ap.add_argument("--pages", type=int, default=None, help="expected exact page count")
    ap.add_argument("--min-chars", type=int, default=200,
                    help="minimum extractable characters for text-layer PASS (default 200)")
    args = ap.parse_args()

    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # older environments
        except ImportError:
            print("FAIL: neither pypdf nor PyPDF2 installed (pip install pypdf)")
            sys.exit(2)

    try:
        reader = PdfReader(args.pdf)
    except Exception as exc:
        print(f"FAIL: cannot read {args.pdf}: {exc}")
        sys.exit(2)

    n_pages = len(reader.pages)
    chars = 0
    for page in reader.pages:
        try:
            chars += len((page.extract_text() or "").strip())
        except Exception:
            pass  # a page that fails extraction contributes 0 chars

    ok = True
    lines = []

    if args.pages is not None:
        good = n_pages == args.pages
        ok = ok and good
        lines.append(f"pages: {n_pages} (expected {args.pages}) -> {'PASS' if good else 'FAIL'}")
    else:
        lines.append(f"pages: {n_pages} (no expectation given)")

    good = chars >= args.min_chars
    ok = ok and good
    lines.append(f"text-layer: {chars} chars extractable (min {args.min_chars}) -> {'PASS' if good else 'FAIL'}")

    print(f"{'PASS' if ok else 'FAIL'}: {args.pdf}")
    for line in lines:
        print("  " + line)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
