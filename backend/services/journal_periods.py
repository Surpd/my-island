"""Grade-specific journal period policy without changing stored term numbers."""


def journal_period_count(grade: int) -> int:
    return 2 if int(grade) in (10, 11) else 3


def valid_journal_period(grade: int, term_number: int | None) -> bool:
    return term_number is None or 1 <= int(term_number) <= journal_period_count(grade)


def journal_period_label(grade: int) -> str:
    return "полугодие" if int(grade) in (10, 11) else "триместр"
