"""Il cantiere: un task smette di essere una nota e diventa lavoro fatto.

Prendi una riga della lavagna, scrivi come la vuoi fatta, scegli chi la fa, e
Plancia lancia l'agente giusto nella cartella giusta, ne segue l'avanzamento e
registra cosa è successo.

Due modi, e la differenza è tutta qui:

- **proposta**: l'agente può leggere e cercare, non può scrivere. Ti risponde
  con cosa farebbe. È il modo predefinito, perché lanciare un agente che
  modifica file senza averlo chiesto esplicitamente è un ottimo modo per
  rovinare una giornata.
- **esegui**: l'agente può modificare i file dentro la cartella del progetto.
  Si sceglie una volta per lancio, mai in automatico.

Il processo gira staccato: se chiudi l'app, il lavoro continua e lo stato
finisce lo stesso nel registro.
"""

import json
import os
import subprocess
import threading
import time
from pathlib import Path

from . import config, eventi, recap, store

LOG_DIR = config.DATA_DIR / "cantiere"

AGENTI = ("claude", "codex")
MODI = ("proposta", "esegui")

# Cosa può toccare l'agente nei due modi.
TOOL_LETTURA = ["Read", "Glob", "Grep", "WebSearch", "WebFetch",
                # git per intero: con i permessi per singolo sottocomando
                # `git -C ...` non passa e l'agente resta cieco
                "Bash(git:*)", "Bash(ls:*)", "Bash(rg:*)", "Bash(cat:*)",
                "mcp__plancia__plancia_briefing", "mcp__plancia__plancia_search",
                "mcp__plancia__plancia_projects", "mcp__plancia__plancia_memory",
                "mcp__plancia__plancia_sessions", "mcp__plancia__plancia_tasks"]
TOOL_SCRITTURA = TOOL_LETTURA + ["Edit", "Write", "MultiEdit", "NotebookEdit", "Bash",
                                 "mcp__plancia__plancia_task_update",
                                 "mcp__plancia__plancia_log",
                                 "mcp__plancia__plancia_project_update"]


def codex_bin() -> str:
    cfg = config.load_config()
    candidati = [cfg.get("codex_bin"),
                 "/Applications/ChatGPT.app/Contents/Resources/codex",
                 str(config.HOME / ".local/bin/codex"), "/opt/homebrew/bin/codex"]
    for c in candidati:
        if c and os.access(c, os.X_OK):
            return c
    import shutil
    return shutil.which("codex") or ""


# --------------------------------------------------------------------------
# il prompt
# --------------------------------------------------------------------------

TESTATA = {
    "proposta": (
        "Non modificare nessun file. Guarda com'è messa la cosa e rispondi con "
        "cosa faresti: i passi concreti, i file toccati, e i punti dove potresti "
        "sbagliare. Se serve una decisione che non puoi prendere tu, chiedila."),
    "esegui": (
        "Fai il lavoro. Parti da quello che c'è invece di riscrivere, e verifica "
        "che quello che hai cambiato funzioni prima di dire che è fatto."),
}

CHIUSURA = (
    "Alla fine scrivi due righe soltanto, in {lingua}, che dicano cosa hai fatto "
    "davvero e cosa resta aperto. Niente elenchi, niente markdown: quelle due "
    "righe vengono lette ad alta voce.")


def componi_prompt(conn, titolo, dettaglio="", progetto=None, istruzioni="",
                   modo="proposta", lingua="it") -> str:
    pezzi = [TESTATA.get(modo, TESTATA["proposta"]), "", f"## Il lavoro\n{titolo}"]
    if dettaglio:
        pezzi.append(dettaglio)
    if istruzioni:
        pezzi.append(f"\n## Come lo voglio fatto\n{istruzioni}")

    if progetto:
        riga = store.get_project(conn, progetto)
        if riga:
            contesto = [f"\n## Il progetto\n{riga['name']}"]
            if riga["summary"]:
                contesto.append(riga["summary"])
            if riga["next_action"]:
                contesto.append(f"Prossimo passo dichiarato: {riga['next_action']}")
            percorsi = [r["value"] for r in conn.execute(
                "SELECT value FROM project_links WHERE project_id=? AND kind='path'",
                (riga["id"],))]
            if percorsi:
                contesto.append("Cartelle: " + ", ".join(percorsi))
            memorie = [r["name"] for r in conn.execute(
                "SELECT name FROM knowledge WHERE project_id=? LIMIT 3", (riga["id"],))]
            if memorie:
                contesto.append("Memoria di riferimento: " + ", ".join(memorie) +
                                ". Leggila con plancia_memory prima di partire.")
            pezzi.append("\n".join(contesto))

    pezzi.append("\n" + CHIUSURA.format(lingua=recap.NOMI_LINGUA.get(lingua, "English")))
    return "\n".join(pezzi)


