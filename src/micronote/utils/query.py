from flask import abort, request
from neosqlite.objectid import ObjectId


def paginated_query(db, q, limit=25, sort_key="_id"):
    def sort_key_as_str(doc):
        return str(doc[sort_key])

    # Copy: cursor keys are added below and must not leak into the caller.
    q = q.copy()

    older_than = newer_than = None
    query_sort = -1
    first_page = not request.args.get("older_than") and not request.args.get(
        "newer_than"
    )

    query_older_than = request.args.get("older_than")
    query_newer_than = request.args.get("newer_than")

    if query_older_than:
        try:
            q["_id"] = {"$lt": ObjectId(query_older_than)}
        except Exception:
            abort(400)
    elif query_newer_than:
        try:
            q["_id"] = {"$gt": ObjectId(query_newer_than)}
        except Exception:
            abort(400)
        query_sort = 1

    outbox_data = list(db.find(q, limit=limit + 1).sort(sort_key, query_sort))
    outbox_len = len(outbox_data)
    if not outbox_data:
        return [], None, None
    outbox_data = sorted(
        outbox_data[:limit], key=sort_key_as_str, reverse=True
    )

    if query_older_than:
        newer_than = str(outbox_data[0]["_id"])
        if outbox_len == limit + 1:
            older_than = str(outbox_data[-1]["_id"])
    elif query_newer_than:
        older_than = str(outbox_data[-1]["_id"])
        if outbox_len == limit + 1:
            newer_than = str(outbox_data[0]["_id"])
    elif first_page and outbox_len == limit + 1:
        older_than = str(outbox_data[-1]["_id"])

    return outbox_data, older_than, newer_than
