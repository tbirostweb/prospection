"""Régressions des correctifs d'audit : charset inconnu (F3), NAT64 / 6to4 et décompression bornée (F8),
variables du worker (F4) et healthcheck MySQL (F10)."""
import gzip
import pathlib
import re
import socket
import zlib

import httpx
import pytest

from worker import config, net, scheduler
from worker.net import UnsafeURL, safe_get, validate_url

ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(net, "_transport", None)


def _dns(monkeypatch, mapping):
    def fake(host, port, proto=0, **kw):
        if host not in mapping:
            raise socket.gaierror("inconnu")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in mapping[host]]
    monkeypatch.setattr(net.socket, "getaddrinfo", fake)


def _serve(monkeypatch, content: bytes, headers: dict):
    # stream= (et non content=) : le corps n'est pas pré-lu, comme une vraie réponse réseau.
    monkeypatch.setattr(net, "_transport", httpx.MockTransport(
        lambda req: httpx.Response(200, headers=headers, stream=httpx.ByteStream(content))))


# ── F3 : charset inconnu ou non textuel ─────────────────────────────────────────────────────────────────────────
def test_charset_rot13_repli_utf8(monkeypatch):
    _dns(monkeypatch, {"a.fr": ["93.184.216.34"]})
    _serve(monkeypatch, content="<html>café</html>".encode(), headers={"content-type": "text/html; charset=rot13"})
    r = safe_get("https://a.fr/")
    assert r.ok and r.text == "<html>café</html>"


def test_charset_inexistant_repli_utf8(monkeypatch):
    _dns(monkeypatch, {"a.fr": ["93.184.216.34"]})
    _serve(monkeypatch, content=b"<p>ok</p>", headers={"content-type": "text/html; charset=pas-un-charset"})
    r = safe_get("https://a.fr/")
    assert r.ok and "ok" in r.text


def test_charset_valide_inchange(monkeypatch):
    _dns(monkeypatch, {"a.fr": ["93.184.216.34"]})
    _serve(monkeypatch, content="été".encode("latin-1"), headers={"content-type": "text/html; charset=iso-8859-1"})
    assert safe_get("https://a.fr/").text == "été"


# ── F8 : adresses IPv4 embarquées ───────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("ip", [
    "64:ff9b::7f00:1", "64:ff9b::a00:5", "64:ff9b::a9fe:a9fe",     # NAT64 -> 127.0.0.1, 10.0.0.5, 169.254.169.254
    "2002:7f00:1::", "2002:c0a8:101::1", "2002:a9fe:a9fe::",       # 6to4 -> 127.0.0.1, 192.168.1.1, 169.254.169.254
    "64:ff9b:1::808:808",                                          # NAT64 à usage local
])
def test_ipv4_embarquee_privee_refusee(ip, monkeypatch):
    _dns(monkeypatch, {"piege.fr": [ip]})
    with pytest.raises(UnsafeURL):
        validate_url("https://piege.fr/")
    with pytest.raises(UnsafeURL):
        validate_url(f"http://[{ip}]/")


@pytest.mark.parametrize("ip", ["64:ff9b::5db8:d822", "2002:5db8:d822::1", "2606:4700::1111"])
def test_ipv4_embarquee_publique_acceptee(ip, monkeypatch):
    _dns(monkeypatch, {"ok.fr": [ip]})
    assert validate_url("https://ok.fr/")[3] == [ip]


# ── F8 : décompression bornée ───────────────────────────────────────────────────────────────────────────────────
def test_gzip_normal_decompresse(monkeypatch):
    _dns(monkeypatch, {"a.fr": ["93.184.216.34"]})
    body = "<html>" + "bonjour " * 200 + "</html>"
    _serve(monkeypatch, content=gzip.compress(body.encode()), headers={"content-type": "text/html", "content-encoding": "gzip"})
    r = safe_get("https://a.fr/")
    assert r.ok and r.text == body and not r.truncated and r.headers.get("content-encoding") == "gzip"


@pytest.mark.parametrize("wbits", [zlib.MAX_WBITS, -zlib.MAX_WBITS])
def test_deflate_zlib_et_brut(wbits, monkeypatch):
    _dns(monkeypatch, {"a.fr": ["93.184.216.34"]})
    c = zlib.compressobj(wbits=wbits)
    _serve(monkeypatch, content=c.compress(b"<p>deflate</p>") + c.flush(), headers={"content-type": "text/html", "content-encoding": "deflate"})
    assert safe_get("https://a.fr/").text == "<p>deflate</p>"


def test_bombe_gzip_bornee(monkeypatch):
    _dns(monkeypatch, {"a.fr": ["93.184.216.34"]})
    bomb = gzip.compress(b"\0" * (50 * 1024 * 1024))                 # 50 Mo décompressés pour ~50 Ko transmis
    _serve(monkeypatch, content=bomb, headers={"content-type": "text/html", "content-encoding": "gzip"})
    produced = []
    real = net._BoundedInflater.decode

    def spy(self, data, max_out):
        out = real(self, data, max_out)
        produced.append(len(out))
        return out
    monkeypatch.setattr(net._BoundedInflater, "decode", spy)
    r = safe_get("https://a.fr/", max_bytes=10_000)
    assert r.truncated and len(r.text) == 10_000
    assert max(produced) <= 10_001                                   # jamais plus que le plafond par lecture


