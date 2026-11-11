"""Build a FAKE partnership's K-1 PDFs and Form 1065 totals for testing.

Every name, number and ID here is invented. The data is shaped to exercise
the interesting cases:

* a profits-interest K-1 issued to the same person as a capital interest
  (kept as a separate partner);
* a partner whose losses exceed basis (suspended losses under section 704(d));
* a distribution larger than basis (gain under section 731);
* a partner who joins in the second year with a capital contribution;
* a drop in liabilities (a deemed distribution under section 752(b));
* a combined PDF package with a cover letter in front of the K-1s;
* optional planted errors so the exception checks have something to catch.

Usage:
    python -m k1_extractor.fake_k1s samples
    python -m k1_extractor.fake_k1s samples --no-errors
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from .amounts import allocate, format_amount

PARTNERSHIP_NAME = "Harborview Sample Partners LP"
PARTNERSHIP_EIN = "00-0000000"


@dataclass
class Partner:
    partner_id: str
    name: str
    entity_type: str


PARTNERS = {
    "P-001": Partner("P-001", "Partner Alpha LLC", "LLC"),
    "P-002": Partner("P-002", "Partner Beta", "Individual"),
    "P-003": Partner("P-003", "Partner Beta - Profits Interest", "Individual"),
    "P-004": Partner("P-004", "Partner Gamma", "Individual"),
    "P-005": Partner("P-005", "Partner Delta Trust", "Trust"),
}

# Partnership-level items each year. Coded boxes use "box:code" keys.
# Deductions (box 13) are stated as positive amounts, as on the K-1.
YEARS = {
    2024: {
        "profit_pct": {"P-001": 40, "P-002": 30, "P-003": 5, "P-004": 25},
        "items": {"1": 120_000, "2": -300_000, "5": 8_000, "13:W": 10_000},
        "distributions": 150_000,
        # Share of qualified nonrecourse financing; the profits interest and
        # Partner Gamma are allocated none.
        "qnrf_weights": {"P-001": 40, "P-002": 30},
        "qnrf_begin": 1_000_000,
        "qnrf_end": 1_100_000,
        "contributions": {},
        "begin_capital": {"P-001": 400_000, "P-002": 300_000, "P-003": 0, "P-004": 30_000},
    },
    2025: {
        "profit_pct": {"P-001": 35, "P-002": 30, "P-003": 5, "P-004": 20, "P-005": 10},
        "items": {"1": 400_000, "2": -100_000, "5": 10_000, "13:W": 8_000},
        "distributions": 200_000,
        "qnrf_weights": {"P-001": 35, "P-002": 30, "P-005": 10},
        "qnrf_end": 1_050_000,
        "contributions": {"P-005": 100_000},
    },
}

# Outside basis going into the first year (normally from the prior workpapers).
OPENING_BASIS = {
    # partner_id: (beginning basis, suspended losses carried in)
    "P-001": (400_000 + 571_429, 0),
    "P-002": (300_000 + 428_571, 0),
    "P-003": (0, 0),
    "P-004": (30_000, 0),
}

BOX_LABELS = {
    "1": "Ordinary business income (loss)",
    "2": "Net rental real estate income (loss)",
    "5": "Interest income",
    "13:W": "Other deductions",
    "19:A": "Distributions - cash and marketable securities",
}


@dataclass
class K1:
    year: int
    partner: Partner
    pct_begin: float
    pct_end: float
    liab_begin: int
    liab_end: int
    cap_begin: int
    contributed: int
    net_income: int
    withdrawals: int
    cap_end: int
    boxes: dict[str, int] = field(default_factory=dict)
    method: str = "Tax basis"
    printed_cap_end: int | None = None  # set when an error is planted


def build_year_data() -> dict[int, list[K1]]:
    """Allocate each year's partnership items to partners."""
    result: dict[int, list[K1]] = {}
    prev_pct: dict[str, float] = {}
    prev_liab: dict[str, int] = {}
    prev_cap: dict[str, int] = {}
    prev_qnrf_end = None

    for year in sorted(YEARS):
        y = YEARS[year]
        ids = list(y["profit_pct"])
        weights = [y["profit_pct"][p] for p in ids]

        alloc = {key: dict(zip(ids, allocate(total, weights))) for key, total in y["items"].items()}
        dist = dict(zip(ids, allocate(y["distributions"], weights)))

        q_ids = list(y["qnrf_weights"])
        q_w = [y["qnrf_weights"][p] for p in q_ids]
        q_begin_total = y.get("qnrf_begin", prev_qnrf_end)
        if year == min(YEARS):
            liab_begin = dict(zip(q_ids, allocate(q_begin_total, q_w)))
        else:
            liab_begin = prev_liab
        liab_end = dict(zip(q_ids, allocate(y["qnrf_end"], q_w)))

        k1s = []
        for pid in ids:
            boxes = {key: alloc[key][pid] for key in alloc}
            boxes["19:A"] = dist[pid]
            cap_begin = y.get("begin_capital", prev_cap).get(pid, 0)
            contributed = y["contributions"].get(pid, 0)
            net_income = sum(v for k, v in boxes.items() if not k.startswith(("13", "19")))
            net_income -= sum(v for k, v in boxes.items() if k.startswith("13"))
            cap_end = cap_begin + contributed + net_income - dist[pid]
            k1s.append(
                K1(
                    year=year,
                    partner=PARTNERS[pid],
                    pct_begin=prev_pct.get(pid, 0 if year != min(YEARS) else y["profit_pct"][pid]),
                    pct_end=y["profit_pct"][pid],
                    liab_begin=liab_begin.get(pid, 0),
                    liab_end=liab_end.get(pid, 0),
                    cap_begin=cap_begin,
                    contributed=contributed,
                    net_income=net_income,
                    withdrawals=dist[pid],
                    cap_end=cap_end,
                    boxes=boxes,
                )
            )
        result[year] = k1s
        prev_pct = dict(y["profit_pct"])
        prev_liab = liab_end
        prev_cap = {k.partner.partner_id: k.cap_end for k in k1s}
        prev_qnrf_end = y["qnrf_end"]
    return result


