# -*- coding: utf-8 -*-
"""Läuft bei GitHub (Repository stiftolu/mira-bilder, Ablauf .github/workflows/posten.yml) alle 15 Minuten.
Postet jedes Stück aus plan.json, dessen Datum heute (Berlin) ist und dessen Uhrzeit vorbei ist; verpasste Stücke desselben
Tages werden nachgeholt. Erledigtes steht in gepostet.json (der Ablauf schreibt die Datei danach zurück ins Repository).
Schlüssel: nur im GitHub-Geheimnis IG_TOKEN (nie im Repository). Gilt 60 Tage ab 18.10.2026 → vor ~15.12. erneuern.
Telegram (Marcel 19.10.2026, wie bei Eduki): jedes Stück, das online ist, kommt sofort als Meldung mit Bildern + Beitragstext in
Marcels Chat mit @mira_instagram_bot; nach dem 3. Fehlversuch eine Warnung. Bot-Daten nur in den GitHub-Geheimnissen
TELEGRAM_TOKEN + TELEGRAM_CHAT_ID; fehlen sie, wird nur nichts gemeldet. Eine gescheiterte Meldung ändert nie etwas am Posten.
Gebaut von 02_Werkzeuge\\instagram\\cloud_vorbereiten.py (Marcel 18.10.2026: nichts soll an sein müssen).
  python posten_cloud.py              führt aus
  python posten_cloud.py --trocken    zeigt nur, was heute fällig wäre (kein Instagram-Aufruf)
  python posten_cloud.py --warten     Sekunden bis zum nächsten offenen Stück (0 = jetzt fällig, -1 = keins in den nächsten 14 h);
                                      danach richtet der Dauerlauf in posten.yml sein Schlafen (20.10.2026: GitHub-Zeitplan kam nur ~4× am Tag)"""
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

API = "https://graph.instagram.com"
IG_ID = "17841428710863480"   # mira_sommerfeld_
RAW = "https://raw.githubusercontent.com/stiftolu/mira-bilder/main/"
BERLIN = ZoneInfo("Europe/Berlin")
TELEGRAM = "https://api.telegram.org/bot%s/%s"
WOCHENTAG = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def lies(p, leer):
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else leer


def schreibe(p, daten):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=1)


def api(methode, pfad, daten, token):
    daten = dict(daten, access_token=token)
    url = "%s/%s" % (API, pfad)
    if methode == "GET":
        req = urllib.request.Request(url + "?" + urllib.parse.urlencode(daten))
    else:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(daten).encode(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        fehler = json.loads(e.read().decode("utf-8", "replace")).get("error", {})
        raise RuntimeError("Schnittstelle %s %s: %s" % (methode, pfad.split("/")[-1], fehler.get("message", e.code)))


def warte_fertig(cid, token):
    for _ in range(20):
        st = api("GET", cid, {"fields": "status_code,status"}, token)
        if st.get("status_code") == "FINISHED":
            return
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError("Container %s: %s" % (st.get("status_code"), st.get("status", "")))
        time.sleep(30)
    raise RuntimeError("Container nach 10 Minuten nicht fertig")


def url(pfad):
    return RAW + urllib.parse.quote(pfad)


def ist_video(p):
    return p.lower().endswith(".mp4")


def container(s, token):
    """Seit 20.10.2026 sind Stories + Karussell-Seiten meist Clips mit Musik (.mp4, gebaut von clips.py auf dem Laptop)."""
    art, dateien, text = s["art"], s["dateien"], s.get("text")
    if art == "STORY":
        quelle = {"video_url": url(dateien[0])} if ist_video(dateien[0]) else {"image_url": url(dateien[0])}
        cid = api("POST", IG_ID + "/media", dict(quelle, media_type="STORIES", is_ai_generated="true"), token)["id"]
    elif art == "REEL":
        d = {"media_type": "REELS", "video_url": url(dateien[0]), "caption": text, "share_to_feed": "true", "is_ai_generated": "true"}
        if len(dateien) > 1:
            d["cover_url"] = url(dateien[1])
        cid = api("POST", IG_ID + "/media", d, token)["id"]
    else:
        kinder = [api("POST", IG_ID + "/media", ({"video_url": url(p), "media_type": "VIDEO"} if ist_video(p) else {"image_url": url(p)})
                      | {"is_carousel_item": "true"}, token)["id"] for p in dateien]
        for k in kinder:   # KI-Kennzeichen nur am Sammel-Container (an den Kindern = Fehler)
            warte_fertig(k, token)
        cid = api("POST", IG_ID + "/media", {"media_type": "CAROUSEL", "children": ",".join(kinder), "caption": text,
                                              "is_ai_generated": "true"}, token)["id"]
    warte_fertig(cid, token)
    return cid


def schon_online(cid, token):
    """Instagram meldet beim Veröffentlichen manchmal einen Fehler (oder antwortet zu spät), obwohl der Beitrag online ist.
    Der Container steht dann auf PUBLISHED → nicht noch einmal posten."""
    try:
        return api("GET", cid, {"fields": "status_code"}, token).get("status_code") == "PUBLISHED"
    except Exception:
        return False


def token_holen():
    t = os.environ.get("IG_TOKEN", "").strip()
    if not t:
        sys.exit("GitHub-Geheimnis IG_TOKEN fehlt")
    return t


def telegram(methode, felder, dateien=()):
    """Ein Telegram-Aufruf; Dateien werden aus dem Repository hochgeladen (Bauweise wie Eduki telegram_meldung.py)."""
    tg, chat = os.environ.get("TELEGRAM_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not (tg and chat):
        return False   # noch nicht eingerichtet
    grenze = uuid.uuid4().hex
    teile = ["--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (grenze, k, v)
             for k, v in dict(felder, chat_id=chat).items()]
    teile = [t.encode("utf-8") for t in teile]
    for name, pfad in dateien:
        art = "video/mp4" if pfad.lower().endswith(".mp4") else "image/jpeg"
        teile.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"
                      % (grenze, name, os.path.basename(pfad), art)).encode("utf-8") + open(pfad, "rb").read() + b"\r\n")
    teile.append(("--%s--\r\n" % grenze).encode())
    req = urllib.request.Request(TELEGRAM % (tg, methode), data=b"".join(teile),
                                 headers={"Content-Type": "multipart/form-data; boundary=" + grenze})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            if not json.load(r).get("ok"):
                raise RuntimeError("Telegram %s: nicht ok" % methode)
    except urllib.error.HTTPError as e:   # Fehlertext ohne Bot-Schlüssel (steckt in der Adresse)
        raise RuntimeError("Telegram %s: %s" % (methode, e.read().decode("utf-8", "replace")[:200]))
    except Exception as e:   # Lauf-Protokoll ist öffentlich → Schlüssel nie im Text
        raise RuntimeError("Telegram %s: %s %s" % (methode, type(e).__name__, str(e).replace(tg, "***")[:200]))
    return True


