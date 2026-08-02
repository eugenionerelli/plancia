"""Raccolta dati: legge il lavoro che è già successo e lo mette in tabella.

Nessuna fonte viene modificata. I transcript di Claude Code, i file di memoria,
le skill e i repo sono di sola lettura: Plancia li osserva e basta.
"""

import glob
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, store

# Oltre questa soglia una riga è quasi sempre un tool_result enorme: leggerla
# con json.loads costa più di quello che vale. Se ne ricava il minimo a byte.
MAX_PARSE = 256 * 1024
SCRATCH_RE = re.compile(r"^(/private/)?(tmp|var)(/|$)|^/var/folders/")


def log(msg, progress=None):
    if progress:
        progress(msg)


# --------------------------------------------------------------------------
# percorsi
# --------------------------------------------------------------------------

def drive_root():
    hits = sorted(glob.glob(str(config.HOME / "Library/CloudStorage/GoogleDrive-*/Il mio Drive")))
    return hits[0] if hits else None


def expand(path: str) -> str:
    if path.startswith("DRIVE/"):
        root = drive_root()
        return os.path.join(root, path[6:]) if root else ""
    return os.path.normpath(os.path.expanduser(path))


def iso(ts) -> str:
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(ts or "")


def to_utc(ts: str) -> str:
    """Tutto in UTC con la Z finale.

    git scrive le date con il fuso locale (+02:00), GitHub con la Z. Se restano
    mescolate, il confronto fra due timestamp fatto come stringa dà l'ordine
    sbagliato e un commit di dieci minuti fa risulta nel futuro.
    """
    ts = (ts or "").strip()
    if not ts:
        return ""
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ts
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_frontmatter(text: str):
    """Frontmatter YAML semplice: chiave: valore e un livello di annidamento."""
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            raw = text[3:end]
            body = text[end + 4:].lstrip("\n")
            section = None
            for line in raw.splitlines():
                if not line.strip() or line.strip().startswith("#"):
                    continue
                indented = line.startswith((" ", "\t"))
                key, _, val = line.strip().partition(":")
                key, val = key.strip(), val.strip()
                if len(val) > 1 and val[0] == val[-1] and val[0] in "'\"":
                    val = val[1:-1].replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")
                if indented and section:
                    meta.setdefault(section, {})[key] = val
                elif val:
                    meta[key] = val
                    section = None
                else:
                    section = key
    return meta, body


# --------------------------------------------------------------------------
# 1. seed: identità dei progetti
# --------------------------------------------------------------------------

def load_seed() -> dict:
    """La mappa dei progetti dell'utente sta in ~/.plancia/seed.json.

    Quella nel repo è solo un esempio: così il progetto si può pubblicare senza
    portarsi dietro i progetti di chi lo ha scritto.
    """
    for path in (config.USER_SEED, config.SEED_FILE):
        try:
            return json.loads(path.read_text("utf-8"))
        except Exception:
            continue
    return {"projects": [], "method_memories": []}


def sync_seed(conn, progress=None) -> dict:
    seed = load_seed()
    keywords = {}
    for spec in seed.get("projects", []):
        pid = store.upsert_project(
            conn,
            spec["key"],
            spec["name"],
            kind=spec.get("kind", "progetto"),
            auto=0,
            priority=spec.get("priority", 2),
            pinned=spec.get("pinned", 0),
            status=spec.get("status", "attivo"),
        )
        for kind, values in (spec.get("links") or {}).items():
            for value in values:
                store.link_project(conn, pid, kind, expand(value) if kind == "path" else value)
        if spec.get("keywords"):
            keywords[pid] = spec["keywords"]
    conn.commit()
    log(f"progetti di riferimento: {len(seed.get('projects', []))}", progress)
    return keywords


def resolve_path_project(conn, path: str):
    """Il progetto che possiede questo percorso, o il più vicino sopra di esso."""
    if not path:
        return None
    path = os.path.normpath(path)
    rows = conn.execute(
        "SELECT project_id, value FROM project_links WHERE kind='path' "
        "ORDER BY length(value) DESC"
    ).fetchall()
    for row in rows:
        base = row["value"]
        if base and (path == base or path.startswith(base + os.sep)):
            return row["project_id"]
    return None


