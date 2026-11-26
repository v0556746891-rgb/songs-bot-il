# -*- coding: utf-8 -*-
import json, os, re, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE = os.path.join(HERE, "seen.json")
CHANNELS_FILE = os.path.join(HERE, "channels.json")

TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
WA_PHONE = os.getenv("WHATSAPP_PHONE", "")
WA_KEY = os.getenv("CALLMEBOT_APIKEY", "")
YT_KEY = os.getenv("YOUTUBE_API_KEY", "")

MAX_AGE_HOURS = 48
RESOLVE_PER_RUN = 30
NS = {"a": "http://www.w3.org/2005/Atom",
      "yt": "http://www.youtube.com/xml/schemas/2015"}


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
        "Cookie": "CONSENT=YES+1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def find_channel_id(name):
    url = "https://www.youtube.com/results?" + urllib.parse.urlencode(
        {"search_query": name, "sp": "EgIQAg=="})
    html = http_get(url).decode("utf-8", "ignore")
    m = re.search(r'"channelRenderer":\{"channelId":"(UC[\w-]{22})"', html)
    if not m:
        m = re.search(r'"channelId":"(UC[\w-]{22})"', html)
    return m.group(1) if m else None


def fetch_feed(ch):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    out = []
    try:
        root = ET.fromstring(http_get(
            "https://www.youtube.com/feeds/videos.xml?channel_id=" + ch["channel_id"]))
    except Exception as e:
        print(f"feed error {ch['name']}: {e}")
        return out
    for entry in root.findall("a:entry", NS):
        vid = entry.findtext("yt:videoId", "", NS)
        pub = entry.findtext("a:published", "", NS)
        try:
            if datetime.fromisoformat(pub.replace("Z", "+00:00")) < cutoff:
                continue
        except ValueError:
            pass
        out.append({"id": vid, "title": entry.findtext("a:title", "", NS),
                    "artist": ch["name"], "category": ch["category"],
                    "channel": ch["channel_id"],
                    "url": f"https://www.youtube.com/watch?v={vid}"})
    return out


def from_search(cfg, known):
    words = cfg.get("search_words", [])
    if not YT_KEY or not words:
        return []
    now = datetime.now(timezone.utc)
    q = words[(now.hour * 3 + now.minute // 20) % len(words)]
    params = urllib.parse.urlencode({
        "part": "snippet", "type": "video", "order": "date", "maxResults": 25,
        "q": q, "regionCode": "IL", "relevanceLanguage": "he",
        "publishedAfter": (now - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "key": YT_KEY})
    try:
        data = json.loads(http_get("https://www.googleapis.com/youtube/v3/search?" + params))
    except Exception as e:
        print(f"search error: {e}")
        return []
    out = []
    for it in data.get("items", []):
        vid = it["id"].get("videoId")
        sn = it["snippet"]
        if vid:
            out.append({"id": vid, "title": sn.get("title", ""),
                        "artist": sn.get("channelTitle", ""),
                        "category": known.get(sn.get("channelId"), "נמצא בחיפוש"),
                        "channel": sn.get("channelId", ""),
                        "url": f"https://www.youtube.com/watch?v={vid}"})
    return out


def send_telegram(text):
    if not (TG_TOKEN and TG_CHAT):
        return
    data = urllib.parse.urlencode({"chat_id": TG_CHAT, "text": text}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data, timeout=20)
    except Exception as e:
        print(f"telegram error: {e}")


def send_whatsapp(text):
    if not (WA_PHONE and WA_KEY):
        return
    try:
        http_get("https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
            {"phone": WA_PHONE, "text": text, "apikey": WA_KEY}), timeout=30)
    except Exception as e:
        print(f"whatsapp error: {e}")


def main():
    cfg = load_json(CHANNELS_FILE, {})
    state = load_json(SEEN_FILE, None)
    first_run = state is None
    if isinstance(state, list):
        state = {"seen": state, "ids": {}}
    if not isinstance(state, dict):
        state = {"seen": [], "ids": {}}
    seen, ids = state.setdefault("seen", []), state.setdefault("ids", {})
    seen_set = set(seen)
    artists = cfg.get("artists", {})

    todo = [n for names in artists.values() for n in names if n not in ids]
    new_channels = set()
    for name in todo[:RESOLVE_PER_RUN]:
        try:
            cid = find_channel_id(name)
        except Exception as e:
            print(f"resolve error {name}: {e}")
            continue
        ids[name] = cid or ""
        print(f"{name} -> {cid}")
        if cid:
            new_channels.add(cid)
    left = len([n for names in artists.values() for n in names if n not in ids])
    print(f"channels found: {len([v for v in ids.values() if v])}, not found: "
          f"{len([v for v in ids.values() if not v])}, still to search: {left}")

    chans, used = [], set()
    for c in cfg.get("channels", []):
        cid = c.get("channel_id", "")
        if cid.startswith("UC") and "xxxx" not in cid and cid not in used:
            used.add(cid)
            chans.append({"name": c.get("name", ""), "channel_id": cid,
                          "category": c.get("category", "כללי")})
    for cat, names in artists.items():
        for n in names:
            cid = ids.get(n)
            if cid and cid not in used:
                used.add(cid)
                chans.append({"name": n, "channel_id": cid, "category": cat})

    with ThreadPoolExecutor(max_workers=10) as pool:
        found = [s for lst in pool.map(fetch_feed, chans) for s in lst]
    found += from_search(cfg, {c["channel_id"]: c["category"] for c in chans})

    wanted = set(cfg.get("notify_categories", []))
    excl = [w.lower() for w in cfg.get("exclude_words", [])]
    new = []
    for s in found:
        if s["id"] in seen_set:
            continue
        seen_set.add(s["id"])
        seen.append(s["id"])
        if s["channel"] in new_channels:
            continue
        if any(w in s["title"].lower() for w in excl):
            continue
        if wanted and s["category"] not in wanted:
            continue
        new.append(s)

    if first_run:
        send_telegram("✅ הבוט הופעל ומתחיל לעקוב אחרי שירים חדשים.")
    else:
        for s in new[:15]:
            msg = (f"🎵 שיר חדש!\n{s['title']}\n"
                   f"👤 {s['artist']}\n📂 {s['category']}\n{s['url']}")
            send_telegram(msg)
            send_whatsapp(msg)
        print(f"sent {min(len(new), 15)} alerts")

    state["seen"] = seen[-8000:]
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(main())