def melden(s, mid, token):
    """Online-Meldung: Story = Bild, Reel = Video, Karussell = Album mit allen Seiten; Beitragstext + Link darunter."""
    d = datetime.date.fromisoformat(s["datum"])
    art = {"STORY": "📱 Story", "REEL": "🎬 Reel"}.get(s["art"], "🖼 Karussell")
    text = "✅ %s ist online\n%s %s · %s Uhr" % (art, WOCHENTAG[d.weekday()], d.strftime("%d.%m."), s["zeit"])
    if s.get("text"):
        text += "\n\n" + s["text"]
    if token and mid and not str(mid).startswith("Container"):
        try:
            link = api("GET", mid, {"fields": "permalink"}, token).get("permalink")
            if link:
                text += "\n\n" + link
        except Exception:
            pass   # ohne Link melden
    text = text[:1024]   # Telegram-Grenze für Bildunterschriften
    dateien = s["dateien"]
    if s["art"] == "STORY" or len(dateien) == 1 or s["art"] == "REEL":
        if ist_video(dateien[0]):
            return telegram("sendVideo", {"caption": text, "supports_streaming": "true"}, [("video", dateien[0])])
        return telegram("sendPhoto", {"caption": text}, [("photo", dateien[0])])
    medien = [{"type": "video" if ist_video(p) else "photo", "media": "attach://f%d" % i} for i, p in enumerate(dateien)]
    medien[0]["caption"] = text
    return telegram("sendMediaGroup", {"media": json.dumps(medien, ensure_ascii=False)},
                    [("f%d" % i, p) for i, p in enumerate(dateien)])


def nachmelden(plan, fertig, heute):
    """Alles, was heute online ging und noch nicht gemeldet ist (auch nach einem Telegram-Fehler), höchstens 3 Versuche."""
    token = os.environ.get("IG_TOKEN", "").strip()
    for s in plan:
        e = fertig.get(s["key"])
        if s["datum"] != heute or not isinstance(e, dict) or e.get("telegram") == "ok" or e.get("telegram_fehler", 0) >= 3:
            continue
        try:
            if not melden(s, e.get("id"), token):
                return   # Bot nicht eingerichtet
            e["telegram"] = "ok"
            print("TELEGRAM gemeldet", s["key"])
        except Exception as ex:
            e["telegram_fehler"] = e.get("telegram_fehler", 0) + 1
            print("TELEGRAM FEHLER", s["key"], ex)
        schreibe("gepostet.json", fertig)


