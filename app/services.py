from __future__ import annotations

import re
from collections import Counter
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from .models import ApprovalStep, Document, Resolution, SystemSetting


DOCUMENT_STATUSES = [
    "Черновик",
    "На согласовании",
    "Согласован",
    "Зарегистрирован",
    "На исполнении",
    "Исполнен",
    "Архив",
    "Отклонен",
]
DOCUMENT_KINDS = ["Входящий", "Исходящий", "Внутренний"]
CONFIDENTIALITY_LEVELS = ["Общий доступ", "Для служебного пользования", "Конфиденциально"]
RESOLUTION_STATUSES = ["Назначена", "В работе", "Исполнена", "Просрочена"]
USER_ROLES = ["Администратор", "Делопроизводитель", "Руководитель", "Согласующий", "Исполнитель", "Контролер"]

DOCUMENT_TRANSITIONS = {
    "Черновик": {"На согласовании", "Зарегистрирован", "Отклонен"},
    "На согласовании": {"Согласован", "Отклонен"},
    "Согласован": {"Зарегистрирован", "На исполнении", "Архив"},
    "Зарегистрирован": {"На согласовании", "На исполнении", "Архив"},
    "На исполнении": {"Исполнен", "Отклонен"},
    "Исполнен": {"Архив"},
    "Отклонен": {"Черновик", "Архив"},
    "Архив": set(),
}

DEFAULT_SETTINGS = {
    "incoming_pattern": "ВХ-{ГОД}-{НОМЕР:4}",
    "outgoing_pattern": "ИСХ-{ГОД}-{НОМЕР:4}",
    "internal_pattern": "ВН-{ГОД}-{НОМЕР:4}",
    "counter_reset": "Ежегодно, 1 января",
    "notify_approval": "1",
    "notify_due": "1",
    "notify_overdue": "1",
    "backup_schedule": "Ежедневно в 02:00",
    "backup_retention": "30 дней",
    "backup_path": "/backup/docflow-rs",
}


def can_transition(current_status: str, target_status: str) -> bool:
    return current_status == target_status or target_status in DOCUMENT_TRANSITIONS.get(current_status, set())


def get_system_settings(db: Session) -> dict[str, str]:
    values = DEFAULT_SETTINGS.copy()
    for item in db.scalars(select(SystemSetting)).all():
        values[item.key] = item.value
    return values


def save_system_settings(db: Session, values: dict[str, str]) -> None:
    for key, value in values.items():
        item = db.get(SystemSetting, key)
        if item is None:
            item = SystemSetting(key=key, value=value)
            db.add(item)
        else:
            item.value = value


def _apply_document_filter(statement, document_ids: set[int] | None, column):
    if document_ids is None:
        return statement
    return statement.where(column.in_(document_ids))


def dashboard_data(
    db: Session,
    today: date,
    document_ids: set[int] | None = None,
    *,
    approver_id: int | None = None,
    resolution_executor_id: int | None = None,
) -> dict:
    document_statement = (
        select(Document)
        .options(joinedload(Document.responsible), joinedload(Document.document_type))
        .order_by(Document.registration_date.desc())
    )
    document_statement = _apply_document_filter(document_statement, document_ids, Document.id)
    documents = db.scalars(document_statement).all()

    resolution_statement = select(Resolution).options(joinedload(Resolution.executor), joinedload(Resolution.document))
    resolution_statement = _apply_document_filter(resolution_statement, document_ids, Resolution.document_id)
    if resolution_executor_id is not None:
        resolution_statement = resolution_statement.where(Resolution.executor_id == resolution_executor_id)
    resolutions = db.scalars(resolution_statement).all()

    approval_statement = (
        select(ApprovalStep)
        .where(ApprovalStep.status == "Ожидает")
        .options(joinedload(ApprovalStep.document), joinedload(ApprovalStep.approver))
    )
    approval_statement = _apply_document_filter(approval_statement, document_ids, ApprovalStep.document_id)
    if approver_id is not None:
        approval_statement = approval_statement.where(ApprovalStep.approver_id == approver_id)
    approvals = db.scalars(approval_statement).all()

    overdue_resolutions = [item for item in resolutions if item.due_date < today and item.status != "Исполнена"]
    status_counts = Counter(item.status for item in documents)
    kind_counts = Counter(item.kind for item in documents)
    return {
        "recent_documents": documents[:6],
        "pending_approvals": approvals[:5],
        "overdue_resolutions": overdue_resolutions[:5],
        "total_documents": len(documents),
        "on_approval": status_counts.get("На согласовании", 0),
        "on_execution": status_counts.get("На исполнении", 0),
        "overdue_count": len(overdue_resolutions),
        "status_counts": status_counts,
        "kind_counts": kind_counts,
    }


def report_data(db: Session, today: date, document_ids: set[int] | None = None) -> dict:
    document_statement = _apply_document_filter(select(Document), document_ids, Document.id)
    resolution_statement = _apply_document_filter(select(Resolution), document_ids, Resolution.document_id)
    documents = db.scalars(document_statement).all()
    resolutions = db.scalars(resolution_statement).all()
    status_counts = Counter(item.status for item in documents)
    kind_counts = Counter(item.kind for item in documents)
    month_counts: Counter[str] = Counter(item.registration_date.strftime("%m.%Y") for item in documents)
    overdue = [item for item in resolutions if item.due_date < today and item.status != "Исполнена"]
    completed = [item for item in resolutions if item.status == "Исполнена"]
    total_resolution_count = len(resolutions)
    execution_rate = round(len(completed) / total_resolution_count * 100, 1) if total_resolution_count else 0
    return {
        "status_counts": status_counts,
        "kind_counts": kind_counts,
        "month_counts": dict(sorted(month_counts.items())),
        "overdue_count": len(overdue),
        "resolution_count": total_resolution_count,
        "execution_rate": execution_rate,
        "documents_count": len(documents),
    }


def next_registration_number(db: Session, kind: str, current_date: date) -> str:
    settings = get_system_settings(db)
    pattern_keys = {
        "Входящий": "incoming_pattern",
        "Исходящий": "outgoing_pattern",
        "Внутренний": "internal_pattern",
    }
    prefixes = {"Входящий": "ВХ", "Исходящий": "ИСХ", "Внутренний": "ВН"}
    year = current_date.year
    count = len(
        db.scalars(
            select(Document).where(
                Document.kind == kind,
                Document.registration_date >= date(year, 1, 1),
                Document.registration_date <= date(year, 12, 31),
            )
        ).all()
    )
    serial = count + 1
    default_pattern = f"{prefixes.get(kind, 'ДОК')}-{{ГОД}}-{{НОМЕР:4}}"
    pattern = settings.get(pattern_keys.get(kind, ""), default_pattern).strip() or default_pattern
    result = pattern.replace("{ГОД}", str(year))
    number_token = re.search(r"\{НОМЕР:(\d+)\}", result)
    if number_token:
        width = max(1, min(int(number_token.group(1)), 12))
        result = result.replace(number_token.group(0), f"{serial:0{width}d}")
    else:
        result = result.replace("{НОМЕР}", str(serial))
    return result