def test_double_gzip_borne(monkeypatch):
    _dns(monkeypatch, {"a.fr": ["93.184.216.34"]})
    bomb = gzip.compress(gzip.compress(b"A" * (20 * 1024 * 1024)))
    _serve(monkeypatch, content=bomb, headers={"content-type": "text/html", "content-encoding": "gzip, gzip"})
    r = safe_get("https://a.fr/", max_bytes=5_000)
    assert r.truncated and r.text == "A" * 5_000


def test_double_gzip_petit_complet(monkeypatch):
    _dns(monkeypatch, {"a.fr": ["93.184.216.34"]})
    body = b"<html>" + bytes(range(256)) * 400 + b"</html>"
    _serve(monkeypatch, content=gzip.compress(gzip.compress(body)), headers={"content-type": "text/plain; charset=latin-1", "content-encoding": "gzip, gzip"})
    r = safe_get("https://a.fr/", max_bytes=1_000_000)
    assert not r.truncated and r.text.encode("latin-1") == body


def test_gzip_corrompu_erreur_reseau(monkeypatch):
    _dns(monkeypatch, {"a.fr": ["93.184.216.34"]})
    _serve(monkeypatch, content=b"pas du gzip", headers={"content-type": "text/html", "content-encoding": "gzip"})
    with pytest.raises(httpx.HTTPError):
        safe_get("https://a.fr/")
    assert net.try_get("https://a.fr/") is None


# ── F4 : environnement explicite du worker ──────────────────────────────────────────────────────────────────────
def _service(name: str) -> str:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    m = re.search(rf"^  {name}:\n(.*?)(?=^  \S|^\S)", text, re.S | re.M)
    assert m, name
    return m.group(1)


def test_worker_sans_env_file_ni_secrets_inutiles():
    block = _service("worker")
    assert "env_file" not in block
    for secret in ("MYSQL_ROOT_PASSWORD", "APP_PASSWORD", "APP_USER", "SEARXNG_SECRET", "MYSQL_PASSWORD"):
        assert not re.search(rf"^\s+{secret}:", block, re.M), secret


def test_worker_recoit_toutes_les_variables_lues():
    block = _service("worker")
    passed = set(re.findall(r"^      ([A-Z][A-Z0-9_]+):", block, re.M))
    src = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "worker" / "worker").rglob("*.py"))
    read = set(re.findall(r"""(?:getenv|environ\.get|\benv)\(\s*["']([A-Z][A-Z0-9_]+)["']""", src))
    read |= set(re.findall(r"""_env_int\(\s*["']([A-Z][A-Z0-9_]+)["']""", src))
    internal = {"ALERT_STATE", "APP_DIR", "LOG_DIR", "LOCAL_LOCK", "MIGRATIONS_DIR", "TELEGRAM_OFFSET_STATE",
                "TELEGRAM_API_BASE", "USER_AGENT"}             # chemins internes / surcharges de test : valeurs par défaut de l'image
    assert read - internal <= passed, read - internal - passed
    for name in passed - {"TZ"}:                               # tout ce qui est transmis passe aussi le filtre des tâches planifiées
        assert scheduler.ENV_PATTERN.match(name) or name == "RETENTION_ENABLED", name


def test_variable_vide_vaut_absente(monkeypatch):
    monkeypatch.setenv("PROSPECTION_TEST_VAR", "")
    assert config.env("PROSPECTION_TEST_VAR", "defaut") == "defaut"
    monkeypatch.setenv("PROSPECTION_TEST_VAR", "valeur")
    assert config.env("PROSPECTION_TEST_VAR", "defaut") == "valeur"
    monkeypatch.delenv("PROSPECTION_TEST_VAR")
    assert config.env("PROSPECTION_TEST_VAR", "defaut") == "defaut"


def test_job_env_inchange():
    env = scheduler.job_env({"DATABASE_URL": "x", "LOCAL_AUDIT": "", "MYSQL_ROOT_PASSWORD": "y", "APP_PASSWORD": "z", "SEARXNG_SECRET": "s"})
    assert env == {"DATABASE_URL": "x", "LOCAL_AUDIT": ""}


# ── F10 : healthcheck MySQL sans mot de passe en ligne de commande ──────────────────────────────────────────────
def test_healthcheck_mysql_sans_mot_de_passe_en_argument():
    block = _service("mysql")
    test_line = next(l for l in block.splitlines() if "test:" in l)
    assert "mysqladmin ping" in test_line and "MYSQL_PWD=" in test_line
    assert not re.search(r"\s-p\S", test_line)


# ── F6 : seed sans donnée personnelle ───────────────────────────────────────────────────────────────────────────
def test_seed_sans_donnee_personnelle():
    seed = (ROOT / "db" / "migrations" / "002_seed.sql").read_text(encoding="utf-8")
    emails = set(re.findall(r"'([^'\s]+@[^'\s]+)'", seed))
    assert emails == {"utilisateur@exemple.invalid"}
    assert "birostweb" not in seed.lower() and "gmail" not in seed.lower()
