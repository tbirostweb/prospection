"""Multi-moteurs SearXNG : un moteur bloqué ne rend JAMAIS toute la recherche vide ; santé, cooldown, reprise."""
import json

import httpx
import pytest

from worker import db
from worker.local import engines, errors


def searx(behaviour: dict, calls: list | None = None):
    """Faux SearXNG : `behaviour[moteur]` = 'ok' | 'captcha' | 'empty' | 'suspended' | 'timeout' | 'http500' ; requête → résultats identiques."""
    def handler(req: httpx.Request):
        eng = req.url.params["engines"]
        if calls is not None:
            calls.append(eng)
        mode = behaviour.get(eng, "ok")
        if mode == "http500":
            return httpx.Response(500, text="boom")
        if mode == "timeout":
            raise httpx.ReadTimeout("lent", request=req)
        if mode == "captcha":
            return httpx.Response(200, json={"results": [], "unresponsive_engines": [[eng, "CAPTCHA"]]})
        if mode == "suspended":
            return httpx.Response(200, json={"results": [], "unresponsive_engines": [[eng, "Suspended: access denied"]]})
        if mode == "empty":
            return httpx.Response(200, json={"results": [], "unresponsive_engines": []})
        return httpx.Response(200, json={"results": [{"url": f"https://site-{eng}-{i}.fr/", "title": f"Résultat {eng} {i}", "content": "x"} for i in range(3)]})
    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def sans_cles_de_l_environnement(monkeypatch):
    """Aucune clé réelle de l'environnement ne doit brancher une API (ni dépenser) pendant les tests."""
    for k in ("BRAVE_API_KEY", "TAVILY_API_KEY", "SERPER_API_KEY"):
        monkeypatch.setattr(engines, k, "")
    monkeypatch.setattr(engines, "PAID_ON_EMPTY", False)
    monkeypatch.setattr(engines, "PAID_MONTHLY_BUDGET", 0)


@pytest.fixture
def pool_factory(env, conn, monkeypatch):
    def make(behaviour, engines_list="google,bing,mojeek", calls=None):
        monkeypatch.setattr(engines, "_transport", searx(behaviour, calls))
        return engines.SearchPool(conn, engines_list, "http://searx.test")
    return make


def health(conn, name):
    return db.fetch_one(conn, "SELECT * FROM local_engine_health WHERE engine=%s", (name,))


def test_un_moteur_captcha_est_retire_et_les_autres_continuent(conn, pool_factory):
    pool = pool_factory({"google": "captcha"})
    hits = pool('"Boulangerie Martin" "Troyes"')
    assert hits and all("google" not in e for h in hits for e in h.engines)          # les résultats viennent des autres moteurs
    assert pool.last.degraded and pool.last.engines_failed == [("google", errors.SEARCH_CAPTCHA)]
    g = health(conn, "google")
    assert g["cooldown_until"] is not None and g["captchas"] == 1 and g["consecutive_failures"] == 1 and "CAPTCHA" in g["last_error"]
    assert g["last_failure_at"] is not None and g["health"] is not None
    assert "google" not in pool.available()                                          # en cooldown : plus interrogé


def test_un_moteur_en_cooldown_n_est_plus_appele_puis_revient(conn, pool_factory):
    calls: list[str] = []
    pool = pool_factory({"google": "suspended"}, calls=calls)
    pool("a")
    calls.clear()
    pool("b")
    assert "google" not in calls                                                     # pas de requête inutile vers un moteur suspendu
    db.execute(conn, "UPDATE local_engine_health SET cooldown_until = UTC_TIMESTAMP() - INTERVAL 1 MINUTE WHERE engine='google'")
    conn.commit()
    assert "google" in pool.available()                                              # cooldown échu : réessayé


