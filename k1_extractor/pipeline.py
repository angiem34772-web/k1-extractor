"""Run the whole process: extract, check, tie out, roll basis, write Excel."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import basis as basis_mod
from .checks import ControlLine, Exception_, check_duplicates, check_k1, tie_out
from .amounts import format_amount
from .extract import K1Record, extract_folder
from .report import write_workbook


@dataclass
class RunResult:
    records: list[K1Record]
    exceptions: list[Exception_]
    controls: list[ControlLine]
    basis_lines: list[basis_mod.BasisLine]
    master: dict
    notes: list[str]


def year_folders(root: Path) -> list[tuple[int, Path]]:
    """A folder of year folders (2024/, 2025/), or a single year folder."""
    root = Path(root)
    if root.name.isdigit() and len(root.name) == 4:
        return [(int(root.name), root)]
    return sorted((int(p.name), p) for p in root.iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 4)


def build_master(records_by_year: dict[int, list[K1Record]]) -> dict:
    """One master partner number per partner ID, in order of first appearance.
    A profits-interest K-1 has its own partner ID, so it is its own partner."""
    master: dict[str, dict] = {}
    for year in sorted(records_by_year):
        for rec in sorted(records_by_year[year], key=lambda r: r.get("partner_id") or ""):
            pid = rec.get("partner_id")
            if pid and pid not in master:
                master[pid] = {"no": len(master) + 1, "name": rec.get("partner_name"), "first_year": year}
    return master


def run(root: str | Path, opening_basis: str | Path | None = None, out: str | Path | None = None) -> RunResult:
    files, records, exceptions, controls = [], [], [], []
    by_year: dict[int, list[K1Record]] = {}

    for year, folder in year_folders(Path(root)):
        recs, file_infos = extract_folder(folder)
        files += [(year, f) for f in file_infos]
        for f in file_infos:
            for p in f.no_text_pages:
                exceptions.append(Exception_(year, None, f"{f.path} p.{p}", "ERROR", "No text layer",
                                             "Scanned page; run OCR (e.g. Acrobat 'Recognize text') and re-run"))
        by_year[year] = recs
        records += recs
        for r in recs:
            exceptions += check_k1(r, expected_year=year)
        exceptions += check_duplicates(recs)
        totals_path = folder / "form1065_totals.json"
        totals = json.loads(totals_path.read_text()) if totals_path.exists() else None
        if totals is None:
            exceptions.append(Exception_(year, None, str(folder), "WARN", "Tie-out",
                                         "No form1065_totals.json in the folder; K-1s not tied to the 1065"))
        controls += tie_out(year, recs, totals)

    master = build_master(by_year)
    basis_lines, notes = basis_mod.roll_years(by_year, basis_mod.load_opening_basis(opening_basis))

    for b in basis_lines:
        if b.gain_over_basis_731 > 0:
            exceptions.append(Exception_(b.tax_year, b.partner_id, "Basis", "WARN", "Distribution over basis",
                                         f"Distributions exceed basis by {format_amount(b.gain_over_basis_731)}; "
                                         f"possible section 731 gain"))
        if b.suspended_losses_out > 0:
            exceptions.append(Exception_(b.tax_year, b.partner_id, "Basis", "INFO", "Suspended losses",
                                         f"{format_amount(b.suspended_losses_out)} of losses carried forward (section 704(d))"))

    if out:
        write_workbook(out, files=files, records=records, master=master, exceptions=exceptions,
                       controls=controls, basis_lines=basis_lines, notes=notes)
    return RunResult(records, exceptions, controls, basis_lines, master, notes)