def infer_project_by_keywords(text: str, keywords: dict):
    if not text:
        return None
    low = text.lower()
    hits = []
    for pid, words in keywords.items():
        score = sum(1 for w in words if w in low)
        if score:
            hits.append((score, pid))
    if not hits:
        return None
    hits.sort(reverse=True)
    if len(hits) > 1 and hits[0][0] == hits[1][0]:
        return None  # ambiguo: meglio nessuna attribuzione che una sbagliata
    return hits[0][1]


# --------------------------------------------------------------------------
# 2. memoria di Claude
# --------------------------------------------------------------------------

def sync_memory(conn, progress=None) -> int:
    seed = load_seed()
    methods = set(seed.get("method_memories", []))
    count = 0
    for md in sorted(config.CLAUDE_PROJECTS.glob("*/memory/*.md")):
        if md.name == "MEMORY.md":
            continue
        try:
            text = md.read_text("utf-8", errors="replace")
        except OSError:
            continue
        meta, body = read_frontmatter(text)
        name = meta.get("name") or md.stem
        mtype = (meta.get("metadata") or {}).get("type", "") if isinstance(meta.get("metadata"), dict) else ""
        links = sorted(set(re.findall(r"\[\[([^\]]+)\]\]", body)))
        pid_row = store.find_project_by_link(conn, "memory", name)
        pid = pid_row["id"] if pid_row else None
        if pid is None and mtype == "project" and name not in methods:
            pid = store.upsert_project(conn, name, name.replace("-", " ").capitalize(),
                                       kind="progetto")
            store.link_project(conn, pid, "memory", name)
        mtime = iso(md.stat().st_mtime)
        conn.execute(
            "INSERT INTO knowledge(name, path, scope, description, type, body, links, "
            "project_id, updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET name=excluded.name, description=excluded.description, "
            "type=excluded.type, body=excluded.body, links=excluded.links, "
            "project_id=COALESCE(excluded.project_id, knowledge.project_id), "
            "updated_at=excluded.updated_at",
            (name, str(md), md.parent.parent.name, meta.get("description", ""), mtype,
             body, json.dumps(links), pid, mtime),
        )
        if pid:
            # il riassunto del progetto sono le parole dell'utente, non le mie
            conn.execute(
                "UPDATE projects SET summary=? WHERE id=? AND (summary='' OR summary IS NULL)",
                (meta.get("description", ""), pid),
            )
            store.touch_project(conn, pid, mtime)
        store.add_event(conn, mtime, "memoria", f"memoria aggiornata: {name}",
                        meta.get("description", ""), pid, name, "memory",
                        dedup=f"memoria:{name}:{mtime}")
        count += 1
    conn.commit()
    log(f"memoria: {count} file", progress)
    return count


# --------------------------------------------------------------------------
# 3. capacità: skill, plugin, routine
# --------------------------------------------------------------------------

