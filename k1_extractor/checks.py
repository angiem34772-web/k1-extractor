"""Checks on each K-1 and tie-out of the K-1s to the Form 1065."""

from __future__ import annotations

from dataclasses import dataclass

from .extract import K1Record

TOLERANCE = 1.0  # dollars, for rounding


@dataclass
class Exception_:
    year: int | None
    partner_id: str | None
    source: str
    severity: str  # "ERROR", "WARN" or "INFO"
    check: str
    detail: str


@dataclass
class ControlLine:
    year: int
    area: str
    item: str
    per_k1s: float
    per_1065: float | None
    tolerance: float = TOLERANCE

    @property
    def difference(self) -> float | None:
        return None if self.per_1065 is None else round(self.per_k1s - self.per_1065, 2)

    @property
    def status(self) -> str:
        if self.per_1065 is None:
            return "NO 1065 AMOUNT"
        return "OK" if abs(self.difference) <= self.tolerance else "DIFF"


def check_k1(rec: K1Record, expected_year: int | None = None) -> list[Exception_]:
    out: list[Exception_] = []
    year, pid, src = rec.get("tax_year"), rec.get("partner_id"), _src(rec)

    def add(sev, check, detail):
        out.append(Exception_(year, pid, src, sev, check, detail))

    for p in rec.problems:
        add("ERROR", "Extraction", p)

    if expected_year is not None and year is not None and year != expected_year:
        add("ERROR", "Tax year", f"K-1 says {year}, folder is {expected_year}")

    f = rec.fields
    if all(k in f for k in ("cap_begin", "cap_end")):
        computed = (
            f["cap_begin"] + f.get("cap_contributed", 0) + f.get("cap_net_income", 0)
            + f.get("cap_other", 0) - f.get("cap_withdrawals", 0)
        )
        diff = f["cap_end"] - computed
        if abs(diff) > TOLERANCE:
            add("ERROR", "Item L foots",
                f"Ending capital {f['cap_end']:,.0f} vs computed {computed:,.0f} (difference {diff:,.0f})")

    method = (f.get("capital_method") or "").lower()
    if method and "tax" not in method:
        add("WARN", "Capital method",
            f"Method box says {f.get('capital_method')!r}; confirm whether the amounts are really tax basis")

    dist_box = sum(v for k, v in rec.boxes.items() if k.startswith("19:"))
    if abs(dist_box - f.get("cap_withdrawals", 0)) > TOLERANCE:
        add("WARN", "Distributions",
            f"Box 19 total {dist_box:,.0f} vs Item L withdrawals {f.get('cap_withdrawals', 0):,.0f}")

    if f.get("cap_end", 0) < 0:
        add("INFO", "Negative capital", f"Ending capital account is {f['cap_end']:,.0f}")

    if f.get("final_k1") == "YES":
        add("INFO", "Final K-1", "Partner exited during the year")
    if f.get("amended_k1") == "YES":
        add("WARN", "Amended K-1", "Make sure the original isn't also in the folder")

    if rec.unparsed_lines:
        add("INFO", "Unread lines", f"{len(rec.unparsed_lines)} line(s) didn't match a rule: "
            + "; ".join(rec.unparsed_lines[:3]))
    return out


def check_duplicates(records: list[K1Record]) -> list[Exception_]:
    seen: dict[tuple, K1Record] = {}
    out = []
    for r in records:
        key = (r.get("tax_year"), r.get("partnership_ein"), r.get("partner_id"))
        if key in seen:
            out.append(Exception_(key[0], key[2], _src(r), "ERROR", "Duplicate K-1",
                                  f"Same partner and year also in {_src(seen[key])}"))
        else:
            seen[key] = r
    return out


def tie_out(year: int, records: list[K1Record], totals: dict | None) -> list[ControlLine]:
    """Sum the K-1s and compare to the Form 1065 (Schedule K, Schedule M-2)."""
    lines: list[ControlLine] = []
    totals = totals or {}

    lines.append(ControlLine(year, "Count", "Number of K-1s", len(records), totals.get("k1_count")))

    box_sums: dict[str, float] = {}
    for r in records:
        for k, v in r.boxes.items():
            box_sums[k] = box_sums.get(k, 0) + v
    sched_k = totals.get("schedule_k", {})
    for key in sorted(set(box_sums) | set(sched_k), key=_box_sort):
        lines.append(ControlLine(year, "Schedule K", f"Box {key.replace(':', ' code ')}",
                                 box_sums.get(key, 0), sched_k.get(key)))

    m2 = totals.get("schedule_m2", {})
    for item, field_name in [
        ("beginning_capital", "cap_begin"),
        ("capital_contributed", "cap_contributed"),
        ("net_income", "cap_net_income"),
        ("distributions", "cap_withdrawals"),
        ("ending_capital", "cap_end"),
    ]:
        lines.append(ControlLine(year, "Schedule M-2", item.replace("_", " ").capitalize(),
                                 sum(r.get(field_name, 0) for r in records), m2.get(item)))

    if "liabilities_end" in totals:
        lines.append(ControlLine(year, "Liabilities", "Partners' share at year end (Item K)",
                                 sum(r.get("liab_total_end", 0) for r in records), totals["liabilities_end"]))

    for kind in ("profit", "loss", "capital"):
        total = sum(r.get(f"{kind}_pct_end", 0) for r in records)
        lines.append(ControlLine(year, "Item J", f"{kind.capitalize()} % at year end",
                                 round(total, 6), 100.0, tolerance=0.0001))
    return lines


def _box_sort(key: str):
    box, _, code = key.partition(":")
    num = int("".join(ch for ch in box if ch.isdigit()))
    return (num, box, code)


def _src(rec: K1Record) -> str:
    pages = f" p.{rec.pages[0]}" if rec.pages else ""
    return f"{rec.source_file}{pages}"
