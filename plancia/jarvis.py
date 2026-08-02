"""Jarvis: quello che dici a voce diventa una cosa fatta.

Due strade, in quest'ordine. Le frasi che si riconoscono con certezza ("apri i
progetti", "ricordami di chiamare Mario") vengono eseguite qui, in un decimo di
secondo e senza chiamare nessuno. Tutto il resto va a Claude Code in modalità non
interattiva con i tool di Plancia aperti, quindi può davvero aggiungere task,
aggiornare progetti e cercare, non solo rispondere.

Le frasi corte si sbagliano facilmente: se un comando non è chiaro, non si tira a
indovinare, si passa a Claude.
"""

import re

from . import actions, agente, recap, risposte, store

# --------------------------------------------------------------------------
# comandi riconosciuti al volo
# --------------------------------------------------------------------------

VISTE = {
    "oggi": "oggi", "today": "oggi", "hoy": "oggi", "cruscotto": "oggi",
    "riepilogo": "riepilogo", "recap": "riepilogo", "resumen": "riepilogo",
    "progetti": "progetti", "projects": "progetti", "proyectos": "progetti",
    "task": "task", "tasks": "task", "cose da fare": "task", "tareas": "task",
    "social": "social", "post": "social",
    "sessioni": "sessioni", "sessions": "sessioni", "sesiones": "sessioni",
    "agenti": "agenti", "agents": "agenti", "codex": "agenti",
    "conoscenza": "conoscenza", "knowledge": "conoscenza", "memoria": "conoscenza",
    "capacità": "capacita", "capacita": "capacita", "skills": "capacita",
}

MODELLI = {
    "vai": [
        r"^(?:apri|apre|vai (?:a|su|in)|mostra(?:mi)?|portami (?:a|su))\s+(?:la |il |le |i |lo )?(.+?)[.?!]*$",
        r"^(?:open|go to|show me|show)\s+(?:the )?(.+?)[.?!]*$",
        r"^(?:abre|abrir|ve a|mu[eé]strame)\s+(?:la |el |los |las )?(.+?)[.?!]*$",
    ],
    "riepilogo": [
        r"^(?:fammi (?:il|un) |dammi (?:il|un) |leggimi (?:il|un) )?riepilogo\b",
        r"^com'?[eè] andata",
        r"^(?:give me |read me )?(?:the )?(?:daily )?recap\b",
        r"^how did (?:the day|today) go",
        r"^(?:dame |l[eé]eme )?el resumen\b",
    ],
    "aggiorna": [r"^(?:aggiorna|sincronizza|rileggi)\b", r"^(?:refresh|sync|update)\b",
                 r"^(?:actualiza|sincroniza)\b"],
    "ferma": [r"^(?:basta|ferma(?:ti)?|stop|zitto|silenzio|smetti)\b",
              r"^(?:quiet|shut up|be quiet)\b", r"^(?:para|c[aá]llate|silencio)\b"],
    "task_add": [
        r"^(?:ricordami di|ricordami|segna(?:ti)? (?:che|di)?|aggiungi (?:un |il )?task|nuovo task|devo)\s+(.+?)[.?!]*$",
        r"^(?:remind me to|add (?:a )?task|new task|note that)\s+(.+?)[.?!]*$",
        r"^(?:recu[eé]rdame|a[ñn]ade (?:una )?tarea|nueva tarea)\s+(.+?)[.?!]*$",
    ],
    "archivia": [
        r"^(?:archivia|chiudi il progetto|(?:ho )?finito con|metti via)\s+(.+?)[.?!]*$",
        r"^(.+?)\s+(?:è|e) (?:finito|finita|concluso|conclusa|chiuso|chiusa)[.?!]*$",
        r"^(?:archive|close the project|done with)\s+(.+?)[.?!]*$",
        r"^(?:archiva|cierra el proyecto|he terminado con)\s+(.+?)[.?!]*$",
    ],
    "riapri_progetto": [
        r"^(?:riapri|riattiva) (?:il progetto )?(.+?)[.?!]*$",
        r"^(?:reopen|reactivate) (?:the project )?(.+?)[.?!]*$",
    ],
    "task_done": [
        r"^(?:ho fatto|fatto|chiudi (?:il )?task|segna(?:lo)? (?:come )?fatto)\s*(.*?)[.?!]*$",
        r"^(?:done|i did|close (?:the )?task|mark (?:it )?done)\s*(.*?)[.?!]*$",
        r"^(?:hecho|he hecho|cierra la tarea)\s*(.*?)[.?!]*$",
    ],
}

