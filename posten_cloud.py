# -*- coding: utf-8 -*-
"""Läuft bei GitHub (Repository stiftolu/mira-bilder, Ablauf .github/workflows/posten.yml) alle 15 Minuten.
Postet jedes Stück aus plan.json, dessen Datum heute (Berlin) ist und dessen Uhrzeit vorbei ist; verpasste Stücke desselben
Tages werden nachgeholt. Erledigtes steht in gepostet.json (der Ablauf schreibt die Datei danach zurück ins Repository).
Schlüssel: nur im GitHub-Geheimnis IG_TOKEN (nie im Repository). Gilt 60 Tage ab 18.10.2026 → vor ~15.12. erneuern.
Gebaut von 02_Werkzeuge\\instagram\\cloud_vorbereiten.py (Marcel 18.10.2026: nichts soll an sein müssen).
  python posten_cloud.py              führt aus
  python posten_cloud.py --trocken    zeigt nur, was heute fällig wäre (kein Instagram-Aufruf)"""
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

API = "https://graph.instagram.com"
IG_ID = "17841428710863480"   # mira_sommerfeld_
RAW = "https://raw.githubusercontent.com/stiftolu/mira-bilder/main/"
BERLIN = ZoneInfo("Europe/Berlin")


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


def container(s, token):
    art, dateien, text = s["art"], s["dateien"], s.get("text")
    if art == "STORY":
        cid = api("POST", IG_ID + "/media", {"image_url": url(dateien[0]), "media_type": "STORIES", "is_ai_generated": "true"}, token)["id"]
    elif art == "REEL":
        d = {"media_type": "REELS", "video_url": url(dateien[0]), "caption": text, "share_to_feed": "true", "is_ai_generated": "true"}
        if len(dateien) > 1:
            d["cover_url"] = url(dateien[1])
        cid = api("POST", IG_ID + "/media", d, token)["id"]
    else:
        kinder = [api("POST", IG_ID + "/media", {"image_url": url(p), "is_carousel_item": "true"}, token)["id"] for p in dateien]
        for k in kinder:   # KI-Kennzeichen nur am Sammel-Container (an den Kindern = Fehler)
            warte_fertig(k, token)
        cid = api("POST", IG_ID + "/media", {"media_type": "CAROUSEL", "children": ",".join(kinder), "caption": text,
                                              "is_ai_generated": "true"}, token)["id"]
    warte_fertig(cid, token)
    return cid


def token_holen():
    t = os.environ.get("IG_TOKEN", "").strip()
    if not t:
        sys.exit("GitHub-Geheimnis IG_TOKEN fehlt")
    return t


def main():
    a = sys.argv[1:]
    trocken = "--trocken" in a
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    plan = lies("plan.json", [])
    fertig = lies("gepostet.json", {})
    jetzt = datetime.datetime.now(BERLIN)
    heute, uhr = jetzt.date().isoformat(), jetzt.strftime("%H:%M")
    if trocken and "--am" in a:   # Probe: --trocken --am 2026-10-20 23:59
        heute, uhr = a[a.index("--am") + 1], a[a.index("--am") + 2]
    versuche = fertig.setdefault("_fehlversuche", {})
    faellig = [s for s in plan if s["datum"] == heute and s["zeit"] <= uhr and s["key"] not in fertig
               and versuche.get(s["key"], 0) < 3]   # nach 3 Fehlschlägen nicht weiter probieren (steht dann im Lauf-Protokoll)
    print("%s %s Berlin · fällig: %d" % (heute, uhr, len(faellig)))
    for s in faellig:
        print("  ", s["zeit"], s["art"], s["key"])
    if trocken or not faellig:
        return 0
    token = token_holen()
    fehler = 0
    for s in faellig:
        try:
            cid = container(s, token)
            mid = api("POST", IG_ID + "/media_publish", {"creation_id": cid}, token)["id"]
            fertig[s["key"]] = {"id": mid, "zeit": datetime.datetime.now(BERLIN).isoformat(timespec="seconds")}
            schreibe("gepostet.json", fertig)   # sofort merken, damit nichts doppelt kommt
            print("ONLINE", s["key"], mid)
        except Exception as ex:
            fehler += 1
            versuche[s["key"]] = versuche.get(s["key"], 0) + 1
            schreibe("gepostet.json", fertig)
            print("FEHLER", s["key"], "Versuch", versuche[s["key"]], ex)
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
