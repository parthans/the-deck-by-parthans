#!/usr/bin/env python3
"""Deck: a touch control panel for your Mac, served to an iPad browser.

Runs on the Mac with the Python that ships with macOS. No packages to install.
Everything the panel can do is defined in config.json. The iPad only ever
sends the id of a tile, never a command, so the panel cannot run anything
you have not listed.
"""

import hmac
import json
import os
import plistlib
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
CERTS = ROOT / "certs"
AGENDA_BIN = ROOT / "bin" / "agenda"
STATE = Path.home() / ".deck"
STATE.mkdir(parents=True, exist_ok=True)
(STATE / "art").mkdir(exist_ok=True)
(STATE / "icons").mkdir(exist_ok=True)

DRY_RUN = os.environ.get("DECK_DRY_RUN") == "1"
UTF8_ENV = dict(os.environ, LANG="en_US.UTF-8", LC_ALL="en_US.UTF-8")
UTF8_ENV["PATH"] = UTF8_ENV.get("PATH", "") + ":/opt/homebrew/bin:/usr/local/bin"

SECTIONS = ["apps", "sites", "workspaces", "call", "focus", "system", "shortcuts"]
MAX_UPLOAD = 60 * 1024 * 1024  # 60 MB of audio is about an hour


def log(*parts):
    print(datetime.now().strftime("%H:%M:%S"), *parts, flush=True)


# ---------------------------------------------------------------- config

class Config:
    """Reloads config.json whenever the file changes, so edits need no restart."""

    def __init__(self, path):
        self.path = path
        self.mtime = 0
        self.data = {}
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            m = self.path.stat().st_mtime
            if m != self.mtime:
                with open(self.path, encoding="utf-8") as f:
                    self.data = json.load(f)
                self.mtime = m
                log("config loaded")
            return self.data

    def tiles(self):
        out = {}
        for section in SECTIONS:
            for tile in self.get().get(section, []):
                out[tile["id"]] = tile
        return out


CONFIG = Config(ROOT / "config.json")


# ---------------------------------------------------------------- running things

class ActionError(Exception):
    pass


def run(cmd, input_text=None, timeout=15):
    if DRY_RUN:
        log("DRY", cmd if input_text is None else (cmd, input_text[:200]))
        return 0, "", ""
    try:
        p = subprocess.run(cmd, input=input_text, capture_output=True, text=True,
                           timeout=timeout, env=UTF8_ENV)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except FileNotFoundError:
        return 127, "", f"{cmd[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"


def osa(script, timeout=15):
    return run(["osascript", "-"], input_text=script, timeout=timeout)