def test_le_cooldown_grandit_apres_des_echecs_repetes(conn, pool_factory):
    pool = pool_factory({"google": "timeout"})
    pool("a")
    first = db.fetch_one(conn, "SELECT TIMESTAMPDIFF(MINUTE, UTC_TIMESTAMP(), cooldown_until) AS m FROM local_engine_health WHERE engine='google'")["m"]
    db.execute(conn, "UPDATE local_engine_health SET cooldown_until = NULL WHERE engine='google'")
    conn.commit()
    pool("b")
    second = db.fetch_one(conn, "SELECT TIMESTAMPDIFF(MINUTE, UTC_TIMESTAMP(), cooldown_until) AS m FROM local_engine_health WHERE engine='google'")["m"]
    assert second > first and health(conn, "google")["timeouts"] == 2


def test_tous_les_moteurs_en_echec_leve_search_unavailable_jamais_une_liste_vide(conn, pool_factory):
    pool = pool_factory({"google": "captcha", "bing": "suspended", "mojeek": "timeout"})
    with pytest.raises(errors.SearchUnavailable) as exc:
        pool("a")
    assert "google" in str(exc.value)
    with pytest.raises(errors.SearchUnavailable) as exc:                              # tous en cooldown : toujours une ERREUR, pas « aucun résultat »
        pool("b")
    assert "cooldown" in str(exc.value)


def test_resultat_vide_sans_erreur_est_un_vrai_vide_et_est_compte(conn, pool_factory):
    pool = pool_factory({"google": "empty", "bing": "empty", "mojeek": "empty"})
    assert pool("requête introuvable") == [] and not pool.last.degraded and pool.empty == 1
    assert health(conn, "google")["empty_results"] == 1 and health(conn, "google")["cooldown_until"] is None


def test_serveur_searxng_injoignable_n_incrimine_aucun_moteur(conn, monkeypatch):
    def down(req):
        raise httpx.ConnectError("refusé", request=req)
    monkeypatch.setattr(engines, "_transport", httpx.MockTransport(down))
    pool = engines.SearchPool(conn, "google,bing", "http://searx.test")
    with pytest.raises(errors.SearchUnavailable):
        pool("a")
    assert health(conn, "google")["cooldown_until"] is None and health(conn, "google")["failures"] == 0


def test_second_moteur_interroge_seulement_si_le_premier_ne_rend_rien(conn, pool_factory):
    calls: list[str] = []
    pool = pool_factory({"google": "ok", "bing": "ok", "mojeek": "ok"}, calls=calls)
    pool("a")
    assert len(calls) == 1                                                            # le 1er moteur a rendu des résultats : économie d'une requête
    calls.clear()
    pool2 = pool_factory({"google": "empty", "bing": "empty", "mojeek": "empty"}, calls=calls)
    pool2("b")
    assert len(calls) == 2                                                            # 0 résultat : un 2e moteur confirme, jamais plus de 2
    calls.clear()
    pool3 = pool_factory({"google": "captcha", "bing": "ok"}, calls=calls)
    pool3("c")
    assert calls == ["google", "bing"]                                                # un moteur en échec n'est pas une réponse : le suivant est essayé


def test_score_de_sante_penalise_captcha_erreurs_et_lenteur():
    good = engines.health_score({"requests": 20, "successes": 20, "useful_results": 18, "avg_ms": 800})
    captcha = engines.health_score({"requests": 20, "successes": 8, "captchas": 12, "useful_results": 6, "avg_ms": 800, "consecutive_failures": 3})
    slow = engines.health_score({"requests": 20, "successes": 20, "useful_results": 18, "avg_ms": 9000})
    assert good > slow > captcha and good >= 90
    assert engines.health_score({"requests": 1}) == 60                                # pas d'historique : neutre


@pytest.mark.parametrize("reason,cat", [("CAPTCHA", errors.SEARCH_CAPTCHA), ("Suspended: too many requests", errors.SEARCH_RATE_LIMIT),
                                        ("HTTP error 429", errors.SEARCH_RATE_LIMIT), ("timeout", errors.SEARCH_TIMEOUT),
                                        ("Suspended: access denied", errors.SEARCH_UNAVAILABLE), ("HTTP error 403", errors.SEARCH_UNAVAILABLE)])