def cartella_per(conn, progetto=None) -> str:
    """Dove far lavorare l'agente.

    Un progetto può avere più cartelle collegate. Si sceglie quella che è un
    repository git, perché è lì che sta il codice; la cartella dei dati di
    Plancia si scarta sempre. Sbagliare qui non è un dettaglio: Claude Code non
    legge fuori dalla cartella in cui parte, quindi l'agente resterebbe cieco.
    """
    dati = os.path.normpath(str(config.DATA_DIR))
    if progetto:
        riga = store.get_project(conn, progetto)
        if riga:
            candidati = [r["value"] for r in conn.execute(
                "SELECT value FROM project_links WHERE project_id=? AND kind='path'",
                (riga["id"],))]
            r = conn.execute("SELECT local_path FROM repos WHERE project_id=? "
                             "AND local_path IS NOT NULL", (riga["id"],)).fetchone()
            if r:
                candidati.append(r["local_path"])
            buoni = [c for c in candidati
                     if c and os.path.isdir(c) and os.path.normpath(c) != dati]
            for c in buoni:
                if os.path.isdir(os.path.join(c, ".git")):
                    return c
            if buoni:
                return buoni[0]
    return str(config.HOME)


# --------------------------------------------------------------------------
# l'esecuzione
# --------------------------------------------------------------------------

def _comando(agente: str, modo: str, cwd: str) -> list:
    if agente == "codex":
        exe = codex_bin()
        if not exe:
            raise RuntimeError("Codex non è installato")
        sandbox = "read-only" if modo == "proposta" else "workspace-write"
        return [exe, "exec", "--cd", cwd, "--sandbox", sandbox,
                "--skip-git-repo-check", "--color", "never"]
    exe = recap.claude_bin()
    if not exe:
        raise RuntimeError("Claude Code non è installato")
    cfg = config.load_config()
    cmd = [exe, "-p", "--model", cfg.get("modello_cantiere", "sonnet"),
           "--output-format", "stream-json", "--verbose"]
    if modo == "esegui":
        cmd += ["--permission-mode", "acceptEdits", "--allowedTools"] + TOOL_SCRITTURA
    else:
        cmd += ["--allowedTools"] + TOOL_LETTURA
    return cmd


def _leggi_claude(riga: str, acc: dict):
    """Estrae dallo stream quello che serve: sessione, esito, token."""
    try:
        d = json.loads(riga)
    except Exception:
        return
    if d.get("session_id") and not acc.get("sessione"):
        acc["sessione"] = d["session_id"]
    if d.get("type") == "result":
        acc["esito"] = (d.get("result") or "").strip()
        acc["token"] = ((d.get("usage") or {}).get("output_tokens") or 0)
        acc["costo"] = d.get("total_cost_usd") or 0
        acc["errore"] = bool(d.get("is_error"))


def avvia(conn, titolo, dettaglio="", progetto=None, istruzioni="", agente="claude",
          modo="proposta", cwd=None, task_id=None, lingua="it", attendi=False) -> dict:
    """Mette in coda un lancio e lo fa partire. Torna subito con l'id."""
    agente = agente if agente in AGENTI else "claude"
    modo = modo if modo in MODI else "proposta"
    cwd = cwd or cartella_per(conn, progetto)
    prompt = componi_prompt(conn, titolo, dettaglio, progetto, istruzioni, modo, lingua)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    cur = conn.execute(
        "INSERT INTO runs(task_id, agente, modo, prompt, cwd, stato, inizio) "
        "VALUES(?,?,?,?,?,'in coda',?)",
        (task_id, agente, modo, prompt, cwd, store.now()))
    run_id = cur.lastrowid
    log = LOG_DIR / f"run-{run_id}.log"
    conn.execute("UPDATE runs SET log=? WHERE id=?", (str(log), run_id))
    if task_id:
        conn.execute("UPDATE tasks SET status='in corso', agent=?, run_id=?, updated_at=? "
                     "WHERE id=?", (agente, run_id, store.now(), task_id))
    conn.commit()

    eventi.scrivi("lavoro.avviato", titolo=titolo, progetto=progetto,
                  dati={"run": run_id, "agente": agente, "modo": modo, "cwd": cwd})

    if attendi:
        _esegui(run_id, agente, modo, prompt, cwd, str(log), titolo, progetto, task_id)
    else:
        threading.Thread(target=_esegui, daemon=True,
                         args=(run_id, agente, modo, prompt, cwd, str(log), titolo,
                               progetto, task_id)).start()
    return {"run": run_id, "agente": agente, "modo": modo, "cwd": cwd, "log": str(log)}