def form_1065_totals(k1s: list[K1]) -> dict:
    """What the Form 1065 would show, computed from the true K-1 amounts."""
    sched_k: dict[str, int] = {}
    for k in k1s:
        for key, v in k.boxes.items():
            sched_k[key] = sched_k.get(key, 0) + v
    return {
        "partnership_name": PARTNERSHIP_NAME,
        "partnership_ein": PARTNERSHIP_EIN,
        "k1_count": len(k1s),
        "schedule_k": sched_k,
        "schedule_m2": {
            "beginning_capital": sum(k.cap_begin for k in k1s),
            "capital_contributed": sum(k.contributed for k in k1s),
            "net_income": sum(k.net_income for k in k1s),
            "distributions": sum(k.withdrawals for k in k1s),
            "ending_capital": sum(k.cap_end for k in k1s),
        },
        "liabilities_end": sum(k.liab_end for k in k1s),
    }


def k1_lines(k: K1) -> list[str]:
    p = k.partner
    cap_end = k.printed_cap_end if k.printed_cap_end is not None else k.cap_end
    lines = [
        f"SCHEDULE K-1 (FORM 1065) TAX YEAR {k.year}",
        "Partner's Share of Income, Deductions, Credits, etc.  SAMPLE - FICTIONAL DATA",
        "FINAL K-1: NO   AMENDED K-1: NO",
        "Part I Information About the Partnership",
        f"A Partnership's employer identification number {PARTNERSHIP_EIN}",
        f"B Partnership's name {PARTNERSHIP_NAME}",
        "Part II Information About the Partner",
        f"E Partner's identifying number {p.partner_id}",
        f"F Partner's name {p.name}",
        f"I1 Entity type of this partner {p.entity_type}",
        f"J Profit share beginning {k.pct_begin:.6f}% ending {k.pct_end:.6f}%",
        f"J Loss share beginning {k.pct_begin:.6f}% ending {k.pct_end:.6f}%",
        f"J Capital share beginning {k.pct_begin:.6f}% ending {k.pct_end:.6f}%",
        "K Nonrecourse liabilities beginning 0 ending 0",
        f"K Qualified nonrecourse financing beginning {format_amount(k.liab_begin)} ending {format_amount(k.liab_end)}",
        "K Recourse liabilities beginning 0 ending 0",
        f"L Beginning capital account {format_amount(k.cap_begin)}",
        f"L Capital contributed during the year {format_amount(k.contributed)}",
        f"L Current year net income (loss) {format_amount(k.net_income)}",
        "L Other increase (decrease) 0",
        f"L Withdrawals and distributions {format_amount(-k.withdrawals)}",
        f"L Ending capital account {format_amount(cap_end)}",
        f"L Capital account method {k.method}",
        "Part III Partner's Share of Current Year Income, Deductions, Credits, and Other Items",
    ]
    for key, v in k.boxes.items():
        box, _, code = key.partition(":")
        label = BOX_LABELS[key]
        lines.append(f"{box} {code + ' ' if code else ''}{label} {format_amount(v)}")
    return lines


