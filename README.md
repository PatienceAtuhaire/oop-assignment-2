# OOP with Python: Assignment 2

This repository contains reusable Python implementations for the assignment's two
mini-projects:

1. Open-Meteo weather lookup and forecast display, with a MET Norway second source.
2. An extensible Uganda tax engine using `Decimal`-based UGX amounts.

## Setup

Use Python 3.11 or newer. From the repository root, create and activate a virtual
environment, then install the dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run the offline tests:

```powershell
python -m pytest
```

Both notebooks can be opened and run in VS Code with the Jupyter extension:

- `project1_weather.ipynb`
- `project2_uganda_tax.ipynb`

The weather notebook uses checked-in sample responses by default so that it runs
offline. To query the live services, enable `USE_LIVE_APIS` in its configuration
cell and set `WEATHER_CONTACT_EMAIL` to your own university email address. No
contact details are included in this repository.

## Findings and implementation notes

- The weather pipeline geocodes candidate cities, requires an explicit selection
  for ambiguous names, fetches timezone-aware daily data, and presents labelled
  values and a temperature/precipitation chart. Requests have timeouts, bounded
  exponential retries for connection failures and server errors, and clear
  exceptions on final failure.
- The tax engine reproduces the resident monthly PAYE bands used for this
  educational example, supports a separate non-resident schedule, and computes
  payroll deductions, VAT, corporate income tax, withholding tax, and rental
  income tax. As a worked example, UGX 500,000 gross pay with UGX 25,000
  allowances and UGX 5,000 local service tax gives UGX 44,250 PAYE, UGX 25,000
  employee NSSF, and UGX 450,750 net pay under the example's rate assumptions. Tax rules
  are educational examples, not tax advice; confirm current rates and filing
  treatment with URA before relying on them.
- `Money` uses `Decimal` and rounds to whole Uganda shillings using
  `ROUND_HALF_UP`. The tests exercise the supplied PAYE boundaries and use
  Hypothesis to check monotonicity, non-negativity, and the income ceiling.

## Sources and assumptions

- The resident PAYE bands match URA's
  [Employment Income guidance](https://ura.go.ug/en/employment-income/), which
  labels them as the FY2026/27 schedule. That page also provides the
  non-resident employee bands implemented here. Both schedules are dated
  1 July 2026 in the code and were accessed 24 October 2026.
- The individual rental tax threshold and rate, and the generic withholding
  example rate, are referenced to PwC's
  [Uganda individual tax summary](https://taxsummaries.pwc.com/uganda/individual/taxes-on-personal-income),
  accessed 24 October 2026. This secondary source is not a substitute for
  confirming the applicable year and rules with URA.
- VAT, corporate income tax, and NSSF rates are example assumptions. Verify
  the applicable tax year with URA and NSSF before using this educational
  example for real payroll or returns.
- Weather requests use the [Open-Meteo Geocoding API](https://open-meteo.com/en/docs/geocoding-api),
  [Open-Meteo Forecast API](https://open-meteo.com/en/docs), and
  [MET Norway Locationforecast API](https://api.met.no/weatherapi/locationforecast/2.0/documentation).
  Review each provider's attribution and usage terms before production use.

## AI-use declaration

An AI coding assistant was used to help draft and validate the implementation,
tests, and documentation. 