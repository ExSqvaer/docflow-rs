from __future__ import annotations

import csv
import io
import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload
from starlette.middleware.sessions import SessionMiddleware

from .database import Base, SessionLocal, engine, get_db
from .models import ApprovalStep, AuditEvent, Counterparty, Document, DocumentType, Resolution, User
from .security import can, hash_password, verify_password
from .seed import seed_database
from .services import (
    CONFIDENTIALITY_LEVELS,
    DOCUMENT_KINDS,
    DOCUMENT_STATUSES,
    RESOLUTION_STATUSES,
    USER_ROLES,
    can_transition,
    dashboard_data,
    get_system_settings,
    next_registration_number,
    report_data,
    save_system_settings,
)

BASE_DIR = Path(__file__).resolve().parent
TODAY = date(2026, 8, 14)
SESSION_SECRET = os.getenv("SESSION_SECRET", "docflow-rs-local-demo-change-me")


class AuthenticationRequired(Exception):
    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        seed_database(db)
    yield


app = FastAPI(title="DocFlow RS", version="1.2.0", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    session_cookie="docflow_session",
    max_age=8 * 60 * 60,
    same_site="lax",
    https_only=os.getenv("COOKIE_HTTPS_ONLY", "0") == "1",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.exception_handler(AuthenticationRequired)
async def authentication_required_handler(request: Request, exc: AuthenticationRequired):
    return RedirectResponse(f"/login?next={request.url.path}", status_code=303)


def get_current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not isinstance(user_id, int):
        return None
    user = db.get(User, user_id)
    if not user or not user.active:
        request.session.clear()
        return None
    return user


def require_user(request: Request, db: Session) -> User:
    user = get_current_user(request, db)
    if not user:
        raise AuthenticationRequired()
    return user


def require_permission(user: User, permission_code: str) -> None:
    if not can(user.role, permission_code):
        raise HTTPException(status_code=403, detail="Недостаточно прав для выполнения операции")


def can_view_document(user: User, document: Document, db: Session | None = None) -> bool:
    if document.confidentiality != "Конфиденциально":
        return True
    if user.role in {"Администратор", "Руководитель", "Делопроизводитель"}:
        return True
    if user.id in {document.author_id, document.responsible_id}:
        return True
    if db is not None:
        assigned_approval = db.scalar(
            select(ApprovalStep.id).where(
                ApprovalStep.document_id == document.id,
                ApprovalStep.approver_id == user.id,
            ).limit(1)
        )
        if assigned_approval:
            return True
        assigned_resolution = db.scalar(
            select(Resolution.id).where(
                Resolution.document_id == document.id,
                Resolution.executor_id == user.id,
            ).limit(1)
        )
        if assigned_resolution:
            return True
    return False


def visible_document_ids(db: Session, user: User) -> set[int]:
    documents = db.scalars(select(Document)).all()
    return {item.id for item in documents if can_view_document(user, item, db)}


def can_view_resolutions(user: User) -> bool:
    return user.role in {"Администратор", "Руководитель", "Делопроизводитель", "Исполнитель", "Контролер"}


def common_context(request: Request, db: Session, current_user: User | None = None) -> dict:
    current_user = current_user or require_user(request, db)
    return {
        "request": request,
        "current_user": current_user,
        "today": TODAY,
        "document_statuses": DOCUMENT_STATUSES,
        "document_kinds": DOCUMENT_KINDS,
        "confidentiality_levels": CONFIDENTIALITY_LEVELS,
        "resolution_statuses": RESOLUTION_STATUSES,
        "can_create_document": can(current_user.role, "document.create"),
        "can_edit_document": can(current_user.role, "document.edit"),
        "can_approve": can(current_user.role, "document.approve"),
        "can_manage_resolutions": can(current_user.role, "resolution.manage"),
        "can_execute_resolutions": can(current_user.role, "resolution.execute"),
        "can_view_resolutions": can_view_resolutions(current_user),
        "can_manage_directories": can(current_user.role, "directory.manage"),
        "can_manage_users": can(current_user.role, "user.manage"),
        "can_view_audit": can(current_user.role, "audit.view"),
        "can_manage_settings": can(current_user.role, "settings.manage"),
    }


def audit(
    db: Session,
    user: User,
    action: str,
    entity_type: str,
    entity_id: int,
    entity_name: str,
    details: str = "",
) -> None:
    db.add(
        AuditEvent(
            user_id=user.id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            details=details,
        )
    )


def csv_response(filename: str, rows: list[list[object]]) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerows(rows)
    payload = "\ufeff" + buffer.getvalue()
    return Response(
        payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def active_document_choices(db: Session) -> dict:
    return {
        "types": db.scalars(select(DocumentType).order_by(DocumentType.name)).all(),
        "counterparties": db.scalars(
            select(Counterparty).where(Counterparty.active.is_(True)).order_by(Counterparty.name)
        ).all(),
        "users": db.scalars(select(User).where(User.active.is_(True)).order_by(User.full_name)).all(),
        "approvers": [
            item
            for item in db.scalars(select(User).where(User.active.is_(True)).order_by(User.full_name)).all()
            if can(item.role, "document.approve")
        ],
        "executors": [
            item
            for item in db.scalars(select(User).where(User.active.is_(True)).order_by(User.full_name)).all()
            if can(item.role, "resolution.execute")
        ],
    }


def validate_document_values(
    db: Session,
    *,
    title: str,
    document_type_id: int,
    counterparty_id: str,
    registration_date: date,
    due_date: str,
    responsible_id: int,
    confidentiality: str,
) -> tuple[date | None, int | None]:
    if not title.strip():
        raise HTTPException(400, "Заголовок документа обязателен")
    if confidentiality not in CONFIDENTIALITY_LEVELS:
        raise HTTPException(400, "Недопустимый уровень конфиденциальности")
    parsed_due_date = date.fromisoformat(due_date) if due_date else None
    if parsed_due_date and parsed_due_date < registration_date:
        raise HTTPException(400, "Срок исполнения не может быть раньше даты регистрации")
    if not db.get(DocumentType, document_type_id):
        raise HTTPException(400, "Неизвестный тип документа")
    responsible = db.get(User, responsible_id)
    if not responsible or not responsible.active:
        raise HTTPException(400, "Ответственный пользователь недоступен")
    parsed_counterparty_id = int(counterparty_id) if counterparty_id else None
    if parsed_counterparty_id is not None:
        counterparty = db.get(Counterparty, parsed_counterparty_id)
        if not counterparty or not counterparty.active:
            raise HTTPException(400, "Контрагент недоступен")
    return parsed_due_date, parsed_counterparty_id


def approval_block_reason(db: Session, step: ApprovalStep) -> str:
    if step.status != "Ожидает":
        return ""
    if step.document.status != "На согласовании":
        return "Маршрут сейчас не активен"
    previous_pending = db.scalar(
        select(ApprovalStep.id).where(
            ApprovalStep.document_id == step.document_id,
            ApprovalStep.order_num < step.order_num,
            ApprovalStep.status != "Согласовано",
        ).limit(1)
    )
    if previous_pending:
        return "Ожидает завершения предыдущего этапа"
    return ""


def initials_from_name(full_name: str) -> str:
    parts = [part for part in full_name.strip().split() if part]
    return "".join(part[0].upper() for part in parts[:2]) or "П"


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/dashboard", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "", db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse("/dashboard", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "", "next": next})


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    normalized_email = email.strip().lower()
    user = db.scalar(select(User).where(User.email == normalized_email))
    if not user or not user.active or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Неверные учетные данные или учетная запись недоступна.",
                "email": email,
                "next": next,
            },
            status_code=401,
        )
    request.session.clear()
    request.session["user_id"] = user.id
    audit(db, user, "вошел в систему", "Сессия", user.id, user.email)
    db.commit()
    target = next if next.startswith("/") and not next.startswith("//") else "/dashboard"
    return RedirectResponse(target, status_code=303)


