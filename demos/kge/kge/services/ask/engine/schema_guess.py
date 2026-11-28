"""The control arm's name-based schema guess, over already-read columns."""
from __future__ import annotations


def guess_columns(units, words, columns):
    """Choose the first highest-scoring column in each unit, exactly as the serving control arm does."""
    out = []
    for unit in units:
        best = None
        for table, column, dtype in columns[unit]:
            score = sum(1 for word in words if word in column.lower()) + (1 if any(word in table.lower() for word in words) else 0)
            if score and (best is None or score > best[0]):
                best = (score, table, column, dtype)
        if best:
            out.append({'unit': unit, 'tbl': best[1], 'col': best[2], 'dtype': best[3], 'grain': '', 'currency': '',
                        'calendar': '', 'conversion': '', 'certified_by': '', 'certified_on': ''})
    return out
