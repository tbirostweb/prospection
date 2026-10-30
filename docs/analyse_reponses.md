# Analyse des réponses

Depuis le dépôt, lance `python3 scripts/analyse_reponses.py`. Le script lit `~/.config/prospection/rediger.env` (URL `PROSPECTION_URL`, identifiants `PROSPECTION_USER` et `PROSPECTION_PASSWORD`; les anciens noms `APP_URL`/`ORIGIN_URL`, `APP_USER` et `APP_PASSWORD` restent acceptés) et affiche uniquement des agrégats.

Le dénominateur comprend les premiers e-mails au statut `sent` ou `bounced`, ainsi que les formulaires marqués envoyés et les appels aboutis. Les e-mails `failed`, relances et réponses automatiques ignorées par le classifieur ne sont pas des envois ou réponses humaines comptés. Les ouvertures ne sont pas suivies. Les taux par métier, angle, tranche de score et canal incluent les taux de réponse et d’intérêt. Les recommandations descriptives sont limitées à trois, uniquement avec au moins 50 envois au total et deux groupes d’au moins 10 envois dans une dimension; elles ne démontrent pas de causalité.

Les angles et scores proviennent des valeurs actuelles de la fiche, sans historique de leur état à l’envoi. Le point d’accès agrégé `GET /api/local/reply-stats` est protégé par le middleware d’authentification et nécessite le redéploiement de l’application web avant d’être disponible. Aucun résultat de production n’est présumé ici.
