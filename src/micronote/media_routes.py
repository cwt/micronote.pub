"""Static and GridFS media serving."""

import os
from datetime import datetime

from flask import Blueprint, abort, current_app, send_from_directory

from micronote.config import MEDIA_CACHE
from micronote.utils.headers import noindex

blueprint = Blueprint("media", __name__, template_folder="templates")

GZIP_MAGIC = b"\x1f\x8b"


def serve_grid_file(grid_out):
    data = grid_out.read()
    upload_date = grid_out.upload_date
    try:
        parsed_date = datetime.fromisoformat(upload_date)
        last_modified = parsed_date.strftime("%a, %d %b %Y %H:%M:%S GMT")
    except (TypeError, ValueError):
        last_modified = upload_date
    content_type = (grid_out.metadata or {}).get("content_type") or "application/octet-stream"
    resp = current_app.response_class(data, mimetype=content_type)
    resp.headers.set("Content-Length", len(data))
    resp.headers.set("ETag", grid_out.md5)
    resp.headers.set("Last-Modified", last_modified)
    resp.headers.set("Cache-Control", "public,max-age=31536000,immutable")
    resp.headers.set("X-Content-Type-Options", "nosniff")
    if data[:2] == GZIP_MAGIC:
        # Legacy entries and non-image blobs are gzip-compressed;
        # WebP entries are stored raw.
        resp.headers.set("Content-Encoding", "gzip")
    return resp


@blueprint.route("/media/<media_id>")
@noindex
def serve_media(media_id):
    grid_out = MEDIA_CACHE.get_media(media_id)
    if grid_out is None:
        abort(404)
    return serve_grid_file(grid_out)


@blueprint.route("/uploads/<oid>/<fname>")
def serve_uploads(oid, fname):
    grid_out = MEDIA_CACHE.get_media(oid)
    if grid_out is None:
        abort(404)
    return serve_grid_file(grid_out)


@blueprint.route("/favicon.ico")
def favicon():
    return send_from_directory(
        directory=os.path.join(current_app.root_path, "static"), path="favicon.ico", mimetype="image/vnd.microsoft.icon"
    )
