"""Tests run against freshly generated fake K-1s, so they never need real data."""

import json

import pytest
from openpyxl import load_workbook

from k1_extractor.amounts import allocate, parse_amount
from k1_extractor.basis import roll_forward
from k1_extractor.extract import K1Record, extract_pdf, parse_k1_text
from k1_extractor.fake_k1s import write_samples
from k1_extractor.pipeline import run


@pytest.fixture(scope="module")
def clean_samples(tmp_path_factory):
    d = tmp_path_factory.mktemp("clean")
    write_samples(d, plant_errors=False)
    return d


@pytest.fixture(scope="module")
def error_samples(tmp_path_factory):
    d = tmp_path_factory.mktemp("errors")
    write_samples(d, plant_errors=True)
    return d


# ---- amounts -------------------------------------------------------------

@pytest.mark.parametrize("text,value", [
    ("1,234", 1234), ("(1,234)", -1234), ("-1,234", -1234), ("$5", 5), ("0", 0), ("12.50", 12.5),
])
def test_parse_amount(text, value):
    assert parse_amount(text) == value


def test_parse_amount_rejects_text():
    with pytest.raises(ValueError):
        parse_amount("N/A")


def test_allocate_adds_back_exactly():
    parts = allocate(100_000, [1, 1, 1])
    assert sum(parts) == 100_000 and max(parts) - min(parts) <= 1
    assert sum(allocate(-300_000, [40, 30, 5, 25])) == -300_000


# ---- extraction ----------------------------------------------------------

def test_combined_package_is_split_and_cover_page_skipped(clean_samples):
    records, info = extract_pdf(clean_samples / "2025" / "K-1 package 2025.pdf")
    assert info.k1_count == 5
    assert info.skipped_pages == [1]
    assert [r.get("partner_id") for r in records] == ["P-001", "P-002", "P-003", "P-004", "P-005"]


def test_fields_read_from_k1(clean_samples):
    records, _ = extract_pdf(clean_samples / "2024" / "K-1 2024 P-004.pdf")
    r = records[0]
    assert r.get("tax_year") == 2024
    assert r.get("partner_name") == "Partner Gamma"
    assert r.get("profit_pct_end") == 25.0
    assert r.boxes["1"] == 30_000
    assert r.boxes["2"] == -75_000
    assert r.boxes["13:W"] == 2_500
    assert r.boxes["19:A"] == 37_500
    assert r.get("cap_withdrawals") == 37_500
    assert r.problems == []


def test_missing_fields_are_reported():
    rec = parse_k1_text("SCHEDULE K-1 (FORM 1065) TAX YEAR 2025\nPart III\n1 Ordinary business income (loss) 5")
    assert "Missing partner_id" in rec.problems


def test_coded_box_without_code_is_flagged():
    rec = parse_k1_text("Part III\n13 Other deductions 500")
    assert any("no code" in p for p in rec.problems)


# ---- whole run, clean data -----------------------------------------------

def test_clean_data_ties_out(clean_samples):
    result = run(clean_samples, clean_samples / "opening_basis.csv")
    assert len(result.records) == 9
    assert [c for c in result.controls if c.status != "OK"] == []
    assert [e for e in result.exceptions if e.severity == "ERROR"] == []


def test_profits_interest_is_a_separate_partner(clean_samples):
    result = run(clean_samples, clean_samples / "opening_basis.csv")
    names = {m["name"] for m in result.master.values()}
    assert {"Partner Beta", "Partner Beta - Profits Interest"} <= names
    assert result.master["P-005"] == {"no": 5, "name": "Partner Delta Trust", "first_year": 2025}


def test_basis_rollforward_matches_hand_calculation(clean_samples):
    result = run(clean_samples, clean_samples / "opening_basis.csv")
    b = {(l.tax_year, l.partner_id): l for l in result.basis_lines}

    # Partner Gamma 2024: 30,000 + 32,000 income - 37,500 distributions = 24,500;
    # losses 77,500 -> 24,500 allowed, 53,000 suspended.
    g = b[(2024, "P-004")]
    assert (g.losses_allowed, g.suspended_losses_out, g.ending_basis) == (24_500, 53_000, 0)

    # 2025: 42,000 of basis absorbs 42,000 of the 74,600 pool (21,600 + 53,000).
    g = b[(2025, "P-004")]
    assert (g.suspended_losses_in, g.losses_allowed, g.suspended_losses_out) == (53_000, 42_000, 32_600)

    # Profits interest 2024: 6,400 of basis, 7,500 distributed -> 1,100 section 731 gain.
    assert b[(2024, "P-003")].gain_over_basis_731 == 1_100

    # Partner Alpha 2025: liability share falls 628,571 -> 490,000, a 138,571 deemed distribution.
    a = b[(2025, "P-001")]
    assert a.deemed_distributions == 138_571
    assert a.ending_basis == 895_771 + 143_500 - 70_000 - 138_571 - 37_800

    # New partner: contribution plus a new liability share.
    d = b[(2025, "P-005")]
    assert (d.contributions, d.liability_increase, d.ending_basis) == (100_000, 140_000, 250_200)


def test_guaranteed_payments_do_not_change_basis():
    rec = K1Record("x", [1], {"tax_year": 2025, "partner_id": "P"}, {"4a": 50_000})
    assert roll_forward(rec, 1_000, 0).ending_basis == 1_000


# ---- whole run, planted errors -------------------------------------------

def test_planted_errors_are_caught(error_samples):
    result = run(error_samples, error_samples / "opening_basis.csv")
    checks = {(e.year, e.partner_id, e.check) for e in result.exceptions}
    assert (2025, "P-001", "Item L foots") in checks
    assert (2024, "P-002", "Capital method") in checks
    diffs = [(c.year, c.item) for c in result.controls if c.status == "DIFF"]
    assert diffs == [(2025, "Ending capital")]


def test_wrong_1065_total_shows_as_diff(tmp_path):
    write_samples(tmp_path, plant_errors=False)
    p = tmp_path / "2024" / "form1065_totals.json"
    totals = json.loads(p.read_text())
    totals["schedule_k"]["1"] += 500
    p.write_text(json.dumps(totals))
    result = run(tmp_path, tmp_path / "opening_basis.csv")
    bad = [c for c in result.controls if c.status == "DIFF"]
    assert len(bad) == 1 and bad[0].item == "Box 1" and bad[0].difference == -500


def test_duplicate_k1_is_flagged(tmp_path):
    write_samples(tmp_path, plant_errors=False)
    src = tmp_path / "2024" / "K-1 2024 P-001.pdf"
    (tmp_path / "2024" / "K-1 2024 P-001 copy.pdf").write_bytes(src.read_bytes())
    result = run(tmp_path, tmp_path / "opening_basis.csv")
    assert any(e.check == "Duplicate K-1" for e in result.exceptions)


def test_workbook_has_every_tab(error_samples, tmp_path):
    out = tmp_path / "results.xlsx"
    run(error_samples, error_samples / "opening_basis.csv", out)
    wb = load_workbook(out)
    assert wb.sheetnames == ["Summary", "File Inventory", "Partner Master", "K-1 Detail", "Box Detail",
                             "Exceptions", "Control Totals", "Basis Rollforward"]
    assert wb["K-1 Detail"].max_row == 10  # header + 9 K-1s