def _esegui(run_id, agente, modo, prompt, cwd, log, titolo, progetto, task_id):
    conn = store.connect()
    store.init_db(conn)
    acc = {"sessione": None, "esito": "", "token": 0, "costo": 0, "errore": False}
    inizio = time.time()
    try:
        cmd = _comando(agente, modo, cwd)
    except RuntimeError as exc:
        _chiudi(conn, run_id, "fallito", str(exc), acc, titolo, progetto, task_id, modo)
        conn.close()
        return

    conn.execute("UPDATE runs SET stato='in corso' WHERE id=?", (run_id,))
    conn.commit()
    try:
        with open(log, "w", encoding="utf-8") as fh:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                                    cwd=cwd, env=dict(os.environ))
            conn.execute("UPDATE runs SET pid=? WHERE id=?", (proc.pid, run_id))
            conn.commit()
            proc.stdin.write(prompt)
            proc.stdin.close()
            ultime = []
            for riga in proc.stdout:
                fh.write(riga)
                if agente == "claude":
                    _leggi_claude(riga, acc)
                else:
                    # Codex scrive testo: l'esito è la coda dell'output
                    ultime.append(riga.rstrip())
                    if len(ultime) > 40:
                        ultime.pop(0)
            proc.wait(timeout=3600)
            if agente == "codex" and not acc["esito"]:
                acc["esito"] = "\n".join(ultime[-12:]).strip()
            stato = "riuscito" if proc.returncode == 0 and not acc["errore"] else "fallito"
    except Exception as exc:
        stato, acc["esito"] = "fallito", f"{type(exc).__name__}: {exc}"

    acc["durata"] = round(time.time() - inizio)
    _chiudi(conn, run_id, stato, acc["esito"], acc, titolo, progetto, task_id, modo)
    conn.close()


def _chiudi(conn, run_id, stato, esito, acc, titolo, progetto, task_id, modo):
    conn.execute(
        "UPDATE runs SET stato=?, fine=?, esito=?, sessione=?, token=?, costo=? WHERE id=?",
        (stato, store.now(), (esito or "")[:4000], acc.get("sessione"),
         acc.get("token") or 0, acc.get("costo") or 0, run_id))
    if task_id:
        # Solo l'esecuzione vera chiude il task: una proposta lo lascia aperto,
        # perché proporre non è fare.
        nuovo = "fatto" if (stato == "riuscito" and modo == "esegui") else "aperto"
        conn.execute("UPDATE tasks SET status=?, updated_at=?, done_at=? WHERE id=?",
                     (nuovo, store.now(), store.now() if nuovo == "fatto" else None, task_id))
    conn.commit()
    eventi.scrivi("lavoro.completato" if stato == "riuscito" else "lavoro.fallito",
                  titolo=titolo, progetto=progetto,
                  dati={"run": run_id, "modo": modo, "esito": (esito or "")[:1200],
                        "durata_s": acc.get("durata"), "token": acc.get("token"),
                        "sessione": acc.get("sessione")})
    try:
        from . import briefing
        briefing.write_cache()
    except Exception:
        pass


# --------------------------------------------------------------------------
# lettura
# --------------------------------------------------------------------------

def elenco(conn, limite=20) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT r.*, t.title AS task FROM runs r LEFT JOIN tasks t ON t.id=r.task_id "
        "ORDER BY r.id DESC LIMIT ?", (limite,)).fetchall()]


def dettaglio(conn, run_id) -> dict:
    r = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        return None
    d = dict(r)
    try:
        d["coda"] = Path(d["log"]).read_text("utf-8", errors="replace")[-4000:]
    except Exception:
        d["coda"] = ""
    return d


def in_corso(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM runs WHERE stato IN "
                        "('in coda','in corso')").fetchone()[0]


def annulla(conn, run_id) -> bool:
    r = conn.execute("SELECT pid, stato FROM runs WHERE id=?", (run_id,)).fetchone()
    if not r or r["stato"] not in ("in coda", "in corso") or not r["pid"]:
        return False
    try:
        os.kill(r["pid"], 15)
    except Exception:
        pass
    conn.execute("UPDATE runs SET stato='annullato', fine=? WHERE id=?",
                 (store.now(), run_id))
    conn.commit()
    return True