def test_classification_des_raisons_d_echec(reason, cat):
    assert engines.classify_reason(reason) == cat


def test_les_pannes_de_moteurs_non_demandes_ne_sont_pas_imputees(conn, monkeypatch):
    """Constaté en réel : mojeek répond « 0 résultat » avec `unresponsive_engines` = [wikipedia timeout…] : ce n'est pas la panne de mojeek."""
    def handler(req):
        return httpx.Response(200, json={"results": [], "unresponsive_engines": [["wikipedia", "timeout"], ["duckduckgo", "Suspended: timeout"]]})
    monkeypatch.setattr(engines, "_transport", httpx.MockTransport(handler))
    pool = engines.SearchPool(conn, "mojeek", "http://searx.test")
    assert pool("x") == [] and not pool.last.degraded
    assert health(conn, "mojeek")["failures"] == 0 and health(conn, "mojeek")["cooldown_until"] is None


def test_vrais_vides_ne_declenchent_pas_le_cooldown_quand_le_temoin_repond(conn, monkeypatch):
    """Noms d'entreprises introuvables = 0 résultat légitime (mojeek respecte les guillemets). La requête témoin distingue vrai vide et blocage."""
    def handler(req):
        q = req.url.params["q"]
        return httpx.Response(200, json={"results": [{"url": "https://x.fr/", "title": "t", "content": "c"}] if "pizza" in q else [], "unresponsive_engines": []})
    monkeypatch.setattr(engines, "_transport", httpx.MockTransport(handler))
    pool = engines.SearchPool(conn, "mojeek", "http://searx.test")
    for i in range(engines.EMPTY_STREAK_LIMIT + 2):
        pool(f'"Entreprise inconnue {i}" "TROYES"')
    m = health(conn, "mojeek")
    assert m["cooldown_until"] is None and m["failures"] == 0 and m["consecutive_empty"] < engines.EMPTY_STREAK_LIMIT


def test_qualite_d_un_moteur_se_mesure_a_la_pertinence_pas_au_volume(conn, pool_factory):
    pool = pool_factory({"google": "ok"}, engines_list="google")
    pool("q")
    assert health(conn, "google")["useful_results"] == 0
    pool.note_relevance({"google"})
    assert health(conn, "google")["useful_results"] == 1


def test_blocage_silencieux_zero_resultat_en_serie_met_le_moteur_en_cooldown(conn, pool_factory):
    """Constaté en réel : après ~700 requêtes, mojeek et startpage répondaient 0 résultat SANS aucune erreur. Un vrai vide ne dure pas 8 fois de suite."""
    pool = pool_factory({"google": "empty", "bing": "ok"}, engines_list="google,bing")
    for i in range(engines.EMPTY_STREAK_LIMIT):
        pool(f"requête {i}")
    g = health(conn, "google")
    assert g["cooldown_until"] is not None and "vides d'affilée" in g["last_error"] and g["consecutive_empty"] == 0
    assert "google" not in pool.available()                                            # retiré ; bing continue
    assert pool("encore")                                                              # la recherche n'est pas vide pour autant
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_events WHERE source='google'")["n"] == 1


def test_un_resultat_non_vide_remet_la_serie_a_zero(conn, pool_factory):
    pool = pool_factory({"google": "empty"}, engines_list="google")
    for i in range(engines.EMPTY_STREAK_LIMIT - 1):
        pool(f"q{i}")
    assert health(conn, "google")["consecutive_empty"] == engines.EMPTY_STREAK_LIMIT - 1
    pool.names = ["google"]
    engines._transport = searx({"google": "ok"})
    pool("q")
    assert health(conn, "google")["consecutive_empty"] == 0 and health(conn, "google")["cooldown_until"] is None