def sync_capabilities(conn, progress=None) -> int:
    found = 0
    for skill in sorted(config.CLAUDE_SKILLS.glob("*/SKILL.md")):
        meta, _ = read_frontmatter(skill.read_text("utf-8", errors="replace")[:4000])
        name = meta.get("name") or skill.parent.name
        conn.execute(
            "INSERT INTO capabilities(name, kind, description, path, meta, updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET name=excluded.name, "
            "description=excluded.description, updated_at=excluded.updated_at",
            (name, "skill", meta.get("description", "")[:600], str(skill), "{}",
             iso(skill.stat().st_mtime)),
        )
        row = store.find_project_by_link(conn, "skill", name)
        if row:
            store.touch_project(conn, row["id"], iso(skill.stat().st_mtime))
        found += 1

    for routine in sorted(config.CLAUDE_ROUTINES.glob("*/SKILL.md")):
        meta, body = read_frontmatter(routine.read_text("utf-8", errors="replace")[:4000])
        conn.execute(
            "INSERT INTO capabilities(name, kind, description, path, meta, updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET name=excluded.name, "
            "description=excluded.description, updated_at=excluded.updated_at",
            (meta.get("name") or routine.parent.name, "routine",
             meta.get("description", "")[:600], str(routine), "{}",
             iso(routine.stat().st_mtime)),
        )
        found += 1

    installed = config.CLAUDE_PLUGINS / "installed_plugins.json"
    if installed.exists():
        try:
            data = json.loads(installed.read_text("utf-8"))
            for name, entries in (data.get("plugins") or {}).items():
                entry = entries[0] if isinstance(entries, list) and entries else {}
                conn.execute(
                    "INSERT INTO capabilities(name, kind, description, path, meta, updated_at) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
                    "description=excluded.description, meta=excluded.meta, "
                    "updated_at=excluded.updated_at",
                    (name, "plugin", f"versione {entry.get('version', '?')}",
                     entry.get("installPath") or f"plugin:{name}", json.dumps(entry),
                     entry.get("lastUpdated", "")),
                )
                found += 1
        except Exception:
            pass
    conn.commit()
    log(f"capacità: {found}", progress)
    return found


# --------------------------------------------------------------------------
# 4. sessioni di Claude Code (incrementale, byte per byte)
# --------------------------------------------------------------------------

def progetto_per_cartella(conn, cwd: str, keywords: dict, testo: str = ""):
    """A quale progetto appartiene una sessione aperta da questa cartella.

    Vale per Claude Code e per Codex: cambia il formato del transcript, non il
    significato di una cartella di lavoro.
    """
    if not cwd:
        return None
    normale = os.path.normpath(cwd)
    if normale == os.path.normpath(str(config.DATA_DIR)):
        # è Plancia che ha chiamato un agente per il riepilogo, non lavoro suo
        return store.upsert_project(conn, "plancia-interno", "Plancia (chiamate interne)",
                                    kind="infra", hidden=1)
    if SCRATCH_RE.match(cwd):
        return store.upsert_project(conn, "temporanee", "Sessioni temporanee",
                                    kind="infra", hidden=1)

    # Certe cartelle non identificano niente. La home e la radice del Drive sono
    # posti da cui si lavora a tutto; Codex invece apre una cartella nuova per
    # ogni conversazione sotto ~/Documents/Codex, col nome preso dalla domanda:
    # sono nomi di chat, non di progetti. In tutti questi casi il progetto si
    # indovina dal testo.
    radici_generiche = {os.path.normpath(str(config.HOME))}
    drive = drive_root()
    if drive:
        radici_generiche.add(os.path.normpath(drive))
    effimere = [os.path.normpath(str(config.HOME / "Documents/Codex"))]
    effimere += [expand(r) for r in config.load_config().get("cartelle_effimere", [])]
    generica = normale in radici_generiche or any(
        normale == e or normale.startswith(e + os.sep) for e in effimere if e)
    if generica:
        trovato = infer_project_by_keywords(testo, keywords)
        return trovato or store.upsert_project(conn, "drive-workspace",
                                               "Senza progetto", kind="infra")

    trovato = resolve_path_project(conn, cwd)
    if trovato is None:
        base = os.path.basename(cwd.rstrip("/")) or cwd
        trovato = store.upsert_project(conn, base, base.replace("-", " "), kind="progetto")
        store.link_project(conn, trovato, "path", normale)
    return trovato


def _bytes_field(raw: bytes, key: bytes, limit: int = 400):
    """Estrae "key":"valore" senza parsare tutta la riga."""
    idx = raw.find(key)
    if idx == -1:
        return None
    start = idx + len(key)
    end = raw.find(b'"', start)
    if end == -1 or end - start > limit:
        return None
    try:
        return raw[start:end].decode("utf-8", "replace")
    except Exception:
        return None


def _is_real_prompt(text: str) -> bool:
    if not text or len(text.strip()) < 3:
        return False
    stripped = text.lstrip()
    if stripped.startswith(("<", "Caveat:", "[Request interrupted", "API Error")):
        return False
    if stripped.startswith("/") and len(stripped.split()) <= 2:
        return False  # comando slash secco, non racconta niente
    return True


