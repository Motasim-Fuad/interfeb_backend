from typing import Optional, Sequence

from fastapi import Response
from sqlalchemy import extract, or_

TOTAL_COUNT_HEADER = "X-Total-Count"
DEFAULT_PAGE_LIMIT = 500
MAX_PAGE_LIMIT = 500


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


def clamp_limit(limit: Optional[int], default: int = DEFAULT_PAGE_LIMIT, maximum: int = MAX_PAGE_LIMIT) -> int:
    if limit is None or int(limit) <= 0:
        return default
    return min(int(limit), maximum)


def count_rows(query) -> int:
    """Count without the ORDER BY / eager loads of the list query."""
    return query.enable_eagerloads(False).order_by(None).count()


def bound_unscoped(query, scoped: bool, response: Optional[Response], limit: Optional[int], offset: int = 0):
    """Report lists filtered by plm_id stay complete; unscoped listings are paged."""
    if scoped:
        return query
    return paginate(query, response, limit, offset, default=50, maximum=200)


def paginate(
    query,
    response: Optional[Response],
    limit: Optional[int],
    offset: int = 0,
    *,
    default: int = DEFAULT_PAGE_LIMIT,
    maximum: int = MAX_PAGE_LIMIT,
    count_query=None,
):
    """Apply a bounded limit/offset and expose the unpaged total in X-Total-Count."""
    if response is not None:
        response.headers[TOTAL_COUNT_HEADER] = str(count_rows(count_query if count_query is not None else query))
    return apply_page(query, limit=clamp_limit(limit, default, maximum), offset=max(0, int(offset or 0)))
