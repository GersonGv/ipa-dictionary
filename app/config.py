"""Configuration, read once at process start.

Passenger does not guarantee the working directory is the app root, so the
.env path is anchored to this file rather than looked up relative to CWD.
Getting this wrong is silent: the app falls back to the placeholder DSN and
fails later with "Can't connect to MySQL server on 'localhost'".
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(dotenv_path=BASE_DIR / ".env")


class Config:
    DATABASE_URL = os.getenv(
        "DATABASE_URL", "mysql+pymysql://root:@localhost/dictionary?charset=utf8mb4"
    )
    SITE_URL = os.getenv("SITE_URL", "http://localhost:5000").rstrip("/")
    SITE_NAME = os.getenv("SITE_NAME", "IPA Dictionary")
    ENV = os.getenv("FLASK_ENV", "production")

    IS_PRODUCTION = ENV == "production"
    # Static assets are fingerprinted by mtime in the templates, so they can be
    # cached hard in production.
    SEND_FILE_MAX_AGE_DEFAULT = 31536000 if IS_PRODUCTION else 0


config = Config()
