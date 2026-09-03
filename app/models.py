from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(140), nullable=False)
    email: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[str] = mapped_column(String(100), nullable=False)
    initials: Mapped[str] = mapped_column(String(8), default="RS")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    authored_documents: Mapped[list[Document]] = relationship(
        back_populates="author", foreign_keys="Document.author_id"
    )
    responsible_documents: Mapped[list[Document]] = relationship(
        back_populates="responsible", foreign_keys="Document.responsible_id"
    )


class Counterparty(Base):
    __tablename__ = "counterparties"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(180), unique=True, nullable=False)
    inn: Mapped[str] = mapped_column(String(12), default="")
    email: Mapped[str] = mapped_column(String(160), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    documents: Mapped[list[Document]] = relationship(back_populates="counterparty")


class DocumentType(Base):
    __tablename__ = "document_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    default_route: Mapped[str] = mapped_column(String(180), default="")
    retention_years: Mapped[int] = mapped_column(Integer, default=5)

    documents: Mapped[list[Document]] = relationship(back_populates="document_type")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    registration_number: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="Черновик", index=True)
    confidentiality: Mapped[str] = mapped_column(String(30), default="Общий доступ")
    external_number: Mapped[str] = mapped_column(String(60), default="")
    registration_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    file_name: Mapped[str] = mapped_column(String(200), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
    document_type_id: Mapped[int] = mapped_column(ForeignKey("document_types.id"))
    counterparty_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("counterparties.id"), nullable=True
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    responsible_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)

    document_type: Mapped[DocumentType] = relationship(back_populates="documents")
    counterparty: Mapped[Optional[Counterparty]] = relationship(back_populates="documents")
    author: Mapped[User] = relationship(
        back_populates="authored_documents", foreign_keys=[author_id]
    )
    responsible: Mapped[User] = relationship(
        back_populates="responsible_documents", foreign_keys=[responsible_id]
    )
    approval_steps: Mapped[list[ApprovalStep]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="ApprovalStep.order_num"
    )
    resolutions: Mapped[list[Resolution]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    attachments: Mapped[list[Attachment]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class ApprovalStep(Base):
    __tablename__ = "approval_steps"
    __table_args__ = (UniqueConstraint("document_id", "order_num", name="uq_approval_document_order"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    approver_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    order_num: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="Ожидает")
    comment: Mapped[str] = mapped_column(Text, default="")
    decision_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    document: Mapped[Document] = relationship(back_populates="approval_steps")
    approver: Mapped[User] = relationship()


class Resolution(Base):
    __tablename__ = "resolutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    executor_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="Назначена")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    document: Mapped[Document] = relationship(back_populates="resolutions")
    author: Mapped[User] = relationship(foreign_keys=[author_id])
    executor: Mapped[User] = relationship(foreign_keys=[executor_id])


class Attachment(Base):
    __tablename__ = "attachments"
    __table_args__ = (UniqueConstraint("document_id", "name", "version", name="uq_attachment_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(300), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    document: Mapped[Document] = relationship(back_populates="attachments")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(180), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    entity_name: Mapped[str] = mapped_column(String(220), nullable=False)
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)

    user: Mapped[User] = relationship()


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
