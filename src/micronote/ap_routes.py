"""ActivityPub protocol endpoints (collections, inbox, outbox)."""

import json
import logging

from active_boxes import activitypub as ap
from active_boxes.activitypub import ActivityType, _to_list, clean_activity, get_backend
from active_boxes.errors import ActivityGoneError, ActivityUnavailableError
from active_boxes.httpsig import verify_request_sync
from flask import Blueprint, Response, abort, request
from itsdangerous import BadSignature

from micronote import ap_serialize, api, repository, tasks
from micronote.boxes import Box
from micronote.config import DB
from micronote.instance import MY_PERSON
from micronote.web import activity_json, activitypub_only

blueprint = Blueprint("ap", __name__, template_folder="templates")

log = logging.getLogger(__name__)


@blueprint.route("/outbox", methods=["GET", "POST"])
@activitypub_only
def outbox():
    if request.method == "GET":
        # TODO(tsileo): returns the whole outbox if authenticated
        q = {
            "box": Box.OUTBOX.value,
            "meta.deleted": False,
            "type": {"$in": [ActivityType.CREATE.value, ActivityType.ANNOUNCE.value]},
        }
        return activity_json(
            **ap_serialize.build_ordered_collection(
                repository.activities(),
                q=q,
                cursor=request.args.get("cursor"),
                map_func=ap_serialize.activity_from_doc_embedded,
                col_name="outbox",
            )
        )

    # Handle POST request
    try:
        api.require_api_auth()
    except BadSignature:
        abort(401)

    data = request.get_json(force=True)
    log.debug(data)
    activity = ap.parse_activity(data)
    activity_id = tasks.post_to_outbox(activity)

    return Response(status=201, headers={"Location": activity_id})


@blueprint.route("/outbox/<item_id>")
def outbox_detail(item_id):
    doc = repository.outbox_item(item_id, include_deleted=True)
    if not doc:
        abort(404)

    if doc["meta"].get("deleted", False):
        obj = ap.parse_activity(doc["activity"])
        resp = activity_json(**obj.get_tombstone().to_dict())
        resp.status_code = 410
        return resp
    return activity_json(**ap_serialize.activity_from_doc(doc))


@blueprint.route("/outbox/<item_id>/activity")
def outbox_activity(item_id):
    data = repository.outbox_item(item_id, include_deleted=True)
    if not data:
        abort(404)
    obj = ap_serialize.activity_from_doc(data)
    if data["meta"].get("deleted", False):
        obj = ap.parse_activity(data["activity"])
        resp = activity_json(**obj.get_object_sync().get_tombstone().to_dict())
        resp.status_code = 410
        return resp

    if obj["type"] != ActivityType.CREATE.value:
        abort(404)
    return activity_json(**obj["object"])


@blueprint.route("/outbox/<item_id>/replies")
@activitypub_only
def outbox_activity_replies(item_id):
    data = repository.outbox_item(item_id)
    if not data:
        abort(404)
    obj = ap.parse_activity(data["activity"])
    if obj.ACTIVITY_TYPE != ActivityType.CREATE:
        abort(404)

    q = {
        "meta.deleted": False,
        "type": ActivityType.CREATE.value,
        "activity.object.inReplyTo": obj.get_object_sync().id,
    }

    return activity_json(
        **ap_serialize.build_ordered_collection(
            DB.activities,
            q=q,
            cursor=request.args.get("cursor"),
            map_func=ap_serialize.activity_object_from_doc,
            col_name=f"outbox/{item_id}/replies",
            first_page=request.args.get("page") == "first",
        )
    )


@blueprint.route("/outbox/<item_id>/likes")
@activitypub_only
def outbox_activity_likes(item_id):
    data = repository.outbox_item(item_id)
    if not data:
        abort(404)
    obj = ap.parse_activity(data["activity"])
    if obj.ACTIVITY_TYPE != ActivityType.CREATE:
        abort(404)

    q = {
        "meta.undo": False,
        "type": ActivityType.LIKE.value,
        "$or": [
            {"activity.object.id": obj.get_object_sync().id},
            {"activity.object": obj.get_object_sync().id},
        ],
    }

    return activity_json(
        **ap_serialize.build_ordered_collection(
            DB.activities,
            q=q,
            cursor=request.args.get("cursor"),
            map_func=ap_serialize.activity_without_context,
            col_name=f"outbox/{item_id}/likes",
            first_page=request.args.get("page") == "first",
        )
    )


