"""Read Schedule K-1 (Form 1065) data out of text-based PDFs.

Each line pattern lives in RULES, so adapting the tool to a different K-1
layout (software vendors print the form differently) means changing patterns
here, not rewriting the logic. Scanned PDFs have no text layer; those pages
are reported so they can be run through OCR first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from .amounts import AMOUNT, parse_amount

K1_HEADER = re.compile(r"SCHEDULE K-1 \(FORM 1065\)", re.I)

# Simple text fields: key -> pattern with one capture group.
TEXT_RULES = {
    "tax_year": r"TAX YEAR (\d{4})",
    "partnership_ein": r"^A Partnership's employer identification number (\S+)$",
    "partnership_name": r"^B Partnership's name (.+)$",
    "partner_id": r"^E Partner's identifying number (\S+)$",
    "partner_name": r"^F Partner's name (.+)$",
    "entity_type": r"^I1 Entity type of this partner (.+)$",
    "final_k1": r"FINAL K-1: (YES|NO)",
    "amended_k1": r"AMENDED K-1: (YES|NO)",
    "capital_method": r"^L Capital account method (.+)$",
}

# Item J percentages.
PCT_RULE = re.compile(r"^J (Profit|Loss|Capital) share beginning ([\d.]+)% ending ([\d.]+)%$")

# Item K liabilities.
LIAB_RULE = re.compile(
    rf"^K (Nonrecourse liabilities|Qualified nonrecourse financing|Recourse liabilities) "
    rf"beginning ({AMOUNT}) ending ({AMOUNT})$"
)
LIAB_KEYS = {
    "Nonrecourse liabilities": "nonrecourse",
    "Qualified nonrecourse financing": "qnrf",
    "Recourse liabilities": "recourse",
}

# Item L capital account analysis.
CAPITAL_RULES = {
    "cap_begin": r"^L Beginning capital account ({amt})$",
    "cap_contributed": r"^L Capital contributed during the year ({amt})$",
    "cap_net_income": r"^L Current year net income \(loss\) ({amt})$",
    "cap_other": r"^L Other increase \(decrease\) ({amt})$",
    "cap_withdrawals": r"^L Withdrawals and distributions ({amt})$",
    "cap_end": r"^L Ending capital account ({amt})$",
}

# Part III boxes. Boxes in CODED_BOXES carry a letter code.
CODED_BOXES = {"11", "12", "13", "14", "15", "16", "17", "18", "19", "20"}
BOX_RULE = re.compile(rf"^(\d{{1,2}}[a-c]?) (?:([A-Z]{{1,2}}) )?(.+?) ({AMOUNT})$")

REQUIRED = ["tax_year", "partnership_ein", "partner_id", "partner_name", "cap_begin", "cap_end"]


@dataclass
class K1Record:
    source_file: str
    pages: list[int]
    fields: dict = field(default_factory=dict)
    boxes: dict[str, float] = field(default_factory=dict)  # "1", "13:W", ...
    unparsed_lines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def get(self, key, default=None):
        return self.fields.get(key, default)


@dataclass
class FileResult:
    path: str
    page_count: int
    k1_count: int
    skipped_pages: list[int]
    no_text_pages: list[int]


def parse_k1_text(text: str, source_file: str = "", pages: list[int] | None = None) -> K1Record:
    rec = K1Record(source_file=source_file, pages=pages or [])
    in_part_iii = False
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if line.startswith("Part III"):
            in_part_iii = True
            continue
        if _match_header_fields(rec, line):
            continue
        m = PCT_RULE.match(line)
        if m:
            kind = m.group(1).lower()
            rec.fields[f"{kind}_pct_begin"] = float(m.group(2))
            rec.fields[f"{kind}_pct_end"] = float(m.group(3))
            continue
        m = LIAB_RULE.match(line)
        if m:
            key = LIAB_KEYS[m.group(1)]
            rec.fields[f"liab_{key}_begin"] = parse_amount(m.group(2))
            rec.fields[f"liab_{key}_end"] = parse_amount(m.group(3))
            continue
        if _match_capital(rec, line):
            continue
        if in_part_iii:
            m = BOX_RULE.match(line)
            if m:
                box, code, _label, amt = m.groups()
                if code and box not in CODED_BOXES:
                    # A one- or two-letter word that isn't a code; fold it back into the label.
                    code = None
                if box in CODED_BOXES and not code:
                    rec.problems.append(f"Box {box} amount has no code: {line!r}")
                    code = "?"
                key = f"{box}:{code}" if code else box
                rec.boxes[key] = rec.boxes.get(key, 0) + parse_amount(amt)
                continue
        if not line.startswith(("Part ", "SCHEDULE", "Partner's Share")):
            rec.unparsed_lines.append(line)

    if "cap_withdrawals" in rec.fields:
        # Printed as a negative; store the size of the withdrawal.
        rec.fields["cap_withdrawals"] = abs(rec.fields["cap_withdrawals"])
    for key in ("nonrecourse", "qnrf", "recourse"):
        for side in ("begin", "end"):
            rec.fields.setdefault(f"liab_{key}_{side}", 0.0)
    rec.fields["liab_total_begin"] = sum(rec.fields[f"liab_{k}_begin"] for k in ("nonrecourse", "qnrf", "recourse"))
    rec.fields["liab_total_end"] = sum(rec.fields[f"liab_{k}_end"] for k in ("nonrecourse", "qnrf", "recourse"))
    if "tax_year" in rec.fields:
        rec.fields["tax_year"] = int(rec.fields["tax_year"])
    for req in REQUIRED:
        if req not in rec.fields:
            rec.problems.append(f"Missing {req}")
    return rec


def _match_header_fields(rec: K1Record, line: str) -> bool:
    hit = False
    for key, pattern in TEXT_RULES.items():
        m = re.search(pattern, line)
        if m:
            rec.fields[key] = m.group(1).strip()
            hit = True
    return hit


def _match_capital(rec: K1Record, line: str) -> bool:
    for key, pattern in CAPITAL_RULES.items():
        m = re.match(pattern.format(amt=AMOUNT), line)
        if m:
            rec.fields[key] = parse_amount(m.group(1))
            return True
    return False


def extract_pdf(path: str | Path) -> tuple[list[K1Record], FileResult]:
    """Split a PDF into K-1s (a new K-1 starts at each K-1 header page) and parse each."""
    path = Path(path)
    groups: list[tuple[list[int], list[str]]] = []
    skipped, no_text = [], []
    with pdfplumber.open(path) as pdf:
        page_count = len(pdf.pages)
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if not text.strip():
                no_text.append(i)
                continue
            if K1_HEADER.search(text):
                groups.append(([i], [text]))
            elif groups:
                groups[-1][0].append(i)  # continuation page of the current K-1
                groups[-1][1].append(text)
            else:
                skipped.append(i)  # cover letter or other pages before the first K-1
    records = [parse_k1_text("\n".join(texts), path.name, pages) for pages, texts in groups]
    return records, FileResult(path.name, page_count, len(records), skipped, no_text)


def extract_folder(folder: str | Path) -> tuple[list[K1Record], list[FileResult]]:
    records, files = [], []
    for pdf in sorted(Path(folder).glob("*.pdf")):
        recs, info = extract_pdf(pdf)
        records.extend(recs)
        files.append(info)
    return records, files
