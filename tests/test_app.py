import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault("PYTHONPATH", str(BASE_DIR))
os.environ.setdefault("SESSION_SECRET", "test-session-secret")
DB_FILE = BASE_DIR / "data" / "docflow.db"
if DB_FILE.exists():
    DB_FILE.unlink()

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, User


def login(client: TestClient, email: str = "e.sokolova@red-soft.ru", password: str = "demo2026"):
    return client.post(
        "/login",
        data={"email": email, "password": password, "next": ""},
        follow_redirects=False,
    )


def test_health_endpoint_is_public():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["service"] == "DocFlow RS"


def test_dashboard_requires_session():
    with TestClient(app) as client:
        response = client.get("/dashboard", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")


def test_invalid_password_does_not_create_session():
    with TestClient(app) as client:
        response = login(client, password="wrong-password")
        assert response.status_code == 401
        assert "Неверные учетные данные" in response.text
        dashboard = client.get("/dashboard", follow_redirects=False)
        assert dashboard.status_code == 303


def test_valid_login_creates_session_and_opens_dashboard():
    with TestClient(app) as client:
        response = login(client)
        assert response.status_code == 303
        assert "docflow_session" in response.headers.get("set-cookie", "")
        dashboard = client.get("/dashboard")
        assert dashboard.status_code == 200
        assert "Панель документационного обеспечения" in dashboard.text
        assert "Елена Соколова" in dashboard.text


def test_role_restriction_blocks_executor_from_user_admin():
    with TestClient(app) as client:
        assert login(client, "i.krylov@red-soft.ru").status_code == 303
        response = client.get("/users")
        assert response.status_code == 403


def test_approver_cannot_decide_other_users_step():
    with TestClient(app) as client:
        assert login(client, "m.lebedeva@red-soft.ru").status_code == 303
        response = client.post(
            "/approvals/2/decision",
            data={"decision": "Согласовано", "comment": ""},
            follow_redirects=False,
        )
        assert response.status_code == 403


def test_create_document_generates_number_and_audits_real_user():
    with TestClient(app) as client:
        assert login(client).status_code == 303
        response = client.post(
            "/documents/new",
            data={
                "title": "Тестовый входящий документ",
                "kind": "Входящий",
                "document_type_id": "1",
                "counterparty_id": "1",
                "external_number": "Т-1",
                "registration_date": "2026-08-14",
                "due_date": "2026-08-20",
                "responsible_id": "2",
                "confidentiality": "Общий доступ",
                "content": "Контрольный пример регистрации.",
                "file_name": "test.pdf",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        detail = client.get(response.headers["location"])
        assert "Тестовый входящий документ" in detail.text
        assert "ВХ-2026-" in detail.text

    with SessionLocal() as db:
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "создал документ").order_by(AuditEvent.id.desc()))
        assert event is not None
        user = db.get(User, event.user_id)
        assert user.email == "e.sokolova@red-soft.ru"


def test_due_date_before_registration_is_rejected():
    with TestClient(app) as client:
        assert login(client).status_code == 303
        response = client.post(
            "/documents/new",
            data={
                "title": "Документ с неверным сроком",
                "kind": "Входящий",
                "document_type_id": "1",
                "counterparty_id": "1",
                "external_number": "T-2",
                "registration_date": "2026-08-14",
                "due_date": "2026-08-13",
                "responsible_id": "2",
                "confidentiality": "Общий доступ",
                "content": "",
                "file_name": "",
            },
        )
        assert response.status_code == 400
        assert "раньше даты регистрации" in response.text


def test_reports_export_requires_login_and_works_after_login():
    with TestClient(app) as client:
        unauth = client.get("/reports/export.csv", follow_redirects=False)
        assert unauth.status_code == 303
        assert login(client).status_code == 303
        response = client.get("/reports/export.csv")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "Номер;Дата;Вид;Статус;Заголовок" in response.text


def test_logout_invalidates_session():
    with TestClient(app) as client:
        assert login(client).status_code == 303
        assert client.get("/dashboard").status_code == 200
        response = client.post("/logout", follow_redirects=False)
        assert response.status_code == 303
        dashboard = client.get("/dashboard", follow_redirects=False)
        assert dashboard.status_code == 303


def test_create_document_as_draft_works():
    with TestClient(app) as client:
        assert login(client).status_code == 303
        response = client.post(
            "/documents/new",
            data={
                "title": "Черновик тестового документа",
                "kind": "Исходящий",
                "document_type_id": "1",
                "counterparty_id": "1",
                "external_number": "",
                "registration_date": "2026-08-14",
                "due_date": "2026-08-20",
                "responsible_id": "2",
                "confidentiality": "Общий доступ",
                "content": "Черновик",
                "file_name": "draft.docx",
                "action": "draft",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        detail = client.get(response.headers["location"])
        assert "Черновик тестового документа" in detail.text
        assert ">Черновик<" in detail.text


def test_document_edit_button_and_route_work():
    with TestClient(app) as client:
        assert login(client).status_code == 303
        page = client.get("/documents/1")
        assert '/documents/1/edit' in page.text
        edit_page = client.get("/documents/1/edit")
        assert edit_page.status_code == 200
        response = client.post(
            "/documents/1/edit",
            data={
                "title": "Запрос КП — отредактировано",
                "document_type_id": "1",
                "counterparty_id": "1",
                "external_number": "ТС-482/26",
                "due_date": "2026-08-20",
                "responsible_id": "2",
                "confidentiality": "Общий доступ",
                "content": "Обновленный текст карточки.",
                "file_name": "zapros_kp.pdf",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        detail = client.get("/documents/1")
        assert "Запрос КП — отредактировано" in detail.text
        assert "Версия 2" in detail.text


def test_documents_export_button_route_works():
    with TestClient(app) as client:
        assert login(client).status_code == 303
        response = client.get("/documents/export.csv?kind=Входящий")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "Входящий" in response.text


def test_approval_tabs_and_blocked_next_step_are_rendered():
    with TestClient(app) as client:
        assert login(client, "o.vorontsov@red-soft.ru").status_code == 303
        response = client.get("/approvals?tab=pending")
        assert response.status_code == 200
        assert "Согласованные" in response.text
        assert "Отклоненные" in response.text
        assert "Ожидает завершения предыдущего этапа" in response.text


def test_executor_does_not_see_document_create_action():
    with TestClient(app) as client:
        assert login(client, "i.krylov@red-soft.ru").status_code == 303
        response = client.get("/documents")
        assert response.status_code == 200
        assert "＋ Новый документ" not in response.text


def test_admin_can_create_and_edit_user():
    with TestClient(app) as client:
        assert login(client, "a.orlova@red-soft.ru").status_code == 303
        response = client.post(
            "/users/new",
            data={
                "full_name": "Тестовый Пользователь",
                "email": "test.user@red-soft.ru",
                "role": "Исполнитель",
                "department": "Тестовый отдел",
                "initials": "ТП",
                "password": "demo2026",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        with SessionLocal() as db:
            created = db.scalar(select(User).where(User.email == "test.user@red-soft.ru"))
            assert created is not None
            user_id = created.id
        response = client.post(
            f"/users/{user_id}/edit",
            data={
                "full_name": "Тестовый Пользователь Изменен",
                "email": "test.user@red-soft.ru",
                "role": "Контролер",
                "department": "Контроль",
                "initials": "ТП",
                "password": "",
                "active": "1",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        with SessionLocal() as db:
            changed = db.get(User, user_id)
            assert changed.role == "Контролер"
            assert changed.department == "Контроль"


def test_directories_add_actions_work():
    with TestClient(app) as client:
        assert login(client).status_code == 303
        response = client.post(
            "/dictionaries/types/new",
            data={"code": "TESTTYPE", "name": "Тестовый тип", "default_route": "Автор → Руководитель", "retention_years": "3"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        response = client.post(
            "/dictionaries/counterparties/new",
            data={"name": "ООО «Тест Контрагент»", "inn": "7700000000", "email": "test@example.ru", "phone": "+7 000 000-00-00"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        page = client.get("/dictionaries")
        assert "Тестовый тип" in page.text
        assert "ООО «Тест Контрагент»" in page.text


def test_audit_export_works():
    with TestClient(app) as client:
        assert login(client, "a.orlova@red-soft.ru").status_code == 303
        response = client.get("/audit/export.csv")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "Пользователь;Действие" in response.text


def test_settings_are_saved_and_change_registration_pattern():
    with TestClient(app) as client:
        assert login(client, "a.orlova@red-soft.ru").status_code == 303
        response = client.post(
            "/settings",
            data={
                "incoming_pattern": "IN-{ГОД}-{НОМЕР:4}",
                "outgoing_pattern": "OUT-{ГОД}-{НОМЕР:4}",
                "internal_pattern": "INT-{ГОД}-{НОМЕР:4}",
                "counter_reset": "Ежегодно, 1 января",
                "notify_approval": "1",
                "notify_due": "1",
                "notify_overdue": "1",
                "backup_schedule": "Ежедневно в 02:00",
                "backup_retention": "30 дней",
                "backup_path": "/backup/test",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        saved_page = client.get("/settings?saved=1")
        assert "Настройки сохранены" in saved_page.text

        # Administrator has document.create permission too.
        response = client.post(
            "/documents/new",
            data={
                "title": "Документ после настройки номера",
                "kind": "Входящий",
                "document_type_id": "1",
                "counterparty_id": "1",
                "external_number": "",
                "registration_date": "2026-08-14",
                "due_date": "2026-08-20",
                "responsible_id": "2",
                "confidentiality": "Общий доступ",
                "content": "",
                "file_name": "",
                "action": "register",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        detail = client.get(response.headers["location"])
        assert "IN-2026-" in detail.text
