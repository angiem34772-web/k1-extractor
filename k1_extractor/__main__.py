"""Command line:

    python -m k1_extractor samples --opening-basis samples/opening_basis.csv --out output/results.xlsx
"""

import argparse
from pathlib import Path

from .pipeline import run


def main() -> None:
    ap = argparse.ArgumentParser(prog="k1_extractor",
                                 description="Extract K-1 (Form 1065) PDFs into Excel, tie to the 1065, roll basis.")
    ap.add_argument("folder", type=Path, help="a folder of year folders (2024/, 2025/), or one year folder")
    ap.add_argument("--opening-basis", type=Path, help="CSV: partner_id, beginning_basis, suspended_losses")
    ap.add_argument("--out", type=Path, default=Path("output/k1_results.xlsx"))
    args = ap.parse_args()

    result = run(args.folder, args.opening_basis, args.out)
    errors = [e for e in result.exceptions if e.severity == "ERROR"]
    diffs = [c for c in result.controls if c.status == "DIFF"]
    print(f"K-1s extracted: {len(result.records)}  partners: {len(result.master)}")
    print(f"Control totals that don't tie: {len(diffs)}")
    for c in diffs:
        print(f"  {c.year} {c.area} - {c.item}: K-1s {c.per_k1s:,.2f} vs 1065 {c.per_1065:,.2f}")
    print(f"Errors: {len(errors)}")
    for e in errors:
        print(f"  {e.year} {e.partner_id or ''} {e.check}: {e.detail}")
    print(f"Workbook: {args.out}")


if __name__ == "__main__":
    main()
