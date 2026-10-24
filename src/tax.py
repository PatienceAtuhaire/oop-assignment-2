"""Educational Uganda tax calculators using Decimal-based UGX money."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Sequence

UGX_QUANTUM = Decimal("1")
ZERO = Decimal("0")


class TaxError(Exception):
    """Base class for invalid tax inputs and unsupported schedules."""


class InvalidIncomeError(TaxError):
    """Raised when an income or deduction is invalid."""


class NoScheduleForDateError(TaxError):
    """Raised when no tax schedule applies on the requested date."""


@dataclass(frozen=True)
class Money:
    """A UGX amount rounded to the nearest whole shilling, half up."""

    amount: Decimal

    def __post_init__(self) -> None:
        if isinstance(self.amount, float):
            raise TypeError("Money accepts Decimal, integer, or string values, never float")
        try:
            decimal_amount = self.amount if isinstance(self.amount, Decimal) else Decimal(self.amount)
        except (TypeError, ValueError) as error:
            raise TypeError("Money amount must be Decimal, integer, or string") from error
        if not decimal_amount.is_finite():
            raise ValueError("Money amount must be finite")
        object.__setattr__(self, "amount", decimal_amount.quantize(UGX_QUANTUM, rounding=ROUND_HALF_UP))

    @classmethod
    def zero(cls) -> Money:
        """Return a zero UGX amount."""
        return cls(Decimal("0"))

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.amount + other.amount)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.amount - other.amount)

    def __mul__(self, multiplier: Decimal | int) -> Money:
        if isinstance(multiplier, float):
            raise TypeError("Money multiplication requires a Decimal or integer multiplier")
        if not isinstance(multiplier, (Decimal, int)):
            return NotImplemented
        return Money(self.amount * Decimal(multiplier))

    def __rmul__(self, multiplier: Decimal | int) -> Money:
        return self * multiplier

    def __lt__(self, other: Money) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        return self.amount < other.amount

    def __repr__(self) -> str:
        return f"Money(UGX {self.amount:,.0f})"

    def __str__(self) -> str:
        return f"UGX {self.amount:,.0f}"


class ResidencyStatus(Enum):
    RESIDENT = "resident"
    NON_RESIDENT = "non_resident"


class SupplyType(Enum):
    STANDARD_RATED = "standard_rated"
    ZERO_RATED = "zero_rated"
    EXEMPT = "exempt"


@dataclass(frozen=True)
class Invoice:
    """An invoice amount before VAT, with its supply type and purchase/sale role."""

    amount: Money
    supply_type: SupplyType
    is_sale: bool

    def __post_init__(self) -> None:
        if self.amount.amount < ZERO:
            raise InvalidIncomeError("Invoice amount cannot be negative")


@dataclass(frozen=True)
class TaxContext:
    """Inputs shared by tax calculators; unused fields keep their zero defaults."""

    gross_income: Money = field(default_factory=Money.zero)
    allowances: Money = field(default_factory=Money.zero)
    residency: ResidencyStatus = ResidencyStatus.RESIDENT
    effective_date: date = field(default_factory=date.today)
    local_service_tax: Money = field(default_factory=Money.zero)
    invoices: tuple[Invoice, ...] = ()
    revenue: Money = field(default_factory=Money.zero)
    allowable_deductions: Money = field(default_factory=Money.zero)


@dataclass(frozen=True)
class TaxResult:
    """Tax amount and an auditable breakdown of component amounts."""

    amount: Money
    components: dict[str, Money] = field(default_factory=dict)


class Tax(ABC):
    """Abstract strategy used by all tax modules."""

    @abstractmethod
    def calculate(self, ctx: TaxContext) -> TaxResult:
        """Calculate a tax result from a validated context."""


@dataclass(frozen=True)
class TaxBand:
    """Upper exclusive threshold and marginal tax rate for a progressive band."""

    upper_limit: Decimal | None
    rate: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.rate, Decimal):
            raise TypeError("Tax band rates must be Decimal values")
        if not self.rate.is_finite() or not ZERO <= self.rate <= Decimal("1"):
            raise ValueError("Tax band rates must be finite and between 0 and 1")
        if self.upper_limit is not None and not isinstance(self.upper_limit, Decimal):
            raise TypeError("Tax band limits must be Decimal values")
        if self.upper_limit is not None and not self.upper_limit.is_finite():
            raise ValueError("Tax band limits must be finite")


@dataclass(frozen=True)
class TaxSchedule:
    """A dated progressive schedule for a residency category."""

    residency: ResidencyStatus
    effective_from: date
    bands: tuple[TaxBand, ...]
    version: str
    source: str

    def __post_init__(self) -> None:
        previous_limit = ZERO
        for band in self.bands:
            if band.rate < ZERO or band.rate > Decimal("1"):
                raise ValueError("Tax band rates must be between 0 and 1")
            if band.upper_limit is not None:
                if band.upper_limit <= previous_limit:
                    raise ValueError("Tax band limits must be strictly increasing")
                previous_limit = band.upper_limit
        if not self.bands or self.bands[-1].upper_limit is not None:
            raise ValueError("The final tax band must have no upper limit")

    def tax_on(self, income: Money) -> Money:
        """Apply the band's marginal rates to a non-negative taxable income."""
        if income.amount < ZERO:
            raise InvalidIncomeError("Taxable income cannot be negative")
        lower_limit = ZERO
        tax = ZERO
        for band in self.bands:
            upper_limit = income.amount if band.upper_limit is None else min(income.amount, band.upper_limit)
            taxable_in_band = max(ZERO, upper_limit - lower_limit)
            tax += taxable_in_band * band.rate
            if band.upper_limit is None or income.amount <= band.upper_limit:
                break
            lower_limit = band.upper_limit
        return Money(tax)


