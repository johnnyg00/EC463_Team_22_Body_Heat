"""Accounts, sign-in sessions and role checks (NFR-007, AC-F5).

Individual staff and athlete accounts so acknowledgements and exports are
attributable. Demo note: passwords are hashed with the standard library's
salted scrypt; the December hub uses Argon2id (6.4) which is not in the
standard library. Role checks happen in the backend on every request."""

import hashlib
import hmac
import os
import secrets

_SCRYPT = dict(n=2 ** 14, r=8, p=1)


def hash_password(password, salt=None):
    salt = salt or os.urandom(16)
    return salt, hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)


class AccessDenied(Exception):
    pass


class Accounts:
    def __init__(self):
        self._users = {}      # username -> dict(role, device_id, salt, hash, display)
        self._sessions = {}   # token -> username
        self.denied_log = []  # (t, username, what) for AC-F5

    def add(self, username, password, role, display, device_id=None):
        salt, h = hash_password(password)
        self._users[username] = {"role": role, "device_id": device_id, "salt": salt, "hash": h, "display": display}

    def sign_in(self, username, password):
        u = self._users.get(username)
        if not u:
            return None
        _, h = hash_password(password, u["salt"])
        if not hmac.compare_digest(h, u["hash"]):
            return None
        token = secrets.token_urlsafe(24)
        self._sessions[token] = username
        return token

    def sign_out(self, token):
        self._sessions.pop(token, None)

    def user_for(self, token):
        name = self._sessions.get(token)
        if not name:
            return None
        u = self._users[name]
        return {"username": name, "role": u["role"], "device_id": u["device_id"], "display": u["display"]}

    # -- RBAC, called by every API route ---------------------------------
    def require_staff(self, user, t, what):
        if not user or user["role"] != "staff":
            self._deny(t, user, what)
        return user

    def require_device_access(self, user, device_id, t, what):
        """Staff may read any wearer; an athlete only their own device.
        Every denial is logged (AC-F5)."""
        if not user:
            self._deny(t, user, what)
        if user["role"] == "staff":
            return user
        if user["role"] == "athlete" and user["device_id"] == device_id:
            return user
        self._deny(t, user, what)

    def _deny(self, t, user, what):
        self.denied_log.append((t, user["username"] if user else "(not signed in)", what))
        raise AccessDenied(what)

    def list_accounts(self):
        """For the demo sign-in page only."""
        return [{"username": n, "role": u["role"], "display": u["display"]} for n, u in self._users.items()]
