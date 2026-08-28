from typing import Optional, Sequence

from sqlalchemy import extract, or_


def apply_text_search(query, model, q: Optional[str], columns: Sequence[str]):
    if not q or not str(q).strip():
        return query
    like = f"%{str(q).strip()}%"
    clauses = [getattr(model, col).ilike(like) for col in columns if hasattr(model, col)]
    return query.filter(or_(*clauses)) if clauses else query


def apply_year_month(query, date_col, year: Optional[int] = None, month: Optional[int] = None):
    if year:
        query = query.filter(extract("year", date_col) == int(year))
    if month:
        query = query.filter(extract("month", date_col) == int(month))
    return query


def apply_page(query, limit: Optional[int] = None, offset: int = 0):
    if offset:
        query = query.offset(int(offset))
    if limit:
        query = query.limit(int(limit))
    return query