def scan_session_file(path: Path, start_offset: int) -> dict:
    """Legge solo i byte nuovi del transcript e ne ricava le statistiche."""
    acc = {
        "offset": start_offset, "cwd": None, "branch": None, "title": None,
        "first_prompt": None, "queued_prompt": None, "ts_min": None, "ts_max": None,
        "n_user": 0, "n_assistant": 0, "n_tools": 0, "models": set(),
        "tools": {}, "in_tokens": 0, "out_tokens": 0,
    }
    with open(path, "rb") as fh:
        if start_offset:
            fh.seek(start_offset)
        for raw in fh:
            acc["offset"] += len(raw)
            if len(raw) < 3:
                continue
            # Il tipo va cercato per intero: dentro message.content ci sono altri
            # "type" (text, tool_use, tool_result) che verrebbero prima.
            if b'"type":"assistant"' in raw:
                rtype = "assistant"
            elif b'"type":"user"' in raw:
                rtype = "user"
            elif b'"type":"custom-title"' in raw:
                rtype = "custom-title"
            elif b'"type":"queue-operation"' in raw:
                rtype = "queue-operation"
            else:
                continue  # attachment, snapshot, riassunti: non servono

            if len(raw) > MAX_PARSE:
                # quasi sempre un tool_result enorme: si prende solo il minimo
                if rtype == "assistant":
                    acc["n_assistant"] += 1
                    model = _bytes_field(raw, b'"model":"', 60)
                    if model:
                        acc["models"].add(model)
                continue

            try:
                rec = json.loads(raw)
            except Exception:
                continue

            rtype = rec.get("type")
            if rtype == "custom-title":
                acc["title"] = rec.get("customTitle") or acc["title"]
                continue
            if rtype == "queue-operation":
                if rec.get("operation") == "enqueue" and not acc["queued_prompt"]:
                    text = rec.get("content") or ""
                    if _is_real_prompt(text):
                        acc["queued_prompt"] = text.strip()
                continue

            ts = rec.get("timestamp")
            if ts:
                if acc["ts_min"] is None or ts < acc["ts_min"]:
                    acc["ts_min"] = ts
                if acc["ts_max"] is None or ts > acc["ts_max"]:
                    acc["ts_max"] = ts
            acc["cwd"] = rec.get("cwd") or acc["cwd"]
            acc["branch"] = rec.get("gitBranch") or acc["branch"]

            msg = rec.get("message") or {}
            if rtype == "user":
                content = msg.get("content")
                if isinstance(content, str):
                    # una content list è un tool_result: non è una cosa che ha
                    # scritto lui, e non va contata come scambio
                    acc["n_user"] += 1
                    if not acc["first_prompt"] and _is_real_prompt(content):
                        acc["first_prompt"] = content.strip()
            elif rtype == "assistant":
                acc["n_assistant"] += 1
                if msg.get("model"):
                    acc["models"].add(msg["model"])
                usage = msg.get("usage") or {}
                acc["in_tokens"] += (usage.get("input_tokens") or 0) + \
                    (usage.get("cache_read_input_tokens") or 0) + \
                    (usage.get("cache_creation_input_tokens") or 0)
                acc["out_tokens"] += usage.get("output_tokens") or 0
                for item in msg.get("content") or []:
                    if isinstance(item, dict) and item.get("type") == "tool_use":
                        acc["n_tools"] += 1
                        name = item.get("name", "?")
                        acc["tools"][name] = acc["tools"].get(name, 0) + 1
    return acc


