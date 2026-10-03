"""Téléchargement sûr d'URL issues du web (résultats de recherche, sites clients).

Problème traité : le worker tourne dans un réseau Docker où `mysql`,
`searxng` répondent en HTTP. Une URL piégée (ou une redirection) pointant vers
ces noms, `localhost`, `169.254.169.254` (métadonnées cloud)… ferait lire des
services internes. Ici :

  * schéma http/https seulement, ports 80/443, pas d'identifiants dans l'URL ;
  * TOUTES les adresses résolues doivent être publiques ;
  * redirections suivies à la main (max 3), chacune revalidée ;
  * connexion faite sur l'IP validée (anti « DNS rebinding »), avec SNI et
    vérification du certificat sur le vrai nom d'hôte ;
  * taille de réponse plafonnée, types de contenu texte uniquement, timeout court.

À NE PAS utiliser pour les services internes voulus (SearXNG) : ceux-là
passent par `Connector.get` / leurs clients dédiés.
"""
from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx

from .config import USER_AGENT, log

MAX_BYTES = 600_000
MAX_REDIRECTS = 3
ALLOWED_PORTS = {None, 80, 443}
TEXT_TYPES = ("text/", "application/xhtml", "application/xml", "application/json",
              "application/rss", "application/atom", "application/ld+json")

# Point d'injection pour les tests (httpx.MockTransport) — jamais utilisé en prod.
_transport: httpx.BaseTransport | None = None


class UnsafeURL(ValueError):
    """URL refusée par la politique de sécurité."""


@dataclass
class Fetched:
    url: str                 # URL finale (après redirections)
    status: int
    text: str
    content_type: str
    truncated: bool = False
    redirects: int = 0
    chain: tuple = ()        # URL successives (départ → finale) : sert à canonicaliser le domaine
    elapsed_ms: int | None = None
    headers: dict = field(default_factory=dict)   # sous-ensemble utile à l'audit léger : content-encoding, cache-control, server

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def _keep(headers) -> dict:
    return {k: headers.get(k, "")[:120] for k in ("content-encoding", "cache-control", "server", "x-powered-by", "expires") if headers.get(k)}


def _is_public(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def resolve_public(host: str, port: int | None) -> list[str]:
    """IP publiques de `host`, ou UnsafeURL si l'une d'elles ne l'est pas."""
    if not host or host.lower() in ("localhost", "localhost.localdomain") or host.endswith(".local"):
        raise UnsafeURL(f"hôte interdit : {host!r}")
    try:
        ipaddress.ip_address(host)
        infos = [host]
    except ValueError:
        try:
            infos = [ai[4][0] for ai in socket.getaddrinfo(host, port or 443, proto=socket.IPPROTO_TCP)]
        except socket.gaierror as exc:
            raise UnsafeURL(f"résolution DNS impossible : {host}") from exc
    if not infos:
        raise UnsafeURL(f"aucune adresse pour {host}")
    for ip in infos:
        if not _is_public(ip):
            raise UnsafeURL(f"{host} -> {ip} : adresse non publique")
    return list(dict.fromkeys(infos))


def validate_url(url: str) -> tuple[str, str, int | None, list[str]]:
    """Vérifie l'URL. Renvoie (scheme, host, port, ips_publiques)."""
    p = urlparse(url or "")
    if p.scheme not in ("http", "https"):
        raise UnsafeURL(f"schéma interdit : {p.scheme!r}")
    if p.username or p.password:
        raise UnsafeURL("identifiants dans l'URL")
    try:
        port = p.port
    except ValueError as exc:
        raise UnsafeURL("port invalide") from exc
    if port not in ALLOWED_PORTS:
        raise UnsafeURL(f"port interdit : {port}")
    host = p.hostname or ""
    return p.scheme, host, port, resolve_public(host, port)


def _pin(url: str, ip: str) -> tuple[str, dict, dict]:
    """URL réécrite sur l'IP + en-tête Host + extension SNI (certificat vérifié
    sur le vrai nom d'hôte)."""
    p = urlparse(url)
    host = p.hostname or ""
    ip_lit = f"[{ip}]" if ":" in ip else ip
    netloc = ip_lit + (f":{p.port}" if p.port else "")
    pinned = p._replace(netloc=netloc).geturl()
    headers = {"Host": p.netloc.split("@")[-1]}
    ext = {"sni_hostname": host} if p.scheme == "https" else {}
    return pinned, headers, ext


def safe_get(url: str, timeout: float | httpx.Timeout = 8.0, max_bytes: int = MAX_BYTES,
             headers: dict | None = None, max_seconds: float = 25.0) -> Fetched:
    """GET sécurisé. Lève UnsafeURL (URL interdite) ou httpx.HTTPError (réseau)."""
    current = url
    hops = 0
    chain = [url]
    t0 = time.monotonic()
    while True:
        _, _, _, ips = validate_url(current)
        pinned, host_hdr, ext = _pin(current, ips[0]) if _transport is None else (current, {}, {})
        h = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,*/*;q=0.5",
             **host_hdr, **(headers or {})}
        with httpx.Client(timeout=timeout, follow_redirects=False, transport=_transport,
                          verify=True) as client:
            with client.stream("GET", pinned, headers=h, extensions=ext) as resp:
                if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                    hops += 1
                    if hops > MAX_REDIRECTS:
                        raise UnsafeURL("trop de redirections")
                    current = urljoin(current, resp.headers["location"])
                    chain.append(current)
                    continue
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype and not ctype.startswith(TEXT_TYPES):
                    return Fetched(current, resp.status_code, "", ctype, redirects=hops, chain=tuple(chain), elapsed_ms=int((time.monotonic() - t0) * 1000), headers=_keep(resp.headers))
                buf, truncated = bytearray(), False
                for chunk in resp.iter_bytes():
                    buf.extend(chunk)
                    if len(buf) >= max_bytes or time.monotonic() - t0 > max_seconds:      # serveur qui goutte-à-goutte : on garde ce qu'on a
                        truncated = True
                        break
                enc = resp.encoding or "utf-8"
                text = bytes(buf[:max_bytes]).decode(enc, errors="replace")
                return Fetched(current, resp.status_code, text, ctype, truncated, hops, tuple(chain), int((time.monotonic() - t0) * 1000), _keep(resp.headers))


def try_get(url: str, **kw) -> Fetched | None:
    """`safe_get` qui n'échoue jamais : None + journal en cas de refus ou d'erreur."""
    try:
        return safe_get(url, **kw)
    except UnsafeURL as exc:
        log.warning("[net] URL refusée %s : %s", url[:120], exc)
    except (httpx.HTTPError, OSError) as exc:
        log.info("[net] échec %s : %s", url[:120], exc)
    return None
