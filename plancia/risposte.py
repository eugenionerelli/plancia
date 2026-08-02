"""Le risposte che i dati sanno già dare, senza chiamare nessun modello.

La maggior parte di quello che si chiede a voce è una domanda sull'archivio:
quanti task, cosa riprendo, quanto ho lavorato, a che punto è quel progetto. Sono
tutte una query e una frase. Farle passare da un modello costa quattro secondi e
non aggiunge niente.

Se la domanda non combacia con certezza si torna al modello: meglio lento che
sbagliato.
"""

import re
from datetime import datetime, timedelta, timezone

from . import store

# Ogni regola: parole che devono esserci, parole che la escludono, e la funzione.
# Il punteggio è quante parole chiave combaciano; sotto la soglia non si risponde.
SOGLIA = 2


def _n(testo: str) -> str:
    testo = testo.lower()
    testo = re.sub(r"[^\w\sàèéìòùáéíóúñ]", " ", testo)
    return " " + " ".join(testo.split()) + " "


def _ha(t: str, parole) -> int:
    return sum(1 for p in parole if f" {p} " in t or t.find(f" {p}") >= 0 and p.endswith("*"))


def _conta(t: str, gruppi) -> int:
    """Un punto per ogni gruppo in cui almeno una parola combacia."""
    punti = 0
    for gruppo in gruppi:
        if any(f" {p}" in t for p in gruppo):
            punti += 1
    return punti


def _elenco(items, lang):
    e = {"it": " e ", "en": " and ", "es": " y "}.get(lang, " and ")
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + e + items[-1]


def _giorni(conn, da, a=None):
    fine = a or store.now()
    return conn.execute(
        f"SELECT COUNT(*) n, COALESCE(SUM(out_tokens),0) t, "
        f"COALESCE(SUM(CASE WHEN COALESCE(agent,'claude')='codex' THEN 1 ELSE 0 END),0) x "
        f"FROM sessions s WHERE started_at >= ? AND started_at < ? AND {store.visibile()}",
        (da, fine)).fetchone()


def _inizio_giorno(scarto=0):
    local = datetime.now().astimezone()
    g = (local - timedelta(days=scarto)).replace(hour=0, minute=0, second=0, microsecond=0)
    return g.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# le regole
# --------------------------------------------------------------------------

def _task_aperti(conn, lang, t):
    righe = conn.execute(
        "SELECT t.title, p.name AS prog FROM tasks t LEFT JOIN projects p ON p.id=t.project_id "
        "WHERE t.status IN ('aperto','in corso','bloccato') "
        "ORDER BY t.priority ASC, t.due IS NULL, t.due ASC LIMIT 3").fetchall()
    n = conn.execute("SELECT COUNT(*) FROM tasks WHERE status IN "
                     "('aperto','in corso','bloccato')").fetchone()[0]
    if not n:
        return {"it": "Non hai task aperti.", "en": "You have no open tasks.",
                "es": "No tienes tareas abiertas."}.get(lang)
    titoli = _elenco([r["title"] for r in righe], lang)
    return {
        "it": f"Hai {n} task aperti. I primi: {titoli}.",
        "en": f"You have {n} open tasks. First up: {titoli}.",
        "es": f"Tienes {n} tareas abiertas. Las primeras: {titoli}.",
    }.get(lang)


def _da_dove_riparto(conn, lang, t):
    p = conn.execute(
        "SELECT name, next_action FROM projects WHERE status='attivo' AND hidden=0 "
        "AND next_action <> '' ORDER BY priority ASC, last_activity DESC LIMIT 1").fetchone()
    task = conn.execute(
        "SELECT title FROM tasks WHERE status IN ('in corso','aperto') "
        "ORDER BY CASE status WHEN 'in corso' THEN 0 ELSE 1 END, priority ASC LIMIT 1").fetchone()
    if p:
        base = {"it": f"Su {p['name']}: {p['next_action']}.",
                "en": f"On {p['name']}: {p['next_action']}.",
                "es": f"En {p['name']}: {p['next_action']}."}.get(lang)
        if task:
            coda = {"it": f" Poi c'è il task {task['title']}.",
                    "en": f" Then there is the task {task['title']}.",
                    "es": f" Luego está la tarea {task['title']}."}.get(lang)
            return base + coda
        return base
    if task:
        return {"it": f"Il primo task aperto è {task['title']}.",
                "en": f"The first open task is {task['title']}.",
                "es": f"La primera tarea abierta es {task['title']}."}.get(lang)
    return {"it": "Non c'è niente segnato da riprendere.",
            "en": "Nothing is marked as next.",
            "es": "No hay nada marcado para retomar."}.get(lang)