def draw_page(c: canvas.Canvas, lines: list[str]) -> None:
    y = 740
    for line in lines:
        c.setFont("Helvetica-Bold" if line.startswith(("SCHEDULE", "Part ")) else "Helvetica", 9)
        c.drawString(50, y, line)
        y -= 16
    c.showPage()


def write_samples(out_dir: Path, plant_errors: bool = True) -> None:
    out_dir = Path(out_dir)
    data = build_year_data()

    # Totals come from the true amounts, before any errors are planted.
    totals = {year: form_1065_totals(k1s) for year, k1s in data.items()}

    if plant_errors:
        # 2025: Partner Alpha's ending capital is misprinted by $100.
        alpha = next(k for k in data[2025] if k.partner.partner_id == "P-001")
        alpha.printed_cap_end = alpha.cap_end + 100
        # 2024: Partner Beta's K-1 has the GAAP box checked.
        beta = next(k for k in data[2024] if k.partner.partner_id == "P-002")
        beta.method = "GAAP"

    for year, k1s in data.items():
        ydir = out_dir / str(year)
        ydir.mkdir(parents=True, exist_ok=True)
        for old in ydir.glob("*.pdf"):
            old.unlink()
        if year == 2024:
            # One PDF per partner.
            for k in k1s:
                c = canvas.Canvas(str(ydir / f"K-1 {year} {k.partner.partner_id}.pdf"), pagesize=letter)
                draw_page(c, k1_lines(k))
                c.save()
        else:
            # One combined package with a cover letter in front.
            c = canvas.Canvas(str(ydir / f"K-1 package {year}.pdf"), pagesize=letter)
            draw_page(c, [
                f"{PARTNERSHIP_NAME}",
                f"Tax year {year} partner K-1 package",
                "SAMPLE - FICTIONAL DATA",
                "Enclosed are the Schedules K-1 for each partner.",
            ])
            for k in k1s:
                draw_page(c, k1_lines(k))
            c.save()
        (ydir / "form1065_totals.json").write_text(json.dumps(totals[year], indent=2))

    rows = ["partner_id,beginning_basis,suspended_losses"]
    rows += [f"{pid},{b},{s}" for pid, (b, s) in OPENING_BASIS.items()]
    (out_dir / "opening_basis.csv").write_text("\n".join(rows) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--no-errors", action="store_true", help="don't plant the sample errors")
    args = ap.parse_args()
    write_samples(args.out_dir, plant_errors=not args.no_errors)
    print(f"Fake K-1s written to {args.out_dir}")


if __name__ == "__main__":
    main()
