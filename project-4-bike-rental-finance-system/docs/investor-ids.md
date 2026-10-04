# Investor names and permanent IDs

Investors are identified by a number, not by a name. A name is only a label that can change; the number never does.
This note explains how that works in the workbook and why.

## Where the IDs live

The `Investors` tab is the directory: one row per person, with the columns `owner_id` and `Name`.
To rename a person, edit `Name` and keep their `owner_id`. In `Bikes`, the owner is still picked by name from a dropdown.
After **Fleet Ledger → Recalculate reports**, the new name propagates to every linked tab, and the dashboard takes its labels from the directory
on the next refresh.

Service columns, hidden from the owner:

- `Bikes`: `owner_id`, `owner_name_snapshot`
- `Operations`: `owner_id`, `owner_name_snapshot`
- `Bike history`: `owner_id`

In `Investor settlement`, `owner_id` is a visible column to the left of `owner`, so a person can still be traced after a rename.

## How a rename is told apart from a new owner

`owner_name_snapshot` stores the last name the recalculation wrote for that row. It lets the calculation tell two edits apart:

- the label in the directory changed, so the same ID has a new name; or
- someone picked a different investor in the dropdown, so the row now points at another ID.

An unchanged label follows its ID through a rename, even if the old name now belongs to someone else.
In history and in reports the ID is always primary: shares, payouts, debts and carried-over comments are all grouped by ID.

## Adding and removing investors

To add an investor, enter a unique name and leave `owner_id` empty. The next recalculation assigns the next free number, higher than every
`owner_id` ever seen in `Investors`, `Bikes`, `Operations`, `Bike history` and `Investor settlement`. A number that belonged to a removed
investor is therefore never handed to a new one. Until that recalculation runs, the dashboard simply does not show the new investor.

Names are compared case-insensitively and ignoring surrounding spaces.

Never delete or reuse the ID of a person who still has history. An unknown ID, a duplicate, or an unconfirmed name stops the calculation
with an explanation instead of guessing.

## What a rename does not change

Historical dates and shares stay as they were. Only the label of the same ID may be updated. A real change of investor or of a share adds
a new row to `Bike history`. For operations without a time of day, the last history record of the day applies to the whole day, both for the
owner label and for the accrual of their share.

## Tests

The regression tests cover: renaming, picking a different person, reusing an old name, deleting an ID, duplicates, changing a share,
keeping comments, and accruals on the day of a change. They also check that renaming every investor creates no historical events and that a second
run plans no writes.

## Settlement date (optional)

`Investors` may have two optional columns, `Settled on` and `Due at settlement` (empty means 0). They are for investors whose early history is
incomplete.

```
to_be_paid = due at settlement + accruals after the settlement date − payouts after the settlement date
```

Operations on the settlement day itself count as already settled. A settlement does not change revenue, ROI, P&L or Cash flow.
A date in the future, or an amount without a date, stops the calculation.
