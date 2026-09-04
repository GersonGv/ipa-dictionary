"""Application factory.

One Flask app serves everything: server-rendered word pages (so search engines
see a real page per word) and the small JSON API the search box talks to.
That is deliberate — a dictionary lives or dies on being indexable, and a
single Passenger app is also one thing to deploy instead of two.
"""

import logging
import os

from flask import Flask, render_template

from app.config import config
from app.db import Base, db_session, engine
from app.services.jsonld import entry_jsonld
from app.services.urls import word_path

log = logging.getLogger(__name__)


def create_app():
    app = Flask(__name__)
    app.config.from_object(config)
    app.url_map.strict_slashes = False

    _init_database()

    from app.routes.api import bp as api_bp
    from app.routes.pages import bp as pages_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.teardown_appcontext
    def remove_session(exception=None):  # noqa: ARG001
        db_session.remove()

    app.jinja_env.filters["word_path"] = word_path
    app.jinja_env.filters["entry_jsonld"] = entry_jsonld

    @app.context_processor
    def inject_globals():
        return {
            "site_name": config.SITE_NAME,
            "site_url": config.SITE_URL,
            "asset": _asset_url(app),
        }

    @app.errorhandler(404)
    def not_found(error):  # noqa: ARG001
        return render_template("404.html", query=None), 404

    @app.errorhandler(500)
    def server_error(error):
        log.exception("unhandled error: %s", error)
        db_session.rollback()
        return render_template("500.html"), 500

    return app


def _init_database():
    """Create any missing tables at startup.

    This only ever *creates*. It will not add a column to a table that already
    exists — a model change like that needs a hand-written ALTER TABLE after
    deploying. A database that is unreachable must not stop Passenger from
    booting, or there is no error page left to show.
    """
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:  # pragma: no cover - depends on the host
        log.error("could not create tables at startup: %s", exc)


def _asset_url(app):
    """Cache-busting static URLs.

    Static files are served with a one-year max-age in production, so the URL
    has to change when the file does; the file's mtime is enough.
    """
    from flask import url_for

    cache = {}

    def asset(filename):
        if filename not in cache:
            path = os.path.join(app.static_folder, filename)
            try:
                cache[filename] = int(os.path.getmtime(path))
            except OSError:
                cache[filename] = 0
        return url_for("static", filename=filename, v=cache[filename])

    return asset