RISPOSTE = {
    "it": {
        "vai": "Apro {vista}.",
        "aggiorna": "Rileggo le fonti.",
        "ferma": "Va bene.",
        "task_add": "Segnato: {titolo}.",
        "task_done": "Chiuso: {titolo}.",
        "task_non_trovato": "Non trovo un task aperto che assomigli a {titolo}.",
        "archiviato": "Archiviato {nome}. Non lo segnalo più.",
        "riaperto": "{nome} torna attivo.",
        "progetto_non_trovato": "Non trovo un progetto che si chiami {nome}.",
        "nessun_task": "Non hai task aperti.",
        "non_capito": "Non ho capito.",
    },
    "en": {
        "vai": "Opening {vista}.",
        "aggiorna": "Re-reading the sources.",
        "ferma": "All right.",
        "task_add": "Noted: {titolo}.",
        "task_done": "Closed: {titolo}.",
        "task_non_trovato": "I cannot find an open task like {titolo}.",
        "archiviato": "Archived {nome}. I will stop bringing it up.",
        "riaperto": "{nome} is active again.",
        "progetto_non_trovato": "I cannot find a project called {nome}.",
        "nessun_task": "You have no open tasks.",
        "non_capito": "I did not catch that.",
    },
    "es": {
        "vai": "Abro {vista}.",
        "aggiorna": "Releo las fuentes.",
        "ferma": "Vale.",
        "task_add": "Apuntado: {titolo}.",
        "task_done": "Cerrado: {titolo}.",
        "task_non_trovato": "No encuentro una tarea abierta parecida a {titolo}.",
        "archiviato": "Archivado {nome}. No lo vuelvo a mencionar.",
        "riaperto": "{nome} vuelve a estar activo.",
        "progetto_non_trovato": "No encuentro un proyecto que se llame {nome}.",
        "nessun_task": "No tienes tareas abiertas.",
        "non_capito": "No te he entendido.",
    },
}


def _dizionario(lang):
    return RISPOSTE.get(lang, RISPOSTE["en"])


def riconosci(testo: str):
    """(comando, argomento) se la frase è chiara, altrimenti (None, None)."""
    t = " ".join(testo.lower().strip().split())
    if not t:
        return None, None
    for comando, modelli in MODELLI.items():
        for m in modelli:
            trovato = re.match(m, t)
            if trovato:
                arg = trovato.group(1).strip() if trovato.groups() else ""
                return comando, arg
    return None, None


def _vista(arg: str):
    arg = (arg or "").strip().lower().rstrip("?.!")
    if arg in VISTE:
        return VISTE[arg]
    for chiave, vista in VISTE.items():
        if chiave in arg:
            return vista
    return None


# --------------------------------------------------------------------------
# la strada lunga: Claude con i tool aperti
# --------------------------------------------------------------------------

TOOL_CONSENTITI = [
    "mcp__plancia__plancia_briefing", "mcp__plancia__plancia_search",
    "mcp__plancia__plancia_projects", "mcp__plancia__plancia_project_update",
    "mcp__plancia__plancia_tasks", "mcp__plancia__plancia_task_add",
    "mcp__plancia__plancia_task_update", "mcp__plancia__plancia_posts",
    "mcp__plancia__plancia_post_add", "mcp__plancia__plancia_post_update",
    "mcp__plancia__plancia_sessions", "mcp__plancia__plancia_memory",
    "mcp__plancia__plancia_log", "mcp__plancia__plancia_recap",
]

PROMPT = """Sei l'assistente vocale di chi ti parla. Ti arriva una frase detta a voce,
trascritta, quindi può avere errori di trascrizione: interpretala con buon senso.

Hai i tool `plancia_*` sul suo archivio di lavoro: progetti, task, post, sessioni
passate di Claude Code e Codex, memoria. Usali davvero. Se ti chiede di segnare,
aggiornare o chiudere qualcosa, fallo e basta: è il suo archivio, non serve
chiedere il permesso. Se ti chiede un'informazione, guardala nei tool invece di
tirare a indovinare.

Rispondi in {lingua}, massimo {parole} parole, scritte per essere ascoltate:
niente elenchi, niente markdown, niente trattini lunghi, niente percorsi di file
o sigle lette a voce. Una o due frasi. Se hai fatto qualcosa, dillo in modo
diretto e corto.

Frase: {frase}"""


