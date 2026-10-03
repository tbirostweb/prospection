# 🤖 Bot Telegram — guide de configuration

Ce guide te permet de recevoir sur Telegram :
- 🔥 les **alertes** de nouveaux prospects « À contacter » ;
- 📅 un **résumé quotidien** (08:00) ;
- 📊 des **statistiques hebdomadaires** (lundi 08:00) ;
- ⚠️ les **infos utiles** du projet (erreurs de collecte, etc.).

Tout arrive dans un espace privé, visible uniquement par toi. **~10 minutes.**

---

## Étape 1 — Créer le bot

1. Dans Telegram, ouvre une conversation avec **[@BotFather](https://t.me/BotFather)**.
2. Envoie `/newbot`.
3. Donne un **nom** (ex: `Prospection`) puis un **username** finissant par `bot` (ex: `theo_prospection_bot`).
4. BotFather te renvoie un **token** du type `123456789:AAE...`.
   → C'est ton `TELEGRAM_BOT_TOKEN`. **Garde-le secret.**

---

## Étape 2 — Créer un canal privé pour toi

Un **canal** (channel) est mieux qu'une simple discussion : c'est un fil propre,
dédié, où seul le bot publie. Personne d'autre n'y a accès.

1. Menu Telegram → **Nouveau canal** (New Channel).
2. Nom : ex. `Prospection – Missions`. Description : facultative.
3. Type : choisis **Canal privé** (Private Channel).
4. À l'étape "Ajouter des abonnés", tu peux **sauter** (skip) : tu es déjà seul dessus.
5. Ouvre le canal → **Administrateurs** → **Ajouter un administrateur** → cherche
   ton bot par son username → ajoute-le (laisse-lui au minimum le droit
   *Publier des messages*).

> Alternative plus rapide (moins propre) : au lieu d'un canal, tu peux juste
> **écrire un message au bot** en discussion directe. Dans ce cas, le `chat_id`
> de l'étape 3 sera ton identifiant personnel (un nombre positif).

---

## Étape 3 — Récupérer le `TELEGRAM_CHAT_ID`

**Cas canal privé (recommandé) :**
1. Publie un message quelconque dans ton canal.
2. Transfère (forward) ce message vers **[@userinfobot](https://t.me/userinfobot)**
   ou **[@getidsbot](https://t.me/getidsbot)** : il te donne l'ID du canal.
   → Il ressemble à `-1001234567890` (commence par `-100`).

**Cas discussion directe avec le bot :**
1. Envoie un message à ton bot.
2. Ouvre dans un navigateur :
   `https://api.telegram.org/bot<TON_TOKEN>/getUpdates`
3. Cherche `"chat":{"id":123456789` → ce nombre est ton `TELEGRAM_CHAT_ID`.

---

## Étape 4 — Renseigner la configuration

Dans Dokploy → onglet **Environment** (ou ton fichier `.env`) :

```
TELEGRAM_BOT_TOKEN=123456789:AAE...ton_token
TELEGRAM_CHAT_ID=-1001234567890
```

**Redéploie** pour prendre en compte les valeurs (un simple redémarrage du
conteneur ne suffit pas : les variables sont figées à sa création).

---

## Étape 5 — Tester

Dans le **terminal du conteneur `worker`** (Dokploy → conteneur worker → Terminal) :

```bash
cd /app && python -m worker.telegram_check
```

La commande vérifie tout dans l'ordre et t'affiche l'erreur **exacte** de Telegram
avec la correction à faire :

1. variables `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` présentes ;
2. jeton valide ;
3. chat joignable (bot bien admin du canal, `/start` envoyé en privé…) ;
4. **envoi d'un vrai message de test** ;
5. prospects locaux en attente de notification

> ⚠️ Après avoir ajouté ou modifié ces variables dans Dokploy, **redéploie** :
> elles ne sont lues qu'à la création du conteneur.

---

## Ce que tu reçois, et quand

| Type | Quand | Contenu |
|------|-------|---------|
| 🔥 / 🟢 Alerte prospect | dès qu'un prospect atteint **« À contacter » ou « Très bon »** (**une seule fois** par prospect ; 8 au plus par passage puis un récapitulatif) | nom, score /100, activité · ville · distance, état du site + confiance, pourquoi, problèmes objectifs, contact, fiabilité des données, lien vers la fiche + **boutons de classement** |
| 🆕 Veille | après la veille d'une campagne (chaque semaine ou chaque mois, au choix) | les entreprises nouvellement créées dans la zone et les activités de la campagne, une fois évaluées : activité, ville, date de création, état du site, score, lien |
| 📅 Résumé quotidien | tous les jours 08:00 (heure de Paris) | prospects évalués, à contacter en attente, campagnes en cours, top 5 |
| 📊 Bilan hebdo | lundi 08:00 | entonnoir, erreurs, état des moteurs de recherche |
| ⚠️ Alerte technique | quand un problème apparaît | tous les moteurs bloqués, chute du taux de sites trouvés, source indisponible… — **une seule fois** tant que le problème dure ; un budget de requêtes du jour atteint est signalé comme simple avertissement (ce n'est pas une panne) |

Ce sont des **prospects à froid** : ils n'ont rien demandé. Rien n'est jamais envoyé à leur place.

---

## Classer depuis ton téléphone

Chaque alerte arrive avec des boutons :

| Bouton | Effet |
|---|---|
| ⭐ **Bon prospect** | statut *À contacter* (l'alerte reste, sans boutons) |
| 📞 **Contacté** | statut *Contacté*, date enregistrée |
| 🚫 **Pas intéressé** | statut *Perdu* puis **choix de la raison** (pas pertinent, site finalement bon, mauvais site, chaîne, déjà client d'une agence, aucun besoin, contact impossible, autre) ; l'alerte est ensuite supprimée du chat |
| ❌ **Mauvais site** | dissocie le site (jamais reproposé pour cette entreprise), retire ses contacts et son audit, relance la recherche |

Ces décisions servent à comprendre les erreurs du score (rapport `python -m worker.local.calibration`) : aucun poids n'est modifié automatiquement.

Les taps sont relevés **toutes les 5 minutes**, 24h/24. Telegram ne permet de supprimer un message que pendant 48 h : au-delà, les boutons sont simplement retirés.

> 🔒 Seul le chat configuré dans `TELEGRAM_CHAT_ID` peut agir : un inconnu qui
> tomberait sur le bot ne peut rien modifier.
>
> Technique : on utilise le *polling* (`getUpdates`), pas un webhook — aucun
> port à ouvrir, aucune route à sortir de l'authentification de l'app.

**Changer les horaires des résumés :** dans `worker/entrypoint.sh` (la crontab y
est générée au démarrage) : `0 8 * * *` = 08:00 chaque jour ; `0 8 * * 1` = 08:00 le lundi.
