# Deploying to a Hostinger VPS with Docker

Three containers: **web** (gunicorn), **nginx** (TLS + static/media), **certbot**
(Let's Encrypt renewal). SQLite lives on a named volume, so the database
survives rebuilds.

---

## 1. Point DNS at the VPS

In your domain's DNS panel, before anything else — Let's Encrypt validates over
HTTP, so these must resolve first:

| Type | Name | Value |
|---|---|---|
| A | `@` | your VPS IP |
| A | `www` | your VPS IP |

Check it took effect (propagation can take a few minutes to a few hours):

```bash
dig +short umrahcompany.co.uk
```

## 2. Install Docker on the VPS

SSH in as root, then:

```bash
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
```

Hostinger VPS images sometimes ship with a firewall enabled. Open the web ports:

```bash
ufw allow 22/tcp && ufw allow 80/tcp && ufw allow 443/tcp
```

Also check **hPanel → VPS → Firewall** — Hostinger's panel firewall sits in
front of the machine, so a rule missing there blocks traffic even when `ufw`
allows it.

## 3. Get the code onto the VPS

```bash
git clone https://github.com/lutonumrah/sacred-travel-ai.git /opt/scared-travel-ai
cd /opt/scared-travel-ai
```

## 4. Write the `.env`

```bash
cp .env.example .env
nano .env
```

The values that matter in production:

```ini
DEBUG=False
SECRET_KEY=<paste a fresh 50+ char random string — see below>
# Hostnames only — no scheme, no port.
ALLOWED_HOSTS=umrahcompany.co.uk,www.umrahcompany.co.uk,153.92.210.143

# Note the `data/` prefix — that is the mounted volume, not the image.
DATABASE_URL=sqlite:///data/db.sqlite3

# Full scheme+host, no trailing slash. Django rejects every form POST from an
# origin not listed here. Both schemes for the IP so it works either way.
CSRF_TRUSTED_ORIGINS=https://umrahcompany.co.uk,https://www.umrahcompany.co.uk,http://153.92.210.143,https://153.92.210.143
USE_X_FORWARDED_PROTO=True
TIME_ZONE=Asia/Kolkata

DOMAIN=umrahcompany.co.uk
CERTBOT_EMAIL=you@example.com
CERTBOT_STAGING=false

SEED_DEMO=false
```

Generate a real secret key:

```bash
docker run --rm python:3.12-slim python -c \
  "import secrets; print(secrets.token_urlsafe(64))"
```

> **`DOMAIN` is the bare apex only** — `umrahcompany.co.uk`, not `https://...`
> and not `www.`. The `www.` host is added automatically in nginx and in the
> certificate request.
>
> **Reaching the box by IP.** `153.92.210.143` is listed in `ALLOWED_HOSTS`, and
> both `http://` and `https://` forms of it are in `CSRF_TRUSTED_ORIGINS`, so
> Django accepts requests and form POSTs that arrive by IP. Two caveats at the
> nginx layer, neither of which Django settings can fix:
>
> - Port 80 redirects to HTTPS, so `http://153.92.210.143/` lands on
>   `https://153.92.210.143/`.
> - Let's Encrypt cannot issue a certificate for a bare IP, so that HTTPS
>   request is served with the certificate for `umrahcompany.co.uk` and the
>   browser shows a name-mismatch warning. Click through and the site works.
>
> IP access is therefore a fallback — useful while DNS propagates. Use the
> domain for day-to-day access, and never for anything you would not want sent
> over a connection the browser has flagged. If you want
> `http://153.92.210.143/` to serve directly over plain HTTP with no redirect
> and no warning, that needs an extra `server` block in
> `docker/nginx/app.conf.template` — say the word and I will add it.

## 5. Build and issue the certificate

```bash
docker compose build
./docker/init-letsencrypt.sh
```

The script plants a temporary self-signed certificate (nginx will not start
without one), brings nginx up, swaps in the real Let's Encrypt certificate and
reloads. Run it **once**.

Testing the plumbing and worried about rate limits? Set
`CERTBOT_STAGING=true` first — you get an untrusted certificate but unlimited
attempts. Then set it back to `false`, delete the staging cert and re-run:

```bash
docker compose run --rm --entrypoint \
  "rm -rf /etc/letsencrypt/live /etc/letsencrypt/archive /etc/letsencrypt/renewal" certbot
./docker/init-letsencrypt.sh
```

## 6. Start everything

```bash
docker compose up -d
docker compose ps
```

Visit **https://umrahcompany.co.uk**.

## 7. Create your admin user

```bash
docker compose exec web python manage.py createsuperuser
```

Want the demo dataset instead? Set `SEED_DEMO=true` in `.env`, run
`docker compose up -d --force-recreate web`, then set it back to `false`.
It signs in as `admin` / `travel1234` — **change that password immediately.**

---

## 8. Check everything works

```bash
cd /opt/scared-travel-ai
./docker/verify.sh
```

It is read-only — it changes nothing — and runs about 25 checks across six
areas: `.env` sanity (`DEBUG` off, real `SECRET_KEY`, database on the volume),
container state and health, migrations / `check --deploy` / superuser / static
files, the HTTP→HTTPS redirect and the health endpoint through nginx, the
certificate's domains and expiry, DNS and public reachability, and disk space.

Every line is `PASS`, `WARN` or `FAIL`, and the script exits non-zero if
anything failed, so it also works as a smoke test in a cron job or a CI step.

A healthy run ends with:

```
Summary
  25 passed   0 warnings   0 failed

  Everything checks out.
```

Useful variants:

```bash
INSECURE=1 ./docker/verify.sh       # while on a staging certificate
SKIP_PUBLIC=1 ./docker/verify.sh    # before DNS has propagated
HTTP_PORT=8080 HTTPS_PORT=8443 ./docker/verify.sh   # non-standard ports
```

### Then check it by hand

The script cannot judge whether the app *looks* right. Open a browser:

| Check | Where |
|---|---|
| Certificate is trusted — padlock, no warning | `https://umrahcompany.co.uk` |
| Login works | `/auth/login/` — your superuser |
| Dashboard renders with CSS and icons | `/dashboard/` |
| The AI chat replies | **Conversations → Widget preview**, send "hotel in Dubai for 4 people" |
| A lead was captured from that chat | **CRM → Leads** — a new *AI Chat* lead |
| Inventory search returns results | **Inventory → Search** |
| Admin loads | `/admin/` |

Sending a widget message and then finding the lead in the CRM exercises the
whole path in one go: nginx → gunicorn → SQLite write → inventory search → lead
capture. If that works, the deployment is sound.

### If something fails

```bash
docker compose logs --tail 50 web      # tracebacks, gunicorn boot errors
docker compose logs --tail 50 nginx    # TLS and upstream errors
docker compose ps                      # who is up, who is unhealthy
```

The troubleshooting table at the end of this file covers the usual causes.

---

## Continuous deployment

[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) runs the test
suite on every push to `master`, and **only if it passes** SSHes into the VPS
and rolls the stack onto the new commit.

If the new build fails its health check, the previous commit is restored and
rebuilt automatically — a bad push costs one build's downtime instead of
staying broken until someone notices. The workflow run is marked failed either
way, so you find out.

### 1. Make a deploy key for GitHub → VPS

On your **laptop** (not the VPS):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/sacred_deploy -N "" -C "github-actions-deploy"
```

Authorise the public half on the VPS:

```bash
ssh-copy-id -i ~/.ssh/sacred_deploy.pub root@<vps-ip>
# or: cat ~/.ssh/sacred_deploy.pub | ssh root@<vps-ip> 'cat >> ~/.ssh/authorized_keys'
```

Confirm it works before going further:

```bash
ssh -i ~/.ssh/sacred_deploy root@153.92.210.143 'echo connected'
```

### 2. Add the repository secrets

**Settings → Secrets and variables → Actions → New repository secret** on
`github.com/lutonumrah/sacred-travel-ai`:

| Secret | Value | Required |
|---|---|---|
| `VPS_HOST` | `153.92.210.143` | yes |
| `VPS_USER` | `root` (or your deploy user) | yes |
| `VPS_SSH_KEY` | the **private** key: `cat ~/.ssh/sacred_deploy` — whole file, including the BEGIN/END lines | yes |
| `VPS_KNOWN_HOSTS` | `ssh-keyscan -H <vps-ip>` output | recommended |
| `SITE_URL` | `https://umrahcompany.co.uk` | recommended |
| `VPS_PORT` | SSH port, if not 22 | no |
| `VPS_APP_DIR` | app path, if not `/opt/scared-travel-ai` | no |

`VPS_KNOWN_HOSTS` pins the server's host key. Without it the workflow accepts
whatever key answers on first connection and logs a warning — fine for a first
run, worth setting properly after:

```bash
ssh-keyscan -H 153.92.210.143
```

`SITE_URL` makes the workflow verify the public URL after deploying, so a
container that is healthy internally but unreachable through nginx still fails
the run.

### 3. Let the VPS pull from GitHub

The deploy script runs `git fetch` on the VPS, which needs read access.

**Public repo** — nothing to do, as long as it was cloned over HTTPS.

**Private repo** — give the VPS its own key and register it on GitHub as a
read-only deploy key:

```bash
# on the VPS
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

Paste that into **Settings → Deploy keys → Add deploy key** (leave *Allow write
access* unchecked), then switch the remote to SSH:

```bash
cd /opt/scared-travel-ai
git remote set-url origin git@github.com:lutonumrah/sacred-travel-ai.git
ssh -T git@github.com   # accept the host key once
git fetch origin        # must succeed before CD will work
```

### 4. Push

```bash
git push origin master
```

Watch it under the repo's **Actions** tab. To deploy the current `master`
without pushing anything, use **Actions → Deploy → Run workflow**.

### Deploying by hand

The same script, run directly on the VPS — identical behaviour, rollback
included:

```bash
cd /opt/scared-travel-ai && ./docker/deploy.sh
```

### Notes

- Deploys are serialised (`concurrency: deploy-production`) and never cancelled
  mid-run, so two quick pushes queue rather than colliding over a migration.
- `git reset --hard` is used rather than `git pull`, so the VPS always matches
  `origin/master` exactly even if something was edited on the box. Only tracked
  files are touched — `.env`, `data/db.sqlite3` and `media/` are gitignored and
  survive untouched.
- **Migrations run automatically** on every deploy, before the health check.
  An irreversible migration cannot be undone by the rollback — the code reverts
  but the schema does not. Take a backup before deploying a destructive one.
- Old images are pruned after each successful deploy so the VPS disk does not
  fill up.
- Want a manual approval gate? Create a `production` environment with required
  reviewers in repo settings and add `environment: production` to the `deploy`
  job.

---

## Bringing your existing database

To carry over the local `db.sqlite3` rather than starting empty:

```bash
# from your laptop
scp db.sqlite3 root@<vps-ip>:/tmp/db.sqlite3

# on the VPS
docker compose up -d web
docker compose cp /tmp/db.sqlite3 web:/app/data/db.sqlite3
docker compose exec web python manage.py migrate
docker compose restart web
```

---

## Day-to-day

```bash
docker compose logs -f web          # application logs
docker compose logs -f nginx        # access + TLS logs
docker compose restart web          # restart the app
docker compose down                 # stop (volumes survive)
docker compose exec web python manage.py shell
```

**Deploying an update** — migrations and `collectstatic` run automatically on
every boot, so this is the whole procedure:

```bash
git pull
docker compose up -d --build
```

**Back up the database** (SQLite in WAL mode must be copied with `.backup`, not
`cp`, or you can capture a torn file):

```bash
docker compose exec web python -c \
  "import sqlite3; s=sqlite3.connect('/app/data/db.sqlite3'); \
   d=sqlite3.connect('/app/data/backup.sqlite3'); s.backup(d); d.close(); s.close()"
docker compose cp web:/app/data/backup.sqlite3 ./backup-$(date +%F).sqlite3
```

Worth a nightly cron on the host:

```bash
0 3 * * * cd /opt/scared-travel-ai && docker compose exec -T web python -c "import sqlite3; s=sqlite3.connect('/app/data/db.sqlite3'); d=sqlite3.connect('/app/data/backup.sqlite3'); s.backup(d); d.close(); s.close()" && docker compose cp web:/app/data/backup.sqlite3 /root/backups/db-$(date +\%F).sqlite3
```

---

## The widget embed

Once live, each website's detail page shows the snippet. It should read:

```html
<script src="https://umrahcompany.co.uk/static/js/widget.js"
        data-scared-key="pk_..."
        data-scared-api="https://umrahcompany.co.uk"
        data-scared-color="#0F766E"
        data-scared-title="Your Brand" defer></script>
```

---

## Certificate renewal

The certbot container retries every 12 hours and nginx reloads every 6, so
renewal is automatic. To check:

```bash
docker compose exec certbot certbot certificates
docker compose run --rm --entrypoint "certbot renew --dry-run" certbot
```

---

## Notes on this setup

**SQLite under gunicorn.** `config/settings.py` enables WAL mode, a 20s busy
timeout and `IMMEDIATE` transactions. Without WAL, two gunicorn workers hitting
the same file raise `database is locked` under even light traffic. It is sized
for a single VPS and modest write volume — if the CRM gets busy, switch
`DATABASE_URL` to a `postgres://` URL (already supported in settings, and
`psycopg2-binary` is already installed) and add a `db` service.

