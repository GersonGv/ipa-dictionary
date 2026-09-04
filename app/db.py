"""Engine and session.

`pool_recycle` matters on shared hosting: MySQL there is usually configured
with a short `wait_timeout`, and a pooled connection that outlives it comes
back as "MySQL server has gone away" on the next request.

The pool arguments are MySQL-only so that the build tooling, which imports the
models against a throwaway SQLite URL purely to compile DDL, does not trip
over them.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

from app.config import config

_options = {"pool_pre_ping": True, "future": True}
if not config.DATABASE_URL.startswith("sqlite"):
    _options.update(pool_recycle=280, pool_size=5, max_overflow=5)

engine = create_engine(config.DATABASE_URL, **_options)

db_session = scoped_session(sessionmaker(bind=engine, autoflush=False, future=True))

Base = declarative_base()
Base.query = db_session.query_property()
