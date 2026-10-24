"""Boundary, validation, and property tests for the Uganda tax engine."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given, strategies as st

from src.tax import (
    CorporateIncomeTax,
    InvalidIncomeError,
    Invoice,
    Money,
    NSSF,
    NoScheduleForDateError,
    PAYE,
    Payroll,
    RentalIncomeTax,
    ResidencyStatus,
    SupplyType,
    TaxContext,
    VAT,
    WithholdingTax,
)

PAYROLL_DATE = date(2026, 10, 24)


def money(amount: int | str) -> Money:
    return Money(Decimal(amount))


@pytest.mark.parametrize(
    ("income", "expected_tax"),
    [
        (335_000, 0),
        (335_001, 0),
        (410_000, 15_000),
        (485_000, 33_750),
        (10_000_000, 2_888_250),
        (10_000_001, 2_888_250),
    ],
)
def test_resident_paye_at_every_assignment_band_edge(income: int, expected_tax: int) -> None:
    result = PAYE().calculate(
        TaxContext(gross_income=money(income), effective_date=PAYROLL_DATE)
    )

    assert result.amount == money(expected_tax)


def test_paye_supports_non_resident_schedule() -> None:
    result = PAYE().calculate(
        TaxContext(
            gross_income=money(335_000),
            residency=ResidencyStatus.NON_RESIDENT,
            effective_date=PAYROLL_DATE,
        )
    )

    assert result.amount == money(33_500)


def test_paye_applies_local_service_tax_before_taxable_income() -> None:
    result = PAYE().calculate(
        TaxContext(
            gross_income=money(410_000),
            local_service_tax=money(10_000),
            effective_date=PAYROLL_DATE,
        )
    )

    assert result.components["taxable_income"] == money(400_000)
    assert result.amount == money(13_000)


def test_paye_rejects_negative_income_and_unsupported_dates() -> None:
    with pytest.raises(InvalidIncomeError):
        PAYE().calculate(
            TaxContext(gross_income=money(-1), effective_date=PAYROLL_DATE)
        )
    with pytest.raises(NoScheduleForDateError):
        PAYE().calculate(
            TaxContext(gross_income=money(400_000), effective_date=date(2025, 6, 30))
        )


def test_money_rounding_and_float_rejection() -> None:
    assert Money(Decimal("2.5")) == money(3)
    with pytest.raises(TypeError, match="never float"):
        Money(2.5)  # type: ignore[arg-type]


def test_payroll_reports_all_deductions_and_employer_contribution() -> None:
    result = Payroll().calculate(
        TaxContext(
            gross_income=money(500_000),
            allowances=money(25_000),
            local_service_tax=money(5_000),
            effective_date=PAYROLL_DATE,
        )
    )

    assert result["employee_nssf"] == money(25_000)
    assert result["employer_nssf"] == money(50_000)
    assert result["paye"] == money(44_250)
    assert result["net_pay"] == money(450_750)


def test_vat_ignores_zero_rated_and_exempt_invoices() -> None:
    result = VAT().calculate(
        TaxContext(
            invoices=(
                Invoice(money(100_000), SupplyType.STANDARD_RATED, is_sale=True),
                Invoice(money(50_000), SupplyType.STANDARD_RATED, is_sale=False),
                Invoice(money(30_000), SupplyType.ZERO_RATED, is_sale=True),
                Invoice(money(20_000), SupplyType.EXEMPT, is_sale=False),
            )
        )
    )

    assert result.components["output_vat"] == money(18_000)
    assert result.components["input_vat"] == money(9_000)
    assert result.amount == money(9_000)


def test_corporate_income_tax_uses_chargeable_income() -> None:
    result = CorporateIncomeTax().calculate(
        TaxContext(revenue=money(10_000_000), allowable_deductions=money(3_000_000))
    )

    assert result.components["chargeable_income"] == money(7_000_000)
    assert result.amount == money(2_100_000)


def test_nssf_and_extension_tax_modules() -> None:
    context = TaxContext(gross_income=money(4_000_000), effective_date=PAYROLL_DATE)

    assert NSSF().calculate(context).components["employer"] == money(400_000)
    assert WithholdingTax().calculate(context).amount == money(600_000)
    assert RentalIncomeTax().calculate(context).amount == money(141_600)


@given(st.integers(min_value=0, max_value=500_000_000))
def test_paye_is_nonnegative_and_never_exceeds_income(income: int) -> None:
    tax = PAYE().calculate(
        TaxContext(gross_income=money(income), effective_date=PAYROLL_DATE)
    ).amount

    assert Decimal("0") <= tax.amount <= Decimal(income)


@given(
    st.integers(min_value=0, max_value=500_000_000),
    st.integers(min_value=0, max_value=500_000_000),
)
def test_paye_is_monotonic_in_income(first: int, second: int) -> None:
    lower, higher = sorted((first, second))
    lower_tax = PAYE().calculate(
        TaxContext(gross_income=money(lower), effective_date=PAYROLL_DATE)
    ).amount
    higher_tax = PAYE().calculate(
        TaxContext(gross_income=money(higher), effective_date=PAYROLL_DATE)
    ).amount

    assert lower_tax.amount <= higher_tax.amount
