import logging
import os
import traceback
from datetime import timedelta

from active_boxes.errors import Error
from flask import Flask, render_template, request, session
from flask import jsonify as flask_jsonify
from flask_wtf.csrf import CSRFProtect

from micronote import (
    admin,
    ap_routes,
    api,
    auth_views,
    config,
    feeds,
    filters,
    indieauth,
    media_routes,
    stats,
    views,
    wellknown,
)
from micronote.config import ME, SCHEME, VERSION
from micronote.utils.key import get_secret_key
from micronote.web import wants_html

app = Flask(__name__)
app.register_blueprint(admin.blueprint)
app.register_blueprint(api.blueprint)
app.register_blueprint(ap_routes.blueprint)
app.register_blueprint(auth_views.blueprint)
app.register_blueprint(feeds.blueprint)
app.register_blueprint(filters.blueprint)
app.register_blueprint(indieauth.blueprint)
app.register_blueprint(media_routes.blueprint)
app.register_blueprint(views.blueprint)
app.register_blueprint(wellknown.blueprint)
app.secret_key = get_secret_key("flask")
app.config.update(
    WTF_CSRF_CHECK_DEFAULT=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=SCHEME == "https",
    PERMANENT_SESSION_LIFETIME=timedelta(days=365),
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,
)
app.jinja_env.trim_blocks = True
app.jinja_env.lstrip_blocks = True
csrf = CSRFProtect(app)

logger = logging.getLogger(__name__)

# Hook up Flask logging with gunicorn
root_logger = logging.getLogger()
if os.getenv("FLASK_DEBUG"):
    logger.setLevel(logging.DEBUG)
    root_logger.setLevel(logging.DEBUG)
else:
    gunicorn_logger = logging.getLogger("gunicorn.error")
    root_logger.handlers = gunicorn_logger.handlers
    root_logger.setLevel(gunicorn_logger.level)

# active_boxes logs a full traceback at ERROR for every failed fetch, including
# the expected 404 when a handle cannot be resolved. That drowns the logs, so
# silence its logger here. Our app handles lookup failures in the UI instead.
logging.getLogger("active_boxes").setLevel(logging.CRITICAL)


@app.context_processor
def inject_config():
    return {
        "micronote_version": VERSION,
        "config": config,
        "logged_in": session.get("logged_in", False),
        "me": ME,
        **stats.counts(),
    }


@app.after_request
def set_x_powered_by(response):
    response.headers["X-Powered-By"] = "micronote.pub"
    if (
        request.path == "/login"
        or request.path.startswith(("/admin", "/indieauth", "/token"))
        or (request.path.startswith("/api/") and session.get("logged_in"))
    ):
        # Private, account-specific responses must never sit in a browser
        # or shared (proxy/CDN) cache.
        response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@app.errorhandler(ValueError)
def handle_value_error(error):
    logger.error(f"caught value error: {error!r}, {traceback.format_tb(error.__traceback__)}")
    message = error.args[0] if error.args else "invalid request"
    if wants_html():
        return render_template("error.html", message=message), 400
    response = flask_jsonify(message=message)
    response.status_code = 400
    return response


@app.errorhandler(Error)
def handle_activitypub_error(error):
    logger.error(f"caught activitypub error {error!r}, {traceback.format_tb(error.__traceback__)}")
    status_code = getattr(error, "status_code", 400)
    if wants_html():
        message = getattr(error, "message", "") or str(error) or error.__class__.__name__
        return render_template("error.html", message=message), status_code
    to_dict = getattr(error, "to_dict", None)
    payload = to_dict() if callable(to_dict) else {"error": str(error) or error.__class__.__name__}
    response = flask_jsonify(payload)
    response.status_code = status_code
    return response


@app.errorhandler(500)
def handle_500(e):
    return render_template("500.html"), 500
