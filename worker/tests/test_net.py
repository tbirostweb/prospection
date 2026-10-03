"""Téléchargement sécurisé : SSRF, redirections, taille."""
import socket

import httpx
import pytest

from worker import net
from worker.net import UnsafeURL, safe_get, validate_url


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(net, "_transport", None)


def _fake_dns(monkeypatch, mapping):
    def fake(host, port, proto=0, **kw):
        if host not in mapping:
            raise socket.gaierror("inconnu")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in mapping[host]]
    monkeypatch.setattr(net.socket, "getaddrinfo", fake)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://exemple.fr/x", "gopher://exemple.fr",
    "http://localhost/admin", "http://127.0.0.1:80/", "http://[::1]/", "http://0.0.0.0/",
    "http://10.0.0.5/", "http://192.168.1.1/", "http://172.16.0.9/", "http://169.254.169.254/latest/meta-data",
    "http://100.64.0.1/", "http://[::ffff:127.0.0.1]/", "http://user:pass@exemple.fr/",
    "https://exemple.fr:3306/", "http://exemple.fr:11434/api/tags", "",
])
def test_urls_interdites(url, monkeypatch):
    _fake_dns(monkeypatch, {"exemple.fr": ["93.184.216.34"]})
    with pytest.raises(UnsafeURL):
        validate_url(url)


def test_nom_interne_docker_refuse(monkeypatch):
    # « mysql », « ollama »… se résolvent en 172.x dans le réseau Docker.
    _fake_dns(monkeypatch, {"mysql": ["172.18.0.2"], "ollama": ["172.18.0.4"]})
    for host in ("mysql", "ollama"):
        with pytest.raises(UnsafeURL):
            validate_url(f"http://{host}/")


def test_une_seule_ip_privee_suffit_a_refuser(monkeypatch):
    _fake_dns(monkeypatch, {"piege.fr": ["93.184.216.34", "10.0.0.1"]})   # DNS à réponses mixtes
    with pytest.raises(UnsafeURL):
        validate_url("https://piege.fr/")


def test_url_publique_acceptee(monkeypatch):
    _fake_dns(monkeypatch, {"exemple.fr": ["93.184.216.34"]})
    scheme, host, port, ips = validate_url("https://exemple.fr/page")
    assert (scheme, host, ips) == ("https", "exemple.fr", ["93.184.216.34"])


def test_redirection_vers_le_reseau_interne_bloquee(monkeypatch):
    _fake_dns(monkeypatch, {"exemple.fr": ["93.184.216.34"], "interne.exemple.fr": ["10.1.2.3"]})

    def handler(request):
        return httpx.Response(302, headers={"location": "http://interne.exemple.fr/secret"})
    monkeypatch.setattr(net, "_transport", httpx.MockTransport(handler))
    with pytest.raises(UnsafeURL):
        safe_get("https://exemple.fr/")


def test_redirection_valide_suivie(monkeypatch):
    _fake_dns(monkeypatch, {"a.fr": ["93.184.216.34"], "b.fr": ["93.184.216.35"]})

    def handler(request):
        if request.url.host == "a.fr":
            return httpx.Response(301, headers={"location": "https://b.fr/final"})
        return httpx.Response(200, text="<html>ok</html>", headers={"content-type": "text/html"})
    monkeypatch.setattr(net, "_transport", httpx.MockTransport(handler))
    r = safe_get("https://a.fr/")
    assert r.ok and r.url == "https://b.fr/final" and r.redirects == 1 and "ok" in r.text


def test_boucle_de_redirections_stoppee(monkeypatch):
    _fake_dns(monkeypatch, {"a.fr": ["93.184.216.34"]})
    monkeypatch.setattr(net, "_transport", httpx.MockTransport(
        lambda req: httpx.Response(302, headers={"location": "https://a.fr/x"})))
    with pytest.raises(UnsafeURL):
        safe_get("https://a.fr/")


def test_taille_plafonnee_et_types_filtres(monkeypatch):
    _fake_dns(monkeypatch, {"a.fr": ["93.184.216.34"]})
    monkeypatch.setattr(net, "_transport", httpx.MockTransport(
        lambda req: httpx.Response(200, text="x" * 5000, headers={"content-type": "text/html"})))
    r = safe_get("https://a.fr/", max_bytes=1000)
    assert r.truncated and len(r.text) == 1000

    monkeypatch.setattr(net, "_transport", httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"\x00" * 100, headers={"content-type": "application/zip"})))
    assert safe_get("https://a.fr/").text == ""       # binaire : jamais lu


def test_try_get_ne_leve_jamais(monkeypatch):
    _fake_dns(monkeypatch, {})
    assert net.try_get("http://localhost/") is None
    assert net.try_get("https://inexistant.invalid/") is None
