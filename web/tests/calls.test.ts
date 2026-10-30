// « À appeler » et formulaire (lib/calls.ts) : argumentaire, résultats d'appel, dates.
import { test } from "node:test";
import assert from "node:assert/strict";
import { CALL_MAX_ATTEMPTS, addDays, callScript, planCallResult } from "../lib/calls";
import { channelLabel, replyTables } from "../lib/replystats";

test("addDays : calendrier, passage de mois et d'année", () => {
  assert.equal(addDays("2026-10-30", 2), "2026-11-01");
  assert.equal(addDays("2026-12-28", 7), "2027-01-04");
  assert.equal(addDays("2026-10-30", 0), "2026-10-30");
});

test("argumentaire : 3 lignes, présentation, constat prudent selon l'angle, question ouverte, jamais de prix", () => {
  for (const angle of ["refonte", "creation", "neutre"] as const) {
    const s = callScript("Plombiers", angle);
    assert.equal(s.length, 3);
    assert.match(s[0], /Théo, développeur web indépendant près de Troyes/);
    assert.match(s[2], /Est-ce que ce serait un sujet pour vous \?/);
    assert.doesNotMatch(s.join(" "), /€|euro|prix|tarif/i);
  }
  assert.match(callScript("Plombiers", "creation")[1], /pas trouvé de site.*votre entreprise.*sauf erreur/);
  assert.doesNotMatch(callScript("Architectes", "creation").join(" "), /architectes|de instituts/i);   // jamais le libellé de catégorie au pluriel
  assert.match(callScript("Plombiers", "refonte")[1], /votre site/);
  assert.doesNotMatch(callScript("Plombiers", "neutre")[1], /votre site|pas trouvé/);   // neutre : ni site existant ni absence de site
  assert.doesNotMatch(callScript(null, "creation")[1], /null|undefined/);
});

test("pas de réponse : rappel J+2, compteur, 3e essai = sortie de la liste sans rappel", () => {
  assert.deepEqual(planCallResult("no_answer", "2026-10-30", 0), { reached: false, attemptsInc: true, remind: { at: "2026-11-01", note: "Rappeler (appel 1 sans réponse)" } });
  assert.equal(planCallResult("no_answer", "2026-10-30", 1)?.remind?.at, "2026-11-01");
  const third = planCallResult("no_answer", "2026-10-30", CALL_MAX_ATTEMPTS - 1)!;
  assert.equal(third.remind, null);
  assert.equal(third.attemptsInc, true);
  assert.equal(third.reached, false);   // personne n'a répondu : pas « contactée »
});

test("rappeler, intéressé, pas intéressé : quelqu'un a répondu, fiche contactée par téléphone", () => {
  const cb = planCallResult("callback", "2026-10-30", 0, "2026-11-05")!;
  assert.deepEqual([cb.reached, cb.status, cb.remind?.at], [true, "CONTACTED", "2026-11-05"]);
  assert.equal(planCallResult("callback", "2026-10-30", 0), null);                // date obligatoire
  assert.equal(planCallResult("callback", "2026-10-30", 0, "2026-02-31"), null);  // date impossible
  assert.equal(planCallResult("callback", "2026-10-30", 0, "2026-99-99"), null);
  const yes = planCallResult("interested", "2026-10-30", 2)!;
  assert.deepEqual([yes.reached, yes.status, yes.response, yes.dealStage, yes.remind?.at], [true, "INTERESTED", "INTERESTED", "REPLIED", "2026-10-30"]);
  const no = planCallResult("not_interested", "2026-10-30", 0)!;
  assert.deepEqual([no.reached, no.status, no.dealStage, no.remind], [true, "LOST", "LOST", null]);
  assert.equal(planCallResult("n'importe quoi", "2026-10-30", 0), null);
});

test("statistiques par canal : e-mail par défaut, formulaire, téléphone", () => {
  assert.equal(channelLabel(null), channelLabel("email"));
  assert.notEqual(channelLabel("formulaire"), channelLabel("telephone"));
  const rows = [{ contact_channel: "telephone", status: "REPLIED" }, { contact_channel: "telephone", status: "CONTACTED" }, { contact_channel: "formulaire", status: "CONTACTED" }, { status: "CONTACTED" }];
  const t = replyTables(rows).channel;
  assert.deepEqual(t.map((r) => [r.name, r.contacted, r.replied]), [[channelLabel("email"), 1, 0], [channelLabel("formulaire"), 1, 0], [channelLabel("telephone"), 2, 1]]);
});
