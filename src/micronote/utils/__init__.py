import logging

logger = logging.getLogger(__name__)


def strtobool(s: str) -> bool:
    match s.lower():
        case "y" | "yes" | "true" | "on" | "1":
            return True
        case "n" | "no" | "false" | "off" | "0":
            return False
        case _:
            raise ValueError(f"cannot convert {s} to bool")
