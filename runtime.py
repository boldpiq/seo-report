"""
Runtime discovery — where Chrome, Node and Lighthouse actually live.

The report itself is pure standard library, but it shells out to two things:
Chrome (to run Lighthouse and to print the PDF) and the Lighthouse CLI. Those
sit in different places on a Mac, on Windows, and inside the Linux container
the web app runs in. This module is the only part of the tool that has to care.

Every lookup can be overridden with an environment variable, which is how the
Docker image pins the exact binaries it ships with.
"""

import glob
import os
import platform
import shutil
import subprocess

HOME = os.path.expanduser("~")
SYSTEM = platform.system()          # 'Darwin' | 'Windows' | 'Linux'


# ── chrome ───────────────────────────────────────────────────────────────────

CHROME_CANDIDATES = {
    "Darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""),
                     r"Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "Linux": [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ],
}

# Chrome refuses to run as root without this, which is exactly what happens
# inside a container. Harmless everywhere else — we only ever load local files
# and pages we are deliberately auditing.
#
# --disable-software-rasterizer is load-bearing, not tidying. With --disable-gpu
# alone Chrome falls back to SwiftShader and renders WebGL on the CPU; a site
# with a continuous canvas animation (boldpiq.com's own 3D hero, for one) then
# pins the main thread until Lighthouse gives up with PAGE_HUNG. That failure was
# 100% reproducible in the container and invisible in the report, because
# Lighthouse still exits 0 and writes a JSON full of nulls.
# The backgrounding flags stop Chrome throttling the tab it is measuring when it
# decides the headless window is not visible.
CHROME_FLAGS = ["--headless=new", "--disable-gpu", "--no-sandbox",
                "--disable-dev-shm-usage", "--disable-software-rasterizer",
                "--disable-renderer-backgrounding",
                "--disable-backgrounding-occluded-windows",
                "--disable-extensions", "--mute-audio"]


def find_chrome():
    """Absolute path to a Chromium-family browser, or None."""
    override = os.environ.get("BOLDPIQ_CHROME")
    if override:
        return override if os.path.exists(override) else None

    for path in CHROME_CANDIDATES.get(SYSTEM, []):
        if path and os.path.exists(path):
            return path

    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def chrome_label():
    """Human-readable name of whatever we found — for the UI and error text."""
    path = find_chrome()
    if not path:
        return None
    low = path.lower()
    if "edge" in low:
        return "Microsoft Edge"
    if "chromium" in low:
        return "Chromium"
    return "Google Chrome"


# ── node + lighthouse ────────────────────────────────────────────────────────

def node_dir():
    """Directory containing the node binary, including fnm/nvm installs."""
    override = os.environ.get("BOLDPIQ_NODE_DIR")
    if override and os.path.isdir(override):
        return override

    found = shutil.which("node") or shutil.which("node.exe")
    if found:
        return os.path.dirname(found)

    for pat in ("/.local/share/fnm/node-versions/*/installation/bin/node",
                "/.fnm/node-versions/*/installation/bin/node",
                "/.nvm/versions/node/*/bin/node"):
        hits = sorted(glob.glob(HOME + pat))
        if hits:
            return os.path.dirname(hits[-1])
    return None


def lighthouse_bin():
    """Path to the Lighthouse CLI, or None."""
    override = os.environ.get("BOLDPIQ_LIGHTHOUSE")
    if override:
        return override if os.path.exists(override) else None

    suffixes = [".cmd", ".ps1", ""] if SYSTEM == "Windows" else [""]
    roots = [
        os.path.join(HOME, "boldpiq-tools/site-audit/node_modules/.bin/lighthouse"),
        os.path.join(HOME, "boldpiq-tools/seo-report/node_modules/.bin/lighthouse"),
        "/usr/local/lib/node_modules/lighthouse/cli/index.js",
        "/opt/lighthouse/node_modules/.bin/lighthouse",
    ]
    for root in roots:
        for suffix in suffixes:
            if os.path.exists(root + suffix):
                return root + suffix

    return shutil.which("lighthouse")


# ── misc ─────────────────────────────────────────────────────────────────────

def open_files(paths):
    """Open finished PDFs in the OS default viewer. No-op on a headless server."""
    if not paths:
        return
    try:
        if SYSTEM == "Darwin":
            subprocess.run(["open"] + list(paths), check=False)
        elif SYSTEM == "Windows":
            for p in paths:
                os.startfile(p)              # noqa: S606  (Windows-only API)
        else:
            subprocess.run(["xdg-open"] + list(paths), check=False)
    except (OSError, AttributeError):
        pass                                  # not worth failing a good report over


def deliver_dir():
    """Where a finished PDF gets dropped for convenience.

    On a workstation that's the Desktop. In a container there isn't one, so the
    caller falls back to the reports directory.
    """
    override = os.environ.get("BOLDPIQ_DELIVER_DIR")
    if override:
        return override
    desktop = os.path.join(HOME, "Desktop")
    return desktop if os.path.isdir(desktop) else None