def chiedi_a_claude(frase: str, lang: str, parole=55) -> str:
    exe = recap.claude_bin()
    if not exe:
        return ""
    import os
    import subprocess
    from . import config
    prompt = PROMPT.format(lingua=recap.NOMI_LINGUA.get(lang, "English"),
                           parole=parole, frase=frase)
    cmd = [exe, "-p", "--model", config.load_config().get("modello_voce", "sonnet"),
           "--allowedTools"] + TOOL_CONSENTITI
    try:
        res = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                             timeout=180, cwd=str(config.DATA_DIR), env=dict(os.environ))
    except Exception:
        return ""
    return (res.stdout or "").strip() if res.returncode == 0 else ""


# --------------------------------------------------------------------------
# ingresso unico
# --------------------------------------------------------------------------

def esegui(testo: str, lang=None, conn=None) -> dict:
    lang = recap.lang_or_default(lang)
    d = _dizionario(lang)
    chiudi = False
    if conn is None:
        conn = store.connect()
        store.init_db(conn)
        chiudi = True
    try:
        comando, arg = riconosci(testo)

        if comando == "ferma":
            return {"tipo": "ferma", "risposta": d["ferma"], "azione": {"tipo": "ferma"},
                    "muto": True}

        if comando == "vai":
            vista = _vista(arg)
            if vista:
                return {"tipo": "vai", "risposta": d["vai"].format(vista=arg),
                        "azione": {"tipo": "vai", "vista": vista}}
            # "apri" seguito da altro non è una vista: probabilmente è un progetto
            riga = store.get_project(conn, arg)
            if riga:
                return {"tipo": "vai", "risposta": d["vai"].format(vista=riga["name"]),
                        "azione": {"tipo": "progetto", "chiave": riga["key"]}}

        if comando in ("archivia", "riapri_progetto") and arg:
            riga = store.get_project(conn, arg)
            if not riga:
                return {"tipo": "progetto",
                        "risposta": d["progetto_non_trovato"].format(nome=arg)}
            nuovo = "archiviato" if comando == "archivia" else "attivo"
            actions.project_update(conn, riga["key"], status=nuovo)
            chiave = "archiviato" if nuovo == "archiviato" else "riaperto"
            return {"tipo": "progetto", "risposta": d[chiave].format(nome=riga["name"]),
                    "azione": {"tipo": "vai", "vista": "progetti"}}

        if comando == "aggiorna":
            return {"tipo": "aggiorna", "risposta": d["aggiorna"],
                    "azione": {"tipo": "aggiorna"}}

        if comando == "riepilogo":
            dati = recap.build(conn, lang=lang)
            return {"tipo": "riepilogo", "risposta": dati["testo"],
                    "azione": {"tipo": "vai", "vista": "riepilogo"}, "lungo": True}

        if comando == "task_add" and arg and len(arg) > 2:
            task = actions.task_add(conn, arg[:200], source="jarvis")
            return {"tipo": "task", "risposta": d["task_add"].format(titolo=task["title"]),
                    "azione": {"tipo": "vai", "vista": "task"}}

        if comando == "task_done":
            aperti = actions.tasks_list(conn, "aperti", limit=30)
            if not aperti:
                return {"tipo": "task", "risposta": d["nessun_task"]}
            scelto = None
            if arg:
                parole = [p for p in re.findall(r"\w{4,}", arg.lower())]
                migliore, punteggio = None, 0
                for t in aperti:
                    titolo = t["title"].lower()
                    n = sum(1 for p in parole if p in titolo)
                    if n > punteggio:
                        migliore, punteggio = t, n
                scelto = migliore
            else:
                scelto = aperti[0]
            if not scelto:
                return {"tipo": "task",
                        "risposta": d["task_non_trovato"].format(titolo=arg)}
            actions.task_update(conn, scelto["id"], status="fatto")
            return {"tipo": "task", "risposta": d["task_done"].format(titolo=scelto["title"]),
                    "azione": {"tipo": "vai", "vista": "task"}}

        # Prima di scomodare un modello: la domanda è una di quelle che i dati
        # sanno già? Costa zero e risponde in un decimo di secondo.
        locale = risposte.prova(conn, testo, lang)
        if locale:
            return {"tipo": "dati", "risposta": locale}

        risposta = agente.chiedi(testo, lang)
        if not risposta:
            # il processo caldo non è partito: si ripiega su quello a freddo
            risposta = chiedi_a_claude(testo, lang)
        return {"tipo": "claude", "risposta": risposta or d["non_capito"]}
    finally:
        if chiudi:
            conn.close()
