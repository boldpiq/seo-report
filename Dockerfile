# Boldpiq Website Audit — web front end
#
# Everything the report needs in one image: Python (stdlib only), Node,
# Lighthouse 13 and Chromium. Nothing is fetched at runtime, so a scan works
# the moment the container starts.

FROM node:22-bookworm-slim

# chromium  — runs Lighthouse and prints the PDF
# fonts-*   — without these the PDF renders boxes instead of text, and the
#             report is full of client-facing typography
# tini      — reaps the zombie Chrome processes Lighthouse leaves behind
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        chromium \
        fonts-liberation \
        fonts-dejavu-core \
        fonts-noto-color-emoji \
        ca-certificates \
        tini \
    && rm -rf /var/lib/apt/lists/*

# Lighthouse 13+ is required — Agentic Browsing does not exist before it.
RUN npm install -g lighthouse@13 && npm cache clean --force

ENV BOLDPIQ_CHROME=/usr/bin/chromium \
    BOLDPIQ_REPORTS=/data/reports \
    PYTHONUNBUFFERED=1 \
    PORT=8090 \
    HOST=0.0.0.0

WORKDIR /app
COPY checks.py fixpack.py lighthouse.py platforms.py runtime.py seo_report.py ./
COPY assets/ ./assets/
COPY webapp/ ./webapp/

# Unprivileged — Chromium runs with --no-sandbox (set in runtime.py) because the
# container has no SYS_ADMIN capability. Keeping the process off root is the
# trade we make for that.
RUN useradd --system --create-home --uid 10001 audit \
    && mkdir -p /data/reports \
    && chown -R audit:audit /data /app
USER audit

EXPOSE 8090
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "webapp/server.py"]

HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request,sys; \
      sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/api/health',timeout=8).status==200 else 1)"