**Scaling gunicorn.** Workers and threads are set in the `Dockerfile` `CMD`
(`--workers 2 --threads 4`). Raising *workers* increases SQLite write
contention; raise *threads* first.

**nginx templating.** `DOMAIN` is substituted into
`docker/nginx/app.conf.template` at container start. `NGINX_ENVSUBST_FILTER`
restricts substitution to that one variable so nginx's own `$host` and
`$request_uri` are left intact. Because a custom `command` bypasses the image's
automatic templating, the compose file invokes the envsubst script explicitly.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `DisallowedHost` in the logs | Domain missing from `ALLOWED_HOSTS` |
| Login form returns **403 CSRF** | `CSRF_TRUSTED_ORIGINS` missing the exact `https://host`, or a port/host mismatch |
| CSS and images missing | nginx cannot read the `static_files` volume — check `docker compose logs nginx` |
| certbot: *challenge failed* | DNS not resolving to the VPS yet, or port 80 blocked by `ufw` / hPanel firewall |
| nginx: *cannot load certificate* | `./docker/init-letsencrypt.sh` has not been run, or `DOMAIN` disagrees with the issued certificate |
| `database is locked` | More gunicorn workers than the volume can take — lower `--workers` |
| Redirect loop | `USE_X_FORWARDED_PROTO` not `True` while nginx terminates TLS |
| CD fails at *Run deploy script* | `VPS_SSH_KEY` truncated (must include the BEGIN/END lines), wrong `VPS_USER`, or the key not in the VPS's `authorized_keys` |
| CD fails at `git fetch` | Private repo without a deploy key on the VPS, or the remote still on HTTPS |
| Deploy says *rolled back* | The pushed commit fails its health check — see `docker compose logs web` on the VPS |