@blueprint.route("/outbox/<item_id>/shares")
@activitypub_only
def outbox_activity_shares(item_id):
    data = repository.outbox_item(item_id)
    if not data:
        abort(404)
    obj = ap.parse_activity(data["activity"])
    if obj.ACTIVITY_TYPE != ActivityType.CREATE:
        abort(404)

    q = {
        "meta.undo": False,
        "type": ActivityType.ANNOUNCE.value,
        "$or": [
            {"activity.object.id": obj.get_object_sync().id},
            {"activity.object": obj.get_object_sync().id},
        ],
    }

    return activity_json(
        **ap_serialize.build_ordered_collection(
            DB.activities,
            q=q,
            cursor=request.args.get("cursor"),
            map_func=ap_serialize.activity_without_context,
            col_name=f"outbox/{item_id}/shares",
            first_page=request.args.get("page") == "first",
        )
    )


@blueprint.route("/inbox", methods=["GET", "POST"])
@activitypub_only
def inbox():
    if request.method == "GET":
        try:
            api.require_api_auth()
        except BadSignature:
            abort(404)

        return activity_json(
            **ap_serialize.build_ordered_collection(
                repository.activities(),
                q={"meta.deleted": False, "box": Box.INBOX.value},
                cursor=request.args.get("cursor"),
                map_func=ap_serialize.activity_without_context,
                col_name="inbox",
            )
        )

    data = request.get_json(force=True)
    if not isinstance(data, dict) or "id" not in data or "type" not in data:
        abort(400)
    log.debug(f"req_headers={request.headers}")
    log.debug(f"raw_data={data}")
    try:
        if not verify_request_sync(request.method, request.path, request.headers, request.data):
            raise Exception("failed to verify request")
    except Exception as err:
        log.info(f"signature verification failed ({err}), attempting verification by dereferencing payload ID")
        try:
            data = get_backend().fetch_iri_sync(data["id"])
        except (ActivityGoneError, ActivityUnavailableError) as fetch_err:
            # XXX Mastodon sends Delete activities that are not dereferencable, it's the actor url with #delete
            # appended, so an `ActivityGoneError` kind of ensure it's "legit"
            is_actor_delete = (
                data["type"] == ActivityType.DELETE.value
                and isinstance(data.get("object"), str)
                and data["id"].startswith(data["object"])
            )
            is_gone = isinstance(fetch_err, ActivityGoneError) or (
                isinstance(fetch_err, ActivityUnavailableError)
                and ("ActivityGoneError" in str(fetch_err) or "is gone" in str(fetch_err))
            )
            if is_actor_delete and is_gone:
                log.info(f"received a Delete for an actor {data!r}")
                if get_backend().inbox_check_duplicate(MY_PERSON, data["id"]):
                    # The activity is already in the inbox
                    log.info(f"received duplicate activity {data!r}, dropping it")
                    return Response(status=201)

                DB.activities.insert_one(
                    {
                        "box": Box.INBOX.value,
                        "activity": data,
                        "type": _to_list(data["type"]),
                        "remote_id": data["id"],
                        "meta": {"undo": False, "deleted": False},
                    }
                )
                # TODO(tsileo): write the callback the the delete external actor event
                return Response(status=201)

            log.warning(f"failed to fetch remote id at {data['id']}: {fetch_err}")
            return Response(
                status=422,
                headers={"Content-Type": "application/json"},
                response=json.dumps({"error": "failed to verify request (using HTTP signatures or fetching the IRI)"}),
            )
        except Exception:
            log.exception(f"failed to fetch remote id at {data['id']}")
            return Response(
                status=422,
                headers={"Content-Type": "application/json"},
                response=json.dumps({"error": "failed to verify request (using HTTP signatures or fetching the IRI)"}),
            )
    activity = ap.parse_activity(data)
    log.debug(f"inbox activity={activity}/{data}")
    tasks.post_to_inbox(activity)

    return Response(status=201)


@blueprint.route("/featured")
@activitypub_only
def featured():
    data = [clean_activity(doc["activity"]["object"]) for doc in repository.pinned_notes()]
    return activity_json(**ap_serialize.simple_build_ordered_collection("featured", data))