def _fermi(conn, lang, t):
    limite = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    righe = conn.execute(
        "SELECT p.name, p.last_activity FROM projects p WHERE p.status='attivo' "
        "AND p.hidden=0 AND p.last_activity IS NOT NULL AND p.last_activity < ? "
        "AND ((SELECT COUNT(*) FROM sessions s WHERE s.project_id=p.id) >= 3 "
        "OR p.id IN (SELECT project_id FROM project_links WHERE kind IN ('repo','memory'))) "
        "ORDER BY p.last_activity ASC LIMIT 3", (limite,)).fetchall()
    if not righe:
        return {"it": "Niente di fermo: tutti i progetti attivi hanno avuto movimento nelle ultime due settimane.",
                "en": "Nothing is stalled: every active project moved in the last two weeks.",
                "es": "Nada parado: todos los proyectos activos se han movido estas dos semanas."}.get(lang)
    nomi = _elenco([r["name"] for r in righe], lang)
    return {"it": f"Fermi da più di due settimane: {nomi}.",
            "en": f"Untouched for more than two weeks: {nomi}.",
            "es": f"Parados desde hace más de dos semanas: {nomi}."}.get(lang)


def _oggi(conn, lang, t):
    r = _giorni(conn, _inizio_giorno())
    commit = conn.execute("SELECT COUNT(*) FROM commits WHERE date >= ?",
                          (_inizio_giorno(),)).fetchone()[0]
    if not r["n"] and not commit:
        return {"it": "Oggi non risulta ancora niente.", "en": "Nothing logged today yet.",
                "es": "Hoy todavía no hay nada."}.get(lang)
    return {"it": f"Oggi {r['n']} sessioni e {commit} commit.",
            "en": f"Today, {r['n']} sessions and {commit} commits.",
            "es": f"Hoy, {r['n']} sesiones y {commit} commits."}.get(lang)


def _ieri(conn, lang, t):
    r = _giorni(conn, _inizio_giorno(1), _inizio_giorno())
    righe = conn.execute(
        f"SELECT p.name, COUNT(*) n FROM sessions s JOIN projects p ON p.id=s.project_id "
        f"WHERE s.started_at >= ? AND s.started_at < ? AND {store.visibile()} "
        f"GROUP BY p.id ORDER BY n DESC LIMIT 2",
        (_inizio_giorno(1), _inizio_giorno())).fetchall()
    if not r["n"]:
        return {"it": "Ieri non risulta niente.", "en": "Nothing logged yesterday.",
                "es": "Ayer no hay nada."}.get(lang)
    nomi = _elenco([x["name"] for x in righe], lang)
    return {"it": f"Ieri {r['n']} sessioni, soprattutto su {nomi}.",
            "en": f"Yesterday, {r['n']} sessions, mostly on {nomi}.",
            "es": f"Ayer, {r['n']} sesiones, sobre todo en {nomi}."}.get(lang)


def _settimana(conn, lang, t):
    r = _giorni(conn, _inizio_giorno(7))
    commit = conn.execute("SELECT COUNT(*) FROM commits WHERE date >= ?",
                          (_inizio_giorno(7),)).fetchone()[0]
    milioni = round((r["t"] or 0) / 1e6, 1)
    return {"it": f"Questa settimana {r['n']} sessioni, {commit} commit e {milioni} milioni di token generati.",
            "en": f"This week, {r['n']} sessions, {commit} commits and {milioni} million tokens out.",
            "es": f"Esta semana, {r['n']} sesiones, {commit} commits y {milioni} millones de tokens."}.get(lang)


