"""JSON API.

Errors are always {"error": "..."} — one shape, so the frontend needs one
parsing helper.
"""

from flask import Blueprint, jsonify, request
from sqlalchemy import text

from app.db import db_session
from app.services import search as search_service

bp = Blueprint("api", __name__)

MAX_LIMIT = 50


@bp.get("/health")
def health():
    try:
        db_session.execute(text("SELECT 1"))
        words = search_service.total_words()
    except Exception as exc:
        return jsonify({"error": f"database unreachable: {exc}"}), 503
    return jsonify({"status": "ok", "words": words})


@bp.get("/suggest")
def suggest():
    q = request.args.get("q", "")
    limit = _limit(request.args.get("limit"), default=8)
    rows = search_service.suggest(q, limit=limit)
    return jsonify({"query": q, "results": [r.summary() for r in rows]})


@bp.get("/search")
def search():
    q = request.args.get("q", "")
    limit = _limit(request.args.get("limit"), default=25)
    rows = search_service.search(q, limit=limit)
    return jsonify({"query": q, "count": len(rows), "results": [r.summary() for r in rows]})


@bp.get("/word/<path:word>")
def word(word):
    entry, redirected_from = search_service.lookup(_from_url(word))
    if entry is None:
        return jsonify({"error": "word not found"}), 404
    payload = entry.detail()
    if redirected_from:
        payload["redirected_from"] = redirected_from
    return jsonify(payload)


def _limit(raw, default):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_LIMIT))


def _from_url(value):
    """URLs use hyphens for the spaces in multi-word entries."""
    return value.replace("+", " ")