def as_str(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


KEYCODES = {
    "return": 36, "enter": 76, "tab": 48, "space": 49, "delete": 51,
    "escape": 53, "esc": 53, "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97,
    "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}
MODS = {
    "cmd": "command down", "command": "command down",
    "shift": "shift down",
    "opt": "option down", "option": "option down", "alt": "option down",
    "ctrl": "control down", "control": "control down",
}


def keystroke_script(combo):
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ActionError("Empty key combination")
    key, mods = parts[-1], []
    for p in parts[:-1]:
        if p not in MODS:
            raise ActionError(f"Unknown modifier '{p}' in {combo}")
        mods.append(MODS[p])
    using = f" using {{{', '.join(mods)}}}" if mods else ""
    if key in KEYCODES:
        return f'tell application "System Events" to key code {KEYCODES[key]}{using}'
    if len(key) != 1:
        raise ActionError(f"Unknown key '{key}' in {combo}")
    return f'tell application "System Events" to keystroke "{as_str(key)}"{using}'


def press_keys(combo):
    code, _, err = osa(keystroke_script(combo))
    if code != 0:
        raise ActionError(accessibility_hint(err))


def accessibility_hint(err):
    if "not allowed" in err or "1002" in err or "assistive" in err.lower():
        return ("macOS blocked the key press. Allow Terminal in System Settings, "
                "Privacy and Security, Accessibility.")
    return err or "Key press failed"


def activate(app):
    code, _, err = osa(f'tell application "{as_str(app)}" to activate')
    if code != 0:
        raise ActionError(f"Could not open {app}")
    time.sleep(0.35)


def pbcopy(text):
    code, _, err = run(["pbcopy"], input_text=text)
    if code != 0:
        raise ActionError("Could not set the clipboard")


def pbpaste():
    code, out, _ = run(["pbpaste"])
    return out[:20000] if code == 0 else ""


# ---------------------------------------------------------------- built-in actions

MIC_FILE = STATE / "mic_volume"


def mic_toggle():
    code, out, _ = osa("input volume of (get volume settings)")
    current = int(float(out)) if code == 0 and out not in ("", "missing value") else 50
    if DRY_RUN:
        current = 50
    if current > 0:
        MIC_FILE.write_text(str(current))
        osa("set volume input volume 0")
        return {"mic": "muted"}
    restore = 75
    try:
        restore = int(MIC_FILE.read_text()) or 75
    except (OSError, ValueError):
        pass
    osa(f"set volume input volume {restore}")
    return {"mic": "live"}


def mic_state():
    code, out, _ = osa("input volume of (get volume settings)")
    if code != 0 or out in ("", "missing value"):
        return "unknown"
    return "muted" if int(float(out)) == 0 else "live"


def screenshot():
    name = datetime.now().strftime("Deck screenshot %Y-%m-%d at %H.%M.%S.png")
    code, _, err = run(["screencapture", "-x", str(Path.home() / "Desktop" / name)])
    if code != 0:
        raise ActionError("Screenshot failed. Allow Terminal under Screen Recording.")
    return {"toast": "Saved to Desktop"}


BUILTINS = {
    "lock": lambda: press_keys("ctrl+cmd+q"),
    "display_sleep": lambda: run(["pmset", "displaysleepnow"]),
    "screenshot": screenshot,
    "mission_control": lambda: run(["open", "-a", "Mission Control"]),
    "spotlight": lambda: press_keys("cmd+space"),
    "vol_up": lambda: osa("set volume output volume ((output volume of (get volume settings)) + 6)"),
    "vol_down": lambda: osa("set volume output volume ((output volume of (get volume settings)) - 6)"),
    "mute_toggle": lambda: osa("set volume output muted not (output muted of (get volume settings))"),
    "mic_toggle": mic_toggle,
    "music_playpause": lambda: osa('tell application "Music" to playpause'),
    "music_next": lambda: osa('tell application "Music" to next track'),
    "music_prev": lambda: osa('tell application "Music" to previous track'),
}


def do_action(do, mode=None, depth=0):
    if depth > 3:
        raise ActionError("Workspace steps nest too deep")

    if "steps" in do:
        tiles = CONFIG.tiles()
        for step in do["steps"]:
            if step not in tiles:
                raise ActionError(f"Workspace step '{step}' is not a tile id")
            do_action(tiles[step]["do"], depth=depth + 1)
            time.sleep(0.5)
        return None

    if "open_app" in do:
        app = do["open_app"]
        if mode == "quit":
            osa(f'tell application "{as_str(app)}" to quit')
            return {"toast": f"Quit {app}"}
        code, _, _ = run(["open", "-a", app])
        if code != 0:
            if do.get("or_url"):
                return open_url(do["or_url"], do.get("browser"))
            raise ActionError(f"{app} is not installed")
        return None

    if "open_url" in do:
        return open_url(do["open_url"], do.get("browser"))

    if "shortcut" in do:
        name = do["shortcut"]
        code, _, err = run(["shortcuts", "run", name], timeout=60)
        if code != 0:
            if "Couldn" in err or "find" in err.lower():
                raise ActionError(f"No shortcut named '{name}' in the Shortcuts app")
            raise ActionError(err or f"Shortcut '{name}' failed")
        return None

    if "keys" in do:
        if do.get("app"):
            activate(do["app"])
        press_keys(do["keys"])
        return None

    if "applescript" in do:
        code, out, err = osa(do["applescript"], timeout=30)
        if code != 0:
            raise ActionError(err or "Script failed")
        return {"toast": out} if out else None

    if "builtin" in do:
        fn = BUILTINS.get(do["builtin"])
        if not fn:
            raise ActionError(f"Unknown builtin '{do['builtin']}'")
        result = fn()
        return result if isinstance(result, dict) else None

    raise ActionError("Tile has nothing to do")


# Browsers whose tabs Deck can look through. Each needs a one-time "Python wants to
# control Safari" (or Chrome) approval from macOS the first time.
TAB_LIST = {
    "Safari": """
if application "Safari" is running then
  set sep to (ASCII character 9)
  set nl to (ASCII character 10)
  tell application "Safari"
    set out to ""
    set wi to 0
    repeat with w in windows
      set wi to wi + 1
      set ti to 0
      try
        repeat with t in tabs of w
          set ti to ti + 1
          set u to ""
          try
            set u to (URL of t) as text
          end try
          if u is "missing value" then set u to ""
          set out to out & (wi as text) & sep & (ti as text) & sep & u & nl
        end repeat
      end try
    end repeat
    return out
  end tell
end if
return ""
""",
    "Google Chrome": """
if application "Google Chrome" is running then
  set sep to (ASCII character 9)
  set nl to (ASCII character 10)
  tell application "Google Chrome"
    set out to ""
    set wi to 0
    repeat with w in windows
      set wi to wi + 1
      set ti to 0
      repeat with t in tabs of w
        set ti to ti + 1
        set u to ""
          try
            set u to (URL of t) as text
          end try
          if u is "missing value" then set u to ""
          set out to out & (wi as text) & sep & (ti as text) & sep & u & nl
      end repeat
    end repeat
    return out
  end tell
end if
return ""
""",
}
TAB_SELECT = {
    "Safari": 'tell application "Safari"\nset w to window {w}\nset current tab of w to tab {t} of w\nset index of w to 1\nactivate\nend tell',
    "Google Chrome": 'tell application "Google Chrome"\nset w to window {w}\nset active tab index of w to {t}\nset index of w to 1\nactivate\nend tell',
}


def site_host(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def focus_existing_tab(url, browser=None):
    """Bring forward a tab already showing the same site. True if one was found."""
    want = site_host(url)
    if not want:
        return False
    browsers = [browser] if browser in TAB_LIST else list(TAB_LIST)
    for name in browsers:
        code, out, err = osa(TAB_LIST[name], timeout=8)
        if code != 0:
            log("tab search failed in", name, err[:200])
            continue
        if not out:
            continue
        for line in out.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
                continue
            if site_host(parts[2]) == want:
                code, _, err = osa(TAB_SELECT[name].format(w=int(parts[0]), t=int(parts[1])))
                if code == 0:
                    log("reused tab", name, want)
                    return True
                log("tab select failed in", name, err[:200])
    return False


def open_url(url, browser=None, reuse=True):
    if not re.match(r"^https?://", url):
        raise ActionError("Only http and https links can be opened")
    if "EDIT-ME" in url:
        raise ActionError("Set this tile's address in config.json")
    if reuse and focus_existing_tab(url, browser):
        return None
    cmd = ["open", "-a", browser, url] if browser else ["open", url]
    code, _, _ = run(cmd)
    if code != 0:
        raise ActionError("Could not open the link")
    return None


# ---------------------------------------------------------------- app icons

def app_path(app):
    code, out, _ = osa(f'POSIX path of (path to application "{as_str(app)}")')
    return Path(out) if code == 0 and out else None


ICON_TTL_MISS = 24 * 3600
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"


def sniff_image(data):
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"GIF8"):
        return "image/gif"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if b"<svg" in data[:600].lower():
        return "image/svg+xml"
    return None


def http_get(url, limit=1024 * 1024):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=6) as r:
        return r.geturl(), r.read(limit)


def icon_candidates(page_url, html):
    """apple-touch-icon first (large), then the biggest declared icon."""
    found = []
    for tag in re.findall(r"<link\b[^>]*>", html, flags=re.I):
        rel = re.search(r'rel=["\']?([^"\'>]+)', tag, flags=re.I)
        href = re.search(r'href=["\']?([^"\'\s>]+)', tag, flags=re.I)
        if not rel or not href or "icon" not in rel.group(1).lower():
            continue
        if "mask-icon" in rel.group(1).lower():
            continue
        size = re.search(r'sizes=["\']?(\d+)', tag, flags=re.I)
        score = int(size.group(1)) if size else 32
        if "apple-touch-icon" in rel.group(1).lower():
            score += 1000
        found.append((score, urllib.parse.urljoin(page_url, href.group(1).replace("&amp;", "&"))))
    return [u for _, u in sorted(found, reverse=True)]


def site_icon(url):
    """Fetch a website's own icon. Returns (bytes, content type) or None."""
    parts = urllib.parse.urlparse(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    tries = []
    try:
        final, page = http_get(url, 600 * 1024)
        tries += icon_candidates(final, page.decode("utf-8", "replace"))
        fparts = urllib.parse.urlparse(final)
        if fparts.netloc != parts.netloc:
            tries.append(f"{fparts.scheme}://{fparts.netloc}/apple-touch-icon.png")
    except Exception:
        pass
    tries += [origin + "/apple-touch-icon.png", origin + "/favicon.ico",
              f"https://www.google.com/s2/favicons?domain={parts.netloc}&sz=128"]
    seen = set()
    for t in tries:
        if t in seen or not t.startswith("http"):
            continue
        seen.add(t)
        try:
            _, data = http_get(t)
        except Exception:
            continue
        kind = sniff_image(data)
        if kind and len(data) > 100:
            return data, kind
    return None


def tile_section(tile_id):
    cfg = CONFIG.get()
    return next((s for s in SECTIONS if any(t["id"] == tile_id for t in cfg.get(s, []))), None)


def tile_url(do):
    return do.get("open_url") or do.get("or_url")


def custom_icon_file(tile):
    """An icon file named in config.json ("icon": "icons/my-site.png"), read fresh each time."""
    custom = tile.get("icon") or ""
    if not custom or re.match(r"^https?://", custom):
        return None
    f = Path(os.path.expanduser(custom))
    if not f.is_absolute():
        f = ROOT / f
    return f if f.is_file() and sniff_image(f.read_bytes()[:600]) else None


def tile_icon(tile_id):
    """Returns (path, content type). Order: icon set in config, website icon for
    sites, app icon for apps, website icon as the last resort."""
    tile = CONFIG.tiles().get(tile_id)
    if not tile or not re.fullmatch(r"[\w.-]+", tile_id):
        return None
    do = tile.get("do", {})
    local = custom_icon_file(tile)
    if local:
        return local, sniff_image(local.read_bytes())
    for p in (STATE / "icons").glob(f"{tile_id}.*"):
        if p.suffix == ".miss":
            if time.time() - p.stat().st_mtime < ICON_TTL_MISS:
                return None
            p.unlink(missing_ok=True)
        elif p.suffix == ".type":
            continue
        else:
            kind = (STATE / "icons" / f"{tile_id}.type")
            return p, kind.read_text() if kind.exists() else "image/png"

    custom = tile.get("icon")
    result = None
    if custom:
        if re.match(r"^https?://", custom):
            try:
                _, data = http_get(custom)
                result = (data, sniff_image(data)) if sniff_image(data) else None
            except Exception:
                result = None
        else:
            f = Path(os.path.expanduser(custom))
            if not f.is_absolute():
                f = ROOT / f
            if f.is_file() and sniff_image(f.read_bytes()):
                data = f.read_bytes()
                result = (data, sniff_image(data))
    if not result and tile_section(tile_id) != "sites" and "open_app" in do:
        app = app_icon(tile_id)
        if app:
            return app, "image/png"
    if not result and tile_url(do) and not DRY_RUN:
        result = site_icon(tile_url(do))
    if not result:
        (STATE / "icons" / f"{tile_id}.miss").touch()
        return None
    data, kind = result
    ext = {"image/svg+xml": ".svg", "image/x-icon": ".ico", "image/jpeg": ".jpg",
           "image/gif": ".gif", "image/webp": ".webp"}.get(kind, ".png")
    out = STATE / "icons" / f"{tile_id}{ext}"
    out.write_bytes(data)
    (STATE / "icons" / f"{tile_id}.type").write_text(kind)
    return out, kind


def app_icon(tile_id):
    """Pull the real icon out of the app bundle and cache it as a PNG."""
    cached = STATE / "icons" / f"{tile_id}.png"
    if cached.exists():
        return cached
    tile = CONFIG.tiles().get(tile_id)
    if not tile or "open_app" not in tile.get("do", {}):
        return None
    path = app_path(tile["do"]["open_app"])
    if not path or DRY_RUN:
        return None
    try:
        with open(path / "Contents" / "Info.plist", "rb") as f:
            info = plistlib.load(f)
    except (OSError, plistlib.InvalidFileException):
        return None
    icon = info.get("CFBundleIconFile") or info.get("CFBundleIconName")
    if not icon:
        return None
    if not icon.endswith(".icns"):
        icon += ".icns"
    icns = path / "Contents" / "Resources" / icon
    if not icns.exists():
        return None
    code, _, _ = run(["sips", "-s", "format", "png", "-Z", "160", str(icns), "--out", str(cached)])
    return cached if code == 0 and cached.exists() else None


# ---------------------------------------------------------------- music

NOW_PLAYING = """
if application "Music" is running then
  tell application "Music"
    set st to player state as text
    if st is "stopped" then return "stopped"
    try
      set t to current track
      set sep to (ASCII character 31)
      return st & sep & (name of t) & sep & (artist of t) & sep & (album of t) & sep & (persistent ID of t) & sep & (player position as text) & sep & (duration of t as text)
    on error
      return st
    end try
  end tell
else
  return "off"
end if
"""

ARTWORK = """
tell application "Music"
  try
    set d to raw data of artwork 1 of current track
  on error
    return "none"
  end try
end tell
set f to open for access POSIX file "{path}" with write permission
set eof f to 0
write d to f
close access f
return "ok"
"""

_art_lock = threading.Lock()


def now_playing():
    code, out, _ = osa(NOW_PLAYING)
    if code != 0 or not out or DRY_RUN:
        return {"state": "off"}
    parts = out.split("\x1f")
    if len(parts) < 7:
        return {"state": parts[0]}

    def num(s):
        try:
            return float(s.replace(",", "."))
        except ValueError:
            return 0.0

    state, name, artist, album, pid, pos, dur = parts[:7]
    if not re.fullmatch(r"[0-9A-Fa-f]+", pid):
        pid = ""
    if pid:
        threading.Thread(target=fetch_art, args=(pid,), daemon=True).start()
    return {"state": state, "name": name, "artist": artist, "album": album,
            "pid": pid, "position": num(pos), "duration": num(dur)}


def fetch_art(pid):
    target = STATE / "art" / pid
    if target.exists():
        return
    with _art_lock:
        if target.exists():
            return
        for old in (STATE / "art").iterdir():
            old.unlink(missing_ok=True)
        code, out, _ = osa(ARTWORK.replace("{path}", as_str(str(target))))
        if out != "ok":
            target.write_bytes(b"")


# ---------------------------------------------------------------- calendar

_agenda_cache = {"at": 0, "data": None}
_join_links = {}


def agenda(force=False):
    if not force and _agenda_cache["data"] and time.time() - _agenda_cache["at"] < 15:
        return _agenda_cache["data"]
    cfg = CONFIG.get().get("calendar", {})
    if DRY_RUN or not AGENDA_BIN.exists():
        data = {"error": "Calendar helper not built yet. Run setup.sh on the Mac."} if not DRY_RUN else demo_agenda()
    else:
        cmd = [str(AGENDA_BIN), "list", "--days", str(cfg.get("days", 2)),
               "--source", cfg.get("source", "all"), "--match", ",".join(cfg.get("match", []))]
        only = cfg.get("only_calendars") or []
        if only:
            cmd += ["--calendars", ",".join(only)]
        code, out, err = run(cmd, timeout=20)
        try:
            data = json.loads(out) if code == 0 else {"error": err or "Calendar unavailable"}
        except json.JSONDecodeError:
            data = {"error": "Calendar helper returned something unexpected"}
    if "events" in data:
        _join_links.clear()
        data = dict(data)
        data["events"] = [dict(ev) for ev in data["events"] if not ev.get("deckTask")]
        for ev in data["events"]:
            ev.pop("eid", None)
            link = ev.pop("joinUrl", None)
            if link:
                _join_links[ev["id"]] = link
                ev["canJoin"] = True
    _agenda_cache.update(at=time.time(), data=data)
    return data


def demo_agenda():
    day = now_local().replace(second=0, microsecond=0)
    def iso(h, m=0):
        return day.replace(hour=h, minute=m).isoformat()
    return {"events": [
        {"id": "d1", "title": "Discovery call", "start": iso(11, 0), "end": iso(11, 45),
         "allDay": False, "calendar": "Calendar", "location": "Microsoft Teams Meeting",
         "joinUrl": "https://teams.microsoft.com/l/meetup-join/demo"},
        {"id": "d2", "title": "Weekly review", "start": iso(16, 0), "end": iso(16, 30),
         "allDay": False, "calendar": "Calendar", "location": ""},
    ] + [dict(b) for b in _demo_blocks]}


_demo_blocks = []


def add_event(title, start, end):
    if DRY_RUN:
        return {"ok": True}
    if not AGENDA_BIN.exists():
        raise ActionError("Calendar helper not built yet. Run setup.sh on the Mac.")
    cal_cfg = CONFIG.get().get("calendar", {})
    cmd = [str(AGENDA_BIN), "add", "--title", title, "--start", start, "--end", end,
           "--source", cal_cfg.get("source", "all"), "--match", ",".join(cal_cfg.get("match", []))]
    target = cal_cfg.get("add_to_calendar")
    if target:
        cmd += ["--calendar", target]
    code, out, err = run(cmd, timeout=20)
    if code != 0:
        raise ActionError(err or "Could not add the event")
    _agenda_cache["at"] = 0
    return {"ok": True}


# ---------------------------------------------------------------- today's plan
#
# A scheduled Claude task writes today's 3 to 5 tasks to a private GitHub repository
# as JSON. Deck shows them as "Proposed". Nothing reaches the calendar until you
# tap Approve: then Deck puts each task in a free block on his calendar, marked
# "Deck task <id>" in the notes so Deck can find, tick off or remove only its own.

PLAN_STATE = STATE / "plan-state.json"
_plan_cache = {"at": 0.0, "plan": None, "error": None}
_plan_lock = threading.Lock()
PLAN_DEFAULTS = {
    "repo": "", "path": "_plan/{date}.json", "branch": "main", "token": "",
    "ready_by": "07:00", "day_start": "09:30", "day_end": "18:00",
    "lunch": ["13:00", "14:00"], "block_minutes": 45, "buffer_minutes": 10,
    "gap_minutes": 15, "title_prefix": "Focus: ", "done_prefix": "Done: ",
}


def now_local():
    fake = os.environ.get("DECK_TEST_NOW") if DRY_RUN else None
    return datetime.fromisoformat(fake) if fake else datetime.now()


def plan_cfg():
    cfg = dict(PLAN_DEFAULTS)
    cfg.update(CONFIG.get().get("plan", {}) or {})
    return cfg


def hm(text, day):
    h, m = (int(x) for x in str(text).split(":"))
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


def demo_plan():
    today = now_local().date().isoformat()
    return {"date": today, "note": "Two follow-ups first, while the morning is fresh.", "tasks": [
        {"id": "t1", "title": "Reply to the training lead who asked for a proposal", "why": "Replied on Tuesday, waiting two days", "kind": "signature", "source": "CRM"},
        {"id": "t2", "title": "Send the workshop outline to the client", "why": "Promised in Monday's call", "kind": "signature", "source": "Email"},
        {"id": "t3", "title": "Outline the first case study", "why": "Case studies page has none yet", "kind": "case-study", "source": "Website"},
        {"id": "t4", "title": "Publish the draft on AI briefs", "why": "Ready in the CMS since Monday", "kind": "content", "source": "CMS"},
    ]}


def fetch_plan(force=False):
    """Today's plan from GitHub, cached for 10 minutes. Returns (plan or None, error or None)."""
    with _plan_lock:
        fresh = time.time() - _plan_cache["at"] < 600
        same_day = (_plan_cache["plan"] or {}).get("date") == now_local().date().isoformat()
        if not force and fresh and (same_day or _plan_cache["plan"] is None and _plan_cache["error"] is None and time.time() - _plan_cache["at"] < 300):
            return _plan_cache["plan"], _plan_cache["error"]
        cfg = plan_cfg()
        plan, error = _plan_cache["plan"], None
        if DRY_RUN:
            demo = os.environ.get("DECK_DEMO_PLAN")
            plan = json.loads(Path(demo).read_text()) if demo else demo_plan()
        elif not cfg["repo"] or not cfg["token"]:
            plan, error = None, "not-configured"
        else:
            import urllib.request, urllib.error
            path = cfg["path"].replace("{date}", now_local().date().isoformat())
            url = (f"https://api.github.com/repos/{cfg['repo']}/contents/"
                   f"{urllib.parse.quote(path)}?ref={urllib.parse.quote(cfg['branch'])}")
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {cfg['token']}",
                "Accept": "application/vnd.github.raw+json",
                "User-Agent": "Deck"})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    plan = json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    plan = None
                else:
                    error = f"GitHub answered {e.code}. Check the plan token in config.json."
            except Exception as e:
                error = "Could not reach GitHub for today's list"
                log("plan fetch failed", repr(e)[:160])
        _plan_cache.update(at=time.time(), plan=plan, error=error)
        return plan, error


