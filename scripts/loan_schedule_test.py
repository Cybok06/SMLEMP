from datetime import datetime

from accounting_routes.loans import _build_amortization_schedule


def _run_case(label, **kwargs):
    schedule, payment, total_interest, total_payable = _build_amortization_schedule(**kwargs)
    print(f"\n=== {label} ===")
    print(f"payment: {payment:.2f} | total_interest: {total_interest:.2f} | total_payable: {total_payable:.2f}")
    print(
        "periods: "
        + str(len(schedule))
        + " | first_due: "
        + str(schedule[0]["period_date_dt"].date())
        + " | last_due: "
        + str(schedule[-1]["period_date_dt"].date())
    )


def main():
    start_jan31 = datetime(2026, 1, 31)

    _run_case(
        "Monthly reducing balance (Jan 31)",
        principal=10000,
        annual_rate=24,
        term_months=12,
        start_date=start_jan31,
        amortization_method="reducing_balance",
        payment_frequency="monthly",
        interest_compounding="monthly",
        day_count_basis="30/360",
    )

    _run_case(
        "Weekly reducing balance",
        principal=8000,
        annual_rate=20,
        term_months=6,
        start_date=datetime(2026, 2, 1),
        amortization_method="reducing_balance",
        payment_frequency="weekly",
        interest_compounding="daily",
        day_count_basis="actual/365",
    )

    _run_case(
        "Flat interest",
        principal=15000,
        annual_rate=18,
        term_months=10,
        start_date=datetime(2026, 3, 15),
        amortization_method="flat",
        payment_frequency="monthly",
        interest_compounding="monthly",
        day_count_basis="30/360",
    )

    _run_case(
        "Interest only",
        principal=5000,
        annual_rate=30,
        term_months=8,
        start_date=datetime(2026, 4, 5),
        amortization_method="interest_only",
        payment_frequency="monthly",
        interest_compounding="monthly",
        day_count_basis="30/360",
    )


if __name__ == "__main__":
    main()
