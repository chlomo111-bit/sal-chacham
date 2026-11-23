#!/usr/bin/env python3
"""
סל חכם – récupère les VRAIS prix des grandes chaînes (loi sur la transparence des prix)
et fabrique prices.json pour le site.

À lancer sur le serveur VPS, une fois par jour (ex. 06:00) :
    pip install il-supermarket-scraper
    python3 fetch_prices.py --out /chemin/du/site/prices

Étapes :
 1. télécharge les fichiers « Stores » de chaque chaîne -> trouve les magasins de chaque ville
 2. télécharge les fichiers « PriceFull » de ces magasins seulement (2 par chaîne et par ville)
 3. écrit prices/<ville>.json + prices/cities.json
 3. garde les produits (code-barres) vendus dans au moins 3 magasins -> comparaison juste
"""
import argparse, asyncio, glob, gzip, json, os, re, shutil, zipfile, datetime, time, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from il_supermarket_scarper.scrappers_factory import ScraperFactory
from il_supermarket_scarper.utils.file_output import DiskFileOutput

# géocodage des adresses de magasin (OpenStreetMap/Nominatim, gratuit, sans clé)
# -> pour afficher chaque magasin à sa vraie position sur la carte 3D, par ville
_GEOCACHE = {}
def geocode(address, city):
    key = (address or "").strip() + "|" + city
    if key in _GEOCACHE:
        return _GEOCACHE[key]
    q = f"{address}, {city}, ישראל" if address else f"{city}, ישראל"
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "json", "limit": 1, "countrycodes": "il"})
    lat = lng = None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sal-chacham-app/1.0 (github.com/chlomo111-bit/sal-chacham)"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        if data:
            lat, lng = float(data[0]["lat"]), float(data[0]["lon"])
    except Exception:
        pass
    time.sleep(1.1)  # politique Nominatim : max 1 requête/seconde
    _GEOCACHE[key] = (lat, lng)
    return lat, lng

# chaîne de l'app -> nom du scraper
CHAINS = {
    "shufersal": "SHUFERSAL", "rami": "RAMI_LEVY", "osher": "OSHER_AD",
    "victory": "VICTORY_NEW_SOURCE", "yochananof": "YOHANANOF", "hazi": "HAZI_HINAM",
    "carrefour": "YAYNO_BITAN_AND_CARREFOUR", "tivtaam": "TIV_TAAM",
    "mahsanei": "MAHSANI_ASHUK_NEW_SOURCE",
}
MAX_STORES_PER_CHAIN = int(os.environ.get("MAX_STORES", "2"))
# villes proposées dans l'app : id -> (nom affiché, mots à chercher dans City/Address, lat, lng)
CITIES = {
    "rishon": ("ראשון לציון", ["ראשון"], 31.964, 34.804), "holon": ("חולון", ["חולון"], 32.01, 34.78),
    "oryehuda": ("אור יהודה", ["אור יהודה", "אור-יהודה"], 32.03, 34.85), "netanya": ("נתניה", ["נתניה"], 32.32, 34.86),
    "ashdod": ("אשדוד", ["אשדוד"], 31.80, 34.65), "ashkelon": ("אשקלון", ["אשקלון"], 31.67, 34.57),
    "haifa": ("חיפה", ["חיפה"], 32.79, 34.99), "beersheva": ("באר שבע", ["באר שבע", "באר-שבע"], 31.25, 34.79),
    "tlv": ("תל אביב-יפו", ["תל אביב", "תל-אביב", "יפו"], 32.08, 34.78), "jlm": ("ירושלים", ["ירושלים"], 31.77, 35.21),
    "petah": ("פתח תקווה", ["פתח תקו", "פתח-תקו"], 32.09, 34.88),
}
# pour ne traiter que certaines villes : ONLY_CITIES="rishon,holon"
ONLY = [c for c in os.environ.get("ONLY_CITIES", "").split(",") if c]
# si une chaîne donne des codes de ville au lieu des noms : forcer les magasins à la main
# ex. MANUAL_STORES='{"rishon":{"rami":[39]}}'
MANUAL = json.loads(os.environ.get("MANUAL_STORES", "{}"))
WORK = os.environ.get("XML_STORE_PATH", "/tmp/sal-dumps")


def read_any(path):
    """lit .xml / .gz / .zip -> texte"""
    raw = open(path, "rb").read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    elif raw[:2] == b"PK":
        with zipfile.ZipFile(path) as z:
            raw = z.read(z.namelist()[0])
    for enc in ("utf-8", "utf-16", "windows-1255"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "ignore")


def parse(path):
    txt = read_any(path).lstrip("﻿")
    return ET.fromstring(txt.encode("utf-8") if not txt.startswith("<?xml") else re.sub(r"^<\?xml[^>]*\?>", "", txt).encode("utf-8"))


def val(el, *names):
    """valeur d'une balise, sans tenir compte des majuscules"""
    low = {n.lower() for n in names}
    for c in el.iter():
        if c.tag.lower() in low and c.text and c.text.strip():
            return c.text.strip()
    return None


def records(root, tag):
    t = tag.lower()
    return [e for e in root.iter() if e.tag.lower() == t]


