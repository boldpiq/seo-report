# Deploying the web app

The CLI still works exactly as it did — `./seo-report.sh clientdomain.co.za`.
This document covers the browser front end, which exists so staff can run an
audit without touching a terminal.

## What it is

A small Python service (`webapp/server.py`, standard library only) that wraps
the CLI. Staff open a URL, type a website address, and get the same branded PDF.

- Jobs run **strictly one at a time**, with a 6-second gap between them.
  seoscore.tools' terms forbid excessive scanning, and one headless Chrome at a
  time is all a small box should be running anyway.
- **There is no login in the app.** Anyone who can reach the port can queue
  scans. Access control is Cloudflare's job — see below.
- Reports persist in a Docker volume and are listed on the page, so a report can
  be re-downloaded without re-scanning.

## Run it locally (Mac, for testing)

```bash
cd ~/boldpiq-tools/seo-report
python3 webapp/server.py            # http://127.0.0.1:8090
```

Uses the Chrome and Lighthouse already on the machine. `PORT=8091` to change port.

## Deploy on the Hetzner box

> **Reality check:** `/opt/seo-report` on `boldpiq-hz` is **not** a git checkout —
> it was copied up with `scp`/`rsync` and the files are owned by uid 501. To ship a
> change, copy the changed file(s) up and rebuild; `git pull` there will fail.
> `scp seo_report.py boldpiq-hz:/opt/seo-report/` then the rebuild below.

```bash
ssh <box>
git clone git@github-boldpiq:boldpiq/seo-report.git /opt/seo-report
cd /opt/seo-report
docker compose up -d --build          # first build ~5 min (Chromium + Lighthouse)
docker compose logs -f audit          # confirm: browser found, lighthouse found
curl -s localhost:8090/api/health     # {"ok": true, "browser": "Chromium", ...}
```

The image carries its own Chromium and Lighthouse 13 — nothing is installed on
the host, and nothing is downloaded at scan time.

**The port binds to `127.0.0.1` only.** That is deliberate. Do not change it to
`0.0.0.0`; the app has no authentication of its own.

### Resource notes

Chromium is memory-hungry in bursts. `docker-compose.yml` caps the container at
2 GB / 1.5 CPU so a runaway scan cannot take n8n or Zammad down with it. Check
the box has that headroom spare before deploying:

```bash
free -m && nproc && docker stats --no-stream
```

If it doesn't, the honest options are a bigger box or keeping this on the Mac.
Do not remove the cap to make it fit.

## Put Cloudflare Access in front

Same pattern as n8n. Two pieces: a Tunnel to reach it, and an Access policy to
decide who may.

1. **Tunnel** — this app runs its **own** `cloudflared` connector
   (`boldpiq-audit-tunnel`, tunnel `ee8e3dbf-…`), deliberately NOT the shared n8n
   tunnel. The shared one proxies its catch-all to n8n, so any unrouted hostname
   on it serves the n8n UI. A dedicated tunnel with one origin cannot.

   Its routes live in the Cloudflare dashboard — the tunnel is token-managed, so
   there is nothing to edit over SSH. Current config:

   ```
   ingress: audit.boldpiq.com → http://boldpiq-audit:8090
            catch-all         → http_status:404
   ```

   **The dashboard config overrides the `--url` flag in docker-compose.yml.**
   That flag is a fallback only; to add or change a hostname, do it in
   Zero Trust → Networks → Tunnels → boldpiq-audit → Public Hostname.

   `TUNNEL_TOKEN` lives in `/opt/seo-report/.env` (git-ignored, chmod 600). It is
   a credential — anyone holding it can connect a tunnel into the account.

2. **Access policy** — Cloudflare Zero Trust → Access → Applications → Add:
   - Type: Self-hosted
   - Domain: `audit.boldpiq.com`
   - Policy: Allow → *Emails* (list each staff member) or *Email domain ending in*
     your company domain
   - Session duration: 24 hours is reasonable for internal tooling

   Free tier covers up to 50 users.

3. **Verify the lock actually holds — properly.** One request is not enough.
   Access policies propagate across Cloudflare's edge over seconds to minutes,
   and during that window *some* edges enforce while others do not. A single
   200 among twenty 302s is an open endpoint, not a fluke.

   ```bash
   # 20 requests; any that render the app are a leak
   for i in $(seq 1 20); do
     code=$(curl -s -o /tmp/v -w "%{http_code}" https://audit.boldpiq.com/)
     grep -q 'Boldpiq · Internal' /tmp/v && echo "#$i $code LEAK" || echo "#$i $code gated"
     sleep 3
   done
   ss -tlnp | grep 8090      # must show 127.0.0.1:8090, never 0.0.0.0:8090
   ```

   **Order matters.** Confirm the gate enforces on a hostname BEFORE pointing a
   live origin at it. During this deploy the Access app was repointed while the
   original hostname was still routed, and the tool was briefly served
   unauthenticated. If you must change hostname or policy, stop the connector
   first (`docker stop boldpiq-audit-tunnel`), make the change, verify 20/20,
   then start it again.

   This is the same check that was missed when Zammad ended up internet-exposed.

## Updating

```bash
cd /opt/seo-report && git pull && docker compose up -d --build
```

Staff reload the page. There is nothing for them to install or update — which is
the whole reason this is a web app rather than a desktop one.

## Backup

Client PDFs and scan JSON live in the `audit-reports` volume. They are
regenerable from a re-scan, so they are not critical — but if you want them in
the offsite restic backup, add the volume path:

```bash
docker volume inspect boldpiq-audit_audit-reports -f '{{.Mountpoint}}'
```
