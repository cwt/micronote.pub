import binascii
import json
import os
from datetime import UTC, datetime
from urllib.parse import urlencode, urlparse

import flask
import mf2py
from flask import Response, abort, current_app, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFProtect
from itsdangerous import BadSignature
from neosqlite import DESCENDING

from micronote.config import DB, ID, jwt
from micronote.utils.login import login_required

blueprint = flask.Blueprint("indieauth", __name__, template_folder="templates")
csrf = CSRFProtect(flask.current_app)


def build_auth_resp(payload):
    if request.headers.get("Accept") == "application/json":
        return Response(
            status=200,
            headers={"Content-Type": "application/json"},
            response=json.dumps(payload),
        )
    return Response(
        status=200,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        response=urlencode(payload),
    )


def _get_prop(props, name, default=None):
    if name in props:
        items = props.get(name)
        if isinstance(items, list):
            return items[0]
        return items
    return default


def _normalize_scope(raw_scope) -> str:
    if isinstance(raw_scope, str):
        return raw_scope
    if isinstance(raw_scope, (list, tuple, set)):
        return " ".join(raw_scope)
    return ""


def get_client_id_data(url):
    fallback = {"logo": None, "name": url, "url": url}
    if not url or not url.startswith(("http://", "https://")):
        return fallback
    try:
        from active_boxes.urlutils import check_url

        check_url(url)
    except Exception:
        return fallback
    data = mf2py.parse(url=url)
    for item in data.get("items", []):
        item_types = item.get("type", [])
        if "h-x-app" in item_types or "h-app" in item_types:
            props = item.get("properties", {})
            current_app.logger.debug(props)
            return {
                "logo": _get_prop(props, "logo"),
                "name": _get_prop(props, "name"),
                "url": _get_prop(props, "url"),
            }
    return fallback


def _same_origin(url_a, url_b) -> bool:
    parsed_a = urlparse(url_a)
    parsed_b = urlparse(url_b)
    return (parsed_a.scheme, parsed_a.netloc) == (parsed_b.scheme, parsed_b.netloc)


@blueprint.route("/indieauth/flow", methods=["POST"])
@login_required
def indieauth_flow():
    csrf.protect()
    auth = {
        "scope": " ".join(request.form.getlist("scopes")),
        "me": request.form.get("me"),
        "client_id": request.form.get("client_id"),
        "state": request.form.get("state"),
        "redirect_uri": request.form.get("redirect_uri"),
        "response_type": request.form.get("response_type"),
    }

    code = binascii.hexlify(os.urandom(8)).decode("utf-8")
    auth |= {"code": code, "verified": False}
    current_app.logger.debug(auth)
    if not auth["redirect_uri"]:
        abort(400)

    if auth["me"] != ID:
        abort(400)

    # The redirect target must belong to the verified client_id page,
    # otherwise this endpoint becomes an open redirector.
    if not _same_origin(auth["redirect_uri"], auth["client_id"]):
        abort(400)

    DB.indieauth.insert_one(auth)

    red = f"{auth['redirect_uri']}?code={code}&state={auth['state']}&me={auth['me']}"
    return redirect(red)


@blueprint.route("/indieauth", methods=["GET", "POST"])
def indieauth_endpoint():
    if request.method == "GET":
        if not session.get("logged_in"):
            return redirect(url_for("admin.admin_login", redirect=request.full_path))

        me = request.args.get("me")
        # me == ID is enforced in indieauth_flow before any code is issued.
        client_id = request.args.get("client_id")
        redirect_uri = request.args.get("redirect_uri")
        state = request.args.get("state", "")
        response_type = request.args.get("response_type", "id")
        scope = request.args.get("scope", "").split()

        current_app.logger.debug(f"STATE {state}")
        return render_template(
            "indieauth_flow.html",
            client=get_client_id_data(client_id),
            scopes=scope,
            redirect_uri=redirect_uri,
            state=state,
            response_type=response_type,
            client_id=client_id,
            me=me,
        )

    # Auth verification via POST
    code = request.form.get("code")
    redirect_uri = request.form.get("redirect_uri")
    client_id = request.form.get("client_id")

    auth = DB.indieauth.find_one_and_update(
        {
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
        },  # },  #  , 'verified': False},
        {"$set": {"verified": True}},
        sort=[("_id", DESCENDING)],
    )
    current_app.logger.debug(auth)
    current_app.logger.debug(f"{code} {redirect_uri} {client_id}")

    if not auth:
        abort(403)
        return

    me = auth["me"]
    state = auth["state"]
    scope = _normalize_scope(auth.get("scope"))
    current_app.logger.debug(f"STATE {state}")
    return build_auth_resp({"me": me, "state": state, "scope": scope})


@blueprint.route("/token", methods=["GET", "POST"])
def token_endpoint():
    if request.method == "POST":
        code = request.form.get("code")
        me = request.form.get("me")
        redirect_uri = request.form.get("redirect_uri")
        client_id = request.form.get("client_id")

        auth = DB.indieauth.find_one(
            {
                "code": code,
                "me": me,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
            }
        )
        if not auth:
            abort(403)
        scope = _normalize_scope(auth.get("scope"))
        payload = {
            "me": me,
            "client_id": client_id,
            "scope": scope,
            "ts": datetime.now(UTC).timestamp(),
        }
        token = jwt().dumps(payload)

        return build_auth_resp({"me": me, "scope": scope, "access_token": token})

    # Token verification
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        abort(403)
    try:
        payload = jwt().loads(authorization.removeprefix("Bearer "))
    except BadSignature:
        abort(403)

    # TODO(tsileo): handle expiration

    return build_auth_resp(
        {
            "me": payload["me"],
            "scope": payload["scope"],
            "client_id": payload["client_id"],
        }
    )
