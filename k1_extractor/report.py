"""Write the results workbook."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(bold=True, color="FFFFFF")
STATUS_FILLS = {
    "OK": PatternFill("solid", fgColor="C6EFCE"),
    "DIFF": PatternFill("solid", fgColor="FFC7CE"),
    "ERROR": PatternFill("solid", fgColor="FFC7CE"),
    "WARN": PatternFill("solid", fgColor="FFEB9C"),
}

DETAIL_COLUMNS = [
    "tax_year", "master_partner_no", "partner_id", "partner_name", "entity_type", "partnership_name",
    "partnership_ein", "final_k1", "amended_k1",
    "profit_pct_begin", "profit_pct_end", "loss_pct_begin", "loss_pct_end", "capital_pct_begin", "capital_pct_end",
    "liab_nonrecourse_begin", "liab_nonrecourse_end", "liab_qnrf_begin", "liab_qnrf_end",
    "liab_recourse_begin", "liab_recourse_end", "liab_total_begin", "liab_total_end",
    "cap_begin", "cap_contributed", "cap_net_income", "cap_other", "cap_withdrawals", "cap_end",
    "capital_method", "source",
]


def _sheet(wb: Workbook, title: str, headers: list[str], rows: list[list], status_col: str | None = None):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for cell in ws[1]:
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
    for row in rows:
        ws.append(row)
    ws.freeze_panes = "A2"
    for i, h in enumerate(headers, start=1):
        width = max([len(str(h))] + [len(str(r[i - 1])) for r in rows if i - 1 < len(r)] or [10])
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 10), 60)
    for col_cells in ws.iter_cols(min_row=2):
        for c in col_cells:
            if isinstance(c.value, float):
                c.number_format = "#,##0.00;(#,##0.00)" if abs(c.value) < 1000 and c.value % 1 else "#,##0;(#,##0)"
    if status_col and status_col in headers:
        idx = headers.index(status_col) + 1
        for r in range(2, ws.max_row + 1):
            cell = ws.cell(r, idx)
            if cell.value in STATUS_FILLS:
                cell.fill = STATUS_FILLS[cell.value]
    return ws


def write_workbook(path: str | Path, *, files, records, master, exceptions, controls, basis_lines, notes) -> None:
    wb = Workbook()
    wb.remove(wb.active)

    summary = wb.create_sheet("Summary")
    errors = sum(1 for e in exceptions if e.severity == "ERROR")
    warns = sum(1 for e in exceptions if e.severity == "WARN")
    diffs = sum(1 for c in controls if c.status == "DIFF")
    years = sorted({r.get("tax_year") for r in records if r.get("tax_year")})
    for row in [
        ["K-1 extraction results"],
        ["Tax years", ", ".join(map(str, years))],
        ["PDF files read", len(files)],
        ["K-1s extracted", len(records)],
        ["Partners", len(master)],
        ["Control totals that don't tie", diffs],
        ["Errors", errors],
        ["Warnings", warns],
        [],
        ["Review the Exceptions and Control Totals tabs before relying on these numbers."],
    ] + [[n] for n in notes]:
        summary.append(row)
    summary["A1"].font = Font(bold=True, size=14)
    summary.column_dimensions["A"].width = 34
    summary.column_dimensions["B"].width = 18

    _sheet(wb, "File Inventory", ["Year folder", "File", "Pages", "K-1s found", "Pages skipped (before first K-1)",
                                  "Pages with no text (need OCR)"],
           [[f[0], f[1].path, f[1].page_count, f[1].k1_count, _list(f[1].skipped_pages), _list(f[1].no_text_pages)]
            for f in files])

    _sheet(wb, "Partner Master", ["Master partner no.", "Partner ID", "Partner name", "First year"],
           [[m["no"], pid, m["name"], m["first_year"]] for pid, m in master.items()])

    detail_rows = []
    for r in records:
        f = dict(r.fields, master_partner_no=master.get(r.get("partner_id"), {}).get("no"),
                 source=f"{r.source_file} p.{_list(r.pages)}")
        detail_rows.append([f.get(c) for c in DETAIL_COLUMNS])
    _sheet(wb, "K-1 Detail", DETAIL_COLUMNS, detail_rows)

    coded = []
    for r in records:
        for key, amt in r.boxes.items():
            box, _, code = key.partition(":")
            coded.append([r.get("tax_year"), r.get("partner_id"), box, code, amt, r.source_file])
    _sheet(wb, "Box Detail", ["Tax year", "Partner ID", "Box", "Code", "Amount", "Source"], coded)

    _sheet(wb, "Exceptions", ["Tax year", "Partner ID", "Source", "Severity", "Check", "Detail"],
           [[e.year, e.partner_id, e.source, e.severity, e.check, e.detail] for e in exceptions],
           status_col="Severity")

    _sheet(wb, "Control Totals", ["Tax year", "Area", "Item", "Per K-1s", "Per 1065", "Difference", "Status"],
           [[c.year, c.area, c.item, c.per_k1s, c.per_1065, c.difference, c.status] for c in controls],
           status_col="Status")

    if basis_lines:
        rows = [asdict(b) for b in basis_lines]
        headers = list(rows[0])
        _sheet(wb, "Basis Rollforward", [h.replace("_", " ").capitalize() for h in headers],
               [[row[h] for h in headers] for row in rows])

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def _list(values) -> str:
    return ", ".join(map(str, values))