def sync_sessions(conn, keywords, progress=None, full=False) -> int:
    files = sorted(config.CLAUDE_PROJECTS.glob("*/*.jsonl"))
    updated = 0
    for i, path in enumerate(files):
        sid = path.stem
        try:
            size = path.stat().st_size
            mtime = path.stat().st_mtime
        except OSError:
            continue
        row = conn.execute(
            "SELECT id, bytes_scanned, file_size, models, tools, title, first_prompt, "
            "started_at, n_user, n_assistant, n_tools, in_tokens, out_tokens, project_id "
            "FROM sessions WHERE session_id=?", (sid,)
        ).fetchone()
        offset = 0 if (full or row is None) else (row["bytes_scanned"] or 0)
        if offset > size:
            offset = 0  # file ricreato o troncato: si riparte da capo
        elif row is not None and not full and size <= offset:
            continue
        log(f"sessioni {i + 1}/{len(files)} · {sid[:8]} ({size // 1024} KB)", progress)
        try:
            acc = scan_session_file(path, offset)
        except OSError:
            continue

        # Se si è ripartiti da zero i totali di prima non vanno sommati, o una
        # rilettura completa raddoppia scambi, tool e token.
        old = (lambda col: row[col] if row else 0) if offset else (lambda col: 0)
        keep = (lambda col: row[col] if row else None) if offset else (lambda col: None)

        prev_models = set(store.jloads(row["models"], []) if (row and offset) else [])
        prev_tools = store.jloads(row["tools"], {}) if (row and offset) else {}
        for name, n in acc["tools"].items():
            prev_tools[name] = prev_tools.get(name, 0) + n
        models = sorted(prev_models | acc["models"])
        title = acc["title"] or keep("title")
        first_prompt = keep("first_prompt") or acc["first_prompt"] or acc["queued_prompt"]
        started = keep("started_at") or acc["ts_min"] or iso(mtime)
        ended = acc["ts_max"] or iso(mtime)
        n_user = old("n_user") + acc["n_user"]
        n_assistant = old("n_assistant") + acc["n_assistant"]
        n_tools = old("n_tools") + acc["n_tools"]
        in_tok = old("in_tokens") + acc["in_tokens"]
        out_tok = old("out_tokens") + acc["out_tokens"]
        cwd = acc["cwd"]

        pid = row["project_id"] if row else None
        if cwd:
            pid = progetto_per_cartella(conn, cwd, keywords,
                                        f"{title or ''} {first_prompt or ''}")

        conn.execute(
            "INSERT INTO sessions(session_id, project_id, file, bytes_scanned, file_size, "
            "cwd, git_branch, title, first_prompt, started_at, ended_at, n_user, n_assistant, "
            "n_tools, models, tools, in_tokens, out_tokens, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(session_id) DO UPDATE SET project_id=excluded.project_id, "
            "bytes_scanned=excluded.bytes_scanned, file_size=excluded.file_size, "
            "cwd=COALESCE(excluded.cwd, sessions.cwd), git_branch=COALESCE(excluded.git_branch, sessions.git_branch), "
            "title=excluded.title, first_prompt=excluded.first_prompt, ended_at=excluded.ended_at, "
            "n_user=excluded.n_user, n_assistant=excluded.n_assistant, n_tools=excluded.n_tools, "
            "models=excluded.models, tools=excluded.tools, in_tokens=excluded.in_tokens, "
            "out_tokens=excluded.out_tokens, updated_at=excluded.updated_at",
            (sid, pid, str(path), acc["offset"], size, cwd, acc["branch"], title,
             (first_prompt or "")[:2000], started, ended, n_user, n_assistant, n_tools,
             json.dumps(models), json.dumps(prev_tools), in_tok, out_tok, store.now()),
        )
        label = title or (first_prompt or "sessione")[:90]
        store.add_event(conn, started, "sessione", label,
                        f"{n_user} messaggi · {n_tools} tool", pid, sid, "claude",
                        dedup=f"sessione:{sid}")
        store.touch_project(conn, pid, ended)
        updated += 1
        if updated % 5 == 0:
            conn.commit()
    reassign_generic(conn, keywords)
    conn.commit()
    log(f"sessioni aggiornate: {updated}", progress)
    return updated