def _agenti(conn, lang, t):
    righe = conn.execute(
        f"SELECT COALESCE(agent,'claude') a, COUNT(*) n, COALESCE(SUM(out_tokens),0) tok "
        f"FROM sessions s WHERE {store.visibile()} GROUP BY a").fetchall()
    per = {r["a"]: r for r in righe}
    c, x = per.get("claude"), per.get("codex")
    if not x:
        return {"it": "Codex non ha sessioni registrate.", "en": "Codex has no sessions recorded.",
                "es": "Codex no tiene sesiones registradas."}.get(lang)
    return {
        "it": f"Claude {c['n'] if c else 0} sessioni, Codex {x['n']}. "
              f"Ma i token generati sono {round((c['tok'] if c else 0)/1e6,1)} milioni contro {round(x['tok']/1e6,1)}.",
        "en": f"Claude {c['n'] if c else 0} sessions, Codex {x['n']}. "
              f"But tokens out are {round((c['tok'] if c else 0)/1e6,1)} million against {round(x['tok']/1e6,1)}.",
        "es": f"Claude {c['n'] if c else 0} sesiones, Codex {x['n']}. "
              f"Pero los tokens son {round((c['tok'] if c else 0)/1e6,1)} millones contra {round(x['tok']/1e6,1)}.",
    }.get(lang)


def _progetti(conn, lang, t):
    n = conn.execute("SELECT COUNT(*) FROM projects WHERE status='attivo' AND hidden=0").fetchone()[0]
    righe = conn.execute("SELECT name FROM projects WHERE status='attivo' AND hidden=0 "
                         "ORDER BY pinned DESC, priority ASC, last_activity DESC LIMIT 3").fetchall()
    nomi = _elenco([r["name"] for r in righe], lang)
    return {"it": f"{n} progetti attivi. In cima: {nomi}.",
            "en": f"{n} active projects. Top of the list: {nomi}.",
            "es": f"{n} proyectos activos. Los primeros: {nomi}."}.get(lang)


def _post(conn, lang, t):
    coda = conn.execute("SELECT COUNT(*) FROM posts WHERE status IN "
                        "('idea','bozza','approvato','programmato')").fetchone()[0]
    pub = conn.execute("SELECT COUNT(*) FROM posts WHERE status='pubblicato'").fetchone()[0]
    return {"it": f"{coda} post in coda e {pub} pubblicati.",
            "en": f"{coda} posts queued and {pub} published.",
            "es": f"{coda} posts en cola y {pub} publicados."}.get(lang)


REGOLE = [
    # (gruppi di sinonimi, funzione)
    ([("task", "tarea", "tareas", "cose"), ("quanti", "quante", "how many", "cuántas", "cuantas",
                                            "aperti", "open", "abiertas", "restano", "left")], _task_aperti),
    ([("riprendo", "riprendere", "riparto", "ripartire", "comincio", "inizio",
       "pick", "start", "retomar", "empiezo"), ("cosa", "che", "what", "qué", "que", "dove", "where")],
     _da_dove_riparto),
    ([("fermo", "fermi", "ferme", "idle", "stalled", "parado", "parados", "abbandonato"),
      ("cosa", "che", "what", "qué", "su", "quale")], _fermi),
    ([("oggi", "today", "hoy"), ("quanto", "cosa", "quante", "how", "what", "cuánto", "cuanto", "qué")],
     _oggi),
    ([("ieri", "yesterday", "ayer"), ("cosa", "quanto", "quante", "what", "how", "qué", "cuánto")],
     _ieri),
    ([("settimana", "week", "semana"), ("quanto", "quante", "how", "cuánto", "cuanto", "lavorato",
                                        "worked", "trabajado")], _settimana),
    ([("codex",), ("quante", "quanto", "how", "cuántas", "confronto", "contro", "versus", "vs",
                   "sessioni", "sessions", "fatto", "done")], _agenti),
    ([("progetti", "projects", "proyectos"), ("quanti", "quante", "how many", "cuántos", "attivi",
                                              "active", "activos", "elenca", "list")], _progetti),
    ([("post", "social"), ("quanti", "quante", "how many", "cuántos", "coda", "queue", "cola",
                           "pubblicati", "published")], _post),
]


def prova(conn, testo: str, lang: str):
    """La risposta locale, se la domanda è di quelle che i dati sanno già."""
    t = _n(testo)
    migliore, punteggio = None, 0
    for gruppi, fn in REGOLE:
        p = _conta(t, gruppi)
        if p > punteggio:
            migliore, punteggio = fn, p
    if migliore is None or punteggio < SOGLIA:
        return None
    try:
        return migliore(conn, lang if lang in ("it", "en", "es") else "en", t)
    except Exception:
        return None
