"""Base de DÉMO pour vérifier l'interface sans données réelles :  python scripts/demo_db.py [mysql://root@127.0.0.1:33099]

Crée `prospection_demo` (schéma + seed + toutes les migrations), y met une campagne et une vingtaine de prospects couvrant les cas utiles
(reprise, ouverture récente, site en panne, relance due, site à moderniser, restaurant écarté, recherche incomplète, résultats pour
l'apprentissage), puis calcule les scores comme le worker. Ensuite : lancer la configuration « web-demo » (.claude/launch.json).
Aucune donnée réelle, aucun réseau. La base est RECRÉÉE à chaque lancement.
"""
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import pymysql
from pymysql.constants import CLIENT

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "worker"))
BASE = sys.argv[1] if len(sys.argv) > 1 else "mysql://root@127.0.0.1:33099"
NAME = "prospection_demo"


def admin(dbname=None):
    u = urlparse(BASE)
    return pymysql.connect(host=u.hostname, port=u.port or 3306, user=u.username or "root", password=u.password or "", database=dbname,
                           charset="utf8mb4", autocommit=True, client_flag=CLIENT.MULTI_STATEMENTS)


with admin() as c, c.cursor() as cur:
    cur.execute(f"DROP DATABASE IF EXISTS `{NAME}`")
    cur.execute(f"CREATE DATABASE `{NAME}` CHARACTER SET utf8mb4")
with admin(NAME) as c, c.cursor() as cur:
    for f in sorted((ROOT / "db" / "migrations").glob("*.sql")):
        cur.execute(f.read_text(encoding="utf-8"))
        while cur.nextset():
            pass

import worker.db as wdb  # noqa: E402
import worker.migrate as wmig  # noqa: E402

wdb.DATABASE_URL, wmig.MIGRATIONS_DIR = f"{BASE.rstrip('/')}/{NAME}", str(ROOT / "db" / "updates")
wmig.run()
from worker import db  # noqa: E402
from worker.local import learning, rescore  # noqa: E402

now = datetime.now(timezone.utc).replace(tzinfo=None)
with db.connect() as c:
    db.execute(c, "INSERT IGNORE INTO users (email) VALUES ('demo@exemple.fr')")
    cid = db.execute(c, """INSERT INTO local_campaigns (name, city, postal_code, department, latitude, longitude, radius_km, activities, status)
                           VALUES ('Troyes 10 km','Troyes','10000','10',48.2924,4.0761,10,%s,'done')""", (json.dumps(["plombiers", "restaurants"]),))

    def add(siret, name, act, label, lat, lon, **o):
        row = {"siret": siret, "siren": siret[:9], "fingerprint": siret, "company_name": name, "activity_key": act, "activity_label": label, "city": "Troyes",
               "address": "1 rue de la Paix 10000 Troyes", "latitude": lat, "longitude": lon, "distance_km": 2.0, "website_status": "NOT_FOUND",
               "website_absence_confidence": 0.85, "company_created_at": "2019-01-01", "phone": "0325000000", "geo_confidence": 0.9,
               "contact_confidence": 0.8, "business_status_confidence": 0.9, "pipeline_stage": "DONE", **o}
        pid = db.execute(c, f"INSERT INTO local_prospects ({', '.join(row)}) VALUES ({', '.join(['%s'] * len(row))})", tuple(row.values()))
        db.execute(c, "INSERT INTO local_prospect_campaigns VALUES (%s,%s)", (pid, cid))

    add("10000000000001", "PLOMBERIE DURAND", "plombiers", "Plombiers", 48.30, 4.08, manager_name="Paul Durand", revenue=240000, revenue_year=2024,
        employee_range="02", deep_enriched_at=now, bodacc=json.dumps({"kind": "change", "events": [{"kind": "takeover", "published": (now - timedelta(days=45)).strftime("%Y-%m-%d")}]}),
        social_links=json.dumps([{"network": "Facebook", "url": "https://www.facebook.com/plomberiedurand", "via": "search", "confidence": 0.7}]))
    add("10000000000002", "LE PETIT BISTROT", "restaurants", "Restaurants", 48.29, 4.06, company_created_at=(now - timedelta(days=95)).strftime("%Y-%m-%d"),
        osm_data=json.dumps({"name": "Le Petit Bistrot", "opening_hours": "Tu-Sa 12:00-14:00,19:00-22:00", "confidence": 0.7}))
    add("10000000000003", "COUVERTURE MARTIN", "couvreurs", "Couvreurs", 48.31, 4.10, website_status="UNREACHABLE", error_category="SITE_DNS_ERROR",
        website_url="http://couverture-martin.fr/")
    add("40000000000001", "ELECTRICITE BERNARD", "electriciens", "Électriciens", 48.295, 4.09, status="CONTACTED", contacted_at=now - timedelta(days=5))
    add("50000000000001", "SALON LEA", "coiffure", "Salons de coiffure", 48.293, 4.07, website_status="CONFIRMED", website_url="https://salon-lea.fr/",
        website_confidence=0.95, audited_at=now, modernization_opportunity="HIGH", email="contact@salon-lea.fr", email_kind="GENERIC_BUSINESS",
        contact_confidence=0.9, employee_range="01", phone=None,
        issues=json.dumps([{"code": "no_viewport", "label": "Pas de balise viewport : affichage mobile probablement non adapté", "severity": "high"},
                           {"code": "no_https", "label": "Site en HTTP non sécurisé (pas de HTTPS)", "severity": "high"}]))
    add("60000000000001", "RESTAURANT LE BON COIN", "restaurants", "Restaurants", 48.29, 4.07, website_status="CONFIRMED", website_url="https://lebon.fr/",
        website_confidence=0.95, audited_at=now, modernization_opportunity="LOW", technical_score=90, seo_score=85, email="a@lebon.fr", email_kind="GENERIC_BUSINESS")
    add("70000000000001", "MENUISERIE PETIT", "menuisiers", "Menuisiers", 48.30, 4.05, website_absence_confidence=0.3)
    for i in range(6):
        add(f"2000000000000{i}", f"PLOMBIER {i}", "plombiers", "Plombiers", 48.28 + i / 100, 4.05, status="REPLIED" if i < 3 else "LOST",
            contacted_at=now - timedelta(days=30), draft_kind="sans_site")
    for i in range(6):
        add(f"3000000000000{i}", f"BAR {i}", "bars", "Bars, cafés", 48.27, 4.04 + i / 100, status="LOST", contacted_at=now - timedelta(days=30), draft_kind="site_ameliorable")
    c.commit()
    learning.refresh(c)
    print(f"{rescore.rescore_all(c)} prospects de démo prêts dans {NAME}")
