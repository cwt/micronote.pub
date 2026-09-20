"""Activity and collection serialization helpers for the HTTP layer."""

from typing import Any

from active_boxes import activitypub as ap
from active_boxes.activitypub import ActivityType, clean_activity
from flask import abort
from neosqlite.objectid import ObjectId

from micronote.config import BASE_URL


def embed_collection(total_items, first_page_id):
    """Helper creating a root OrderedCollection with a link to the first page."""
    return {
        "type": ActivityType.ORDERED_COLLECTION.value,
        "totalItems": total_items,
        "first": f"{first_page_id}?page=first",
        "id": first_page_id,
    }


def simple_build_ordered_collection(col_name, data):
    return {
        "@context": ap.COLLECTION_CTX,
        "id": f"{BASE_URL}/{col_name}",
        "totalItems": len(data),
        "type": ActivityType.ORDERED_COLLECTION.value,
        "orderedItems": data,
    }


def build_ordered_collection(col, q=None, cursor=None, map_func=None, limit=50, col_name=None, first_page=False):
    """Helper for building an OrderedCollection from a MongoDB query (with pagination support)."""
    col_name = col_name or col.name
    collection_id = f"{BASE_URL}/{col_name}"
    base_q = q.copy() if q is not None else {}
    total_items = col.count_documents(base_q)

    query_with_cursor = base_q.copy()
    if cursor:
        try:
            query_with_cursor["_id"] = {"$lt": ObjectId(cursor)}
        except Exception:
            abort(400)
    data = list(col.find(query_with_cursor, limit=limit).sort("_id", -1))

    if not data:
        # Returns an empty page if there's a cursor
        if cursor:
            return {
                "@context": ap.COLLECTION_CTX,
                "type": ActivityType.ORDERED_COLLECTION_PAGE.value,
                "id": f"{collection_id}?cursor={cursor}",
                "partOf": collection_id,
                "totalItems": total_items,
                "orderedItems": [],
            }
        return {
            "@context": ap.COLLECTION_CTX,
            "id": collection_id,
            "totalItems": total_items,
            "type": ActivityType.ORDERED_COLLECTION.value,
            "orderedItems": [],
        }

    start_cursor = str(data[0]["_id"])
    next_page_cursor = str(data[-1]["_id"])

    data = [_remove_id(doc) for doc in data]
    if map_func:
        data = [map_func(doc) for doc in data]

    page = {
        "id": f"{collection_id}?cursor={start_cursor}",
        "orderedItems": data,
        "partOf": collection_id,
        "totalItems": total_items,
        "type": ActivityType.ORDERED_COLLECTION_PAGE.value,
    }
    if len(data) == limit:
        page["next"] = f"{collection_id}?cursor={next_page_cursor}"

    # No cursor, this is the first page and we return an OrderedCollection
    if not cursor:
        if first_page:
            return page
        return {
            "@context": ap.COLLECTION_CTX,
            "id": collection_id,
            "totalItems": total_items,
            "type": ActivityType.ORDERED_COLLECTION.value,
            "first": page,
        }

    # If there's a cursor, then we return an OrderedCollectionPage
    # XXX(tsileo): implements prev with prev=<first item cursor>?
    return {"@context": ap.COLLECTION_CTX, **page}


def _remove_id(doc):
    """Helper for removing the document store's `_id` field."""
    doc = doc.copy()
    doc.pop("_id", None)
    return doc


def add_extra_collection(raw_doc: dict[str, Any]) -> dict[str, Any]:
    if raw_doc["activity"]["type"] != ActivityType.CREATE.value:
        return raw_doc

    raw_doc["activity"]["object"]["replies"] = embed_collection(
        raw_doc.get("meta", {}).get("count_direct_reply", 0),
        f"{raw_doc['remote_id']}/replies",
    )

    raw_doc["activity"]["object"]["likes"] = embed_collection(
        raw_doc.get("meta", {}).get("count_like", 0), f"{raw_doc['remote_id']}/likes"
    )

    raw_doc["activity"]["object"]["shares"] = embed_collection(
        raw_doc.get("meta", {}).get("count_boost", 0), f"{raw_doc['remote_id']}/shares"
    )

    return raw_doc


def remove_context(activity: dict[str, Any]) -> dict[str, Any]:
    activity = activity.copy()
    activity.pop("@context", None)
    return activity


def activity_from_doc(raw_doc: dict[str, Any], embed: bool = False) -> dict[str, Any]:
    raw_doc = add_extra_collection(raw_doc)
    activity = clean_activity(raw_doc["activity"])
    if embed:
        return remove_context(activity)
    return activity


def activity_from_doc_embedded(raw_doc: dict[str, Any]) -> dict[str, Any]:
    return activity_from_doc(raw_doc, embed=True)


def activity_object_from_doc(raw_doc: dict[str, Any]) -> dict[str, Any]:
    return raw_doc["activity"]["object"]


def activity_object_id_from_doc(raw_doc: dict[str, Any]) -> str:
    return raw_doc["activity"]["object"]["id"]


def activity_actor_from_doc(raw_doc: dict[str, Any]) -> str:
    return raw_doc["activity"]["actor"]


def activity_without_context(raw_doc: dict[str, Any]) -> dict[str, Any]:
    return remove_context(raw_doc["activity"])