def load_state(today):
    try:
        st = json.loads(PLAN_STATE.read_text())
        if st.get("date") == today:
            return st
    except (OSError, ValueError):
        pass
    return {"date": today, "status": "proposed", "excluded": [], "blocks": {}, "done": [], "unplaced": []}


def save_state(st):
    PLAN_STATE.write_text(json.dumps(st, indent=1))


def today_events():
    """Every timed event today on every calendar, with ids and Deck markers."""
    if DRY_RUN:
        return [e for e in demo_agenda()["events"] if not e.get("allDay")]
    if not AGENDA_BIN.exists():
        raise ActionError("Calendar helper not built yet. Run setup.sh on the Mac.")
    code, out, err = run([str(AGENDA_BIN), "list", "--days", "1", "--source", "all"], timeout=20)
    if code != 0:
        raise ActionError(err or "Could not read the calendar")
    return [e for e in json.loads(out).get("events", []) if not e.get("allDay")]


def parse_iso(text):
    d = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return d.astimezone().replace(tzinfo=None) if d.tzinfo else d


def find_slots(count, events, now=None):
    """Earliest free blocks today, inside working hours, clear of meetings, lunch and each other."""
    cfg = plan_cfg()
    now = now or now_local()
    day = now.replace(second=0, microsecond=0)
    block = timedelta(minutes=int(cfg["block_minutes"]))
    pad = timedelta(minutes=int(cfg["buffer_minutes"]))
    gap = timedelta(minutes=int(cfg["gap_minutes"]))
    start, end = hm(cfg["day_start"], day), hm(cfg["day_end"], day)
    earliest = now + timedelta(minutes=5)
    earliest = earliest + timedelta(minutes=(15 - earliest.minute % 15) % 15)
    t = max(start, earliest.replace(second=0, microsecond=0))
    busy = []
    for ev in events:
        if ev.get("deckTask"):
            busy.append((parse_iso(ev["start"]) - gap, parse_iso(ev["end"]) + gap))
        else:
            busy.append((parse_iso(ev["start"]) - pad, parse_iso(ev["end"]) + pad))
    if cfg.get("lunch"):
        busy.append((hm(cfg["lunch"][0], day), hm(cfg["lunch"][1], day)))
    slots = []
    while len(slots) < count and t + block <= end:
        clash = next((b for b in busy if t < b[1] and t + block > b[0]), None)
        if clash:
            t = clash[1]
            t = t + timedelta(minutes=(5 - t.minute % 5) % 5)
            continue
        slots.append((t, t + block))
        busy.append((t - gap, t + block + gap))
        t = t + block + gap
    return slots