RESIDENT_PAYE_2026 = TaxSchedule(
    residency=ResidencyStatus.RESIDENT,
    effective_from=date(2026, 7, 1),
    version="URA FY2026/27 schedule, accessed 2026-10-24",
    source="URA Employment Income: https://ura.go.ug/en/employment-income/",
    bands=(
        TaxBand(Decimal("335000"), Decimal("0")),
        TaxBand(Decimal("410000"), Decimal("0.20")),
        TaxBand(Decimal("485000"), Decimal("0.25")),
        TaxBand(Decimal("10000000"), Decimal("0.30")),
        TaxBand(None, Decimal("0.40")),
    ),
)

NON_RESIDENT_PAYE_2026 = TaxSchedule(
    residency=ResidencyStatus.NON_RESIDENT,
    effective_from=date(2026, 7, 1),
    version="URA FY2026/27 schedule, accessed 2026-10-24",
    source="URA Employment Income: https://ura.go.ug/en/employment-income/",
    bands=(
        TaxBand(Decimal("335000"), Decimal("0.10")),
        TaxBand(Decimal("410000"), Decimal("0.20")),
        TaxBand(Decimal("485000"), Decimal("0.30")),
        TaxBand(Decimal("10000000"), Decimal("0.30")),
        TaxBand(None, Decimal("0.40")),
    ),
)


class PAYE(Tax):
    """Resident and non-resident monthly PAYE calculator."""

    def __init__(self, schedules: Sequence[TaxSchedule] | None = None) -> None:
        self._schedules = tuple(
            schedules if schedules is not None else (RESIDENT_PAYE_2026, NON_RESIDENT_PAYE_2026)
        )

    def calculate(self, ctx: TaxContext) -> TaxResult:
        _validate_nonnegative(
            ("gross income", ctx.gross_income),
            ("allowances", ctx.allowances),
            ("local service tax", ctx.local_service_tax),
        )
        schedule = self._find_schedule(ctx.residency, ctx.effective_date)
        taxable_income = ctx.gross_income + ctx.allowances - ctx.local_service_tax
        taxable_income = Money(max(ZERO, taxable_income.amount))
        amount = schedule.tax_on(taxable_income)
        return TaxResult(
            amount=amount,
            components={
                "taxable_income": taxable_income,
                "local_service_tax_before_PAYE": ctx.local_service_tax,
            },
        )

    def _find_schedule(self, residency: ResidencyStatus, on_date: date) -> TaxSchedule:
        candidates = [
            schedule
            for schedule in self._schedules
            if schedule.residency is residency and schedule.effective_from <= on_date
        ]
        if not candidates:
            raise NoScheduleForDateError(
                f"No {residency.value} PAYE schedule is available for {on_date.isoformat()}"
            )
        return max(candidates, key=lambda schedule: schedule.effective_from)


class NSSF(Tax):
    """Employee and employer NSSF contribution calculator."""

    def __init__(
        self,
        employee_rate: Decimal = Decimal("0.05"),
        employer_rate: Decimal = Decimal("0.10"),
    ) -> None:
        self.employee_rate = _validate_rate(employee_rate, "employee_rate")
        self.employer_rate = _validate_rate(employer_rate, "employer_rate")

    def calculate(self, ctx: TaxContext) -> TaxResult:
        _validate_nonnegative(("gross income", ctx.gross_income))
        employee = ctx.gross_income * self.employee_rate
        employer = ctx.gross_income * self.employer_rate
        return TaxResult(
            amount=employee,
            components={"employee": employee, "employer": employer},
        )


