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

1. **Tunnel** — add a public hostname to the existing `cloudflared` config
   (e.g. `audit.boldpiq.com` → `http://localhost:8090`), or add a service to the
   existing tunnel container. No firewall port needs opening; the tunnel dials out.

2. **Access policy** — Cloudflare Zero Trust → Access → Applications → Add:
   - Type: Self-hosted
   - Domain: `audit.boldpiq.com`
   - Policy: Allow → *Emails* (list each staff member) or *Email domain ending in*
     your company domain
   - Session duration: 24 hours is reasonable for internal tooling

   Free tier covers up to 50 users.

3. **Verify the lock actually holds.** Open the hostname in a private window and
   confirm you get the Cloudflare login *before* the app. Then, on the box:

   ```bash
   ss -tlnp | grep 8090      # must show 127.0.0.1:8090, never 0.0.0.0:8090
   ```

   This is the check that was missed when Zammad ended up internet-exposed. Do
   it every time.

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