def plan_view(force=False):
    today = now_local().date().isoformat()
    plan, error = fetch_plan(force)
    cfg = plan_cfg()
    if error == "not-configured":
        return {"status": "not-configured"}
    if not plan or plan.get("date") != today:
        before = now_local() < hm(cfg["ready_by"], now_local())
        return {"status": "waiting" if before else "none", "readyBy": cfg["ready_by"], "error": error}
    st = load_state(today)
    tasks = []
    for t in plan.get("tasks", [])[:5]:
        tid = str(t.get("id", ""))
        blk = st["blocks"].get(tid)
        tasks.append({
            "id": tid, "title": str(t.get("title", ""))[:140], "why": str(t.get("why", ""))[:160],
            "kind": t.get("kind", ""), "source": t.get("source", ""),
            "included": tid not in st["excluded"], "done": tid in st["done"],
            "start": blk["start"] if blk else None, "end": blk["end"] if blk else None,
            "unplaced": tid in st.get("unplaced", []),
        })
    return {"status": st["status"], "date": today, "note": str(plan.get("note", ""))[:200],
            "tasks": tasks, "blockMinutes": int(cfg["block_minutes"]), "error": error}


def plan_and_state():
    today = now_local().date().isoformat()
    plan, _ = fetch_plan()
    if not plan or plan.get("date") != today:
        raise ActionError("There is no list for today")
    return plan, load_state(today)


def plan_toggle(tid):
    plan, st = plan_and_state()
    if st["status"] != "proposed":
        raise ActionError("The list is already approved" if st["status"] == "approved" else "You set today aside. Tap Show list again first")
    if tid in st["excluded"]:
        st["excluded"].remove(tid)
    else:
        st["excluded"].append(tid)
    save_state(st)


def marker(today, tid):
    return f"{today}-{re.sub(r'[^A-Za-z0-9_-]', '', tid)}"


