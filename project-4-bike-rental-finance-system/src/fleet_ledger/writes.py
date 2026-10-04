"""The write plan: which cells change, compared with what was read."""
import pandas as pd


class Writes:
    """Collects cell writes; only cells whose value differs from the snapshot are kept."""

    def __init__(self, read_values: dict[str, list[list]]):
        self.read_values = read_values
        self.changes: dict[tuple[str, int, int], dict] = {}

    def put(self, sheet: str, row: int, column: int, value, number_format: dict | None = None) -> None:
        if value is None or pd.isna(value):
            value = ""
        row, column = int(row), int(column)
        if row < 1 or column < 1:
            raise ValueError("A write address needs positive row and column numbers.")
        if not isinstance(value, str) and abs(float(value)) == float("inf"):
            raise ValueError(f"{sheet}: an infinite value in the calculation; the write is cancelled.")
        old_rows = self.read_values[sheet]
        old_row = old_rows[row - 1] if row <= len(old_rows) else []
        old_value = old_row[column - 1] if column <= len(old_row) else ""
        if old_value is None:
            old_value = ""
        key = (sheet, row, column)
        numbers = (isinstance(old_value, (int, float)) and not isinstance(old_value, bool)
                   and not isinstance(value, str))
        if old_value == value or (numbers and abs(old_value - float(value)) < 1e-6):
            self.changes.pop(key, None)
            return
        entered = {"stringValue": value} if isinstance(value, str) else {"numberValue": float(value)}
        fields = {"userEnteredValue": entered}
        if number_format:
            fields["userEnteredFormat"] = {"numberFormat": number_format}
        self.changes[key] = fields


def entered_value(fields: dict):
    entered = fields["userEnteredValue"]
    return entered.get("stringValue", entered.get("numberValue"))
