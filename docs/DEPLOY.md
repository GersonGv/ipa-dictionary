# Deploying to cPanel

One Passenger Python app on one subdomain serves the whole site — pages and
API. There is no Node app, no build step, and nothing to zip.

Assume throughout:

| | |
|---|---|
| cPanel account | `cpaneluser` |
| Subdomain | `dictionary.example.com` |
| Application root | `/home/cpaneluser/dictionary` |
| Database | `cpaneluser_dictionary` |

## 1. Create the subdomain

cPanel → **Domains → Create A New Domain**.

- Domain: `dictionary.example.com`
- Uncheck *Share document root*.
- Document root: `/home/cpaneluser/dictionary` — the same directory the Python
  app will use as its application root. If these two disagree, Apache never
  reads the `.htaccess` Passenger needs and the browser gets an `Index of /`
  listing instead of the site.

## 2. Create the database

cPanel → **MySQL® Databases**.

1. New database: `dictionary` (cPanel stores it as `cpaneluser_dictionary`).
2. New user: `dict` with a generated password — save it.
3. Add the user to the database with **ALL PRIVILEGES**.

## 3. Put the code on the server

Over SSH:

```sh
cd ~
git clone <your-repo-url> dictionary
```

## 4. Create the Python app

cPanel → **Setup Python App → Create Application**.

- Python version: 3.9 or newer.
- Application root: `dictionary`
- Application URL: `dictionary.example.com`
- Application startup file: `passenger_wsgi.py`
- Application Entry point: `application`

Create it, then install the dependencies. cPanel shows the exact `source
.../activate` line for the app at the top of that page — run it, then:

```sh
cd ~/dictionary
pip install -r requirements.txt
```

## 5. Write the server's `.env`

```sh
cd ~/dictionary
cp .env.example .env
nano .env
```

```ini
DATABASE_URL=mysql+pymysql://cpaneluser_dict:THEPASSWORD@localhost/cpaneluser_dictionary?charset=utf8mb4
SITE_URL=https://dictionary.example.com
SITE_NAME=IPA Dictionary
FLASK_ENV=production
```

A `@` or `#` in the password must be percent-encoded (`%40`, `%23`) — it is
part of a URL. Easiest to regenerate a password without them.

`.env` is read once, at process start. Editing it does nothing until the app
restarts.

Restart the app (cPanel → Setup Python App → Restart, or `touch
~/dictionary/tmp/restart.txt`) and check it is alive:

```sh
curl -i https://dictionary.example.com/api/health
# {"status":"ok","words":0}
```

`words: 0` is expected — the tables exist but are empty. The app creates them
at startup.

## 6. Build and import the dictionary data

On your own machine, not the server. The parse needs ~4 GB of disk and more
memory than shared hosting will give a process.

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./tools/build_data.sh
```

That downloads the ~3 GB English Wiktextract dump (resumable — rerun it if the
connection drops), filters it, and writes `build/dictionary.sql.gz` — about
11 MB gzipped for ~64,000 headwords. Budget 10–15 minutes for the parse, plus
however long the download takes.

Then configure the upload and run it:

```sh
cp tools/deploy.env.example tools/deploy.env
$EDITOR tools/deploy.env
./tools/import_remote.sh
```

It uploads the gzipped dump and pipes it through `mysql` on the server. The
same import takes 13 seconds against a local MySQL 8; on shared hosting expect
a few minutes. The last step, building the FULLTEXT index, is the slowest part
and looks like a hang.

Confirm:

```sh
curl -s https://dictionary.example.com/api/health
# {"status":"ok","words":63925}
```

The import starts with `DROP TABLE`, so it replaces the lexicon wholesale.
Re-running it is how the data is updated.

## 7. Faster static assets (optional)

Paste the blocks in `tools/htaccess.snippet` below whatever cPanel generated in
`~/dictionary/.htaccess`, with `CPANELUSER` replaced. That lets Apache serve
`/static` without waking a Python process. Restarting the app in cPanel can
regenerate `.htaccess` and drop them — check after a restart.

## Deploying a code change

```sh
./tools/deploy.sh
```

`git reset --hard origin/main` on the server, then `touch tmp/restart.txt`, then
a health check. Nothing else is needed — no build, no asset upload.

## When something is wrong

Passenger writes both apps' stderr to the application root:

```sh
tail -f ~/dictionary/stderr.log
```

| Symptom | Cause | Fix |
|---|---|---|
| `Index of /` instead of the site | Subdomain document root is not the app root, so Apache never reads the Passenger `.htaccess` | Fix the document root in cPanel → Domains |
| `Can't connect to MySQL server on 'localhost'` with a literal placeholder host | `.env` was not found, so the placeholder DSN was used | Confirm `~/dictionary/.env` exists and is readable; `config.py` loads it by absolute path, so this usually means the file is missing, not misplaced |
| `Access denied for user` | The database user was created but never added to the database | cPanel → MySQL Databases → Add User To Database → ALL PRIVILEGES |
| `/api/health` returns `words: 0` after an import | The import ran against a different database than `.env` points at | Compare `DB_NAME` in `tools/deploy.env` with `DATABASE_URL` in the server's `.env` |
| A code change has no effect | Flask read the old module at process start | Restart the app; a fresh startup line appears in `stderr.log` |
| `Unknown column ...` after a model change | Startup only ever runs `CREATE TABLE`, never `ALTER TABLE` | `ALTER TABLE` by hand in phpMyAdmin or the `mysql` CLI, or re-import |
| Search finds nothing but exact words | The FULLTEXT index did not get built | `SHOW INDEX FROM words;` — if `ft_words_gloss` is missing, re-run the import |
| `MySQL server has gone away` under low traffic | A pooled connection outlived the server's `wait_timeout` | Already handled by `pool_recycle=280`; if it persists the host's timeout is under 280s — lower it in `app/db.py` |
| `'cryptography' package is required for sha256_password or caching_sha2_password auth methods` | MySQL 8 defaults to `caching_sha2_password`, which PyMySQL cannot do alone | `cryptography` is already in `requirements.txt`; this means the install did not pick it up — re-run `pip install -r requirements.txt` inside the app's virtualenv and restart |

## Memory

Passenger holds one Python process (sometimes a few) per app. This one loads
Flask, SQLAlchemy and nothing else, and never holds the lexicon in memory —
every query is a bounded, indexed lookup. It sits comfortably inside a typical
1 GB shared-hosting `LVE` limit.

The database is smaller than it sounds: the default ~64,000-word build measures
**60 MB** including every index. Raising `MAX_RANK` scales that roughly
linearly. Nothing here needs a large-disk plan.
