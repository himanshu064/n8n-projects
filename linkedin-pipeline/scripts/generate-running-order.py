"""
Generate a SAMPLE 90-day LinkedIn running order as Excel.

The client will provide the real Excel later — this file lets you build and
test the whole pipeline today with realistic fake data.

Usage:
    python generate-running-order.py [start-date]

    start-date  optional, format YYYY-MM-DD. Default: next Monday.

Output:
    running-order-sample.xlsx  (in this same folder)
    Columns: scheduled_date | process_name | style | sequence_number
"""
import sys
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

# Fake but realistic FullSpec-style business processes to rotate through
PROCESSES = [
    "Client onboarding",
    "Proposal writing",
    "Weekly reporting",
    "Invoice and payment chasing",
    "Meeting notes and follow-ups",
    "Lead qualification",
    "Content repurposing",
    "Customer support triage",
    "Hiring and screening",
    "Project handover",
]

# Post styles to rotate through (client's real style list replaces these)
STYLES = [
    "How-to",
    "Contrarian",
    "Story",
    "Checklist",
    "Myth-busting",
    "Before/After",
    "Question",
]


def next_monday(today: date) -> date:
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def main() -> None:
    if len(sys.argv) > 1:
        start = date.fromisoformat(sys.argv[1])
    else:
        start = next_monday(date.today())

    wb = Workbook()
    ws = wb.active
    ws.title = "running_order"
    ws.append(["scheduled_date", "process_name", "style", "sequence_number"])

    for i in range(90):
        d = start + timedelta(days=i)
        ws.append([
            d.isoformat(),
            PROCESSES[i % len(PROCESSES)],
            STYLES[i % len(STYLES)],
            i + 1,
        ])

    out = Path(__file__).parent / "running-order-sample.xlsx"
    wb.save(out)
    print(f"Wrote {out}")
    print(f"90 rows, {start} .. {start + timedelta(days=89)}")


if __name__ == "__main__":
    main()
