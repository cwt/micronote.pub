from functools import wraps
from urllib.parse import urlparse

from flask import redirect, request, session, url_for


def safe_next_url(value, fallback):
    """Returns value only when it is a relative path on this host."""
    if not value:
        return fallback
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        return fallback
    if not parsed.path.startswith("/"):
        return fallback
    return value


def login_required(f):

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("admin.admin_login", redirect=request.path))
        return f(*args, **kwargs)

    return decorated_function
