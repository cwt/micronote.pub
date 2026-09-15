"""WebAuthn second factor (replaces the deprecated U2F flow).

Single-user: credentials live in the ``webauthn`` collection, ceremony
state in ``webauthn_state`` (the Flask cookie session cannot hold the
raw bytes Fido2Server hands back).
"""

import base64

from fido2.server import Fido2Server
from fido2.webauthn import AttestedCredentialData

from micronote.config import DB, DOMAIN, USERNAME


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _freeze(value):
    if isinstance(value, bytes):
        return {"$bytes": _b64encode(value)}
    if isinstance(value, dict):
        return {key: _freeze(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_freeze(item) for item in value]
    return value


def _thaw(value):
    if isinstance(value, dict) and set(value) == {"$bytes"}:
        return _b64decode(value["$bytes"])
    if isinstance(value, dict):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_thaw(item) for item in value]
    return value


def get_server() -> Fido2Server:
    rp_id = DOMAIN.split(":")[0]
    return Fido2Server({"id": rp_id, "name": f"{USERNAME}'s micronote.pub"})


def save_state(name: str, state) -> None:
    DB.webauthn_state.update_one(
        {"_id": name}, {"$set": {"state": _freeze(state)}}, upsert=True
    )


def load_state(name: str):
    doc = DB.webauthn_state.find_one({"_id": name})
    return _thaw(doc["state"]) if doc else None


def clear_state(name: str) -> None:
    DB.webauthn_state.delete_one({"_id": name})


def stored_credentials():
    """Rebuilds AttestedCredentialData objects for every registered key."""
    credentials = []
    for doc in DB.webauthn.find():
        credentials.append(AttestedCredentialData(_b64decode(doc["attested"])))
    return credentials


def save_credential(auth_data, name: str = "key") -> None:
    DB.webauthn.insert_one({
        "name": name,
        "credential_id": _b64encode(auth_data.credential_data.credential_id),
        "attested": _b64encode(bytes(auth_data.credential_data)),
        "sign_count": auth_data.counter,
    })


def update_sign_count(credential_id: bytes, sign_count: int) -> None:
    DB.webauthn.update_one(
        {"credential_id": _b64encode(credential_id)},
        {"$set": {"sign_count": sign_count}},
    )


def credential_options(options) -> dict:
    """Serializes Fido2Server options (bytes and all) for tojson."""
    return _freeze(dict(options))
