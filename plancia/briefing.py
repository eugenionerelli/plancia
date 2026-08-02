"""Il briefing: cosa deve sapere una sessione di Claude appena si apre.

Viene scritto su file a ogni sync e a ogni scrittura, così l'hook SessionStart
lo legge in un millisecondo invece di aprire il database.
"""

from datetime import datetime, timedelta, timezone

from . import config, store

PRIORITY = {1: "alta", 2: "media", 3: "bassa"}


def _ago(ts: str) -> str:
    if not ts:
        return "mai"
    try:
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
    except ValueError:
        return ts[:10]
    delta = datetime.now(timezone.utc) - when
    if delta < timedelta(minutes=90):
        return f"{int(delta.total_seconds() // 60)} min fa"
    if delta < timedelta(days=1):
        return f"{int(delta.total_seconds() // 3600)} ore fa"
    if delta.days == 1:
        return "ieri"
    if delta.days < 30:
        return f"{delta.days} giorni fa"
    return ts[:10]


def build(conn=None, project=None, limit_projects=6) -> str:
    close = False
    if conn is None:
        conn = store.connect()
        store.init_db(conn)
        close = True
    try:
        lines = []
        today = datetime.now().strftime("%d/%m/%Y")
        lines.append(f"# Plancia · {today}")

        where = "WHERE p.status='attivo' AND p.hidden=0"
        params = []
        if project:
            row = store.get_project(conn, project)
            if row:
                where = "WHERE p.id=?"
                params = [row["id"]]
        projects = conn.execute(
            f"SELECT p.*, (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id "
            f"AND t.status IN ('aperto','in corso','bloccato')) AS open_tasks "
            f"FROM projects p {where} "
            f"ORDER BY p.pinned DESC, p.priority ASC, p.last_activity DESC LIMIT ?",
            params + [limit_projects],
        ).fetchall()
        if projects:
            lines.append("\n## Progetti attivi")
            for p in projects:
                bits = [f"ultimo lavoro {_ago(p['last_activity'])}"]
                if p["open_tasks"]:
                    bits.append(f"{p['open_tasks']} task aperti")
                lines.append(f"- **{p['name']}** ({p['key']}) · {', '.join(bits)}")
                if p["next_action"]:
                    lines.append(f"  → prossimo passo: {p['next_action']}")

        tasks = conn.execute(
            "SELECT t.*, p.name AS pname FROM tasks t LEFT JOIN projects p ON p.id=t.project_id "
            "WHERE t.status IN ('in corso','aperto','bloccato') "
            "ORDER BY CASE t.status WHEN 'in corso' THEN 0 WHEN 'bloccato' THEN 1 ELSE 2 END, "
            "t.priority ASC, t.due IS NULL, t.due ASC LIMIT 10"
        ).fetchall()
        if tasks:
            lines.append("\n## Task aperti")
            for t in tasks:
                tag = f" [{t['pname']}]" if t["pname"] else ""
                due = f" · scade {t['due']}" if t["due"] else ""
                state = "" if t["status"] == "aperto" else f" ({t['status']})"
                lines.append(f"- #{t['id']} {t['title']}{tag}{state}{due}")

        posts = conn.execute(
            "SELECT id, platform, status, substr(text,1,70) AS text FROM posts "
            "WHERE status IN ('idea','bozza','approvato','programmato') "
            "ORDER BY updated_at DESC LIMIT 5"
        ).fetchall()
        if posts:
            lines.append("\n## Social in coda")
            for o in posts:
                lines.append(f"- #{o['id']} [{o['platform']}·{o['status']}] {o['text']}…")

        events = conn.execute(
            "SELECT e.ts, e.kind, e.title, p.name AS pname FROM events e "
            "LEFT JOIN projects p ON p.id=e.project_id "
            "WHERE e.kind IN ('sessione','commit','post','task') "
            f"AND {store.visibile('e')} ORDER BY e.ts DESC LIMIT 5"
        ).fetchall()
        if events:
            lines.append("\n## Ultima attività")
            for e in events:
                tag = f" [{e['pname']}]" if e["pname"] else ""
                lines.append(f"- {_ago(e['ts'])} · {e['kind']}: {(e['title'] or '')[:70]}{tag}")

        lines.append(
            "\nPlancia è l'archivio del suo lavoro con l'IA. Usa i tool `plancia_*` "
            "per leggere il contesto, aggiungere task, registrare quello che fai e i post "
            "sociali. Dashboard: http://127.0.0.1:%d" % config.load_config().get("port", 7773)
        )
        return "\n".join(lines)
    finally:
        if close:
            conn.close()


def write_cache() -> str:
    text = build()
    config.ensure_dirs()
    config.BRIEFING_FILE.write_text(text, "utf-8")
    return text
