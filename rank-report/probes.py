"""Per-source health checks.

Every sub-function that reaches for data goes through Probes.run(). It records
whether that source produced anything, and if not, why — so two audiences are
served from one pass:

  staff   a diagnostics rail listing each source with a status and a remedy,
          so a broken key or a dead endpoint is visible the moment the report
          is generated rather than three clients later.

  client  a section that could not be measured says so, with the reason. A
          failed fetch must never render as a zero. "0 referring domains"
          and "we could not reach the link graph" are different statements
          and a client is entitled to know which one they are reading.
"""

import time

OK = "ok"                    # ran, returned usable data
EMPTY = "empty"              # ran cleanly, the source genuinely holds nothing
FAILED = "failed"            # raised, timed out, or returned malformed data
NOT_CONFIGURED = "not_conf"  # a key or an input this run was never given
SKIPPED = "skipped"          # a prerequisite failed, so this never ran

# Order matters: worst first, so the rail sorts by what needs attention.
SEVERITY = {FAILED: 0, SKIPPED: 1, NOT_CONFIGURED: 2, EMPTY: 3, OK: 4}

LABEL = {
    OK: "ok",
    EMPTY: "no data",
    FAILED: "failed",
    NOT_CONFIGURED: "not set up",
    SKIPPED: "skipped",
}


class Probes:
    def __init__(self):
        self.rows = []
        self._by_key = {}

    # ── recording ────────────────────────────────────────────────────────────

    def run(self, key, label, fn, *, check=None, source="", remedy="",
            client_note="", requires=None, configured=True, fallback=None):
        """Call fn() and record what came back.

        check        result -> bool. True means the source produced something
                     usable. Defaults to plain truthiness, which is wrong for
                     anything that legitimately returns 0, so pass one.
        source       where the data came from, shown to the client.
        remedy       what a staff member does about a failure. Staff only.
        client_note  what the report says in place of the missing figure.
        requires     keys of earlier probes that must be ok/empty for this to
                     run at all.
        configured   False when the input or key for this run was never given.
        fallback     returned when the probe does not produce data.
        """
        if requires:
            dead = [self._by_key.get(k, {}) for k in requires
                    if self._by_key.get(k, {}).get("status")
                    in (FAILED, NOT_CONFIGURED, SKIPPED)]
            if dead:
                # A step blocked by something that merely was not set up is
                # itself not set up — not a failure. Reporting "failed" for an
                # empty form field sends someone hunting a fault that is not
                # there. Only a real break upstream makes this a break.
                broke = any(d.get("status") == FAILED for d in dead)
                inherited = SKIPPED if broke else NOT_CONFIGURED
                names = ", ".join(d.get("label", "?") for d in dead)
                return self._record(key, label, inherited, source, remedy, client_note,
                                    f"needs {names}", 0, fallback)

        if not configured:
            return self._record(key, label, NOT_CONFIGURED, source, remedy, client_note,
                                "", 0, fallback)

        t0 = time.time()
        try:
            out = fn()
        except Exception as exc:                      # noqa: BLE001 — any failure is a finding
            ms = int((time.time() - t0) * 1000)
            return self._record(key, label, FAILED, source, remedy, client_note,
                                f"{type(exc).__name__}: {exc}"[:200], ms, fallback)
        ms = int((time.time() - t0) * 1000)

        good = check(out) if check else bool(out)
        if good:
            self._record(key, label, OK, source, remedy, client_note, "", ms, out)
            return out
        return self._record(key, label, EMPTY, source, remedy, client_note, "", ms,
                            out if out is not None else fallback)

    def note(self, key, label, status, *, source="", remedy="", client_note="", detail=""):
        """Record a status decided by the caller rather than by a call."""
        self._record(key, label, status, source, remedy, client_note, detail, 0, None)

    def _record(self, key, label, status, source, remedy, client_note, detail, ms, value):
        row = {"key": key, "label": label, "status": status, "source": source,
               "remedy": remedy, "client_note": client_note, "detail": detail, "ms": ms}
        self.rows.append(row)
        self._by_key[key] = row
        return value

    # ── reading ──────────────────────────────────────────────────────────────

    def status(self, key):
        return (self._by_key.get(key) or {}).get("status")

    def ok(self, key):
        return self.status(key) == OK

    def client_note(self, key):
        """The sentence a section prints when its source produced nothing."""
        r = self._by_key.get(key)
        if not r or r["status"] == OK:
            return None
        if r["client_note"]:
            return r["client_note"]
        return {
            EMPTY: "This source holds no data for this site yet.",
            FAILED: "We could not reach this source when the report was generated. "
                    "The figure is missing, not zero.",
            NOT_CONFIGURED: "This source was not connected for this report.",
            SKIPPED: "This could not be measured because an earlier step failed.",
        }[r["status"]]

    def failures(self):
        """Anything a staff member should look at, worst first."""
        return sorted([r for r in self.rows if r["status"] in (FAILED, SKIPPED)],
                      key=lambda r: SEVERITY[r["status"]])

    def attention(self):
        return sorted([r for r in self.rows if r["status"] != OK],
                      key=lambda r: SEVERITY[r["status"]])

    def summary(self):
        c = {s: 0 for s in SEVERITY}
        for r in self.rows:
            c[r["status"]] += 1
        return {"total": len(self.rows), "counts": c,
                # broken     something that should have worked did not
                # unconnected  an input or key this run was never given
                # Kept apart because they call for different actions: one is a
                # fault to fix, the other is an access request to the client.
                "healthy": c[FAILED] == 0 and c[SKIPPED] == 0,
                "broken": c[FAILED] + c[SKIPPED],
                "unconnected": c[NOT_CONFIGURED],
                "degraded": c[FAILED] + c[SKIPPED]}

    def as_dict(self):
        return {"checks": self.rows, "summary": self.summary()}
