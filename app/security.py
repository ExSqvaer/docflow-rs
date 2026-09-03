from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Permission:
    code: str
    roles: tuple[str, ...]


PERMISSIONS = {
    "document.create": Permission("document.create", ("Администратор", "Делопроизводитель")),
    "document.edit": Permission("document.edit", ("Администратор", "Делопроизводитель")),
    "document.approve": Permission("document.approve", ("Руководитель", "Согласующий")),
    "resolution.manage": Permission("resolution.manage", ("Администратор", "Руководитель", "Делопроизводитель")),
    "resolution.execute": Permission("resolution.execute", ("Администратор", "Исполнитель")),
    "directory.manage": Permission("directory.manage", ("Администратор", "Делопроизводитель")),
    "user.manage": Permission("user.manage", ("Администратор",)),
    "audit.view": Permission("audit.view", ("Администратор", "Руководитель")),
    "settings.manage": Permission("settings.manage", ("Администратор",)),
}

PBKDF2_ALGORITHM = "sha256"
PBKDF2_ITERATIONS = 210_000


def can(role: str, permission_code: str) -> bool:
    permission = PERMISSIONS.get(permission_code)
    return bool(permission and role in permission.roles)


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    """Return a salted PBKDF2 hash; the clear-text password is never persisted."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        PBKDF2_ALGORITHM,
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return "pbkdf2_sha256${}${}${}".format(
        iterations,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    """Verify a password against the salted PBKDF2 hash in constant time."""
    try:
        scheme, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac(
        PBKDF2_ALGORITHM,
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual, expected)