def test_budget_quotidien_par_moteur_menage_les_moteurs(conn, pool_factory, monkeypatch):
    monkeypatch.setattr(engines, "DAILY_BUDGET", 3)
    pool = pool_factory({}, engines_list="google")
    for i in range(3):
        pool(f"q{i}")
    assert pool.available() == []
    with pytest.raises(errors.SearchUnavailable) as exc:
        pool("q4")
    assert "budget" in str(exc.value)
    db.execute(conn, "UPDATE local_engine_health SET window_start = UTC_TIMESTAMP() - INTERVAL 25 HOUR")       # la fenêtre de 24 h est écoulée
    conn.commit()
    assert pool.available() == ["google"]


def test_moteur_demande_qui_ne_sert_rien_est_ecarte_mais_les_resultats_restent_utilises(conn, monkeypatch):
    """Constaté sur le VPS : engines=mojeek → résultats de brave / google cse / duckduckgo uniquement (mojeek désactivé sur l'instance)."""
    def handler(req):
        return httpx.Response(200, json={"results": [{"url": "https://site.fr/", "title": "t", "content": "c", "engines": ["brave", "google cse"]}]})
    monkeypatch.setattr(engines, "_transport", httpx.MockTransport(handler))
    pool = engines.SearchPool(conn, "mojeek,brave", "http://searx.test")
    hits = pool("q")
    assert hits and hits[0].engines == ["brave", "google cse"]                       # les résultats sont gardés, avec leurs VRAIS moteurs
    m = health(conn, "mojeek")
    assert m["failures"] == 1 and m["cooldown_until"] is not None and "ne fournit aucun résultat" in m["last_error"]
    assert pool.last.degraded


def test_moteurs_decouverts_depuis_la_config_de_l_instance(conn, monkeypatch):
    cfg = {"engines": [{"name": "duckduckgo", "enabled": True, "categories": ["general"]}, {"name": "brave", "enabled": True, "categories": ["general"]},
                       {"name": "mojeek", "enabled": False, "categories": ["general"]}, {"name": "wikipedia", "enabled": True, "categories": ["general", "wikimedia"]},
                       {"name": "currency", "enabled": True, "categories": ["general"], "paging": False}, {"name": "lingva", "enabled": True, "categories": ["general"]},
                       {"name": "google cse", "enabled": True, "categories": ["general"], "paging": True},
                       {"name": "youtube", "enabled": True, "categories": ["videos"]}]}
    monkeypatch.setattr(engines, "_transport", httpx.MockTransport(lambda req: httpx.Response(200, json=cfg)))
    assert engines.discover_enabled("http://searx.test") == ["brave", "duckduckgo", "google cse"]      # ni désactivé, ni vidéos, ni outils annexes (wikipedia, currency, lingva)
    pool = engines.SearchPool(conn, None, "http://searx.test")
    assert pool.names == ["brave", "duckduckgo", "google cse"] and pool.discovered
    monkeypatch.setattr(engines, "_transport", httpx.MockTransport(lambda req: httpx.Response(500)))
    assert engines.discover_enabled("http://searx.test") == []                                       # /config illisible : retomber sur la liste par défaut


# ── Secours : API officielle Brave Search (clé gratuite) ─────────────────────────────────────────────────
def with_brave(behaviour: dict, brave_status: int = 200, calls: list | None = None, searx_down: bool = False):
    searx_handler = searx(behaviour, calls).handler

    def handler(req: httpx.Request):
        if req.url.host == "api.search.brave.com":
            if calls is not None:
                calls.append("brave_api")
            assert req.headers["X-Subscription-Token"] == "cle-test" and req.url.params["country"] == "FR"
            if brave_status != 200:
                return httpx.Response(brave_status, json={})
            return httpx.Response(200, json={"web": {"results": [{"url": "https://plomberie-durand.fr/", "title": "Plomberie Durand", "description": "Troyes"}]}})
        if searx_down:
            raise httpx.ConnectError("refusé", request=req)
        return searx_handler(req)
    return httpx.MockTransport(handler)