async def download(scraper_name, files_types, store_id=None, folder=None):
    cls = ScraperFactory.get(scraper_name)
    if cls is None:
        return []
    os.makedirs(folder, exist_ok=True)
    inst = cls(file_output=DiskFileOutput(storage_path=folder))  # dossier explicite (le défaut de la librairie plante)
    gen = inst.scrape(files_types=files_types, store_id=store_id) if store_id else inst.scrape(files_types=files_types)
    if hasattr(gen, "__aiter__"):      # la librairie renvoie les fichiers au fur et à mesure
        async for _ in gen:
            pass
    else:
        await gen
    return glob.glob(os.path.join(folder, "**", "*"), recursive=True)


def all_stores(files):
    out, seen = [], set()
    for f in files:
        if os.path.isdir(f) or "store" not in os.path.basename(f).lower():
            continue
        try:
            root = parse(f)
        except Exception:
            continue
        for s in records(root, "Store") + records(root, "Branch"):
            sid = val(s, "StoreId", "StoreID")
            if not sid or not sid.isdigit() or int(sid) in seen:
                continue
            seen.add(int(sid))
            out.append({"id": int(sid), "name": val(s, "StoreName") or "", "city": val(s, "City") or "",
                        "address": (val(s, "Address") or "").strip(),
                        "lat": float(val(s, "Latitude") or 0) or None, "lng": float(val(s, "Longitude") or 0) or None})
    return out


def stores_in_city(stores, words):
    hit = [s for s in stores if any(w in (s["city"] + " " + s["address"] + " " + s["name"]) for w in words)]
    return hit[:MAX_STORES_PER_CHAIN]


def read_prices(files):
    items = {}
    for f in files:
        if os.path.isdir(f) or "price" not in os.path.basename(f).lower():
            continue
        try:
            root = parse(f)
        except Exception:
            continue
        for it in records(root, "Item") + records(root, "Product"):
            code, price = val(it, "ItemCode"), val(it, "ItemPrice")
            if not code or not price or len(code) < 7:  # on ignore les codes internes (pain du rayon, etc.)
                continue
            try:
                p = round(float(price), 2)
            except ValueError:
                continue
            if p <= 0:
                continue
            items[code] = {"n": val(it, "ItemName", "ItemNm") or "", "m": val(it, "ManufacturerName") or "", "price": p}
    return items


async def main(outdir):
    os.makedirs(outdir, exist_ok=True)
    cities = {k: v for k, v in CITIES.items() if not ONLY or k in ONLY}
    per_city = {k: {"stores": [], "catalog": {}} for k in cities}
    for chain, scraper in CHAINS.items():
        base = os.path.join(WORK, chain)
        shutil.rmtree(base, ignore_errors=True); os.makedirs(base, exist_ok=True)
        try:
            stores = all_stores(await download(scraper, ["STORE_FILE"], folder=os.path.join(base, "stores")))
            print(f"{chain}: {len(stores)} magasins dans le pays")
            cache = {}  # un magasin n'est téléchargé qu'une fois, même s'il sert à 2 villes
            for cid, (cname, words, *_ ) in cities.items():
                manual = MANUAL.get(cid, {}).get(chain)
                chosen = [{"id": int(i), "address": "", "lat": None, "lng": None} for i in manual] if manual else stores_in_city(stores, words)
                for st in chosen:
                    if st["id"] not in cache:
                        cache[st["id"]] = read_prices(await download(scraper, ["PRICE_FULL_FILE"], store_id=st["id"], folder=os.path.join(base, f"p{st['id']}")))
                    prices = cache[st["id"]]
                    if not prices:
                        print(f"  ! {chain} {st['id']} ({cname}) : aucun prix"); continue
                    sid = f"{chain}-{st['id']}"
                    lat, lng = st.get("lat"), st.get("lng")
                    if not lat or not lng:
                        lat, lng = geocode(st.get("address", ""), cname)
                    per_city[cid]["stores"].append({"id": sid, "chain": chain, "short": st["address"][:18], "address": st["address"], "lat": lat, "lng": lng})
                    for code, x in prices.items():
                        c = per_city[cid]["catalog"].setdefault(code, {"b": code, "n": x["n"], "m": x["m"], "p": {}})
                        c["p"][sid] = x["price"]
                    print(f"  {cname} · {sid}: {len(prices)} produits")
        except Exception as e:  # une chaîne en panne ne bloque pas les autres
            print(f"  ! {chain}: {type(e).__name__}: {e}")
    now = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    index = []
    for cid, d in per_city.items():
        cname, _, lat, lng = cities[cid]
        if not d["stores"]:
            print(f"{cname}: aucun magasin trouvé"); continue
        need = min(3, len(d["stores"]))
        items = [c for c in d["catalog"].values() if len(c["p"]) >= need]
        path = os.path.join(outdir, f"{cid}.json")
        with open(path + ".tmp", "w", encoding="utf-8") as fh:
            json.dump({"updated": now, "city": cname, "stores": d["stores"], "items": items}, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(path + ".tmp", path)
        index.append({"id": cid, "name": cname, "lat": lat, "lng": lng})
        print(f"OK {cname}: {len(d['stores'])} magasins, {len(items)} produits comparables")
    with open(os.path.join(outdir, "cities.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, ensure_ascii=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="prices", help="dossier de sortie (un fichier par ville + cities.json)")
    asyncio.run(main(ap.parse_args().out))
