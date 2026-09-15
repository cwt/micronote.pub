"""Removes duplicate FOLLOW activities. Run explicitly: python dedup.py"""

from active_boxes.activitypub import ActivityType

from micronote.activitypub import Box
from micronote.config import DB


def remove_duplicate_follows(box: str, field: str, label: str) -> None:
    seen: set[str] = set()
    query = {"box": box, "type": ActivityType.FOLLOW.value, "meta.undo": False}
    for doc in DB.activities.find(query):
        target = doc["activity"][field]
        if target not in seen:
            seen.add(target)
            print(f"{label}: {target}")
        else:
            DB.activities.delete_one({"_id": doc["_id"]})
            print(f"duplicate: {target} -- deleted")


def main() -> None:
    remove_duplicate_follows(Box.OUTBOX.value, "object", "following")
    remove_duplicate_follows(Box.INBOX.value, "actor", "follower")


if __name__ == "__main__":
    main()
