import Link from "next/link";
import { REPLY_KINDS } from "@/lib/local";
import { getReminders, parisDay } from "@/lib/localdb";

const dayFr = (d: any) => { const [y, m, j] = new Date(d).toISOString().slice(0, 10).split("-"); return `${j}/${m}/${y}`; };

/** « À rappeler » : rappels d'aujourd'hui et en retard (les « intéressés » en tête). N'affiche rien s'il n'y en a pas. */
export default async function RemindersPanel() {
  const rows = await getReminders();
  if (rows.length === 0) return null;
  const today = parisDay();
  return (
    <section className="panel mb-8 border-amber-300 bg-amber-50">
      <h2 className="section-title">À rappeler · {rows.length}</h2>
      <div className="row-list">{rows.map((p) => {
        const late = new Date(p.remind_at).toISOString().slice(0, 10) < today;
        return (
          <div key={p.id} className="row-item py-3 text-sm">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <Link href={`/local/${p.id}`} className="font-semibold hover:text-accent">{String(p.trade_name || p.company_name).replace(/\s+/g, " ")}</Link>
              {p.reply_kind === "interested" && <span className="chip">{REPLY_KINDS.interested}</span>}
              <span className={late ? "text-red-700" : "text-muted"}>{late ? `en retard (${dayFr(p.remind_at)})` : "aujourd'hui"}</span>
              {p.phone && <a href={`tel:${p.phone}`} className="text-accent hover:underline">☎ {p.phone}</a>}
            </div>
            {p.remind_note && <div className="text-xs text-muted">{p.remind_note}</div>}
            {p.reply_excerpt && <div className="truncate text-xs text-muted">« {p.reply_excerpt} »</div>}
          </div>);
      })}</div>
    </section>
  );
}
