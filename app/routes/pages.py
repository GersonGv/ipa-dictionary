"""Server-rendered pages.

Every word has its own URL and its own fully rendered HTML. That is the whole
reason this stopped being a static hash-routed app: /word/schedule is
something a search engine can index and a person can link to.
"""

import string

from flask import Blueprint, Response, abort, redirect, render_template, request, url_for

from app.config import config
from app.services import search as search_service
from app.services.urls import word_path

bp = Blueprint("pages", __name__)

# Words whose British and American pronunciations differ enough to be worth
# putting on the front page.
FEATURED_WORDS = [
    "schedule", "tomato", "privacy", "herb", "advertisement",
    "either", "vitamin", "clerk", "garage", "zebra",
]

LETTERS = list(string.ascii_lowercase)
BROWSE_PER_PAGE = 200
SITEMAP_CHUNK = 20000


@bp.get("/")
def home():
    return render_template(
        "home.html",
        featured=search_service.featured(FEATURED_WORDS),
        total=search_service.total_words(),
        letters=LETTERS,
    )


@bp.get("/word/<path:word>")
def word(word):
    entry, redirected_from = search_service.lookup(word.replace("+", " "))
    if entry is None:
        suggestions = search_service.search(word, limit=8)
        return (
            render_template("404.html", query=word, suggestions=suggestions),
            404,
        )
    # Canonicalise: /word/Schedule and /word/ran land on the real page rather
    # than duplicating it under a second URL.
    canonical = word_path(entry.word)
    if not redirected_from and request.path != canonical:
        return redirect(canonical, code=301)
    return render_template(
        "entry.html", entry=entry, redirected_from=redirected_from
    )


@bp.get("/search")
def search():
    query = request.args.get("q", "").strip()
    if not query:
        return redirect(url_for("pages.home"))
    results = search_service.search(query, limit=25)
    # A single exact hit is not a result list, it is the word.
    if len(results) == 1 and results[0].word == search_service.normalise(query):
        return redirect(word_path(results[0].word))
    return render_template("search.html", query=query, results=results)


@bp.get("/browse")
def browse_index():
    return redirect(url_for("pages.browse", letter="a"))


@bp.get("/browse/<letter>")
def browse(letter):
    letter = letter[:1].lower()
    if letter not in LETTERS:
        abort(404)
    page = max(1, request.args.get("page", 1, type=int))
    rows, total = search_service.browse(letter, page=page, per_page=BROWSE_PER_PAGE)
    if not rows and page > 1:
        abort(404)
    pages = max(1, -(-total // BROWSE_PER_PAGE))
    return render_template(
        "browse.html",
        letter=letter,
        letters=LETTERS,
        words=rows,
        total=total,
        page=page,
        pages=pages,
    )


@bp.get("/about")
def about():
    return render_template("about.html", total=search_service.total_words())


@bp.get("/robots.txt")
def robots():
    body = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "Disallow: /api/",
            "Disallow: /search",
            f"Sitemap: {config.SITE_URL}/sitemap.xml",
            "",
        ]
    )
    return Response(body, mimetype="text/plain")


@bp.get("/sitemap.xml")
def sitemap_index():
    """A sitemap index — a full lexicon is far past the 50,000-URL limit for a
    single sitemap file."""
    total = search_service.total_words()
    chunks = max(1, -(-total // SITEMAP_CHUNK))
    urls = [
        f"{config.SITE_URL}/sitemap-{i}.xml" for i in range(1, chunks + 1)
    ]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    xml += [f"<sitemap><loc>{u}</loc></sitemap>" for u in urls]
    xml.append("</sitemapindex>")
    return Response("\n".join(xml), mimetype="application/xml")


@bp.get("/sitemap-<int:chunk>.xml")
def sitemap_chunk(chunk):
    from sqlalchemy import select

    from app.db import db_session
    from app.models import Word

    if chunk < 1:
        abort(404)
    rows = db_session.execute(
        select(Word.word)
        .order_by(Word.id)
        .offset((chunk - 1) * SITEMAP_CHUNK)
        .limit(SITEMAP_CHUNK)
    ).scalars()
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    empty = True
    for w in rows:
        empty = False
        xml.append(f"<url><loc>{config.SITE_URL}{word_path(w)}</loc></url>")
    if empty:
        abort(404)
    xml.append("</urlset>")
    return Response("\n".join(xml), mimetype="application/xml")