def plan_approve():
    plan, st = plan_and_state()
    if st["status"] == "approved":
        return {"toast": "Already on your calendar"}
    cfg = plan_cfg()
    chosen = [t for t in plan.get("tasks", [])[:5] if str(t.get("id")) not in st["excluded"]]
    if not chosen:
        raise ActionError("Keep at least one task, or tap Not today")
    slots = find_slots(len(chosen), today_events())
    cal_cfg = CONFIG.get().get("calendar", {})
    st["blocks"], st["unplaced"] = {}, []
    for task, slot in zip(chosen, slots + [None] * (len(chosen) - len(slots))):
        tid = str(task.get("id"))
        if slot is None:
            st["unplaced"].append(tid)
            continue
        s, e = slot
        title = cfg["title_prefix"] + str(task.get("title", ""))[:120]
        notes = f"Deck task {marker(st['date'], tid)}\n{task.get('why', '')}\nPlaced by Deck after you approved today's list."
        if DRY_RUN:
            _demo_blocks.append({"id": "b" + tid, "title": title, "start": s.isoformat(), "end": e.isoformat(),
                                 "allDay": False, "calendar": "Calendar", "location": "",
                                 "deckTask": marker(st["date"], tid), "eid": "demo-" + tid})
        else:
            cmd = [str(AGENDA_BIN), "add", "--title", title, "--start", s.strftime("%Y-%m-%dT%H:%M:%S"),
                   "--end", e.strftime("%Y-%m-%dT%H:%M:%S"), "--notes", notes,
                   "--source", cal_cfg.get("source", "all"), "--match", ",".join(cal_cfg.get("match", []))]
            if cal_cfg.get("add_to_calendar"):
                cmd += ["--calendar", cal_cfg["add_to_calendar"]]
            code, out, err = run(cmd, timeout=20)
            if code != 0:
                save_state(st)
                raise ActionError(err or "Could not add a block to the calendar")
        st["blocks"][tid] = {"start": s.isoformat(), "end": e.isoformat(), "title": title}
    st["status"] = "approved"
    save_state(st)
    _agenda_cache["at"] = 0
    placed = len(st["blocks"])
    msg = f"{placed} block{'s' if placed != 1 else ''} on your calendar"
    if st["unplaced"]:
        msg += f". No free {cfg['block_minutes']} minutes left for {len(st['unplaced'])}"
    return {"toast": msg}


def find_block_event(today, tid):
    want = marker(today, tid)
    return next((e for e in today_events() if e.get("deckTask") == want), None)


def plan_done(tid, done):
    plan, st = plan_and_state()
    if done and tid not in st["done"]:
        st["done"].append(tid)
    if not done and tid in st["done"]:
        st["done"].remove(tid)
    blk = st["blocks"].get(tid)
    if blk:
        cfg = plan_cfg()
        task = next((t for t in plan.get("tasks", []) if str(t.get("id")) == tid), {})
        title = (cfg["done_prefix"] if done else cfg["title_prefix"]) + str(task.get("title", ""))[:120]
        if DRY_RUN:
            for b in _demo_blocks:
                if b["deckTask"] == marker(st["date"], tid):
                    b["title"] = title
        else:
            ev = find_block_event(st["date"], tid)
            if ev:
                code, _, err = run([str(AGENDA_BIN), "rename", "--eid", ev["eid"], "--title", title], timeout=20)
                if code != 0:
                    log("rename failed", err[:160])
    save_state(st)
    left = sum(1 for t in plan.get("tasks", [])[:5] if str(t.get("id")) not in st["excluded"] and str(t.get("id")) not in st["done"])
    return {"toast": "All done for today" if done and left == 0 else ("Done" if done else "Back on the list")}


def plan_dismiss():
    plan, st = plan_and_state()
    st["status"] = "dismissed"
    save_state(st)
    return {"toast": "Nothing blocked today"}


def plan_reopen():
    plan, st = plan_and_state()
    if st["status"] == "dismissed":
        st["status"] = "proposed"
        save_state(st)
    return {"toast": "List is back"}


def plan_reset():
    """Take Deck's blocks off the calendar and show the list as proposed again."""
    plan, st = plan_and_state()
    if not DRY_RUN:
        for tid in list(st["blocks"]):
            ev = find_block_event(st["date"], tid)
            if ev:
                run([str(AGENDA_BIN), "remove", "--eid", ev["eid"]], timeout=20)
    else:
        _demo_blocks.clear()
    st.update(status="proposed", blocks={}, unplaced=[])
    save_state(st)
    _agenda_cache["at"] = 0
    return {"toast": "Blocks removed. Change the list and approve again"}


# ---------------------------------------------------------------- morning roundup
#
# A daily reading roundup (_roundup/YYYY-MM-DD.md in a GitHub repository) that a
# scheduled job writes each morning. See docs/DAILY.md for the format.
# Deck only reads it. Links open by item id, so the iPad can only open links that
# are in today's roundup.

ROUNDUP_DEFAULTS = {"repo": "", "path": "_roundup/{date}.md", "branch": "main", "token": ""}
_roundup_cache = {"at": 0.0, "data": None}
_roundup_links = {}


def roundup_cfg():
    cfg = dict(ROUNDUP_DEFAULTS)
    cfg.update(CONFIG.get().get("roundup", {}) or {})
    if not cfg["token"]:
        cfg["token"] = plan_cfg().get("token", "")
    return cfg


def github_text(repo, path, branch, token):
    """A file's text from GitHub, or None if it does not exist."""
    import urllib.request, urllib.error
    url = f"https://api.github.com/repos/{repo}/contents/{urllib.parse.quote(path)}?ref={urllib.parse.quote(branch)}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "Deck",
                                               "Accept": "application/vnd.github.raw+json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise ActionError(f"GitHub answered {e.code}. Check the token can read {repo}.")


SOURCE_RE = re.compile(r"^Source:\s*\[([^\]]+)\]\((https?://[^\s)]+)\)\s*,?\s*(.*)$")


