"""Storage box discriminator shared by tasks, routes, and the backend."""

from enum import StrEnum


class Box(StrEnum):
    INBOX = "inbox"
    OUTBOX = "outbox"
    REPLIES = "replies"
