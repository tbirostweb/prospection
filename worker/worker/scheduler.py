"""Planificateur du worker, exécuté SANS privilèges (remplace `cron`, qui exige root).

    python -m worker.scheduler            (lancé par entrypoint.sh, en dernier)
    python -m worker.scheduler --list     (affiche les tâches et leur planification)

Mêmes tâches et mêmes horaires que l'ancienne crontab (heure de Paris, variable TZ). Différences voulues :
  * aucun secret n'est écrit sur disque (l'ancienne crontab recopiait l'environnement en clair) ;
  * les tâches reçoivent uniquement les variables dont elles ont besoin (même liste qu'avant) ;
  * fichiers de journal créés en 0640 (umask 027), tronqués chaque nuit aux 5 000 dernières lignes.
Comme cron, chaque tâche est un processus séparé lancé à l'heure dite ; les verrous propres aux tâches évitent les doublons.
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import re
import signal
import subprocess
import sys
import time

APP_DIR = pathlib.Path(os.environ.get("APP_DIR", "/app"))
LOG_DIR = pathlib.Path(os.environ.get("LOG_DIR", str(APP_DIR / "logs")))
LOG_KEEP_LINES = 5000

# (planification cron, arguments `python`, journal) — `None` = tâche interne.
JOBS: list[tuple[str, list[str] | None, str | None]] = [
    # Boutons Telegram (⭐ 📞 🚫 ❌) : relevé toutes les 5 min, 24h/24.
    ("*/5 * * * *", ["-m", "worker.telegram_poll"], "telegram.log"),
    # Prospection locale : reprend les campagnes en attente, par étapes bornées. Sans campagne, sort aussitôt.
    ("*/15 6-22 * * *", ["-m", "worker.local.run"], "local.log"),
    # Veille des nouvelles entreprises (BODACC + SIRENE).
    ("10 7 * * *", ["-m", "worker.local.watch"], "local.log"),
    # Remise à zéro demandée depuis l'interface.
    ("* * * * *", ["-m", "worker.reset", "--pending"], "local.log"),
    # Surveillance des dégradations : alerte Telegram throttlée.
    ("5 * * * *", ["-m", "worker.local.monitor"], "local.log"),
    ("15 7 * * *", ["-m", "worker.digest", "daily"], "digest.log"),
    ("0 8 * * 1", ["-m", "worker.digest", "weekly"], "digest.log"),
    # Conservation limitée des données (purge / anonymisation).
    ("30 4 * * *", ["-m", "worker.local.retention"], "local.log"),
    # Rotation des journaux.
    ("0 4 * * *", None, None),
]

# Variables transmises aux tâches (identique à l'ancien filtre de la crontab, + allowlist Telegram et variables système).
ENV_PATTERN = re.compile(
    r"^(DATABASE_URL|SEARXNG_URL|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|TELEGRAM_ALLOWED_USER_IDS|TELEGRAM_OFFSET_STATE|APP_URL|TZ|"
    r"LOG_LEVEL|MIGRATIONS_DIR|PAGESPEED_API_KEY|LOCAL_[A-Z_]+|PATH|HOME|LANG|LC_[A-Z]+|PYTHONUNBUFFERED)$"
)

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = int(s)
            if step < 1:
                raise ValueError(f"pas invalide : {spec}")
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
        if a < lo or b > hi or a > b:
            raise ValueError(f"valeur hors limites : {spec}")
        out.update(range(a, b + 1, step))
    return out


def parse(expr: str) -> tuple[set[int], ...]:
    """Expression cron à 5 champs -> ensembles de valeurs autorisées."""
    fields = expr.split()
    if len(fields) != 5:
        raise ValueError(f"expression cron invalide : {expr}")
    sets = tuple(_field(f, lo, hi) for f, (lo, hi) in zip(fields, _RANGES))
    dow = sets[4]
    if 7 in dow:                       # 0 et 7 = dimanche
        dow = (dow - {7}) | {0}
    return sets[0], sets[1], sets[2], sets[3], dow


def matches(expr: str, when: dt.datetime) -> bool:
    minute, hour, dom, month, dow = parse(expr)
    cron_dow = (when.weekday() + 1) % 7            # cron : dimanche = 0
    fields = expr.split()
    dom_ok, dow_ok = when.day in dom, cron_dow in dow
    # Règle cron : si jour du mois ET jour de semaine sont restreints, l'un OU l'autre suffit.
    day_ok = (dom_ok or dow_ok) if fields[2] != "*" and fields[4] != "*" else (dom_ok and dow_ok)
    return when.minute in minute and when.hour in hour and when.month in month and day_ok


def job_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    src = os.environ if environ is None else environ
    return {k: v for k, v in src.items() if ENV_PATTERN.match(k)}


def trim_logs(log_dir: pathlib.Path = LOG_DIR, keep: int = LOG_KEEP_LINES) -> None:
    for f in sorted(log_dir.glob("*.log")):
        try:
            lines = f.read_bytes().splitlines(keepends=True)
            if len(lines) <= keep:
                continue
            tmp = f.with_suffix(f.suffix + ".tmp")
            tmp.write_bytes(b"".join(lines[-keep:]))
            os.chmod(tmp, 0o640)
            os.replace(tmp, f)
        except OSError as exc:
            print(f"[scheduler] rotation impossible pour {f.name} : {exc}", file=sys.stderr)


class Scheduler:
    def __init__(self, jobs=JOBS, log_dir: pathlib.Path = LOG_DIR, popen=subprocess.Popen):
        for expr, _, _ in jobs:
            parse(expr)                                # refuse une planification invalide dès le démarrage
        # Keep all schedules except retention unless deletion is explicitly authorized.
        enabled = os.environ.get("RETENTION_ENABLED") == "true"
        jobs = [job for job in jobs if enabled or job[1] != ["-m", "worker.local.retention"]]
        self.jobs, self.log_dir, self.popen = jobs, log_dir, popen
        self.children: list = []
        self.stopping = False

    def run_due(self, when: dt.datetime) -> list[list[str] | None]:
        started = []
        for expr, args, log_name in self.jobs:
            if not matches(expr, when):
                continue
            if args is None:
                trim_logs(self.log_dir)
            else:
                with open(self.log_dir / log_name, "ab") as out:
                    self.children.append(self.popen([sys.executable, *args], cwd=str(APP_DIR), env=job_env(),
                                                    stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL))
            started.append(args)
        self.reap()
        return started

    def reap(self) -> None:
        self.children = [c for c in self.children if c.poll() is None]

    def stop(self, *_):
        self.stopping = True
        for c in self.children:
            try:
                c.terminate()
            except OSError:
                pass

    def loop(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        last = dt.datetime.now().replace(second=0, microsecond=0)
        while not self.stopping:
            nxt = last + dt.timedelta(minutes=1)
            delay = (nxt - dt.datetime.now()).total_seconds()
            if delay > 0:
                time.sleep(min(delay, 5))
                self.reap()
                continue
            now = dt.datetime.now().replace(second=0, microsecond=0)
            # Rattrape au plus 2 minutes manquées (mise en veille, horloge) ; au-delà, comme cron, on les saute.
            cur = max(nxt, now - dt.timedelta(minutes=2))
            while cur <= now:
                self.run_due(cur)
                cur += dt.timedelta(minutes=1)
            last = now


def main(argv: list[str]) -> int:
    if "--list" in argv:
        for expr, args, log_name in JOBS:
            print(f"{expr:<18} {' '.join(args) if args else '(rotation des journaux)'}{f'  >> {log_name}' if log_name else ''}")
        return 0
    os.umask(0o027)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[scheduler] démarrage (uid={os.getuid()}), {len(JOBS)} tâches.", flush=True)
    Scheduler().loop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