def reassign_generic(conn, keywords) -> int:
    """Ripassa le sessioni finite nel contenitore generico: le parole chiave
    migliorano nel tempo, e queste devono poter migrare al progetto giusto."""
    row = conn.execute("SELECT id FROM projects WHERE key='drive-workspace'").fetchone()
    if not row:
        return 0
    moved = 0
    for s in conn.execute(
            "SELECT id, title, first_prompt FROM sessions WHERE project_id=?",
            (row["id"],)).fetchall():
        pid = infer_project_by_keywords(f"{s['title'] or ''} {s['first_prompt'] or ''}", keywords)
        if pid and pid != row["id"]:
            conn.execute("UPDATE sessions SET project_id=? WHERE id=?", (pid, s["id"]))
            conn.execute("UPDATE events SET project_id=? WHERE ref=(SELECT session_id FROM sessions WHERE id=?)",
                         (pid, s["id"]))
            moved += 1
    return moved


# --------------------------------------------------------------------------
# 5. repo GitHub e git locale
# --------------------------------------------------------------------------

def run(cmd, timeout=30, cwd=None):
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return res.stdout.strip() if res.returncode == 0 else None
    except Exception:
        return None


def sync_repos(conn, progress=None) -> int:
    cfg = config.load_config()
    if not cfg.get("gh_enabled", True):
        return 0
    out = run(["gh", "repo", "list", "--limit", "60", "--json",
               "name,description,visibility,url,pushedAt"], timeout=40)
    if not out:
        log("gh non disponibile: salto i repo", progress)
        return 0
    try:
        repos = json.loads(out)
    except Exception:
        return 0
    owner = run(["gh", "api", "user", "--jq", ".login"], timeout=20) or cfg.get("gh_user", "")
    if owner and owner != cfg.get("gh_user"):
        cfg["gh_user"] = owner
        config.save_config(cfg)

    cutoff = (datetime.now(timezone.utc) - timedelta(days=180)).strftime("%Y-%m-%dT%H:%M:%SZ")
    fresh = 0
    for repo in repos:
        name = repo["name"]
        prow = store.find_project_by_link(conn, "repo", name)
        pid = prow["id"] if prow else None
        old = conn.execute("SELECT pushed_at FROM repos WHERE name=?", (name,)).fetchone()
        conn.execute(
            "INSERT INTO repos(name, description, visibility, url, pushed_at, project_id, updated_at) "
            "VALUES(?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET description=excluded.description, "
            "visibility=excluded.visibility, url=excluded.url, pushed_at=excluded.pushed_at, "
            "project_id=COALESCE(excluded.project_id, repos.project_id), updated_at=excluded.updated_at",
            (name, repo.get("description") or "", repo.get("visibility", ""), repo.get("url", ""),
             to_utc(repo.get("pushedAt", "")), pid, store.now()),
        )
        if pid:
            store.touch_project(conn, pid, to_utc(repo.get("pushedAt", "")))
        # Solo i commit presi da GitHub hanno una url: quelli letti da git in
        # locale sono uno per cartella e non bastano a dire "già scaricato".
        known = conn.execute(
            "SELECT COUNT(*) FROM commits WHERE repo=? AND url<>''", (name,)).fetchone()[0]
        changed = old is None or old["pushed_at"] != to_utc(repo.get("pushedAt", "")) or known == 0
        if owner and changed and (repo.get("pushedAt") or "") > cutoff and fresh < 12:
            fresh += 1
            log(f"commit di {name}", progress)
            data = run(["gh", "api", f"repos/{owner}/{name}/commits?per_page=20"], timeout=30)
            try:
                commits = json.loads(data) if data else []
            except Exception:
                commits = []
            for commit in commits if isinstance(commits, list) else []:
                sha = commit.get("sha")
                info = (commit.get("commit") or {})
                msg = (info.get("message") or "").split("\n")[0]
                date = to_utc(((info.get("author") or {}).get("date")) or "")
                if not sha:
                    continue
                conn.execute(
                    "INSERT INTO commits(repo, sha, message, date, url) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(sha) DO NOTHING",
                    (name, sha, msg, date, commit.get("html_url", "")),
                )
                store.add_event(conn, date, "commit", msg, name, pid, sha, "github",
                                dedup=f"commit:{sha}")
    conn.commit()
    log(f"repo: {len(repos)}", progress)
    return len(repos)