def test_secours_brave_seulement_quand_searxng_ne_repond_pas(env, conn, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(engines, "_transport", with_brave({"google": "ok", "bing": "ok"}, calls=calls))
    pool = engines.SearchPool(conn, "google,bing", "http://searx.test", api_key="cle-test")
    assert pool.names[-1] == "brave_api"
    pool("a")
    assert "brave_api" not in calls                                                   # SearXNG répond : le quota Brave est épargné
    calls.clear()
    monkeypatch.setattr(engines, "_transport", with_brave({"google": "captcha", "bing": "suspended"}, calls=calls))
    hits = pool("b")
    assert calls[-1] == "brave_api" and hits[0].url == "https://plomberie-durand.fr/" and hits[0].engines == ["brave_api"]
    assert pool.last.degraded                                                         # des moteurs ont échoué : jamais une preuve d'absence


def test_secours_brave_quand_searxng_est_en_panne(env, conn, monkeypatch):
    monkeypatch.setattr(engines, "_transport", with_brave({}, searx_down=True))
    pool = engines.SearchPool(conn, "google,bing", "http://searx.test", api_key="cle-test")
    assert pool("a")[0].title == "Plomberie Durand"
    assert health(conn, "google")["failures"] == 0                                   # la panne du serveur n'incrimine aucun moteur


def test_cle_brave_refusee_ou_budget_epuise(env, conn, monkeypatch):
    monkeypatch.setattr(engines, "_transport", with_brave({"google": "captcha"}, brave_status=401))
    pool = engines.SearchPool(conn, "google", "http://searx.test", api_key="cle-test")
    with pytest.raises(errors.SearchUnavailable):
        pool("a")
    assert "clé refusée" in health(conn, "brave_api")["last_error"] and health(conn, "brave_api")["cooldown_until"] is not None
    db.execute(conn, "UPDATE local_engine_health SET cooldown_until=NULL, window_start=UTC_TIMESTAMP(), window_requests=%s WHERE engine='brave_api'",
               (engines.BRAVE_DAILY_BUDGET,))
    conn.commit()
    assert "brave_api" not in pool.available()                                        # budget du jour épuisé : quota mensuel protégé


def test_sans_cle_aucun_secours(env, conn, monkeypatch):
    monkeypatch.setattr(engines, "_transport", searx({}))
    assert "brave_api" not in engines.SearchPool(conn, "google,brave_api", "http://searx.test", api_key="").names


def test_requete_temoin_au_plus_une_fois_par_jour_et_par_moteur(conn, monkeypatch):
    calls: list[str] = []

    def handler(req):
        q = req.url.params["q"]
        calls.append(q)
        return httpx.Response(200, json={"results": [{"url": "https://x.fr/", "title": "t", "content": "c"}] if "pizza" in q else [], "unresponsive_engines": []})
    monkeypatch.setattr(engines, "_transport", httpx.MockTransport(handler))
    pool = engines.SearchPool(conn, "mojeek", "http://searx.test")
    for i in range(engines.EMPTY_STREAK_LIMIT):
        pool(f"inconnue {i}")
    assert calls.count(engines.CANARY_QUERY) == 1 and health(conn, "mojeek")["last_canary_at"] is not None
    for i in range(engines.EMPTY_STREAK_LIMIT):                                       # 2e série de vides le même jour : pas de 2e témoin, prudence = cooldown
        pool(f"autre {i}")
    assert calls.count(engines.CANARY_QUERY) == 1 and health(conn, "mojeek")["cooldown_until"] is not None


def test_cooldown_captcha_double_puis_plafonne(conn, pool_factory):
    pool = pool_factory({"google": "captcha"}, engines_list="google")
    got = []
    for i in range(6):
        db.execute(conn, "UPDATE local_engine_health SET cooldown_until = NULL WHERE engine='google'")
        conn.commit()
        with pytest.raises(errors.SearchUnavailable):
            pool(f"q{i}")
        got.append(db.fetch_one(conn, "SELECT TIMESTAMPDIFF(MINUTE, UTC_TIMESTAMP(), cooldown_until) AS m FROM local_engine_health WHERE engine='google'")["m"])
    assert [round(m / 60) for m in got] == [3, 6, 12, 24, 24, 24]                       # 180 → 360 → 720 → 1 440 min, jamais au-delà de 24 h
    db.execute(conn, "UPDATE local_engine_health SET cooldown_until = NULL WHERE engine='google'")
    conn.commit()
    engines._transport = searx({})
    pool("ok")
    assert health(conn, "google")["consecutive_failures"] == 0                         # un succès remet l'escalade à zéro


# ── Paliers : gratuit (SearXNG) → API gratuites → PAYANT (Serper), à budget plafonné ───────────────────
def apis(behaviour: dict, calls: list, *, brave: str | None = None, tavily: str | None = None, serper: str = "ok", searx_down: bool = False):
    """Faux SearXNG + fausses API. `brave` / `tavily` / `serper` = 'ok' | 'empty' | 'quota' | None (non configuré)."""
    searx_handler = searx(behaviour, calls).handler

    def handler(req: httpx.Request):
        host = req.url.host
        if host in ("api.search.brave.com", "api.tavily.com", "google.serper.dev"):
            name = {"api.search.brave.com": "brave_api", "api.tavily.com": "tavily", "google.serper.dev": "serper"}[host]
            calls.append(name)
            mode = {"brave_api": brave, "tavily": tavily, "serper": serper}[name]
            if mode == "quota":
                return httpx.Response(429, json={})
            if name == "serper":
                body = json.loads(req.content)
                assert req.method == "POST" and req.headers["X-API-KEY"] == "cle-serper" and body["gl"] == "fr" and body["hl"] == "fr" and body["num"] == 10
                return httpx.Response(200, json={"organic": [] if mode == "empty" else [{"link": "https://menuiserie-roux.fr/", "title": "Menuiserie Roux", "snippet": "Troyes"}]})
            if name == "tavily":
                assert req.headers["Authorization"] == "Bearer cle-tavily"
                return httpx.Response(200, json={"results": [] if mode == "empty" else [{"url": "https://tavily-site.fr/", "title": "T", "content": "c"}]})
            return httpx.Response(200, json={"web": {"results": [] if mode == "empty" else [{"url": "https://brave-site.fr/", "title": "B", "description": "d"}]}})
        if searx_down:
            raise httpx.ConnectError("refusé", request=req)
        return searx_handler(req)
    return httpx.MockTransport(handler)


def paid_pool(conn, monkeypatch, behaviour, calls, engines_list="google,bing", brave=None, tavily=None, **kw):
    monkeypatch.setattr(engines, "_transport", apis(behaviour, calls, brave=brave, tavily=tavily, **kw))
    return engines.SearchPool(conn, engines_list, "http://searx.test", api_key="cle-test" if brave else "",
                              tavily_key="cle-tavily" if tavily else "", serper_key="cle-serper")


def test_serper_est_le_dernier_palier(env, conn, monkeypatch):
    pool = paid_pool(conn, monkeypatch, {}, [], brave="ok", tavily="ok")
    assert pool.names[-3:] == ["brave_api", "tavily", "serper"] and [pool.tier(n) for n in pool.names] == [0, 0, 1, 1, 2]


def test_pas_de_payant_tant_qu_un_gratuit_repond(env, conn, monkeypatch):
    calls: list[str] = []
    pool = paid_pool(conn, monkeypatch, {"google": "ok"}, calls)
    pool("a")
    assert "serper" not in calls and pool.paid_queries == 0
    calls.clear()
    pool = paid_pool(conn, monkeypatch, {"google": "empty", "bing": "empty"}, calls)
    assert pool("b") == [] and "serper" not in calls                                  # gratuit VIDE : par défaut, on ne paie pas pour un vide


def test_payant_sur_vide_seulement_si_active(env, conn, monkeypatch):
    monkeypatch.setattr(engines, "PAID_ON_EMPTY", True)
    calls: list[str] = []
    pool = paid_pool(conn, monkeypatch, {"google": "empty", "bing": "empty"}, calls)
    assert pool("a")[0].url == "https://menuiserie-roux.fr/" and calls[-1] == "serper" and pool.paid_queries == 1
    calls.clear()
    pool2 = paid_pool(conn, monkeypatch, {"google": "ok"}, calls)
    pool2("c")
    assert "serper" not in calls                                                      # le gratuit a des résultats : jamais de payant


def test_cascade_gratuit_api_gratuite_puis_payant(env, conn, monkeypatch):
    calls: list[str] = []
    pool = paid_pool(conn, monkeypatch, {"google": "captcha", "bing": "suspended"}, calls, brave="ok", tavily="ok")
    hits = pool("a")
    assert calls == ["google", "bing", "brave_api"] and hits[0].engines == ["brave_api"]   # palier 1 a répondu : ni Tavily ni Serper
    calls.clear()
    db.execute(conn, "DELETE FROM local_engine_health")
    conn.commit()
    pool = paid_pool(conn, monkeypatch, {}, calls, brave="quota", tavily="quota", searx_down=True)
    hits = pool("b")                                                                  # SearXNG en panne, API gratuites à quota épuisé : le payant sert
    assert calls == ["brave_api", "tavily", "serper"] and hits[0].engines == ["serper"] and pool.paid_queries == 1
    assert pool.last.degraded and health(conn, "google")["failures"] == 0
    calls.clear()
    pool("c")                                                                         # API gratuites en cooldown : plus interrogées
    assert calls == ["serper"]


def test_budget_quotidien_payant_jamais_depasse(env, conn, monkeypatch):
    monkeypatch.setattr(engines, "SERPER_DAILY_BUDGET", 2)
    calls: list[str] = []
    pool = paid_pool(conn, monkeypatch, {}, calls, searx_down=True)
    pool("a")
    pool("b")
    with pytest.raises(errors.SearchUnavailable):
        pool("c")
    assert calls.count("serper") == 2 and health(conn, "serper")["window_requests"] == 2
    assert "serper" not in pool.available() and engines.next_available_at(conn, ["serper"]) is not None


def test_plafond_mensuel_payant(env, conn, monkeypatch):
    monkeypatch.setattr(engines, "PAID_MONTHLY_BUDGET", 5)
    pool = paid_pool(conn, monkeypatch, {}, [], searx_down=True)
    pool("a")
    assert health(conn, "serper")["month_requests"] == 1
    db.execute(conn, "UPDATE local_engine_health SET month_requests=5 WHERE engine='serper'")
    conn.commit()
    assert "serper" not in pool.available()
    db.execute(conn, "UPDATE local_engine_health SET month_start = month_start - INTERVAL 1 MONTH WHERE engine='serper'")   # mois écoulé
    conn.commit()
    assert "serper" in pool.available()


def test_reponse_vide_d_une_api_n_est_pas_un_blocage_silencieux(env, conn, monkeypatch):
    calls: list[str] = []
    pool = paid_pool(conn, monkeypatch, {}, calls, serper="empty", searx_down=True)
    for i in range(engines.EMPTY_STREAK_LIMIT + 1):
        assert pool(f"q{i}") == []
    assert engines.CANARY_QUERY not in calls and health(conn, "serper")["cooldown_until"] is None    # aucun témoin payant


def test_description_des_paliers(monkeypatch):
    monkeypatch.setattr(engines, "SERPER_API_KEY", "x")
    lines = engines.describe_tiers(["google"])
    assert lines[0].startswith("palier 0") and any("serper" in ln and "ACTIF" in ln and "palier 2" in ln for ln in lines)
    assert any("tavily" in ln and "LOCAL_TAVILY_API_KEY" in ln for ln in lines)


def test_le_worker_declare_tous_les_fournisseurs_avec_palier_et_budgets(env, conn, monkeypatch):
    """L'écran Statistiques ne voit pas les variables LOCAL_* : chaque fournisseur est déclaré (même sans clé, même à 0 requête), sans aucune clé en base."""
    monkeypatch.setattr(engines, "PAID_MONTHLY_BUDGET", 400)
    monkeypatch.setattr(engines, "_transport", searx({}))
    pool = engines.SearchPool(conn, "google,bing", "http://searx.test", api_key="cle-secrete-brave", tavily_key="", serper_key="")
    rows = {r["engine"]: r for r in db.fetch_all(conn, "SELECT * FROM local_engine_health")}
    assert set(rows) == {"google", "bing", "brave_api", "tavily", "serper"}
    assert rows["google"]["tier"] == 0 and rows["google"]["configured"] == 1 and rows["google"]["daily_budget"] == engines.DAILY_BUDGET
    b = rows["brave_api"]
    assert (b["tier"], b["configured"], b["daily_budget"], b["monthly_budget"], b["config_note"]) == (1, 1, engines.BRAVE_DAILY_BUDGET, 0, None)
    t, s = rows["tavily"], rows["serper"]
    assert t["tier"] == 1 and t["configured"] == 0 and "LOCAL_TAVILY_API_KEY" in t["config_note"] and t["monthly_budget"] == engines.TAVILY_MONTHLY_BUDGET
    assert s["tier"] == 2 and s["configured"] == 0 and "LOCAL_SERPER_API_KEY" in s["config_note"] and s["monthly_budget"] == 400
    assert all(r["declared_at"] is not None and r["requests"] == 0 for r in rows.values())
    assert not any("cle-secrete" in str(v) for r in rows.values() for v in r.values())        # jamais une clé en base
    assert pool.names == ["google", "bing", "brave_api"]                                      # la cascade ne change pas : sans clé, pas de fournisseur

    # idempotent : redéclarer ne touche ni aux compteurs, ni au cooldown, ni à `enabled` ; une clé ajoutée passe la ligne en « configuré »
    db.execute(conn, """UPDATE local_engine_health SET window_requests=7, window_start=UTC_TIMESTAMP(), month_requests=9, requests=7,
                        cooldown_until=UTC_TIMESTAMP() + INTERVAL 1 HOUR WHERE engine='brave_api'""")
    conn.commit()
    engines.SearchPool(conn, "google,bing", "http://searx.test", api_key="cle-secrete-brave", tavily_key="cle-tavily", serper_key="")
    b = health(conn, "brave_api")
    assert (b["window_requests"], b["month_requests"], b["requests"], b["enabled"]) == (7, 9, 7, 1) and b["cooldown_until"] is not None
    assert health(conn, "tavily")["configured"] == 1 and health(conn, "tavily")["config_note"] is None
    assert db.fetch_one(conn, "SELECT COUNT(*) AS n FROM local_engine_health")["n"] == 5


def test_fournisseur_sans_cle_ni_ok_ni_en_panne_pour_la_surveillance(env, conn, monkeypatch):
    from worker.local import health as health_mod
    engines.declare_providers(conn)                                                            # contrôle horaire : API déclarées sans pool ni SearXNG
    assert {r["engine"] for r in db.fetch_all(conn, "SELECT engine FROM local_engine_health WHERE configured=0")} == {"brave_api", "tavily", "serper"}
    assert list(health_mod.engines_status(conn)) == [] and health_mod.degradations(conn) == []
    assert engines.next_available_at(conn) is None                                            # un fournisseur sans clé ne « redevient » jamais disponible
    db.execute(conn, "INSERT INTO local_engine_health (engine, cooldown_until, consecutive_failures) VALUES ('google', UTC_TIMESTAMP() + INTERVAL 1 HOUR, 3)")
    conn.commit()
    assert [p["code"] for p in health_mod.degradations(conn)] == ["ALL_ENGINES_DOWN"]        # les lignes sans clé ne masquent pas une panne réelle


def test_paliers_identiques_web_et_worker():
    from pathlib import Path
    ts = (Path(__file__).resolve().parents[3] / "web/lib/local.ts").read_text(encoding="utf-8")
    for p in engines.provider_catalog():
        assert f'"{p["engine"]}": {p["tier"]}' in ts
    assert '0: "Gratuit", 1: "Gratuit à quota", 2: "Payant"' in ts
