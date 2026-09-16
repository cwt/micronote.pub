from flask import current_app

from micronote.config import DB


def published_of(doc: dict) -> str:
    return doc["activity"]["object"].get("published") or ""


def _build_thread(data: dict, include_children: bool = True) -> list[dict]:
    data["_requested"] = True
    current_app.logger.debug(data)
    root_object = data["activity"].get("object")
    if not isinstance(root_object, dict):
        return [data]
    root_id = data["meta"].get("thread_root_parent", root_object["id"])

    query = {
        "$or": [
            {"meta.thread_root_parent": root_id, "type": "Create"},
            {"activity.object.id": root_id},
            # Direct replies, visible even before the worker links the
            # thread via meta.thread_root_parent (e.g. right after posting).
            {"activity.object.inReplyTo": root_id},
        ]
    }
    if data["activity"]["object"].get("inReplyTo"):
        query["$or"].append({"activity.object.id": data["activity"]["object"]["inReplyTo"]})

    # Fetch the root replies, and the children
    replies = [data, *DB.activities.find(query)]
    # Thread members need a full object; Like/Announce docs referencing it
    # by bare IRI are listed separately on the note page, not in the tree.
    replies = [rep for rep in replies if isinstance(rep["activity"].get("object"), dict)]
    replies = sorted(replies, key=published_of)
    # Index all the IDs in order to build a tree
    idx = {}
    replies2 = []
    for rep in replies:
        rep_id = rep["activity"]["object"]["id"]
        if rep_id in idx:
            continue
        idx[rep_id] = rep.copy()
        idx[rep_id]["_nodes"] = []
        replies2.append(rep)

    # Build the tree
    for rep in replies2:
        rep_id = rep["activity"]["object"]["id"]
        if rep_id == root_id:
            continue
        reply_of = rep["activity"]["object"].get("inReplyTo")
        if not reply_of:
            current_app.logger.info(f"{rep_id} has no inReplyTo, skipping {rep}")
            continue
        try:
            idx[reply_of]["_nodes"].append(rep)
        except KeyError:
            current_app.logger.info(f"{reply_of} is not there! skipping {rep}")

    # Flatten the tree
    thread = []

    def _flatten(node, level=0):
        node["_level"] = level
        thread.append(node)

        for snode in sorted(
            idx[node["activity"]["object"]["id"]]["_nodes"],
            key=published_of,
        ):
            _flatten(snode, level=level + 1)

    try:
        _flatten(idx[root_id])
    except KeyError:
        current_app.logger.info(f"{root_id} is not there! skipping")

    return thread
