from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .security import hash_password

from .models import (
    ApprovalStep,
    Attachment,
    AuditEvent,
    Counterparty,
    Document,
    DocumentType,
    Resolution,
    User,
)


def seed_database(db: Session) -> None:
    if db.scalar(select(User.id).limit(1)):
        return

    demo_password_hashes = [hash_password("demo2026") for _ in range(6)]
    users = [
        User(full_name="Елена Соколова", email="e.sokolova@red-soft.ru", role="Делопроизводитель", password_hash=demo_password_hashes[0], department="Административный департамент", initials="ЕС"),
        User(full_name="Олег Воронцов", email="o.vorontsov@red-soft.ru", role="Руководитель", password_hash=demo_password_hashes[1], department="Проектный департамент", initials="ОВ"),
        User(full_name="Мария Лебедева", email="m.lebedeva@red-soft.ru", role="Согласующий", password_hash=demo_password_hashes[2], department="Юридическая служба", initials="МЛ"),
        User(full_name="Илья Крылов", email="i.krylov@red-soft.ru", role="Исполнитель", password_hash=demo_password_hashes[3], department="Отдел сопровождения", initials="ИК"),
        User(full_name="Анна Орлова", email="a.orlova@red-soft.ru", role="Администратор", password_hash=demo_password_hashes[4], department="Департамент ИТ", initials="АО"),
        User(full_name="Петр Смирнов", email="disabled@red-soft.ru", role="Контролер", password_hash=demo_password_hashes[5], department="Архивная учетная запись", initials="ПС", active=False),
    ]
    db.add_all(users)
    db.flush()

    types = [
        DocumentType(code="LETTER", name="Письмо", default_route="Делопроизводитель → руководитель", retention_years=5),
        DocumentType(code="CONTRACT", name="Договор", default_route="Инициатор → юрист → руководитель", retention_years=10),
        DocumentType(code="ORDER", name="Приказ", default_route="Инициатор → юрист → генеральный директор", retention_years=10),
        DocumentType(code="MEMO", name="Служебная записка", default_route="Автор → руководитель подразделения", retention_years=5),
        DocumentType(code="ACT", name="Акт", default_route="Ответственный → комиссия → руководитель", retention_years=5),
        DocumentType(code="PROTOCOL", name="Протокол", default_route="Автор → руководитель", retention_years=10),
    ]
    counterparties = [
        Counterparty(name="АО «ТехноСфера»", inn="7708123456", email="office@technosphere.ru", phone="+7 495 100-20-30"),
        Counterparty(name="ООО «ИнфраПро»", inn="7812456789", email="docs@infrapro.ru", phone="+7 812 555-14-22"),
        Counterparty(name="ФГБУ «Цифровые сервисы»", inn="7711122233", email="inbox@digital.gov.ru", phone="+7 495 700-40-10"),
        Counterparty(name="ООО «ДатаЛайн»", inn="7722334455", email="contract@dataline.ru", phone="+7 495 987-65-43"),
        Counterparty(name="ООО «ОблакоСервис»", inn="7733445566", email="docs@cloudservice.ru", phone="+7 495 555-19-70"),
    ]
    db.add_all(types + counterparties)
    db.flush()

    today = date(2026, 8, 14)
    documents = [
        Document(registration_number="ВХ-2026-0127", title="Запрос коммерческого предложения на поставку лицензий", kind="Входящий", status="Зарегистрирован", confidentiality="Общий доступ", external_number="ТС-482/26", registration_date=today - timedelta(days=1), due_date=today + timedelta(days=4), content="Подготовить коммерческое предложение и направить ответ контрагенту.", file_name="zapros_kp.pdf", document_type_id=types[0].id, counterparty_id=counterparties[0].id, author_id=users[0].id, responsible_id=users[1].id),
        Document(registration_number="ИСХ-2026-0084", title="Ответ о совместимости программных компонентов", kind="Исходящий", status="На согласовании", confidentiality="Общий доступ", registration_date=today - timedelta(days=2), due_date=today + timedelta(days=2), content="Проект ответа на технический запрос партнера.", file_name="otvet_sovmestimost.docx", document_type_id=types[0].id, counterparty_id=counterparties[1].id, author_id=users[3].id, responsible_id=users[1].id),
        Document(registration_number="ВН-2026-0045", title="О назначении ответственных за пилотное внедрение", kind="Внутренний", status="Согласован", confidentiality="Для служебного пользования", registration_date=today - timedelta(days=4), due_date=today + timedelta(days=7), content="Определить состав рабочей группы и закрепить зоны ответственности.", file_name="prikaz_pilot.docx", document_type_id=types[2].id, counterparty_id=None, author_id=users[1].id, responsible_id=users[0].id),
        Document(registration_number="ВХ-2026-0126", title="Акт сверки взаимных расчетов", kind="Входящий", status="На исполнении", confidentiality="Общий доступ", external_number="ИП-91/2026", registration_date=today - timedelta(days=5), due_date=today - timedelta(days=1), content="Проверить показатели акта и вернуть подписанный экземпляр.", file_name="akt_sverki.pdf", document_type_id=types[4].id, counterparty_id=counterparties[1].id, author_id=users[0].id, responsible_id=users[3].id),
        Document(registration_number="ВН-2026-0044", title="Служебная записка о расширении тестового контура", kind="Внутренний", status="На согласовании", confidentiality="Для служебного пользования", registration_date=today - timedelta(days=6), due_date=today + timedelta(days=1), content="Обоснование ресурсов для расширения стенда тестирования.", file_name="memo_test_env.docx", document_type_id=types[3].id, counterparty_id=None, author_id=users[3].id, responsible_id=users[1].id),
        Document(registration_number="ИСХ-2026-0083", title="Дополнительное соглашение к договору сопровождения", kind="Исходящий", status="На согласовании", confidentiality="Конфиденциально", registration_date=today - timedelta(days=7), due_date=today + timedelta(days=6), content="Изменение срока и стоимости услуг технической поддержки.", file_name="dop_soglashenie_v1.docx", document_type_id=types[1].id, counterparty_id=counterparties[3].id, author_id=users[1].id, responsible_id=users[2].id),
        Document(registration_number="ВХ-2026-0125", title="Уведомление о проведении приемочных испытаний", kind="Входящий", status="Исполнен", confidentiality="Общий доступ", external_number="ЦС-887", registration_date=today - timedelta(days=9), due_date=today - timedelta(days=2), content="Согласовать участие представителей и подтвердить дату испытаний.", file_name="uvedomlenie.pdf", document_type_id=types[0].id, counterparty_id=counterparties[2].id, author_id=users[0].id, responsible_id=users[1].id),
        Document(registration_number="ВН-2026-0043", title="Акт ввода в опытную эксплуатацию", kind="Внутренний", status="Архив", confidentiality="Для служебного пользования", registration_date=today - timedelta(days=16), due_date=None, content="Результаты запуска подсистемы в опытную эксплуатацию.", file_name="akt_oe.pdf", document_type_id=types[4].id, counterparty_id=None, author_id=users[3].id, responsible_id=users[1].id),
        Document(registration_number="ИСХ-2026-0082", title="Письмо о направлении протокола разногласий", kind="Исходящий", status="Зарегистрирован", confidentiality="Конфиденциально", registration_date=today - timedelta(days=18), due_date=None, content="Направление протокола разногласий по проекту договора.", file_name="protocol_disagreement.pdf", document_type_id=types[0].id, counterparty_id=counterparties[0].id, author_id=users[2].id, responsible_id=users[0].id),
        Document(registration_number="ВХ-2026-0124", title="Запрос сведений о технической поддержке", kind="Входящий", status="Отклонен", confidentiality="Общий доступ", external_number="DL-1081", registration_date=today - timedelta(days=20), due_date=today - timedelta(days=12), content="Запрос не относится к компетенции подразделения; перенаправлен владельцу продукта.", file_name="request_support.pdf", document_type_id=types[0].id, counterparty_id=counterparties[3].id, author_id=users[0].id, responsible_id=users[3].id),
    ]
    db.add_all(documents)
    db.flush()

    approvals = [
        ApprovalStep(document_id=documents[1].id, approver_id=users[2].id, order_num=1, status="Ожидает"),
        ApprovalStep(document_id=documents[1].id, approver_id=users[1].id, order_num=2, status="Ожидает"),
        ApprovalStep(document_id=documents[4].id, approver_id=users[1].id, order_num=1, status="Ожидает"),
        ApprovalStep(document_id=documents[5].id, approver_id=users[2].id, order_num=1, status="Ожидает"),
        ApprovalStep(document_id=documents[2].id, approver_id=users[2].id, order_num=1, status="Согласовано", decision_at=datetime(2026, 8, 11, 15, 40)),
        ApprovalStep(document_id=documents[5].id, approver_id=users[1].id, order_num=2, status="Ожидает"),
        ApprovalStep(document_id=documents[6].id, approver_id=users[2].id, order_num=1, status="Согласовано", decision_at=datetime(2026, 8, 9, 11, 20)),
        ApprovalStep(document_id=documents[8].id, approver_id=users[1].id, order_num=1, status="Согласовано", decision_at=datetime(2026, 7, 30, 14, 10)),
    ]
    resolutions = [
        Resolution(document_id=documents[0].id, author_id=users[1].id, executor_id=users[3].id, text="Подготовить проект коммерческого предложения", due_date=today + timedelta(days=3), status="В работе"),
        Resolution(document_id=documents[3].id, author_id=users[1].id, executor_id=users[3].id, text="Проверить суммы и представить замечания", due_date=today - timedelta(days=1), status="В работе"),
        Resolution(document_id=documents[6].id, author_id=users[1].id, executor_id=users[3].id, text="Подтвердить участие в испытаниях", due_date=today - timedelta(days=3), status="Исполнена", completed_at=datetime(2026, 8, 10, 16, 10)),
        Resolution(document_id=documents[2].id, author_id=users[1].id, executor_id=users[0].id, text="Ознакомить участников рабочей группы", due_date=today + timedelta(days=5), status="Назначена"),
    ]
    attachments = [
        Attachment(document_id=documents[0].id, name="zapros_kp.pdf", storage_path="/storage/2026/08/zapros_kp.pdf", version=1),
        Attachment(document_id=documents[1].id, name="otvet_sovmestimost.docx", storage_path="/storage/2026/08/otvet_sovmestimost.docx", version=2),
        Attachment(document_id=documents[5].id, name="dop_soglashenie_v1.docx", storage_path="/storage/2026/08/dop_soglashenie_v1.docx", version=1),
    ]
    db.add_all(approvals + resolutions + attachments)
    db.flush()

    audit_actions = ["зарегистрировал документ", "изменил статус документа", "просмотрел карточку", "сформировал отчет"]
    for index in range(22):
        document = documents[index % len(documents)]
        actor = users[index % 5]
        db.add(
            AuditEvent(
                user_id=actor.id,
                action=audit_actions[index % len(audit_actions)],
                entity_type="Документ",
                entity_id=document.id,
                entity_name=document.registration_number,
                details=document.title,
                created_at=datetime(2026, 8, 14, 9, 0) - timedelta(hours=index * 2),
            )
        )
    db.commit()
