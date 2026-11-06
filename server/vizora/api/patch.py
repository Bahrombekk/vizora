"""PATCH yordamchisi: NOT NULL ustunga `null` yuborilsa 500 emas, 422.

Ilgari `{"name": null}` kabi PATCH to'g'ridan `setattr` qilinardi va commit'da
IntegrityError -> 500 bo'lardi (audit 2026-11-03, Low): xato matni log'ga stack
bilan tushardi va mijoz nima noto'g'ri ekanini bilmasdi.
"""
from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import inspect as sa_inspect


def reject_null_columns(obj, data: dict) -> None:
    """`data` da None qiymatli, lekin modelda NOT NULL bo'lgan maydon bo'lsa 422."""
    cols = sa_inspect(type(obj)).columns
    bad = [k for k, v in data.items() if v is None and k in cols and not cols[k].nullable]
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Bu maydonlar bo'sh (null) bo'lishi mumkin emas: {', '.join(sorted(bad))}")


def apply_patch(obj, data: dict) -> None:
    reject_null_columns(obj, data)
    for k, v in data.items():
        setattr(obj, k, v)
