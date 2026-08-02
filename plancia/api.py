"""Server locale: API REST e dashboard.

Ascolta solo su 127.0.0.1. Le scritture chiedono il token in ~/.plancia/token,
così un'altra pagina aperta nel browser non può toccare i dati.
"""

import json
import mimetypes
import re
import threading
import traceback
import urllib.parse
from pathlib import Path
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import actions, briefing, config, ingest, jarvis, recap, store, voice

SYNC_LOCK = threading.Lock()
SYNC_STATE = {"running": False, "message": "", "started": None, "result": None}


def _sync_worker(full=False):
    with SYNC_LOCK:
        SYNC_STATE.update(running=True, message="avvio", started=store.now(), result=None)
        try:
            res = ingest.sync(full=full, progress=lambda m: SYNC_STATE.update(message=m))
            SYNC_STATE.update(result=res, message="fatto")
        except Exception as exc:
            SYNC_STATE.update(message=f"errore: {exc}")
            traceback.print_exc()
        finally:
            SYNC_STATE.update(running=False)


def start_sync(full=False) -> bool:
    if SYNC_STATE["running"]:
        return False
    threading.Thread(target=_sync_worker, args=(full,), daemon=True).start()
    return True


# --------------------------------------------------------------------------
# letture aggregate
# --------------------------------------------------------------------------

def overview(conn) -> dict:
    week = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    month = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    one = lambda sql, p=(): conn.execute(sql, p).fetchone()[0]

    stats = {
        "progetti_attivi": one("SELECT COUNT(*) FROM projects WHERE status='attivo' AND hidden=0"),
        "task_aperti": one("SELECT COUNT(*) FROM tasks WHERE status IN ('aperto','in corso','bloccato')"),
        "task_scaduti": one("SELECT COUNT(*) FROM tasks WHERE status IN ('aperto','in corso','bloccato') "
                            "AND due IS NOT NULL AND due < date('now')"),
        "sessioni_totali": one(f"SELECT COUNT(*) FROM sessions s WHERE {store.visibile()}"),
        "sessioni_settimana": one(f"SELECT COUNT(*) FROM sessions s WHERE started_at > ? AND {store.visibile()}", (week,)),
        "commit_mese": one("SELECT COUNT(*) FROM commits WHERE date > ?", (month,)),
        "post_pubblicati": one("SELECT COUNT(*) FROM posts WHERE status='pubblicato'"),
        "post_in_coda": one("SELECT COUNT(*) FROM posts WHERE status IN ('idea','bozza','approvato','programmato')"),
        "memorie": one("SELECT COUNT(*) FROM knowledge"),
        "scambi": one(f"SELECT COUNT(*) FROM sessions s WHERE scambi > 0 AND {store.visibile()}"),
        "token_out_mese": one(f"SELECT COALESCE(SUM(out_tokens),0) FROM sessions s WHERE started_at > ? AND {store.visibile()}", (month,)),
    }

    projects = [dict(r) for r in conn.execute(
        "SELECT p.*, "
        "(SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id AND t.status IN ('aperto','in corso','bloccato')) AS task_aperti, "
        "(SELECT COUNT(*) FROM sessions s WHERE s.project_id=p.id) AS sessioni, "
        "(SELECT GROUP_CONCAT(r.name) FROM repos r WHERE r.project_id=p.id) AS repos "
        "FROM projects p WHERE p.hidden=0 "
        "ORDER BY p.pinned DESC, CASE p.status WHEN 'attivo' THEN 0 WHEN 'idea' THEN 1 "
        "WHEN 'in pausa' THEN 2 ELSE 3 END, p.priority ASC, p.last_activity DESC"
    ).fetchall()]

    events = [dict(r) for r in conn.execute(
        "SELECT e.*, p.name AS progetto, p.key AS project_key FROM events e "
        f"LEFT JOIN projects p ON p.id=e.project_id WHERE {store.visibile('e')} "
        "AND e.kind <> 'hook' ORDER BY e.ts DESC LIMIT 60"
    ).fetchall()]

    # attività per giorno, ultimi 30, separata per agente
    vuoto = {"claude": 0, "codex": 0, "commit": 0}
    activity = {}
    for row in conn.execute(
            f"SELECT substr(started_at,1,10) AS d, COALESCE(agent,'claude') AS a, "
            f"COUNT(*) AS n FROM sessions s WHERE started_at > ? AND {store.visibile()} "
            f"GROUP BY d, a", (month,)):
        activity.setdefault(row["d"], dict(vuoto))[row["a"]] = row["n"]
    for row in conn.execute(
            "SELECT substr(date,1,10) AS d, COUNT(*) AS n FROM commits WHERE date > ? GROUP BY d",
            (month,)):
        activity.setdefault(row["d"], dict(vuoto))["commit"] = row["n"]
    days = []
    for i in range(29, -1, -1):
        day = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
        cell = activity.get(day, dict(vuoto))
        days.append({"giorno": day, "sessioni": cell["claude"] + cell["codex"], **cell})

    agenti = [dict(r) for r in conn.execute(
        f"SELECT COALESCE(agent,'claude') AS agente, COUNT(*) AS sessioni, "
        f"COALESCE(SUM(out_tokens),0) AS token, COALESCE(SUM(n_tools),0) AS tool, "
        f"COALESCE(SUM(n_user),0) AS scambi_tuoi, MAX(started_at) AS ultimo "
        f"FROM sessions s WHERE {store.visibile()} GROUP BY agente ORDER BY token DESC"
    ).fetchall()]

    return {
        "stats": stats,
        "agenti": agenti,
        "progetti": projects,
        "task": actions.tasks_list(conn, "aperti", limit=20),
        "post": actions.posts_list(conn, limit=40),
        "eventi": events,
        "attivita": days,
        "sessioni_recenti": [dict(r) for r in conn.execute(
            "SELECT s.session_id, s.title, substr(s.first_prompt,1,180) AS prompt, "
            "s.started_at, s.ended_at, s.n_user, s.n_tools, s.out_tokens, s.cwd, "
            "p.name AS progetto, p.key AS project_key FROM sessions s "
            f"LEFT JOIN projects p ON p.id=s.project_id WHERE {store.visibile()} "
            "ORDER BY s.started_at DESC LIMIT 12"
        ).fetchall()],
        "ultimo_sync": store.get_meta(conn, "last_sync_end"),
        "sync": dict(SYNC_STATE),
    }


