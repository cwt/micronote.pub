"""Authentication-adjacent browser flows: remote follow and WebAuthn registration."""

import json

from active_boxes import activitypub as ap
from active_boxes.activitypub import ActivityType
from active_boxes.webfinger import get_actor_url_sync, get_remote_follow_template_sync
from flask import Blueprint, abort, current_app, redirect, render_template, request
from flask_wtf.csrf import CSRFProtect

from micronote import tasks
from micronote.boxes import Box
from micronote.config import DB, DOMAIN, USERNAME
from micronote.instance import MY_PERSON
from micronote.utils.login import login_required

blueprint = Blueprint("auth", __name__, template_folder="templates")
csrf = CSRFProtect(current_app)


@blueprint.route("/remote_follow", methods=["GET", "POST"])
def remote_follow():
    if request.method == "GET":
        return render_template("remote_follow.html")

    csrf.protect()
    profile = (request.form.get("profile") or "").strip()
    if not profile:
        abort(400)
    if not profile.startswith("@"):
        profile = f"@{profile}"
    template = get_remote_follow_template_sync(profile)
    if not template:
        abort(404)
    return redirect(template.format(uri=f"{USERNAME}@{DOMAIN}"))


@blueprint.route("/authorize_follow", methods=["GET", "POST"])
@login_required
def authorize_follow():
    if request.method == "GET":
        return render_template("authorize_remote_follow.html", profile=request.args.get("profile"))

    csrf.protect()
    profile = request.form.get("profile")
    actor = get_actor_url_sync(profile) if profile else None
    if not actor:
        abort(404)

    q = {
        "box": Box.OUTBOX.value,
        "type": ActivityType.FOLLOW.value,
        "meta.undo": False,
        "activity.object": actor,
    }
    if DB.activities.count_documents(q) > 0:
        return redirect("/following")

    follow = ap.Follow(actor=MY_PERSON.id, object=actor)
    tasks.post_to_outbox(follow)

    return redirect("/following")


@blueprint.route("/webauthn/register", methods=["GET", "POST"])
@login_required
def webauthn_register():
    from fido2.webauthn import PublicKeyCredentialUserEntity

    from micronote.utils.webauthn import (
        clear_state,
        credential_options,
        get_server,
        load_state,
        save_credential,
        save_state,
        stored_credentials,
    )

    server = get_server()
    if request.method == "GET":
        user = PublicKeyCredentialUserEntity(id=b"admin", name=USERNAME)
        options, state = server.register_begin(user, credentials=stored_credentials())
        save_state("register", state)
        return render_template("webauthn_register.html", options=credential_options(options))

    csrf.protect()
    raw_cred = request.form.get("credential")
    state = load_state("register")
    if not raw_cred or not state:
        abort(400)
    try:
        credential = json.loads(raw_cred)
        auth_data = server.register_complete(state, credential)
    except (ValueError, json.JSONDecodeError, Exception):
        abort(400)
    finally:
        clear_state("register")
    save_credential(auth_data)
    return redirect("/admin")