def warten(plan, fertig, jetzt):
    """Sekunden bis zum nächsten Stück, das noch posten soll; vergangene Tage zählen nicht (holt main ohnehin nicht nach)."""
    versuche = fertig.get("_fehlversuche", {})
    sek = []
    for s in plan:
        if s["key"] in fertig or versuche.get(s["key"], 0) >= 3 or s["datum"] < jetzt.date().isoformat():
            continue
        t = datetime.datetime.fromisoformat("%sT%s" % (s["datum"], s["zeit"])).replace(tzinfo=BERLIN)
        sek.append(max(0, int((t - jetzt).total_seconds())))
    m = min(sek, default=-1)
    return m if 0 <= m <= 14 * 3600 else -1


def main():
    a = sys.argv[1:]
    trocken = "--trocken" in a
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    plan = lies("plan.json", [])
    fertig = lies("gepostet.json", {})
    jetzt = datetime.datetime.now(BERLIN)
    if "--warten" in a:
        print(warten(plan, fertig, jetzt))
        return 0
    heute, uhr = jetzt.date().isoformat(), jetzt.strftime("%H:%M")
    if trocken and "--am" in a:   # Probe: --trocken --am 2026-10-20 23:59
        heute, uhr = a[a.index("--am") + 1], a[a.index("--am") + 2]
    versuche = fertig.setdefault("_fehlversuche", {})
    faellig = [s for s in plan if s["datum"] == heute and s["zeit"] <= uhr and s["key"] not in fertig
               and versuche.get(s["key"], 0) < 3]   # nach 3 Fehlschlägen nicht weiter probieren (steht dann im Lauf-Protokoll)
    print("%s %s Berlin · fällig: %d" % (heute, uhr, len(faellig)))
    for s in faellig:
        print("  ", s["zeit"], s["art"], s["key"])
    if trocken:
        return 0
    if fertig.get("_schluessel_geprueft", {}).get("datum") != heute:   # einmal täglich nur lesend prüfen (sichtbar in gepostet.json)
        token = token_holen()
        try:
            me = api("GET", "me", {"fields": "username"}, token)
        except Exception as ex:   # 20.10.2026: Meta sperrte („API access blocked.“), bis Marcel sein Facebook-Konto verifizierte
            if faellig and fertig.get("_schluessel_fehler") != heute:   # einmal am Tag warnen, nur wenn etwas posten soll
                fertig["_schluessel_fehler"] = heute
                schreibe("gepostet.json", fertig)
                try:
                    telegram("sendMessage", {"text": "⚠️ Mira: Instagram lässt das Programm gerade nicht rein (%s).\n"
                                             "Bis das gelöst ist, geht nichts online. Schreib im Mira-Fenster „/weiter“." % ex})
                except Exception as tx:
                    print("TELEGRAM FEHLER", tx)
            raise
        fertig["_schluessel_geprueft"] = {"datum": heute, "uhr": uhr, "konto": me.get("username")}
        schreibe("gepostet.json", fertig)
        print("Schlüssel ok:", me.get("username"))
    if not faellig:
        nachmelden(plan, fertig, heute)
        return 0
    token = token_holen()
    offen = fertig.setdefault("_container", {})   # Container, die veröffentlicht werden sollten, aber mit Fehler endeten
    fehler = 0
    for s in faellig:
        k, cid, mid = s["key"], None, None
        if k in offen and schon_online(offen[k], token):   # letzter Versuch kam doch an
            mid = "Container " + offen[k]
        else:
            try:
                cid = container(s, token)
                offen[k] = cid
                schreibe("gepostet.json", fertig)
                mid = api("POST", IG_ID + "/media_publish", {"creation_id": cid}, token)["id"]
            except Exception as ex:
                if cid:
                    time.sleep(30)
                    if schon_online(cid, token):
                        mid = "Container " + cid
                if not mid:
                    fehler += 1
                    versuche[k] = versuche.get(k, 0) + 1
                    schreibe("gepostet.json", fertig)
                    print("FEHLER", k, "Versuch", versuche[k], ex)
                    if versuche[k] == 3:   # gibt auf → Marcel Bescheid sagen
                        try:
                            telegram("sendMessage", {"text": "⚠️ %s %s Uhr ist nicht online gegangen (3 Versuche).\n"
                                                     "Schreib im Mira-Fenster „/weiter“." % (s["art"].capitalize(), s["zeit"])})
                        except Exception as tx:
                            print("TELEGRAM FEHLER", k, tx)
                    continue
        fertig[k] = {"id": mid, "zeit": datetime.datetime.now(BERLIN).isoformat(timespec="seconds")}
        offen.pop(k, None)
        schreibe("gepostet.json", fertig)   # sofort merken, damit nichts doppelt kommt
        print("ONLINE", k, mid)
        nachmelden([s], fertig, heute)
    nachmelden(plan, fertig, heute)   # Meldungen, die vorher gescheitert sind
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