def project_detail(conn, ident) -> dict:
    row = store.get_project(conn, ident)
    if not row:
        return None
    pid = row["id"]
    return {
        "progetto": dict(row),
        "link": [dict(r) for r in conn.execute(
            "SELECT kind, value FROM project_links WHERE project_id=?", (pid,)).fetchall()],
        "task": actions.tasks_list(conn, "tutti", pid, limit=100),
        "post": [dict(r) for r in conn.execute(
            "SELECT * FROM posts WHERE project_id=? ORDER BY updated_at DESC", (pid,)).fetchall()],
        "sessioni": [dict(r) for r in conn.execute(
            "SELECT session_id, title, substr(first_prompt,1,200) AS prompt, started_at, "
            "n_user, n_tools, out_tokens, models FROM sessions WHERE project_id=? "
            "ORDER BY started_at DESC LIMIT 40", (pid,)).fetchall()],
        "memoria": [dict(r) for r in conn.execute(
            "SELECT id, name, description, type, updated_at FROM knowledge WHERE project_id=? "
            "ORDER BY updated_at DESC", (pid,)).fetchall()],
        "repo": [dict(r) for r in conn.execute(
            "SELECT * FROM repos WHERE project_id=?", (pid,)).fetchall()],
        "commit": [dict(r) for r in conn.execute(
            "SELECT c.* FROM commits c JOIN repos r ON r.name=c.repo WHERE r.project_id=? "
            "ORDER BY c.date DESC LIMIT 30", (pid,)).fetchall()],
        "eventi": [dict(r) for r in conn.execute(
            "SELECT * FROM events WHERE project_id=? ORDER BY ts DESC LIMIT 60", (pid,)).fetchall()],
    }


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "Plancia/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # niente rumore sul terminale

    # --- utilità ---------------------------------------------------------
    def _send(self, code, body=b"", ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, val in (extra or {}).items():
            self.send_header(key, val)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code=200):
        self._send(code, json.dumps(data, ensure_ascii=False, default=str))

    def _error(self, code, msg):
        self._json({"errore": msg}, code)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _authorised(self) -> bool:
        return self.headers.get("X-Plancia-Token") == config.get_token()

    # --- rotte -----------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path.startswith("/api/"):
                return self._api_get(path, query)
            return self._static(path)
        except BrokenPipeError:
            pass
        except Exception as exc:
            traceback.print_exc()
            self._error(500, str(exc))

    do_HEAD = do_GET

    def do_POST(self):
        self._write_request("POST")

    def do_PATCH(self):
        self._write_request("PATCH")

    def do_DELETE(self):
        self._write_request("DELETE")

    def _write_request(self, method):
        parsed = urllib.parse.urlparse(self.path)
        if not self._authorised():
            return self._error(403, "token mancante o non valido")
        try:
            self._api_write(method, parsed.path, self._body())
        except actions.BadInput as exc:
            self._error(400, str(exc))
        except BrokenPipeError:
            pass
        except Exception as exc:
            traceback.print_exc()
            self._error(500, str(exc))

    # --- API -------------------------------------------------------------
    def _api_get(self, path, query):
        first = lambda k, d=None: (query.get(k) or [d])[0]
        conn = store.connect()
        try:
            if path == "/api/overview":
                return self._json(overview(conn))
            if path == "/api/briefing":
                return self._send(200, briefing.build(conn, first("project")),
                                  "text/markdown; charset=utf-8")
            if path == "/api/projects":
                return self._json([dict(r) for r in conn.execute(
                    "SELECT * FROM projects ORDER BY pinned DESC, priority, last_activity DESC"
                ).fetchall()])
            m = re.match(r"^/api/projects/([^/]+)$", path)
            if m:
                data = project_detail(conn, urllib.parse.unquote(m.group(1)))
                return self._json(data) if data else self._error(404, "progetto inesistente")
            if path == "/api/tasks":
                return self._json(actions.tasks_list(conn, first("status"), first("project"),
                                                     int(first("limit", 200))))
            if path == "/api/posts":
                return self._json(actions.posts_list(conn, first("status"), first("platform")))
            if path == "/api/sessions":
                sql = ("SELECT s.*, p.name AS progetto, p.key AS project_key FROM sessions s "
                       "LEFT JOIN projects p ON p.id=s.project_id WHERE 1=1")
                params = []
                if first("agent"):
                    sql += " AND COALESCE(s.agent,'claude')=?"
                    params.append(first("agent"))
                if first("project"):
                    row = store.get_project(conn, first("project"))
                    sql += " AND s.project_id=?"
                    params.append(row["id"] if row else -1)
                if first("q"):
                    sql += " AND (s.first_prompt LIKE ? OR s.title LIKE ? OR s.cwd LIKE ?)"
                    params += [f"%{first('q')}%"] * 3
                sql += " ORDER BY s.started_at DESC LIMIT ?"
                params.append(int(first("limit", 200)))
                return self._json([dict(r) for r in conn.execute(sql, params).fetchall()])
            if path == "/api/events":
                sql = ("SELECT e.*, p.name AS progetto, p.key AS project_key FROM events e "
                       "LEFT JOIN projects p ON p.id=e.project_id WHERE 1=1")
                params = []
                if first("kind"):
                    sql += " AND e.kind=?"
                    params.append(first("kind"))
                sql += " ORDER BY e.ts DESC LIMIT ?"
                params.append(int(first("limit", 200)))
                return self._json([dict(r) for r in conn.execute(sql, params).fetchall()])
            if path == "/api/knowledge":
                if first("name"):
                    row = conn.execute("SELECT * FROM knowledge WHERE name=?",
                                       (first("name"),)).fetchone()
                    return self._json(dict(row)) if row else self._error(404, "non trovata")
                return self._json([dict(r) for r in conn.execute(
                    "SELECT k.id, k.name, k.description, k.type, k.updated_at, k.links, "
                    "p.name AS progetto, p.key AS project_key FROM knowledge k "
                    "LEFT JOIN projects p ON p.id=k.project_id ORDER BY k.updated_at DESC"
                ).fetchall()])
            if path == "/api/agents":
                mese = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
                totali = [dict(r) for r in conn.execute(
                    f"SELECT COALESCE(agent,'claude') AS agente, COUNT(*) AS sessioni, "
                    f"COALESCE(SUM(out_tokens),0) AS token, COALESCE(SUM(n_tools),0) AS tool, "
                    f"COALESCE(SUM(n_user),0) AS messaggi, COALESCE(SUM(scambi),0) AS scambi, "
                    f"MIN(started_at) AS primo, MAX(started_at) AS ultimo "
                    f"FROM sessions s WHERE {store.visibile()} GROUP BY agente").fetchall()]
                per_giorno = [dict(r) for r in conn.execute(
                    f"SELECT substr(started_at,1,10) AS giorno, COALESCE(agent,'claude') AS agente, "
                    f"COUNT(*) AS n FROM sessions s WHERE started_at > ? AND {store.visibile()} "
                    f"GROUP BY giorno, agente", (mese,)).fetchall()]
                per_progetto = [dict(r) for r in conn.execute(
                    f"SELECT p.name AS progetto, p.key AS chiave, "
                    f"SUM(CASE WHEN COALESCE(s.agent,'claude')='claude' THEN 1 ELSE 0 END) AS claude, "
                    f"SUM(CASE WHEN s.agent='codex' THEN 1 ELSE 0 END) AS codex "
                    f"FROM sessions s JOIN projects p ON p.id=s.project_id "
                    f"WHERE p.hidden=0 GROUP BY p.id HAVING claude+codex > 0 "
                    f"ORDER BY claude+codex DESC LIMIT 12").fetchall()]
                scambi = [dict(r) for r in conn.execute(
                    "SELECT e.ts, e.title, e.detail, p.name AS progetto FROM events e "
                    "LEFT JOIN projects p ON p.id=e.project_id WHERE e.kind='scambio' "
                    "ORDER BY e.ts DESC LIMIT 20").fetchall()]
                from . import codex as codex_mod
                return self._json({"totali": totali, "per_giorno": per_giorno,
                                   "per_progetto": per_progetto, "scambi": scambi,
                                   "codex": codex_mod.stato()})
            if path == "/api/capabilities":
                return self._json([dict(r) for r in conn.execute(
                    "SELECT * FROM capabilities ORDER BY kind, name").fetchall()])
            if path == "/api/search":
                return self._json(store.search(conn, first("q", ""), int(first("limit", 40))))
            if path == "/api/recap":
                data = recap.build(conn, first("day"), first("lang"), first("engine"))
                if first("compact"):
                    data.pop("dati", None)
                return self._json(data)
            if path == "/api/voice/status":
                return self._json(voice.stato())
            if path == "/api/status":
                return self._json({
                    "sync": dict(SYNC_STATE),
                    "ultimo_sync": store.get_meta(conn, "last_sync_end"),
                    "sessione_viva": store.get_meta(conn, "live_session"),
                    "ultima_voce": store.get_meta(conn, "ultima_voce"),
                    "ultima_voce_da": store.get_meta(conn, "ultima_voce_da"),
                })
            return self._error(404, "rotta inesistente")
        finally:
            conn.close()

    def _api_write(self, method, path, body):
        conn = store.connect()
        try:
            if path.startswith("/api/voice/") or path == "/api/recap":
                # utile a capire da dove è partita la voce quando qualcosa non torna
                store.set_meta(conn, "ultima_voce", store.now())
                store.set_meta(conn, "ultima_voce_da",
                               (self.headers.get("User-Agent") or "?")[:80])
                conn.commit()
            if path == "/api/voice/speak" and method == "POST":
                testo = (body.get("testo") or "").strip()
                if not testo:
                    raise actions.BadInput("serve un testo")
                info = voice.sintesi(testo, recap.lang_or_default(body.get("lang")),
                                     body.get("motore"))
                info["url"] = "/audio/" + Path(info["file"]).name
                if body.get("riproduci"):
                    voice.riproduci(info["file"])
                return self._json(info)
            if path == "/api/voice/stop" and method == "POST":
                voice.ferma()
                return self._json({"fermato": True})
            if path == "/api/jarvis" and method == "POST":
                testo = (body.get("testo") or "").strip()
                if not testo:
                    raise actions.BadInput("serve una frase")
                lang = recap.lang_or_default(body.get("lang"))
                esito = jarvis.esegui(testo, lang, conn)
                esito["lingua"] = lang
                esito["detto"] = testo
                if not esito.get("muto") and body.get("voce", True) and esito.get("risposta"):
                    info = voice.sintesi(esito["risposta"], lang)
                    esito["url"] = "/audio/" + Path(info["file"]).name
                    esito["file"] = info["file"]
                    esito["motore"] = info["motore"]
                return self._json(esito)
            if path == "/api/voice/ask" and method == "POST":
                domanda = (body.get("domanda") or "").strip()
                if not domanda:
                    raise actions.BadInput("serve una domanda")
                lang = recap.lang_or_default(body.get("lang"))
                risposta = recap.answer(domanda, lang, conn)
                out = {"domanda": domanda, "risposta": risposta, "lingua": lang}
                if body.get("voce", True):
                    info = voice.sintesi(risposta, lang)
                    out["url"] = "/audio/" + Path(info["file"]).name
                    out["file"] = info["file"]
                    out["motore"] = info["motore"]
                return self._json(out)
            if path == "/api/recap" and method == "POST":
                data = recap.build(conn, body.get("day"), body.get("lang"), body.get("engine"))
                if body.get("voce", True):
                    info = voice.sintesi(data["testo"], data["lingua"])
                    data["url"] = "/audio/" + Path(info["file"]).name
                    data["file"] = info["file"]
                    data["motore"] = info["motore"]
                data.pop("dati", None)
                return self._json(data)
            if path == "/api/sync" and method == "POST":
                started = start_sync(bool(body.get("full")))
                return self._json({"avviato": started, "stato": SYNC_STATE})
            if path == "/api/tasks" and method == "POST":
                return self._json(actions.task_add(
                    conn, body.get("title"), body.get("body", ""), body.get("project"),
                    body.get("priority", 2), body.get("due"), body.get("tags", ""), "dashboard"))
            m = re.match(r"^/api/tasks/(\d+)$", path)
            if m and method == "PATCH":
                return self._json(actions.task_update(conn, int(m.group(1)), **body))
            if m and method == "DELETE":
                conn.execute("DELETE FROM tasks WHERE id=?", (int(m.group(1)),))
                conn.commit()
                return self._json({"eliminato": int(m.group(1))})
            if path == "/api/posts" and method == "POST":
                return self._json(actions.post_add(
                    conn, body.get("text"), body.get("platform", "x"),
                    body.get("status", "bozza"), body.get("project"), body.get("url"),
                    body.get("source_ref", ""), body.get("scheduled_for")))
            m = re.match(r"^/api/posts/(\d+)$", path)
            if m and method == "PATCH":
                return self._json(actions.post_update(conn, int(m.group(1)), **body))
            if m and method == "DELETE":
                conn.execute("DELETE FROM posts WHERE id=?", (int(m.group(1)),))
                conn.commit()
                return self._json({"eliminato": int(m.group(1))})
            if path == "/api/projects" and method == "POST":
                return self._json(actions.project_create(
                    conn, body.get("name"), body.get("key"), body.get("kind", "progetto"),
                    body.get("summary", ""), body.get("priority", 2)))
            m = re.match(r"^/api/projects/([^/]+)$", path)
            if m and method == "PATCH":
                return self._json(actions.project_update(
                    conn, urllib.parse.unquote(m.group(1)), **body))
            if path == "/api/events" and method == "POST":
                return self._json(actions.log_event(
                    conn, body.get("title"), body.get("kind", "nota"), body.get("detail", ""),
                    body.get("project"), body.get("ref"), "dashboard"))
            return self._error(404, "rotta inesistente")
        finally:
            conn.close()

    # --- file statici ----------------------------------------------------
    def _static(self, path):
        if path in ("/", "/index.html"):
            html = (config.WEB_DIR / "index.html").read_text("utf-8")
            html = html.replace("__PLANCIA_TOKEN__", config.get_token())
            # marca css e js con la loro data: un aggiornamento non lascia in
            # giro la versione vecchia nella cache del browser
            stamp = int(max((config.WEB_DIR / n).stat().st_mtime
                            for n in ("style.css", "app.js")))
            html = html.replace("__PLANCIA_V__", str(stamp))
            return self._send(200, html, "text/html; charset=utf-8")
        m = re.match(r"^/audio/([0-9a-f]{8,32}\.wav)$", path)
        if m:
            target = voice.AUDIO_DIR / m.group(1)
            if not target.is_file():
                return self._error(404, "audio non trovato")
            return self._send(200, target.read_bytes(), "audio/wav")
        name = path.lstrip("/")
        if "/" in name or name.startswith("."):
            return self._error(404, "non trovato")
        target = config.WEB_DIR / name
        if not target.is_file():
            return self._error(404, "non trovato")
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        return self._send(200, target.read_bytes(), ctype)


def serve(port=None, open_browser=False, sync_first=True) -> None:
    cfg = config.load_config()
    port = int(port or cfg.get("port", config.DEFAULT_PORT))
    conn = store.connect()
    store.init_db(conn)
    conn.close()
    if sync_first:
        start_sync(False)

    interval = int(cfg.get("sync_interval_minutes", 15))
    if interval > 0:
        def ticker():
            import time
            while True:
                time.sleep(interval * 60)
                start_sync(False)
        threading.Thread(target=ticker, daemon=True).start()

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"Plancia in ascolto su {url}")
    print(f"Dati in {config.DB_PATH}")
    if open_browser:
        import webbrowser
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nchiuso")
        httpd.server_close()
