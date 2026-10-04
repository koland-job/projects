"""Employees' salaries and recurring expenses."""
import re
from dataclasses import dataclass

import pandas as pd

from .names import SALARY
from .parse import as_date, number, table, text
from .writes import Writes


@dataclass
class Staff:
    employees: pd.DataFrame   # employee, start_date, end_date, monthly_salary
    recurring: pd.DataFrame   # Operation, Monthly amount, Schedule, days, sheet_row

    def salary_for_half(self, month, first_half: bool) -> float:
        # Each fully worked half of a month is 50% of the monthly salary;
        # a partial half is paid by calendar days worked inside it.
        month_start = pd.Timestamp(month).replace(day=1)
        month_end = month_start + pd.offsets.MonthEnd(0)
        period_start = month_start if first_half else month_start + pd.Timedelta(days=15)
        period_end = month_start + pd.Timedelta(days=14) if first_half else month_end
        period_days = (period_end - period_start).days + 1
        total = 0.0
        for _, person in self.employees.iterrows():
            first_day = max(period_start, person["start_date"])
            last_day = period_end if pd.isna(person["end_date"]) else min(period_end, person["end_date"])
            if first_day <= last_day:
                days_worked = (last_day - first_day).days + 1
                total += person["monthly_salary"] / 2 * days_worked / period_days
        return round(total, 2)

    def salary_for_month(self, month) -> float:
        return round(self.salary_for_half(month, True) + self.salary_for_half(month, False), 2)


def load_staff(values: dict, signs: dict, today: pd.Timestamp, writes: Writes) -> Staff:
    employees = table(values["Staff"], [
        "Employee", "Start date", "End date", "Monthly salary",
    ], "Staff").frame
    employees = employees.rename(columns={
        "Employee": "employee", "Start date": "start_date",
        "End date": "end_date", "Monthly salary": "monthly_salary",
    })
    employees["employee"] = employees["employee"].map(lambda value: text(value).strip())
    if employees["employee"].eq("").any() or employees["employee"].duplicated().any():
        raise ValueError("Staff: every person needs a unique name.")
    for _, person in employees.iterrows():
        if not text(person["start_date"]).strip() or not text(person["monthly_salary"]).strip():
            raise ValueError(f"Staff, row {person['sheet_row']}: start date and salary are required.")
    employees["start_date"] = pd.to_datetime(employees["start_date"].map(as_date))
    employees["end_date"] = pd.to_datetime(employees["end_date"].map(
        lambda value: as_date(value) if text(value).strip() else pd.NaT
    ))
    employees["monthly_salary"] = employees["monthly_salary"].map(number)
    if (employees["monthly_salary"] < 0).any() or (employees["end_date"] < employees["start_date"]).any():
        raise ValueError("Staff: salary cannot be negative, and the end date cannot be before the start date.")

    recurring_table = table(values["Fixed costs"], [
        "Operation", "Monthly amount", "Schedule",
    ], "Fixed costs")
    recurring = recurring_table.frame
    recurring["Operation"] = recurring["Operation"].map(lambda value: text(value).strip())
    if recurring["Operation"].eq("").any() or recurring["Operation"].duplicated().any():
        raise ValueError("Fixed costs: operations must be filled in and not repeated.")
    schedule_days = []
    for _, expense in recurring.iterrows():
        category = expense["Operation"]
        if signs.get(category) != -1:
            raise ValueError(f"Fixed costs, row {expense['sheet_row']}: {category!r} must be an expense from Operation types.")
        period = text(expense["Schedule"]).strip().lower()
        if not re.fullmatch(r"\d{1,2}(?:st|nd|rd|th)(?:\s+and\s+\d{1,2}(?:st|nd|rd|th))*", period):
            raise ValueError(f'Fixed costs, row {expense["sheet_row"]}: enter days of the month, e.g. "15th" or "5th and 20th".')
        days = [int(value) for value in re.findall(r"\d{1,2}", period)]
        if any(day < 1 or day > 31 for day in days) or len(days) != len(set(days)):
            raise ValueError(f"Fixed costs, row {expense['sheet_row']}: days must be different numbers from 1 to 31.")
        schedule_days.append(sorted(days))
    recurring["days"] = schedule_days
    salary_rows = recurring.loc[recurring["Operation"] == SALARY]
    if len(salary_rows) != 1:
        raise ValueError(f'Fixed costs: exactly one "{SALARY}" row is required.')
    if salary_rows.iloc[0]["days"] != [5, 20]:
        raise ValueError('Fixed costs: salary is paid on the "5th and 20th".')

    staff = Staff(employees, recurring)
    salary_now = staff.salary_for_month(today)
    recurring.loc[recurring["Operation"] == SALARY, "Monthly amount"] = salary_now
    writes.put("Fixed costs", salary_rows.iloc[0]["sheet_row"],
               recurring_table.columns["Monthly amount"], salary_now)
    return staff