class Payroll:
    """Compute deductions in the documented NSSF, LST, PAYE order."""

    def __init__(self, paye: PAYE | None = None, nssf: NSSF | None = None) -> None:
        self.paye = PAYE() if paye is None else paye
        self.nssf = NSSF() if nssf is None else nssf

    def calculate(self, ctx: TaxContext) -> dict[str, Money]:
        _validate_nonnegative(
            ("gross income", ctx.gross_income),
            ("allowances", ctx.allowances),
            ("local service tax", ctx.local_service_tax),
        )
        nssf = self.nssf.calculate(ctx)
        paye = self.paye.calculate(ctx)
        total_income = ctx.gross_income + ctx.allowances
        net_pay = total_income - nssf.amount - ctx.local_service_tax - paye.amount
        return {
            "gross_pay": total_income,
            "employee_nssf": nssf.amount,
            "employer_nssf": nssf.components["employer"],
            "local_service_tax": ctx.local_service_tax,
            "paye": paye.amount,
            "net_pay": net_pay,
        }


class VAT(Tax):
    """Calculate VAT output, recoverable input VAT, and net VAT payable."""

    def __init__(self, standard_rate: Decimal = Decimal("0.18")) -> None:
        self.standard_rate = _validate_rate(standard_rate, "standard_rate")

    def calculate(self, ctx: TaxContext) -> TaxResult:
        output_vat = Money.zero()
        input_vat = Money.zero()
        for invoice in ctx.invoices:
            if invoice.supply_type is not SupplyType.STANDARD_RATED:
                continue
            vat_amount = invoice.amount * self.standard_rate
            if invoice.is_sale:
                output_vat += vat_amount
            else:
                input_vat += vat_amount
        return TaxResult(
            amount=output_vat - input_vat,
            components={"output_vat": output_vat, "input_vat": input_vat},
        )


class CorporateIncomeTax(Tax):
    """Resident-company corporate income tax on chargeable income."""

    def __init__(self, rate: Decimal = Decimal("0.30")) -> None:
        self.rate = _validate_rate(rate, "rate")

    def calculate(self, ctx: TaxContext) -> TaxResult:
        _validate_nonnegative(
            ("revenue", ctx.revenue),
            ("allowable deductions", ctx.allowable_deductions),
        )
        chargeable_income = Money(max(ZERO, (ctx.revenue - ctx.allowable_deductions).amount))
        amount = chargeable_income * self.rate
        return TaxResult(amount, {"chargeable_income": chargeable_income})


class WithholdingTax(Tax):
    """Simple withholding tax on a supplied taxable payment."""

    def __init__(self, rate: Decimal = Decimal("0.15")) -> None:
        self.rate = _validate_rate(rate, "rate")

    def calculate(self, ctx: TaxContext) -> TaxResult:
        _validate_nonnegative(("gross income", ctx.gross_income))
        amount = ctx.gross_income * self.rate
        return TaxResult(amount, {"withheld_from": ctx.gross_income})


class RentalIncomeTax(Tax):
    """Tax individual rental income above the annual UGX 2,820,000 threshold."""

    def __init__(
        self,
        rate: Decimal = Decimal("0.12"),
        annual_threshold: Money = Money(Decimal("2820000")),
    ) -> None:
        self.rate = _validate_rate(rate, "rate")
        self.annual_threshold = annual_threshold

    def calculate(self, ctx: TaxContext) -> TaxResult:
        _validate_nonnegative(("gross rental income", ctx.gross_income))
        chargeable_income = Money(max(ZERO, (ctx.gross_income - self.annual_threshold).amount))
        amount = chargeable_income * self.rate
        return TaxResult(amount, {"chargeable_rental_income": chargeable_income})


def _validate_nonnegative(*amounts: tuple[str, Money]) -> None:
    for label, amount in amounts:
        if amount.amount < ZERO:
            raise InvalidIncomeError(f"{label} cannot be negative")


def _validate_rate(rate: Decimal, name: str) -> Decimal:
    if not isinstance(rate, Decimal):
        raise TypeError(f"{name} must be a Decimal")
    if not ZERO <= rate <= Decimal("1"):
        raise ValueError(f"{name} must be between 0 and 1")
    return rate