def parse_roundup(text):
    body = text.lstrip("﻿")
    meta = {}
    m = re.match(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n?", body)
    if m:
        body = body[m.end():]
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    sections, intro, current, item = [], [], None, None

    def close_item():
        nonlocal item
        if item and current is not None:
            item["summary"] = "\n\n".join(p.strip() for p in "\n".join(item.pop("lines")).split("\n\n") if p.strip())
            current["items"].append(item)
        item = None

    for line in body.splitlines():
        if line.startswith("## "):
            close_item()
            current = {"name": line[3:].strip(), "items": []}
            sections.append(current)
        elif line.startswith("### "):
            close_item()
            if current is None:
                current = {"name": "Today", "items": []}
                sections.append(current)
            item = {"title": line[4:].strip(), "lines": []}
        elif line.strip() == "---":
            close_item()
        elif item is not None:
            src = SOURCE_RE.match(line.strip())
            if src:
                item["linkTitle"], item["url"] = src.group(1), src.group(2)
                rest = [x.strip() for x in src.group(3).split(",") if x.strip()]
                item["publisher"] = rest[0] if rest else ""
                item["kind"] = rest[1] if len(rest) > 1 else ""
                item["when"] = ", ".join(rest[2:]) if len(rest) > 2 else ""
            elif line.strip().startswith("Post idea:"):
                item["idea"] = line.strip()[len("Post idea:"):].strip()
            else:
                item["lines"].append(line)
        elif current is None and line.strip():
            intro.append(line.strip())
    close_item()
    return {"title": meta.get("title", "Morning roundup"), "date": meta.get("date", ""),
            "intro": " ".join(intro), "sections": [x for x in sections if x["items"]]}


def roundup(force=False):
    if not force and _roundup_cache["data"] and time.time() - _roundup_cache["at"] < 900:
        return _roundup_cache["data"]
    cfg = roundup_cfg()
    today = now_local().date()
    data = None
    if DRY_RUN:
        demo = os.environ.get("DECK_DEMO_ROUNDUP")
        data = parse_roundup(Path(demo).read_text()) if demo else {"status": "none"}
        if demo:
            data.update(status="ok", day="today")
    elif not cfg["repo"] or not cfg["token"]:
        data = {"status": "not-configured"}
    else:
        for offset, label in ((0, "today"), (1, "yesterday")):
            day = (today - timedelta(days=offset)).isoformat()
            try:
                text = github_text(cfg["repo"], cfg["path"].replace("{date}", day), cfg["branch"], cfg["token"])
            except ActionError as e:
                data = {"status": "error", "error": str(e)}
                break
            except Exception as e:
                log("roundup fetch failed", repr(e)[:160])
                data = {"status": "error", "error": "Could not reach GitHub for the roundup"}
                break
            if text:
                data = parse_roundup(text)
                data.update(status="ok", day=label)
                break
        if data is None:
            data = {"status": "none"}
    if data.get("status") == "ok":
        _roundup_links.clear()
        for si, sec in enumerate(data["sections"]):
            for ii, it in enumerate(sec["items"]):
                it["id"] = f"r{si}-{ii}"
                if it.get("url"):
                    _roundup_links[it["id"]] = it["url"]
                    it["hasLink"] = True
    _roundup_cache.update(at=time.time(), data=data)
    return data


def open_roundup_link(rid):
    url = _roundup_links.get(rid)
    if not url:
        raise ActionError("That link is no longer in the roundup. Refresh the Read tab.")
    needs_unlock()
    open_url(url, "Safari")
    return {"toast": "Opened on your Mac"}


# ---------------------------------------------------------------- voice notes

_transcribing = set()
AUDIO_EXT = (".m4a", ".mp4", ".webm", ".ogg", ".wav")


def voice_dir():
    d = Path(os.path.expanduser(CONFIG.get().get("voice", {}).get("folder", "~/Documents/Voice Notes")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def whisper_bin():
    for name in ("whisper-cli", "whisper-cpp", "whisper"):
        for d in ["/opt/homebrew/bin", "/usr/local/bin"] + UTF8_ENV["PATH"].split(":"):
            p = Path(d) / name
            if p.exists() and os.access(p, os.X_OK):
                return str(p)
    return None


def whisper_model():
    p = Path(os.path.expanduser(CONFIG.get().get("voice", {}).get("whisper_model", "")))
    return p if p.is_file() else None


def save_voice(body, content_type):
    ext = ".webm" if "webm" in content_type else ".m4a"
    name = datetime.now().strftime("Voice %Y-%m-%d %H.%M.%S") + ext
    path = voice_dir() / name
    path.write_bytes(body)
    if whisper_bin() and whisper_model():
        _transcribing.add(name)
        threading.Thread(target=transcribe, args=(path,), daemon=True).start()
    return name


def transcribe(path):
    name = path.name
    wav = STATE / f"tmp-{secrets.token_hex(4)}.wav"
    try:
        code, _, err = run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(path), str(wav)], timeout=300)
        if code != 0:
            log("afconvert failed", name, err)
            return
        lang = CONFIG.get().get("voice", {}).get("language", "en")
        out_base = path.with_suffix("")
        code, _, err = run([whisper_bin(), "-m", str(whisper_model()), "-f", str(wav),
                            "-l", lang, "-nt", "-otxt", "-of", str(out_base)], timeout=1800)
        if code != 0:
            log("whisper failed", name, err[-300:])
            return
        txt = out_base.with_suffix(".txt")
        if txt.exists():
            txt.write_text(" ".join(txt.read_text(encoding="utf-8").split()), encoding="utf-8")
        log("transcribed", name)
    finally:
        wav.unlink(missing_ok=True)
        _transcribing.discard(name)


def list_voice():
    d = voice_dir()
    files = sorted((p for p in d.iterdir() if p.suffix.lower() in AUDIO_EXT),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    notes = []
    for p in files:
        txt = p.with_suffix(".txt")
        notes.append({
            "name": p.name,
            "when": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
            "text": txt.read_text(encoding="utf-8")[:4000] if txt.exists() else "",
            "status": "transcribing" if p.name in _transcribing else ("done" if txt.exists() else "audio"),
        })
    return {"notes": notes, "canTranscribe": bool(whisper_bin() and whisper_model())}


def voice_text(name):
    if "/" in name or "\\" in name or name.startswith("."):
        raise ActionError("Bad note name")
    txt = (voice_dir() / name).with_suffix(".txt")
    if not txt.exists():
        raise ActionError("No transcript yet")
    return txt.read_text(encoding="utf-8")


# ---------------------------------------------------------------- Claude

def to_claude(text, send=False):
    cfg = CONFIG.get().get("claude", {})
    pbcopy(text)
    activate(cfg.get("app", "Claude"))
    time.sleep(0.4)
    if cfg.get("new_chat_keys"):
        press_keys(cfg["new_chat_keys"])
        time.sleep(0.6)
    press_keys("cmd+v")
    if send:
        time.sleep(0.3)
        press_keys("return")
    return {"toast": "Sent to Claude" if send else "Pasted in Claude"}


# ---------------------------------------------------------------- search

_search_results = {}


def search(q):
    q = q.strip()
    if len(q) < 2:
        return {"results": []}
    code, out, _ = run(["mdfind", "-onlyin", str(Path.home()), "-name", q], timeout=10)
    paths = [p for p in out.splitlines() if p and "/Library/" not in p and "/." not in p][:30]
    if DRY_RUN:
        paths = [str(Path.home() / "Documents" / f"{q} notes.md"), str(Path.home() / "Desktop" / f"{q}.pdf")]
    _search_results.clear()
    results = []
    for p in paths:
        rid = secrets.token_hex(4)
        _search_results[rid] = p
        path = Path(p)
        results.append({"id": rid, "name": path.name,
                        "folder": str(path.parent).replace(str(Path.home()), "~")})
    return {"results": results}


def open_result(rid, reveal=False):
    p = _search_results.get(rid)
    if not p:
        raise ActionError("Search again, that result has expired")
    code, _, _ = run(["open", "-R", p] if reveal else ["open", p])
    if code != 0:
        raise ActionError("Could not open it")
    return {"toast": "Opened on Mac"}


# ---------------------------------------------------------------- status

# ---------------------------------------------------------------- lock screen

class MacLocked(Exception):
    pass


LOCKED_MESSAGE = "Your Mac is locked. Its screen is on now: unlock it with Touch ID, then tap again."
# These do nothing visible on screen, so they are safe to run while the Mac is locked.
SAFE_WHEN_LOCKED = {"lock", "display_sleep", "vol_up", "vol_down", "mute_toggle", "mic_toggle",
                    "music_playpause", "music_next", "music_prev"}
_lock_cache = {"at": 0.0, "locked": False}


def screen_locked():
    """True when the login window is covering the session (locked, or asleep and locked)."""
    if DRY_RUN:
        return os.environ.get("DECK_TEST_LOCKED") == "1"
    if time.time() - _lock_cache["at"] < 2:
        return _lock_cache["locked"]
    locked = False
    try:
        out = subprocess.run(["ioreg", "-n", "Root", "-d1", "-a"], capture_output=True, timeout=5).stdout
        root = plistlib.loads(out)
        if isinstance(root, list):
            root = root[0] if root else {}
        for user in root.get("IOConsoleUsers", []):
            if user.get("CGSSessionScreenIsLocked"):
                locked = True
    except Exception as e:  # if the check itself fails, never block the panel
        log("lock check failed", repr(e)[:120])
    _lock_cache.update(at=time.time(), locked=locked)
    return locked


def wake_screen():
    """Turn the display on so the lock screen and Touch ID prompt are showing."""
    subprocess.Popen(["caffeinate", "-u", "-t", "5"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def needs_unlock(builtin=None):
    """Raise MacLocked for anything that would open, type or show something on screen."""
    if builtin in SAFE_WHEN_LOCKED:
        return
    if screen_locked():
        if not DRY_RUN:
            wake_screen()
        raise MacLocked(LOCKED_MESSAGE)


def status():
    battery, charging = None, None
    code, out, _ = run(["pmset", "-g", "batt"])
    m = re.search(r"(\d+)%;\s*([a-zA-Z ]+)", out or "")
    if m:
        battery = int(m.group(1))
        charging = m.group(2).strip() in ("charging", "charged", "finishing charge") or "AC Power" in out
    return {"mac": mac_name(), "battery": battery, "charging": charging, "locked": screen_locked(),
            "mic": mic_state(), "time": datetime.now().isoformat()}


_mac_name = None


def mac_name():
    global _mac_name
    if _mac_name is None:
        code, out, _ = run(["scutil", "--get", "ComputerName"])
        _mac_name = out if code == 0 and out else socket.gethostname().split(".")[0]
    return _mac_name


def local_hostname():
    code, out, _ = run(["scutil", "--get", "LocalHostName"])
    if code == 0 and out and not DRY_RUN:
        return out + ".local"
    return socket.gethostname()


# ---------------------------------------------------------------- auth

TOKENS_FILE = STATE / "tokens.json"
_failures = {}


def load_tokens():
    try:
        return set(json.loads(TOKENS_FILE.read_text()))
    except (OSError, ValueError):
        return set()


TOKENS = load_tokens()


def save_tokens():
    TOKENS_FILE.write_text(json.dumps(sorted(TOKENS)))
    os.chmod(TOKENS_FILE, 0o600)


def check_pin(ip, pin):
    f = _failures.get(ip, {"count": 0, "until": 0})
    if time.time() < f["until"]:
        raise ActionError(f"Too many tries. Wait {int(f['until'] - time.time())} seconds.")
    real = str(CONFIG.get().get("pin", ""))
    if len(real) < 4:
        raise ActionError("Set a PIN of at least 4 digits in config.json")
    if hmac.compare_digest(pin.encode(), real.encode()):
        _failures.pop(ip, None)
        token = secrets.token_urlsafe(32)
        TOKENS.add(token)
        save_tokens()
        return token
    f["count"] += 1
    if f["count"] >= 5:
        f["until"] = time.time() + 30 * (2 ** min(f["count"] - 5, 6))
    _failures[ip] = f
    raise ActionError("Wrong PIN")


def token_ok(token):
    return bool(token) and any(hmac.compare_digest(token, t) for t in TOKENS)


# ---------------------------------------------------------------- HTTP

def public_config():
    cfg = CONFIG.get()
    sections = {}
    for s in SECTIONS:
        sections[s] = [{
            "id": t["id"], "label": t.get("label", t["id"]),
            "kind": next((k for k in ("open_app", "open_url", "shortcut", "keys", "steps", "builtin", "applescript") if k in t["do"]), ""),
            "builtin": t["do"].get("builtin", ""),
            "icon": bool(t.get("icon") or "open_app" in t["do"] or tile_url(t["do"])),
            "iv": str(int(custom_icon_file(t).stat().st_mtime)) if custom_icon_file(t) else "",
        } for t in cfg.get(s, [])]
    return {"name": cfg.get("name", "Deck"), "owner": str(cfg.get("owner", ""))[:40],
            "mac": mac_name(), "sections": sections}


class TLSThreadingServer(ThreadingHTTPServer):
    """Accepts plain connections and does the TLS handshake inside each
    connection's own thread, with a time limit. A client that stalls during
    the handshake (an iPad going to sleep, a network drop) then only ties up
    its own thread, never the whole server."""

    daemon_threads = True
    HANDSHAKE_SECONDS = 10

    def __init__(self, addr, handler, ctx):
        self.ctx = ctx
        super().__init__(addr, handler)

    def finish_request(self, request, client_address):
        request.settimeout(self.HANDSHAKE_SECONDS)
        try:
            tls = self.ctx.wrap_socket(request, server_side=True)
        except (OSError, ssl.SSLError):
            return
        try:
            self.RequestHandlerClass(tls, client_address, self)
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def handle_error(self, request, client_address):
        log("connection error", client_address[0], repr(sys.exc_info()[1])[:120])


class Handler(BaseHTTPRequestHandler):
    server_version = "Deck/1.0"
    timeout = 60  # idle keep-alive connections are closed after a minute

    def log_message(self, fmt, *args):
        pass

    # helpers
    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path, ctype=None, cache=False):
        data = path.read_bytes()
        if ctype is None:
            ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                     ".css": "text/css; charset=utf-8", ".json": "application/json",
                     ".png": "image/png", ".svg": "image/svg+xml",
                     ".webmanifest": "application/manifest+json"}.get(path.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_body(self, limit=1024 * 1024):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ActionError("Upload too large")
        return self.rfile.read(n) if n else b""

    def read_json(self):
        try:
            return json.loads(self.read_body() or b"{}")
        except json.JSONDecodeError:
            raise ActionError("Bad request")

    def authed(self, query):
        token = self.headers.get("X-Deck-Token") or (query.get("t") or [""])[0]
        return token_ok(token)

    # routing
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(url.query)
        path = url.path
        try:
            if not path.startswith("/api/"):
                return self.serve_static(path)
            if not self.authed(q):
                return self.send_json({"error": "auth"}, 401)
            if path == "/api/config":
                return self.send_json(public_config())
            if path == "/api/status":
                return self.send_json(status())
            if path == "/api/nowplaying":
                return self.send_json(now_playing())
            if path == "/api/agenda":
                return self.send_json(agenda(force=q.get("force") == ["1"]))
            if path == "/api/voice":
                return self.send_json(list_voice())
            if path == "/api/roundup":
                return self.send_json(roundup(force=q.get("force") == ["1"]))
            if path == "/api/plan":
                return self.send_json(plan_view(force=q.get("force") == ["1"]))
            if path == "/api/clipboard":
                return self.send_json({"text": pbpaste()})
            if path == "/api/search":
                return self.send_json(search((q.get("q") or [""])[0]))
            if path.startswith("/api/icon/"):
                icon = tile_icon(path.rsplit("/", 1)[-1])
                return self.send_file(icon[0], icon[1], cache=True) if icon else self.send_json({}, 404)
            if path == "/api/art":
                pid = (q.get("pid") or [""])[0]
                art = STATE / "art" / pid
                if re.fullmatch(r"[0-9A-Fa-f]+", pid) and art.exists() and art.stat().st_size:
                    head = art.read_bytes()[:4]
                    return self.send_file(art, "image/png" if head.startswith(b"\x89PNG") else "image/jpeg", cache=True)
                return self.send_json({}, 404)
            return self.send_json({"error": "not found"}, 404)
        except ActionError as e:
            return self.send_json({"error": str(e)}, 400)
        except Exception as e:  # keep the panel alive whatever happens
            log("error", path, repr(e))
            return self.send_json({"error": "Something went wrong on the Mac"}, 500)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        q = urllib.parse.parse_qs(url.query)
        try:
            if path == "/api/login":
                pin = str(self.read_json().get("pin", ""))
                return self.send_json({"token": check_pin(self.client_address[0], pin)})
            if not self.authed(q):
                return self.send_json({"error": "auth"}, 401)

            if path == "/api/voice":
                ctype = self.headers.get("Content-Type", "")
                if not ctype.startswith("audio/"):
                    raise ActionError("Expected audio")
                name = save_voice(self.read_body(MAX_UPLOAD), ctype)
                log("voice note saved", name)
                return self.send_json({"name": name})

            body = self.read_json()
            if path == "/api/action":
                tile = CONFIG.tiles().get(body.get("id", ""))
                if not tile:
                    raise ActionError("Unknown tile. Reload the panel.")
                log("tile", tile["id"], body.get("mode") or "")
                needs_unlock(tile["do"].get("builtin"))
                return self.send_json(do_action(tile["do"], mode=body.get("mode")) or {"ok": True})
            if path == "/api/music":
                cmd = str(body.get("cmd", ""))
                if cmd not in ("music_prev", "music_playpause", "music_next"):
                    raise ActionError("Unknown music control")
                code, _, err = BUILTINS[cmd]()
                if code != 0:
                    raise ActionError("Music did not respond")
                return self.send_json(now_playing())
            if path == "/api/claude":
                text = str(body.get("text", "")).strip()
                if not text:
                    raise ActionError("Nothing to send")
                needs_unlock()
                return self.send_json(to_claude(text, bool(body.get("send"))))
            if path == "/api/clipboard":
                pbcopy(str(body.get("text", "")))
                return self.send_json({"toast": "On the Mac clipboard"})
            if path == "/api/voice/use":
                text = voice_text(str(body.get("name", "")))
                if body.get("to") == "claude":
                    needs_unlock()
                    return self.send_json(to_claude(text, False))
                pbcopy(text)
                return self.send_json({"toast": "On the Mac clipboard"})
            if path == "/api/open":
                needs_unlock()
                return self.send_json(open_result(str(body.get("id", "")), bool(body.get("reveal"))))
            if path == "/api/join":
                link = _join_links.get(str(body.get("id", "")))
                if not link:
                    raise ActionError("No meeting link found for this event")
                needs_unlock()
                open_url(link, reuse=False)
                return self.send_json({"toast": "Joining on Mac"})
            if path == "/api/event":
                title = str(body.get("title", "")).strip()[:200]
                start, end = str(body.get("start", "")), str(body.get("end", ""))
                if not title:
                    raise ActionError("Give the event a title")
                datetime.fromisoformat(start), datetime.fromisoformat(end)
                return self.send_json(add_event(title, start, end))
            if path == "/api/roundup/open":
                return self.send_json(open_roundup_link(str(body.get("id", ""))))
            if path == "/api/plan/toggle":
                plan_toggle(str(body.get("id", "")))
                return self.send_json(plan_view())
            if path == "/api/plan/approve":
                r = plan_approve()
                return self.send_json(dict(plan_view(), toast=r["toast"]))
            if path == "/api/plan/done":
                r = plan_done(str(body.get("id", "")), bool(body.get("done")))
                return self.send_json(dict(plan_view(), toast=r["toast"]))
            if path == "/api/plan/dismiss":
                r = plan_dismiss()
                return self.send_json(dict(plan_view(), toast=r["toast"]))
            if path == "/api/plan/reopen":
                r = plan_reopen()
                return self.send_json(dict(plan_view(), toast=r["toast"]))
            if path == "/api/plan/reset":
                r = plan_reset()
                return self.send_json(dict(plan_view(), toast=r["toast"]))
            if path == "/api/logout":
                TOKENS.discard(self.headers.get("X-Deck-Token", ""))
                save_tokens()
                return self.send_json({"ok": True})
            return self.send_json({"error": "not found"}, 404)
        except MacLocked as e:
            return self.send_json({"error": str(e), "locked": True}, 423)
        except ActionError as e:
            return self.send_json({"error": str(e)}, 400)
        except ValueError:
            return self.send_json({"error": "Bad date or time"}, 400)
        except Exception as e:
            log("error", path, repr(e))
            return self.send_json({"error": "Something went wrong on the Mac"}, 500)

    def serve_static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        target = (STATIC / path.lstrip("/")).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            return self.send_json({"error": "not found"}, 404)
        return self.send_file(target, cache=target.suffix == ".png")


# ---------------------------------------------------------------- setup helper (plain HTTP)

SETUP_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deck setup</title>
<style>
body{margin:0;background:#181B20;color:#ECEEF2;font:17px/1.55 -apple-system,BlinkMacSystemFont,sans-serif;padding:32px 20px}
main{max-width:620px;margin:0 auto}
h1{font-weight:600;font-size:32px;margin:0 0 6px}
p.lead{color:#AEB6C2;margin:0 0 28px}
ol{padding:0;list-style:none;counter-reset:s;margin:0}
li{counter-increment:s;background:#22262D;border:1px solid rgba(255,255,255,.08);border-radius:8px;padding:16px 18px;margin:0 0 12px}
li:before{content:counter(s);color:#5B9DFF;font-weight:600;font-size:20px;margin-right:12px}
a.btn{display:inline-block;background:#5B9DFF;color:#111317;text-decoration:none;font-weight:600;padding:10px 18px;border-radius:8px;margin-top:10px}
code{background:#111317;padding:2px 6px;border-radius:4px;color:#8FBCFF}
</style></head><body><main>
<h1>Set up Deck on this iPad</h1>
<p class="lead">Do this once. It lets Safari trust your Mac, which the microphone needs.</p>
<ol>
<li>Download the Mac's certificate. Safari will say a profile was downloaded.<br>
<a class="btn" href="/deck-ca.crt">Download certificate</a></li>
<li>Open <b>Settings</b>. Tap <b>Profile Downloaded</b> near the top, then <b>Install</b>.</li>
<li>In Settings, go to <b>General, About, Certificate Trust Settings</b>. Turn on <b>Deck Local CA</b>.</li>
<li>Open the panel: <a class="btn" href="https://{host}:{port}/">Open Deck</a></li>
<li>In Safari, tap Share, then <b>Add to Home Screen</b>. Open Deck from the home screen from now on.</li>
</ol>
</main></body></html>"""


def make_setup_handler(host, port):
    class SetupHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            if self.path.startswith("/deck-ca.crt"):
                data = (CERTS / "ca.crt").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-x509-ca-cert")
                self.send_header("Content-Disposition", 'attachment; filename="deck-ca.crt"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            body = SETUP_PAGE.replace("{host}", host).replace("{port}", str(port)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    return SetupHandler


def local_ips():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(ips)


def main():
    if not (ROOT / "config.json").exists():
        shutil.copy(ROOT / "config.example.json", ROOT / "config.json")
        print("Created config.json from config.example.json")
    cfg = CONFIG.get()
    port = int(cfg.get("port", 8443))
    setup_port = int(cfg.get("setup_port", 8080))
    host = local_hostname()

    if len(str(cfg.get("pin", ""))) < 4:
        print("Set a PIN of at least 4 digits in config.json first (or run setup.sh).")
        sys.exit(1)
    if not (CERTS / "server.crt").exists():
        print("No certificate yet. Run ./setup.sh first.")
        sys.exit(1)

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERTS / "server.crt", CERTS / "server.key")

    httpd = TLSThreadingServer(("0.0.0.0", port), Handler, ctx)

    setupd = ThreadingHTTPServer(("0.0.0.0", setup_port), make_setup_handler(host, port))
    setupd.daemon_threads = True
    threading.Thread(target=setupd.serve_forever, daemon=True).start()

    print()
    print(f"  Deck is running on {mac_name()}")
    print(f"  Panel:        https://{host}:{port}")
    print(f"  First time:   http://{host}:{setup_port}   (open this on the iPad)")
    for ip in local_ips():
        print(f"  By address:   https://{ip}:{port}")
    if not whisper_bin() or not whisper_model():
        print("  Voice notes save as audio only. Run setup.sh to add transcription.")
    print("  Leave this window open. Press Ctrl+C to stop.")
    print()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
