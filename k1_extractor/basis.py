"""Roll a partner's outside basis forward from one year's K-1 to the next.

Ordering follows the basis worksheet in the IRS Partner's Instructions for
Schedule K-1 (Form 1065), sections 705, 731, 733 and 752, and Treas. Reg.
1.704-1(d)(2):

1. Increases: contributions, income items, tax-exempt income, and any increase
   in the partner's share of liabilities (section 752(a)).
2. Distributions: cash and property, plus any decrease in the share of
   liabilities (a deemed distribution, section 752(b)). The part above basis is
   gain (section 731(a)) and basis stops at zero.
3. Nondeductible, noncapital expenses (box 18 code C).
4. Losses and deductions, including losses suspended from prior years. The part
   above remaining basis is suspended and carried forward (section 704(d)).

Simplifications, stated so a reviewer can see them: suspended losses are
tracked as one pool rather than by character; property distributions are taken
at the amount shown in box 19; at-risk (section 465) and passive activity
(section 469) limits apply after basis and aren't computed here.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, asdict
from pathlib import Path

from .extract import K1Record

# Boxes whose positive amounts increase basis and negative amounts are losses.
INCOME_BOXES = {"1", "2", "3", "5", "6a", "7", "8", "9a", "9b", "9c", "10"}
CODED_INCOME_BOXES = {"11"}
# Boxes whose amounts (stated positive) are deductions that reduce basis.
DEDUCTION_BOXES = {"12", "21"}
CODED_DEDUCTION_BOXES = {"13"}
TAX_EXEMPT_CODES = {"18:A", "18:B"}
NONDEDUCTIBLE_CODES = {"18:C"}
DISTRIBUTION_CODES = {"19:A", "19:B", "19:C"}
# Guaranteed payments (box 4) are not basis items: the partner is taxed on them
# but the partnership pays them out, so they leave basis unchanged.


@dataclass
class BasisLine:
    tax_year: int
    partner_id: str
    partner_name: str
    beginning_basis: float
    contributions: float
    income_items: float
    tax_exempt_income: float
    liability_increase: float
    basis_before_distributions: float
    cash_distributions: float
    deemed_distributions: float
    distributions_applied: float
    gain_over_basis_731: float
    nondeductible_expenses: float
    current_losses_deductions: float
    suspended_losses_in: float
    losses_allowed: float
    suspended_losses_out: float
    ending_basis: float


def split_items(rec: K1Record) -> dict[str, float]:
    income = losses = tax_exempt = nondeductible = distributions = 0.0
    for key, amount in rec.boxes.items():
        box, _, code = key.partition(":")
        if box in INCOME_BOXES or box in CODED_INCOME_BOXES:
            if amount >= 0:
                income += amount
            else:
                losses += -amount
        elif box in DEDUCTION_BOXES or box in CODED_DEDUCTION_BOXES:
            losses += abs(amount)
        elif key in TAX_EXEMPT_CODES:
            tax_exempt += amount
        elif key in NONDEDUCTIBLE_CODES:
            nondeductible += abs(amount)
        elif key in DISTRIBUTION_CODES:
            distributions += abs(amount)
    return {"income": income, "losses": losses, "tax_exempt": tax_exempt,
            "nondeductible": nondeductible, "distributions": distributions}


def roll_forward(rec: K1Record, beginning_basis: float, suspended_in: float) -> BasisLine:
    items = split_items(rec)
    contributions = rec.get("cap_contributed", 0.0)
    liab_change = rec.get("liab_total_end", 0.0) - rec.get("liab_total_begin", 0.0)
    liab_increase = max(liab_change, 0.0)
    deemed_dist = abs(min(liab_change, 0.0))

    basis = beginning_basis + contributions + items["income"] + items["tax_exempt"] + liab_increase
    before_dist = basis

    total_dist = items["distributions"] + deemed_dist
    applied = min(total_dist, basis)
    gain = total_dist - applied
    basis -= applied

    nondeductible = min(items["nondeductible"], basis)
    basis -= nondeductible

    loss_pool = items["losses"] + suspended_in
    allowed = min(loss_pool, basis)
    basis -= allowed

    return BasisLine(
        tax_year=rec.get("tax_year"),
        partner_id=rec.get("partner_id"),
        partner_name=rec.get("partner_name"),
        beginning_basis=beginning_basis,
        contributions=contributions,
        income_items=items["income"],
        tax_exempt_income=items["tax_exempt"],
        liability_increase=liab_increase,
        basis_before_distributions=before_dist,
        cash_distributions=items["distributions"],
        deemed_distributions=deemed_dist,
        distributions_applied=applied,
        gain_over_basis_731=gain,
        nondeductible_expenses=nondeductible,
        current_losses_deductions=items["losses"],
        suspended_losses_in=suspended_in,
        losses_allowed=allowed,
        suspended_losses_out=loss_pool - allowed,
        ending_basis=basis,
    )


def load_opening_basis(path: str | Path | None) -> dict[str, tuple[float, float]]:
    """partner_id -> (beginning basis, suspended losses) for the first year."""
    if not path:
        return {}
    with open(path, newline="") as fh:
        return {row["partner_id"]: (float(row["beginning_basis"]), float(row.get("suspended_losses") or 0))
                for row in csv.DictReader(fh)}


def roll_years(records_by_year: dict[int, list[K1Record]],
               opening: dict[str, tuple[float, float]]) -> tuple[list[BasisLine], list[str]]:
    """Chain basis from the earliest year to the latest. Returns the lines and
    notes about partners who start with no opening basis."""
    carry = dict(opening)
    lines, notes = [], []
    for year in sorted(records_by_year):
        for rec in sorted(records_by_year[year], key=lambda r: r.get("partner_id") or ""):
            pid = rec.get("partner_id")
            if pid not in carry:
                if rec.get("cap_begin", 0) or rec.get("liab_total_begin", 0):
                    notes.append(f"{year} {pid}: no opening basis on file but the K-1 shows a beginning "
                                 f"balance; started at zero")
                carry[pid] = (0.0, 0.0)
            begin, suspended = carry[pid]
            line = roll_forward(rec, begin, suspended)
            lines.append(line)
            carry[pid] = (line.ending_basis, line.suspended_losses_out)
    return lines, notes


def as_rows(lines: list[BasisLine]) -> list[dict]:
    return [asdict(l) for l in lines]
