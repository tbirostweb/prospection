// Tableau « Ce qui répond » (lib/replystats.ts) : regroupement, taux, lignes de moins de 3 contactés fondues dans « Autres ».
import { test } from "node:test";
import assert from "node:assert/strict";
import { ANGLE_LABELS, groupReplies, hasReplied, isInterested, replyTables, scoreBand, type ReplyInput } from "../lib/replystats";

const mk = (n: number, p: ReplyInput): ReplyInput[] => Array.from({ length: n }, () => ({ ...p }));

test("réponse et intérêt : worker, statut ou réponse notée à la main", () => {
  assert.equal(hasReplied({ status: "CONTACTED" }), false);
  assert.equal(hasReplied({ status: "LOST", response_status: "NO_ANSWER" }), false);
  assert.equal(hasReplied({ status: "LOST", response_status: "NOT_INTERESTED" }), true);   // « pas intéressé » est une réponse
  assert.equal(hasReplied({ status: "CONTACTED", reply_kind: "later" }), true);
  assert.equal(isInterested({ reply_kind: "interested" }), true);
  assert.equal(isInterested({ status: "WON" }), true);
  assert.equal(isInterested({ reply_kind: "later", status: "REPLIED" }), false);
});

test("tranches de score", () => {
  assert.deepEqual([95, 80, 79, 60, 40, 39, 0].map(scoreBand), ["80 et plus", "80 et plus", "60 à 79", "60 à 79", "40 à 59", "Moins de 40", "Moins de 40"]);
  assert.equal(scoreBand(null), "Sans score");
});

test("par métier : taux, intéressés et regroupement « Autres » sous 3 contactés", () => {
  const rows = [...mk(4, { activity_label: "Garages", status: "CONTACTED" }), ...mk(1, { activity_label: "Garages", status: "REPLIED", reply_kind: "interested" }),
    ...mk(3, { activity_label: "Coiffure", status: "CONTACTED" }), ...mk(1, { activity_label: "Coiffure", status: "LOST", response_status: "NOT_INTERESTED", reply_kind: "not_interested" }),
    ...mk(2, { activity_label: "Fleuristes", status: "REPLIED", reply_kind: "replied" }), ...mk(1, { activity_label: "Opticiens", status: "CONTACTED" })];
  const t = groupReplies(rows, (r) => r.activity_label ?? "");
  assert.deepEqual(t.map((r) => r.name.split(" (")[0]), ["Garages", "Coiffure", "Autres"]);
  assert.deepEqual(t[0], { name: "Garages", contacted: 5, replied: 1, interested: 1, rate: 0.2 });
  assert.deepEqual([t[1].contacted, t[1].replied, t[1].interested], [4, 1, 0]);
  assert.deepEqual([t[2].contacted, t[2].replied], [3, 2]);        // Fleuristes (2) + Opticiens (1)
});

test("par angle (draft_kind) et par score", () => {
  const rows = [...mk(3, { draft_kind: "sans_site", prospect_score: 85, status: "REPLIED" }), ...mk(3, { draft_kind: "site_refonte", prospect_score: 65, status: "CONTACTED" }),
    ...mk(3, { draft_kind: "neutre", prospect_score: 45, status: "CONTACTED" })];
  const t = replyTables(rows);
  assert.deepEqual(t.angle.map((r) => r.name), [ANGLE_LABELS.refonte, ANGLE_LABELS.creation, ANGLE_LABELS.neutre]);
  assert.equal(t.angle.find((r) => r.name === ANGLE_LABELS.creation)?.rate, 1);
  assert.deepEqual(t.score.map((r) => r.name), ["80 et plus", "60 à 79", "40 à 59"]);
});

test("aucune donnée : tableaux vides", () => {
  const t = replyTables([]);
  assert.deepEqual([t.activity, t.angle, t.score], [[], [], []]);
});
