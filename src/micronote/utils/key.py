import binascii
import os
from collections.abc import Callable
from pathlib import Path

from active_boxes.key import Key

KEY_DIR = Path(os.getenv("MICRONOTE_CONFIG_DIR", os.path.abspath("config")))


def _new_key() -> str:
    return binascii.hexlify(os.urandom(32)).decode("utf-8")


def get_secret_key(name: str, new_key: Callable[[], str] = _new_key) -> str:
    """Loads or generates a cryptographic key."""
    key_path = KEY_DIR / f"{name}.key"
    if not key_path.exists():
        k = new_key()
        key_path.write_text(k)
        return k

    return key_path.read_text()


def get_key(owner: str, user: str, domain: str) -> Key:
    """Loads or generates an RSA key."""
    k = Key(owner)
    user = user.replace(".", "_")
    domain = domain.replace(".", "_")
    key_path = KEY_DIR / f"key_{user}_{domain}.pem"
    if key_path.is_file():
        k.load(key_path.read_text())
    else:
        k.new()
        key_path.write_text(k.privkey_pem)

    return k