def sync_local_git(conn, progress=None) -> int:
    cfg = config.load_config()
    roots = [expand(r) for r in cfg.get("code_roots", [])]
    drive = drive_root()
    if drive:
        roots.append(drive)
    seen = 0
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        try:
            entries = sorted(os.scandir(root), key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if not os.path.isdir(os.path.join(entry.path, ".git")):
                continue
            head = run(["git", "-C", entry.path, "log", "-1", "--format=%H\x1f%cI\x1f%s"], timeout=12)
            branch = run(["git", "-C", entry.path, "branch", "--show-current"], timeout=12)
            dirty = run(["git", "-C", entry.path, "status", "--porcelain"], timeout=20)
            pid = resolve_path_project(conn, entry.path)
            if pid is None:
                pid = store.upsert_project(conn, entry.name, entry.name.replace("-", " "),
                                           kind="progetto")
                store.link_project(conn, pid, "path", entry.path)
            conn.execute(
                "INSERT INTO repos(name, local_path, branch, dirty, project_id, updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET local_path=excluded.local_path, "
                "branch=excluded.branch, dirty=excluded.dirty, "
                "project_id=COALESCE(repos.project_id, excluded.project_id)",
                (entry.name, entry.path, branch or "", len((dirty or "").splitlines()),
                 pid, store.now()),
            )
            if head:
                sha, date, msg = (head.split("\x1f") + ["", "", ""])[:3]
                date = to_utc(date)
                conn.execute(
                    "INSERT INTO commits(repo, sha, message, date, url) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(sha) DO NOTHING", (entry.name, sha, msg, date, ""))
                store.touch_project(conn, pid, date)
            seen += 1
    conn.commit()
    log(f"cartelle git locali: {seen}", progress)
    return seen


# --------------------------------------------------------------------------
# 6. coda degli hook (sessioni aperte in tempo reale)
# --------------------------------------------------------------------------

def drain_queue(conn, progress=None) -> int:
    path = config.QUEUE_FILE
    if not path.exists():
        return 0
    try:
        lines = path.read_text("utf-8", errors="replace").splitlines()
        path.write_text("", "utf-8")
    except OSError:
        return 0
    n = 0
    for line in lines:
        try:
            rec = json.loads(line)
        except Exception:
            continue
        ts = rec.get("ts") or store.now()
        event = rec.get("event", "hook")
        cwd = rec.get("cwd") or ""
        sid = rec.get("session_id") or ""
        pid = resolve_path_project(conn, cwd) if cwd else None
        titles = {"SessionStart": "sessione aperta", "SessionEnd": "sessione chiusa"}
        store.add_event(conn, ts, "hook", titles.get(event, event),
                        os.path.basename(cwd.rstrip("/")), pid, sid, "hook",
                        dedup=f"hook:{event}:{sid}:{ts}")
        if event == "SessionStart":
            store.set_meta(conn, "live_session", sid)
            store.set_meta(conn, "live_since", ts)
        n += 1
    conn.commit()
    log(f"eventi hook: {n}", progress)
    return n


# --------------------------------------------------------------------------
# orchestrazione
# --------------------------------------------------------------------------

def sync(full=False, progress=None, skip_git=False) -> dict:
    conn = store.connect()
    store.init_db(conn)
    started = store.now()
    result = {}
    keywords = sync_seed(conn, progress)
    result["memoria"] = sync_memory(conn, progress)
    result["capacita"] = sync_capabilities(conn, progress)
    result["hook"] = drain_queue(conn, progress)
    result["sessioni"] = sync_sessions(conn, keywords, progress, full=full)
    from . import codex
    result["codex"] = codex.sync(conn, keywords, progress, full=full)
    if not skip_git:
        result["repo"] = sync_repos(conn, progress)
        result["git_locali"] = sync_local_git(conn, progress)
    log("indice di ricerca", progress)
    store.rebuild_search(conn)
    store.set_meta(conn, "last_sync", started)
    store.set_meta(conn, "last_sync_end", store.now())
    conn.commit()
    conn.close()
    from . import briefing
    briefing.write_cache()
    return result
