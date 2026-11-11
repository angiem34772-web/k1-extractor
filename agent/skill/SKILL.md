---
name: k1-extractor
description: Extract partnership Schedule K-1 (Form 1065) PDFs into an Excel results workbook, tie them to the Form 1065, and roll partner outside basis forward. Use when the user attaches K-1 PDFs or asks to process a year of K-1s.
---

# K-1 extractor

Run the tested Python tool. Do not write your own extraction or basis code;
the point of this skill is that every run gives the same, tested result.

## Inputs

- A folder named for the tax year (for example `2025/`) holding the K-1 PDFs.
  A combined package PDF is fine; cover pages are skipped.
- `form1065_totals.json` in the same folder, with the 1065 Schedule K and M-2
  totals, so the K-1s can be tied out.
- Optionally, `opening_basis.csv` (partner_id, beginning_basis,
  suspended_losses) for the first year.

## Run

```
python -m k1_extractor <folder> --opening-basis opening_basis.csv --out k1_results.xlsx
```

Requires `pdfplumber` and `openpyxl`. If `pdfplumber` is missing, stop and say
so; don't fall back to another PDF reader.

## After the run

1. Report the printed counts: K-1s extracted, partners, items that don't tie,
   and errors.
2. If anything doesn't tie or there are errors, list them before anything else.
3. Give the user `k1_results.xlsx`.
4. Pages with no text layer are scans. Ask the user to OCR them (for example
   Acrobat "Recognize text") and run again.
