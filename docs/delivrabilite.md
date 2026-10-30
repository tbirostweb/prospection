# Test d'envoi

Dans Dokploy, ouvrir le terminal du conteneur `worker`, se placer dans `/app`, puis lancer :

```sh
python -m worker.local.sendtest ADRESSE
```

Remplacer `ADRESSE` par une seule adresse nue. Cette commande envoie réellement un seul mail pour une entreprise fictive, avec la configuration SMTP, les en-têtes, le `List-Unsubscribe` et la signature des vrais mails. Elle applique leur contrôle DNS bloquant ; sans DMARC, elle s'arrête avant SMTP. Elle lit uniquement `settings.local_sender` et ne modifie aucune fiche, aucun historique ni état d'envoi. Le test explicite ne dépend pas de l'activation de l'envoi automatique, de ses horaires ou de ses quotas ; il ne traite pas la file et n'interroge pas IMAP. Aucun nouvel essai automatique ; une erreur après acceptation peut laisser le résultat incertain.

1. Ouvrir mail-tester et copier l'adresse temporaire proposée.
2. Utiliser cette adresse comme seul argument de la commande ci-dessus, une seule fois.
3. Consulter le résultat sur mail-tester ; il évalue ce message, sans garantir son arrivée en boîte de réception ailleurs.

## Vérification DNS du 30 octobre 2026

Contrôles `dig` sur le résolveur local, Cloudflare et, pour DMARC, les deux serveurs autoritaires OVH. Aucune modification DNS effectuée.

| Enregistrement | Verdict | Action |
| --- | --- | --- |
| SPF TXT `birostweb.fr` | Un seul SPF : `v=spf1 include:mx.ovh.com -all` ; autorise OVH | Rien à changer si tous les envois utilisent le SMTP OVH. L'alignement du message sera confirmé par mail-tester. |
| MX | Présents : priorité 1 `mx1.mail.ovh.net.`, 5 `mx2.mail.ovh.net.`, 100 `mx3.mail.ovh.net.` | Aucune correction. |
| DKIM | CNAME et clés publiques présents pour `ovhmo-selector-1` et `ovhmo-selector-2` | Aucune correction DNS. Vérifier le sélecteur actif `s=` dans le message reçu, puis renseigner `LOCAL_DKIM_SELECTOR` avec sa valeur pour activer le contrôle de l'app. Une clé publiée ne prouve pas que le SMTP signe. |
| DMARC TXT `_dmarc.birostweb.fr` | Absent, confirmé sur `dns106.ovh.net` et `ns106.ovh.net` | Dans la zone OVH, ajouter **un seul TXT**, sous-domaine `_dmarc`, valeur exacte `v=DMARC1; p=none`. TTL : valeur par défaut. |

`p=none` permet de valider l'authentification avant de choisir une politique plus stricte. Le contrôle DNS des vrais envois bloque tant que DMARC manque ; attendre sa propagation avant le test. Ne pas ajouter un deuxième SPF.

Les sélecteurs courants `default`, `mail`, `dkim`, `s1`, `google`, `ovh` ne donnent aucune clé ; les sélecteurs OVH ont été trouvés en complément. Ne pas copier une clé DKIM privée dans le DNS : les CNAME publiés suffisent. [Documentation DKIM OVH](https://docs.ovhcloud.com/en/guides/web-cloud/domains/dns-zone-dkim).

## Google Postmaster Tools

1. Se connecter à [Postmaster Tools](https://postmaster.google.com/), ajouter le domaine d'envoi `birostweb.fr`, puis ouvrir « Valider le domaine ».
2. Copier le TXT de vérification fourni par Google. Dans OVH → zone DNS de `birostweb.fr`, ajouter ce TXT à la racine (`@` ou sous-domaine vide), sans supprimer les TXT existants, puis revenir cliquer sur « Valider ». La valeur exacte est propre au compte : elle doit venir de Google.

[Étapes officielles Google](https://support.google.com/mail/answer/9981691?hl=fr).