@app.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if user:
        audit(db, user, "вышел из системы", "Сессия", user.id, user.email)
        db.commit()
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    ids = visible_document_ids(db, user)
    context = common_context(request, db, user)
    context.update(
        dashboard_data(
            db,
            TODAY,
            ids,
            approver_id=user.id if can(user.role, "document.approve") else -1,
            resolution_executor_id=user.id if user.role == "Исполнитель" else None,
        )
    )
    return templates.TemplateResponse("dashboard.html", context)


@app.get("/documents", response_class=HTMLResponse)
def document_list(
    request: Request,
    query: str = "",
    kind: str = "",
    status: str = "",
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    statement = (
        select(Document)
        .options(joinedload(Document.document_type), joinedload(Document.counterparty), joinedload(Document.responsible))
        .order_by(Document.registration_date.desc(), Document.id.desc())
    )
    if query:
        pattern = f"%{query}%"
        statement = statement.where(
            or_(
                Document.registration_number.ilike(pattern),
                Document.title.ilike(pattern),
                Document.external_number.ilike(pattern),
            )
        )
    if kind:
        statement = statement.where(Document.kind == kind)
    if status:
        statement = statement.where(Document.status == status)
    items = [item for item in db.scalars(statement).unique().all() if can_view_document(user, item, db)]
    context = common_context(request, db, user)
    context.update({"documents": items, "query": query, "selected_kind": kind, "selected_status": status})
    return templates.TemplateResponse("documents.html", context)


@app.get("/documents/export.csv")
def export_documents(
    request: Request,
    query: str = "",
    kind: str = "",
    status: str = "",
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    statement = select(Document).order_by(Document.registration_date.desc(), Document.id.desc())
    if query:
        pattern = f"%{query}%"
        statement = statement.where(
            or_(
                Document.registration_number.ilike(pattern),
                Document.title.ilike(pattern),
                Document.external_number.ilike(pattern),
            )
        )
    if kind:
        statement = statement.where(Document.kind == kind)
    if status:
        statement = statement.where(Document.status == status)
    documents = [item for item in db.scalars(statement).all() if can_view_document(user, item, db)]
    rows: list[list[object]] = [["Номер", "Дата", "Вид", "Статус", "Заголовок", "Срок"]]
    rows.extend(
        [
            item.registration_number,
            item.registration_date.isoformat(),
            item.kind,
            item.status,
            item.title,
            item.due_date.isoformat() if item.due_date else "",
        ]
        for item in documents
    )
    return csv_response("documents.csv", rows)


@app.get("/documents/new", response_class=HTMLResponse)
def document_form(request: Request, kind: str = "Входящий", db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_permission(user, "document.create")
    context = common_context(request, db, user)
    context.update(active_document_choices(db))
    context.update({"selected_kind": kind if kind in DOCUMENT_KINDS else "Входящий", "document": None, "editing": False})
    return templates.TemplateResponse("document_form.html", context)


@app.post("/documents/new")
def create_document(
    request: Request,
    title: str = Form(...),
    kind: str = Form(...),
    document_type_id: int = Form(...),
    counterparty_id: str = Form(""),
    external_number: str = Form(""),
    registration_date: date = Form(...),
    due_date: str = Form(""),
    responsible_id: int = Form(...),
    confidentiality: str = Form(...),
    content: str = Form(""),
    file_name: str = Form(""),
    action: str = Form("register"),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_permission(user, "document.create")
    if kind not in DOCUMENT_KINDS:
        raise HTTPException(400, "Недопустимый вид документа")
    if action not in {"draft", "register"}:
        raise HTTPException(400, "Недопустимое действие")
    parsed_due_date, parsed_counterparty_id = validate_document_values(
        db,
        title=title,
        document_type_id=document_type_id,
        counterparty_id=counterparty_id,
        registration_date=registration_date,
        due_date=due_date,
        responsible_id=responsible_id,
        confidentiality=confidentiality,
    )
    registration_number = next_registration_number(db, kind, registration_date)
    document = Document(
        registration_number=registration_number,
        title=title.strip(),
        kind=kind,
        status="Черновик" if action == "draft" else "Зарегистрирован",
        confidentiality=confidentiality,
        external_number=external_number.strip(),
        registration_date=registration_date,
        due_date=parsed_due_date,
        content=content.strip(),
        file_name=file_name.strip(),
        document_type_id=document_type_id,
        counterparty_id=parsed_counterparty_id,
        author_id=user.id,
        responsible_id=responsible_id,
    )
    db.add(document)
    db.flush()
    action_text = "сохранил черновик" if action == "draft" else "создал документ"
    audit(db, user, action_text, "Документ", document.id, document.registration_number, document.title)
    db.commit()
    return RedirectResponse(f"/documents/{document.id}", status_code=303)


@app.get("/documents/{document_id}/edit", response_class=HTMLResponse)
def edit_document_form(document_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_permission(user, "document.edit")
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Документ не найден")
    if not can_view_document(user, document, db):
        raise HTTPException(403, "Недостаточно прав для просмотра документа")
    context = common_context(request, db, user)
    context.update(active_document_choices(db))
    context.update({"selected_kind": document.kind, "document": document, "editing": True})
    return templates.TemplateResponse("document_form.html", context)


@app.post("/documents/{document_id}/edit")
def edit_document(
    document_id: int,
    request: Request,
    title: str = Form(...),
    document_type_id: int = Form(...),
    counterparty_id: str = Form(""),
    external_number: str = Form(""),
    due_date: str = Form(""),
    responsible_id: int = Form(...),
    confidentiality: str = Form(...),
    content: str = Form(""),
    file_name: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_permission(user, "document.edit")
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Документ не найден")
    if not can_view_document(user, document, db):
        raise HTTPException(403, "Недостаточно прав для просмотра документа")
    parsed_due_date, parsed_counterparty_id = validate_document_values(
        db,
        title=title,
        document_type_id=document_type_id,
        counterparty_id=counterparty_id,
        registration_date=document.registration_date,
        due_date=due_date,
        responsible_id=responsible_id,
        confidentiality=confidentiality,
    )
    document.title = title.strip()
    document.document_type_id = document_type_id
    document.counterparty_id = parsed_counterparty_id
    document.external_number = external_number.strip()
    document.due_date = parsed_due_date
    document.responsible_id = responsible_id
    document.confidentiality = confidentiality
    document.content = content.strip()
    document.file_name = file_name.strip()
    document.version += 1
    document.updated_at = datetime.utcnow()
    audit(db, user, "отредактировал документ", "Документ", document.id, document.registration_number, document.title)
    db.commit()
    return RedirectResponse(f"/documents/{document.id}", status_code=303)


@app.get("/documents/{document_id}", response_class=HTMLResponse)
def document_detail(document_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    document = db.scalar(
        select(Document)
        .where(Document.id == document_id)
        .options(
            joinedload(Document.document_type),
            joinedload(Document.counterparty),
            joinedload(Document.author),
            joinedload(Document.responsible),
            joinedload(Document.approval_steps).joinedload(ApprovalStep.approver),
            joinedload(Document.resolutions).joinedload(Resolution.executor),
            joinedload(Document.attachments),
        )
    )
    if not document:
        raise HTTPException(404, "Документ не найден")
    if not can_view_document(user, document, db):
        raise HTTPException(403, "Недостаточно прав для просмотра документа")
    choices = active_document_choices(db)
    context = common_context(request, db, user)
    context.update(
        {
            "document": document,
            "users": choices["users"],
            "approvers": choices["approvers"],
            "executors": choices["executors"],
            "available_document_statuses": [
                status for status in DOCUMENT_STATUSES if can_transition(document.status, status)
            ],
        }
    )
    return templates.TemplateResponse("document_detail.html", context)


@app.post("/documents/{document_id}/status")
def change_document_status(document_id: int, request: Request, status: str = Form(...), db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_permission(user, "document.edit")
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Документ не найден")
    if status not in DOCUMENT_STATUSES:
        raise HTTPException(400, "Недопустимый статус")
    old_status = document.status
    if not can_transition(old_status, status):
        raise HTTPException(409, f"Переход {old_status} → {status} не разрешен")
    document.status = status
    document.updated_at = datetime.utcnow()
    audit(db, user, f"изменил статус: {old_status} → {status}", "Документ", document.id, document.registration_number)
    db.commit()
    return RedirectResponse(f"/documents/{document_id}", status_code=303)


@app.post("/documents/{document_id}/approval")
def add_approval_step(document_id: int, request: Request, approver_id: int = Form(...), db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_permission(user, "document.edit")
    document = db.scalar(select(Document).where(Document.id == document_id).options(joinedload(Document.approval_steps)))
    if not document:
        raise HTTPException(404, "Документ не найден")
    approver = db.get(User, approver_id)
    if not approver or not approver.active or not can(approver.role, "document.approve"):
        raise HTTPException(400, "Указанный пользователь не может согласовывать документы")
    order_num = max((item.order_num for item in document.approval_steps), default=0) + 1
    db.add(ApprovalStep(document_id=document_id, approver_id=approver_id, order_num=order_num, status="Ожидает"))
    document.status = "На согласовании"
    audit(db, user, "добавил этап согласования", "Документ", document.id, document.registration_number, approver.full_name)
    db.commit()
    return RedirectResponse(f"/documents/{document_id}", status_code=303)


@app.get("/approvals", response_class=HTMLResponse)
def approval_queue(request: Request, tab: str = "pending", db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_permission(user, "document.approve")
    tab_statuses = {"pending": "Ожидает", "approved": "Согласовано", "rejected": "Отклонено"}
    if tab not in tab_statuses:
        tab = "pending"
    all_items = db.scalars(
        select(ApprovalStep)
        .where(ApprovalStep.approver_id == user.id)
        .options(joinedload(ApprovalStep.document).joinedload(Document.document_type), joinedload(ApprovalStep.approver))
        .order_by(ApprovalStep.id.desc())
    ).unique().all()
    visible_items = [item for item in all_items if can_view_document(user, item.document, db)]
    items = [item for item in visible_items if item.status == tab_statuses[tab]]
    block_reasons = {item.id: approval_block_reason(db, item) for item in items}
    counts = {
        "pending": sum(item.status == "Ожидает" for item in visible_items),
        "approved": sum(item.status == "Согласовано" for item in visible_items),
        "rejected": sum(item.status == "Отклонено" for item in visible_items),
    }
    context = common_context(request, db, user)
    context.update({"approvals": items, "approval_tab": tab, "approval_counts": counts, "block_reasons": block_reasons})
    return templates.TemplateResponse("approvals.html", context)


@app.post("/approvals/{approval_id}/decision")
def approval_decision(
    approval_id: int,
    request: Request,
    decision: str = Form(...),
    comment: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_permission(user, "document.approve")
    step = db.scalar(select(ApprovalStep).where(ApprovalStep.id == approval_id).options(joinedload(ApprovalStep.document)))
    if not step:
        raise HTTPException(404, "Этап согласования не найден")
    if step.approver_id != user.id:
        raise HTTPException(403, "Нельзя принять решение по чужому этапу согласования")
    if step.document.status != "На согласовании":
        raise HTTPException(409, "Документ не находится на согласовании")
    if step.status != "Ожидает":
        raise HTTPException(409, "Решение по этапу уже принято")
    if approval_block_reason(db, step):
        raise HTTPException(409, "Предыдущий этап согласования еще не завершен")
    if decision not in {"Согласовано", "Отклонено"}:
        raise HTTPException(400, "Недопустимое решение")
    if decision == "Отклонено" and not comment.strip():
        raise HTTPException(400, "Для отклонения необходимо указать комментарий")

    step.status = decision
    step.comment = comment.strip()
    step.decision_at = datetime.utcnow()
    step.document.status = "Отклонен" if decision == "Отклонено" else "На согласовании"
    if decision == "Согласовано":
        pending = db.scalar(
            select(ApprovalStep.id).where(
                ApprovalStep.document_id == step.document_id,
                ApprovalStep.id != step.id,
                ApprovalStep.status == "Ожидает",
            ).limit(1)
        )
        if not pending:
            step.document.status = "Согласован"
    audit(db, user, f"принял решение «{decision}»", "Документ", step.document.id, step.document.registration_number, step.comment)
    db.commit()
    return RedirectResponse("/approvals?tab=pending", status_code=303)


@app.get("/resolutions", response_class=HTMLResponse)
def resolution_list(request: Request, status: str = "", db: Session = Depends(get_db)):
    user = require_user(request, db)
    if not can_view_resolutions(user):
        raise HTTPException(403, "Недостаточно прав для просмотра поручений")
    statement = (
        select(Resolution)
        .options(joinedload(Resolution.document), joinedload(Resolution.author), joinedload(Resolution.executor))
        .order_by(Resolution.due_date)
    )
    if user.role == "Исполнитель":
        statement = statement.where(Resolution.executor_id == user.id)
    if status:
        statement = statement.where(Resolution.status == status)
    items = [item for item in db.scalars(statement).unique().all() if can_view_document(user, item.document, db)]
    editable_resolution_ids = {
        item.id
        for item in items
        if can(user.role, "resolution.manage") or (can(user.role, "resolution.execute") and item.executor_id == user.id)
    }
    context = common_context(request, db, user)
    context.update({"resolutions": items, "selected_status": status, "editable_resolution_ids": editable_resolution_ids})
    return templates.TemplateResponse("resolutions.html", context)


@app.post("/documents/{document_id}/resolution")
def add_resolution(
    document_id: int,
    request: Request,
    executor_id: int = Form(...),
    text: str = Form(...),
    due_date: date = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_permission(user, "resolution.manage")
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, "Документ не найден")
    if due_date < document.registration_date:
        raise HTTPException(400, "Срок поручения не может быть раньше даты регистрации документа")
    executor = db.get(User, executor_id)
    if not executor or not executor.active or not can(executor.role, "resolution.execute"):
        raise HTTPException(400, "Указанный пользователь не может быть исполнителем")
    if not text.strip():
        raise HTTPException(400, "Текст поручения обязателен")
    resolution = Resolution(
        document_id=document_id,
        author_id=user.id,
        executor_id=executor_id,
        text=text.strip(),
        due_date=due_date,
        status="Назначена",
    )
    document.status = "На исполнении"
    db.add(resolution)
    db.flush()
    audit(db, user, "создал резолюцию", "Резолюция", resolution.id, document.registration_number, resolution.text)
    db.commit()
    return RedirectResponse(f"/documents/{document_id}", status_code=303)


@app.post("/resolutions/{resolution_id}/status")
def change_resolution_status(resolution_id: int, request: Request, status: str = Form(...), db: Session = Depends(get_db)):
    user = require_user(request, db)
    resolution = db.scalar(select(Resolution).where(Resolution.id == resolution_id).options(joinedload(Resolution.document)))
    if not resolution:
        raise HTTPException(404, "Резолюция не найдена")
    if not (can(user.role, "resolution.manage") or (can(user.role, "resolution.execute") and resolution.executor_id == user.id)):
        raise HTTPException(403, "Нельзя изменять чужое поручение")
    if status not in RESOLUTION_STATUSES:
        raise HTTPException(400, "Недопустимый статус")
    resolution.status = status
    resolution.completed_at = datetime.utcnow() if status == "Исполнена" else None
    if status == "Исполнена":
        active = db.scalar(
            select(Resolution.id).where(
                Resolution.document_id == resolution.document_id,
                Resolution.id != resolution.id,
                Resolution.status != "Исполнена",
            ).limit(1)
        )
        if not active:
            resolution.document.status = "Исполнен"
    audit(db, user, f"изменил статус резолюции на «{status}»", "Резолюция", resolution.id, resolution.document.registration_number)
    db.commit()
    return RedirectResponse("/resolutions", status_code=303)


@app.get("/reports", response_class=HTMLResponse)
def reports(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    ids = visible_document_ids(db, user)
    context = common_context(request, db, user)
    context.update(report_data(db, TODAY, ids))
    return templates.TemplateResponse("reports.html", context)


@app.get("/reports/export.csv")
def export_report(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    documents = [
        item
        for item in db.scalars(select(Document).order_by(Document.registration_date)).all()
        if can_view_document(user, item, db)
    ]
    rows: list[list[object]] = [["Номер", "Дата", "Вид", "Статус", "Заголовок"]]
    rows.extend([item.registration_number, item.registration_date, item.kind, item.status, item.title] for item in documents)
    return csv_response("report-documents.csv", rows)


@app.get("/users", response_class=HTMLResponse)
def user_list(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_permission(user, "user.manage")
    items = db.scalars(select(User).order_by(User.department, User.full_name)).all()
    context = common_context(request, db, user)
    context["users"] = items
    return templates.TemplateResponse("users.html", context)


@app.get("/users/new", response_class=HTMLResponse)
def new_user_form(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_permission(user, "user.manage")
    context = common_context(request, db, user)
    context.update({"edited_user": None, "roles": USER_ROLES, "editing": False})
    return templates.TemplateResponse("user_form.html", context)


@app.post("/users/new")
def create_user(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    role: str = Form(...),
    department: str = Form(...),
    initials: str = Form(""),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    actor = require_user(request, db)
    require_permission(actor, "user.manage")
    normalized_email = email.strip().lower()
    if role not in USER_ROLES:
        raise HTTPException(400, "Неизвестная роль")
    if not full_name.strip() or not department.strip() or not normalized_email:
        raise HTTPException(400, "ФИО, e-mail и подразделение обязательны")
    if len(password) < 6:
        raise HTTPException(400, "Пароль должен содержать не менее 6 символов")
    if db.scalar(select(User.id).where(User.email == normalized_email)):
        raise HTTPException(409, "Пользователь с таким e-mail уже существует")
    item = User(
        full_name=full_name.strip(),
        email=normalized_email,
        role=role,
        password_hash=hash_password(password),
        department=department.strip(),
        initials=(initials.strip().upper() or initials_from_name(full_name))[:8],
        active=True,
    )
    db.add(item)
    db.flush()
    audit(db, actor, "создал пользователя", "Пользователь", item.id, item.email, item.full_name)
    db.commit()
    return RedirectResponse("/users", status_code=303)


@app.get("/users/{user_id}/edit", response_class=HTMLResponse)
def edit_user_form(user_id: int, request: Request, db: Session = Depends(get_db)):
    actor = require_user(request, db)
    require_permission(actor, "user.manage")
    edited_user = db.get(User, user_id)
    if not edited_user:
        raise HTTPException(404, "Пользователь не найден")
    context = common_context(request, db, actor)
    context.update({"edited_user": edited_user, "roles": USER_ROLES, "editing": True})
    return templates.TemplateResponse("user_form.html", context)


@app.post("/users/{user_id}/edit")
def edit_user(
    user_id: int,
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    role: str = Form(...),
    department: str = Form(...),
    initials: str = Form(""),
    password: str = Form(""),
    active: str = Form("1"),
    db: Session = Depends(get_db),
):
    actor = require_user(request, db)
    require_permission(actor, "user.manage")
    edited_user = db.get(User, user_id)
    if not edited_user:
        raise HTTPException(404, "Пользователь не найден")
    normalized_email = email.strip().lower()
    duplicate = db.scalar(select(User.id).where(User.email == normalized_email, User.id != user_id))
    if duplicate:
        raise HTTPException(409, "Пользователь с таким e-mail уже существует")
    if role not in USER_ROLES:
        raise HTTPException(400, "Неизвестная роль")
    is_active = active == "1"
    if actor.id == edited_user.id and not is_active:
        raise HTTPException(400, "Нельзя деактивировать собственную учетную запись")
    edited_user.full_name = full_name.strip()
    edited_user.email = normalized_email
    edited_user.role = role
    edited_user.department = department.strip()
    edited_user.initials = (initials.strip().upper() or initials_from_name(full_name))[:8]
    edited_user.active = is_active
    if password:
        if len(password) < 6:
            raise HTTPException(400, "Новый пароль должен содержать не менее 6 символов")
        edited_user.password_hash = hash_password(password)
    audit(db, actor, "изменил пользователя", "Пользователь", edited_user.id, edited_user.email, edited_user.full_name)
    db.commit()
    return RedirectResponse("/users", status_code=303)


@app.get("/dictionaries", response_class=HTMLResponse)
def dictionaries(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_permission(user, "directory.manage")
    context = common_context(request, db, user)
    context.update(
        {
            "types": db.scalars(select(DocumentType).order_by(DocumentType.name)).all(),
            "counterparties": db.scalars(select(Counterparty).order_by(Counterparty.name)).all(),
        }
    )
    return templates.TemplateResponse("dictionaries.html", context)


@app.get("/dictionaries/types/new", response_class=HTMLResponse)
def new_document_type_form(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_permission(user, "directory.manage")
    context = common_context(request, db, user)
    context.update({"dictionary_kind": "type"})
    return templates.TemplateResponse("dictionary_form.html", context)


@app.post("/dictionaries/types/new")
def create_document_type(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    default_route: str = Form(""),
    retention_years: int = Form(5),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_permission(user, "directory.manage")
    normalized_code = code.strip().upper()
    if not normalized_code or not name.strip():
        raise HTTPException(400, "Код и название обязательны")
    if retention_years < 1 or retention_years > 100:
        raise HTTPException(400, "Срок хранения должен быть от 1 до 100 лет")
    if db.scalar(select(DocumentType.id).where(or_(DocumentType.code == normalized_code, DocumentType.name == name.strip()))):
        raise HTTPException(409, "Такой тип документа уже существует")
    item = DocumentType(code=normalized_code, name=name.strip(), default_route=default_route.strip(), retention_years=retention_years)
    db.add(item)
    db.flush()
    audit(db, user, "добавил тип документа", "Справочник", item.id, item.code, item.name)
    db.commit()
    return RedirectResponse("/dictionaries", status_code=303)


@app.get("/dictionaries/counterparties/new", response_class=HTMLResponse)
def new_counterparty_form(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_permission(user, "directory.manage")
    context = common_context(request, db, user)
    context.update({"dictionary_kind": "counterparty"})
    return templates.TemplateResponse("dictionary_form.html", context)


@app.post("/dictionaries/counterparties/new")
def create_counterparty(
    request: Request,
    name: str = Form(...),
    inn: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_permission(user, "directory.manage")
    if not name.strip():
        raise HTTPException(400, "Наименование контрагента обязательно")
    if db.scalar(select(Counterparty.id).where(Counterparty.name == name.strip())):
        raise HTTPException(409, "Такой контрагент уже существует")
    item = Counterparty(name=name.strip(), inn=inn.strip(), email=email.strip(), phone=phone.strip(), active=True)
    db.add(item)
    db.flush()
    audit(db, user, "добавил контрагента", "Справочник", item.id, item.name, item.inn)
    db.commit()
    return RedirectResponse("/dictionaries", status_code=303)


@app.get("/audit", response_class=HTMLResponse)
def audit_log(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_permission(user, "audit.view")
    items = db.scalars(select(AuditEvent).options(joinedload(AuditEvent.user)).order_by(AuditEvent.created_at.desc())).all()
    context = common_context(request, db, user)
    context["events"] = items
    return templates.TemplateResponse("audit.html", context)


@app.get("/audit/export.csv")
def export_audit(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_permission(user, "audit.view")
    events = db.scalars(select(AuditEvent).options(joinedload(AuditEvent.user)).order_by(AuditEvent.created_at.desc())).all()
    rows: list[list[object]] = [["Дата и время", "Пользователь", "Действие", "Тип объекта", "Объект", "Детали"]]
    rows.extend(
        [item.created_at.isoformat(sep=" ", timespec="seconds"), item.user.full_name, item.action, item.entity_type, item.entity_name, item.details]
        for item in events
    )
    return csv_response("audit.csv", rows)


@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request, saved: int = 0, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_permission(user, "settings.manage")
    context = common_context(request, db, user)
    context.update({"settings": get_system_settings(db), "settings_saved": bool(saved)})
    return templates.TemplateResponse("settings.html", context)


@app.post("/settings")
def save_settings(
    request: Request,
    incoming_pattern: str = Form(...),
    outgoing_pattern: str = Form(...),
    internal_pattern: str = Form(...),
    counter_reset: str = Form(...),
    notify_approval: str | None = Form(None),
    notify_due: str | None = Form(None),
    notify_overdue: str | None = Form(None),
    backup_schedule: str = Form(...),
    backup_retention: str = Form(...),
    backup_path: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_permission(user, "settings.manage")
    patterns = [incoming_pattern, outgoing_pattern, internal_pattern]
    if any("{ГОД}" not in item or "{НОМЕР" not in item for item in patterns):
        raise HTTPException(400, "Шаблон номера должен содержать {ГОД} и {НОМЕР:4}")
    values = {
        "incoming_pattern": incoming_pattern.strip(),
        "outgoing_pattern": outgoing_pattern.strip(),
        "internal_pattern": internal_pattern.strip(),
        "counter_reset": counter_reset.strip(),
        "notify_approval": "1" if notify_approval else "0",
        "notify_due": "1" if notify_due else "0",
        "notify_overdue": "1" if notify_overdue else "0",
        "backup_schedule": backup_schedule.strip(),
        "backup_retention": backup_retention.strip(),
        "backup_path": backup_path.strip(),
    }
    save_system_settings(db, values)
    audit(db, user, "изменил настройки системы", "Настройки", 1, "Системные настройки")
    db.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok", "service": "DocFlow RS", "date": TODAY.isoformat()}
