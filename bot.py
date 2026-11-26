# -*- coding: utf-8 -*-
import json, os, sys, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
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
NS = {"a": "http://www.w3.org/2005/Atom",
      "yt": "http://www.youtube.com/xml/schemas/2015"}


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def is_excluded(title, cfg):
    t = title.lower()
    return any(w.lower() in t for w in cfg.get("exclude_words", []))


def from_channels(cfg):
    out = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    for ch in cfg.get("channels", []):
        cid = ch.get("channel_id", "")
        if not cid.startswith("UC") or "xxxx" in cid:
            continue
        try:
            root = ET.fromstring(http_get(
                f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"))
        except Exception as e:
            print(f"error in channel {ch.get('name')}: {e}")
            continue
        for entry in root.findall("a:entry", NS):
            vid = entry.findtext("yt:videoId", "", NS)
            title = entry.findtext("a:title", "", NS)
            pub = entry.findtext("a:published", "", NS)
            try:
                if datetime.fromisoformat(pub.replace("Z", "+00:00")) < cutoff:
                    continue
            except ValueError:
                pass
            out.append({"id": vid, "title": title, "artist": ch.get("name", ""),
                        "category": ch.get("category", "כללי"),
                        "url": f"https://www.youtube.com/watch?v={vid}"})
    return out


def from_search(cfg, known_channels):
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
        data = json.loads(http_get(
            "https://www.googleapis.com/youtube/v3/search?" + params))
    except Exception as e:
        print(f"search error: {e}")
        return []
    out = []
    for it in data.get("items", []):
        vid = it["id"].get("videoId")
        sn = it["snippet"]
        if not vid:
            continue
        cat = known_channels.get(sn.get("channelId"), "נמצא בחיפוש")
        out.append({"id": vid, "title": sn.get("title", ""),
                    "artist": sn.get("channelTitle", ""), "category": cat,
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
    url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode(
        {"phone": WA_PHONE, "text": text, "apikey": WA_KEY})
    try:
        http_get(url, timeout=30)
    except Exception as e:
        print(f"whatsapp error: {e}")


def main():
    cfg = load_json(CHANNELS_FILE, {})
    wanted = set(cfg.get("notify_categories", []))
    known = {c["channel_id"]: c.get("category", "כללי")
             for c in cfg.get("channels", []) if c.get("channel_id")}
    first_run = not os.path.exists(SEEN_FILE)
    seen = load_json(SEEN_FILE, [])
    seen_set = set(seen)

    found = from_channels(cfg) + from_search(cfg, known)
    new = []
    for s in found:
        if s["id"] in seen_set or is_excluded(s["title"], cfg):
            continue
        seen_set.add(s["id"])
        seen.append(s["id"])
        if wanted and s["category"] not in wanted:
            continue
        new.append(s)

    if first_run:
        print(f"first run: saved {len(new)} songs without sending")
        send_telegram("✅ הבוט הופעל ומתחיל לעקוב אחרי שירים חדשים.")
    else:
        for s in new[:15]:
            msg = (f"🎵 שיר חדש!\n{s['title']}\n"
                   f"👤 {s['artist']}\n📂 {s['category']}\n{s['url']}")
            send_telegram(msg)
            send_whatsapp(msg)
        print(f"sent {min(len(new), 15)} alerts")

    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen[-5000:], f, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(main())
