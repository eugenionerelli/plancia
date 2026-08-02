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

from . import actions, agente, cantiere, proposte, recap, risposte, store

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
    # "Non ho capito" detto a una macchina vuol dire ripeti, non spiegami.
    "ripeti": [r"^(?:ripeti|come(?: hai| ha)? detto|non ho (?:capito|sentito)|di nuovo)\b",
               r"^(?:repeat|say (?:that )?again|what did you say|come again)\b",
               r"^(?:repite|repítelo|c[oó]mo dices|otra vez)\b"],
    "velocita": [r"^(?:parla |vai |più |piu )?(più|piu|meno) (piano|lento|lentamente|veloce|svelto|rapido)\b",
                 r"^(?:slow(?:er)? down|speak slower|speed up|faster|slower)\b",
                 r"^(?:m[aá]s (?:despacio|lento|r[aá]pido)|habla m[aá]s (?:despacio|r[aá]pido))\b"],
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
    "fallo": [
        r"^(?:fallo|falla|s[iì](?:,? fallo| grazie)?|procedi|vai|ok(?:,? procedi)?|d'accordo)\b(.*)$",
        r"^(?:do it|yes(?:,? do it)?|go ahead|proceed)\b(.*)$",
        r"^(?:hazlo|s[ií](?:,? hazlo)?|adelante)\b(.*)$",
        r"^(?:la |il )?(prima|primo|seconda|secondo|terza|terzo)\b(.*)$",
    ],
    "eseguilo": [
        r"^(?:esegui(?:lo|la)?|fallo davvero|fallo per davvero|fallo e basta)\b(.*)$",
        r"^(?:actually do it|really do it|execute it)\b(.*)$",
    ],
    "aggiorna": [r"^(?:aggiorna|sincronizza|rileggi)\b", r"^(?:refresh|sync|update)\b",
                 r"^(?:actualiza|sincroniza)\b"],
    # Fermare un lavoro partito è diverso dal far tacere la voce: se uno dice
    # "annulla" mentre un agente sta lavorando, vuole fermare quello.
    "annulla": [r"^(?:annulla|ferma il lavoro|ferma il lancio|interrompi)\b(.*)$",
                r"^(?:cancel|stop the run|abort)\b(.*)$",
                r"^(?:cancela|para el trabajo)\b(.*)$"],
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
        "niente_da_ripetere": "Non ho ancora detto niente.",
        "piu_piano": "Vado più piano.",
        "piu_veloce": "Vado più svelto.",
        "niente_da_fermare": "Non c'è niente in corso da fermare.",
        "fermato": "Fermato. Lavori interrotti: {n}.",
        "task_add": "Segnato: {titolo}.",
        "task_done": "Chiuso: {titolo}.",
        "task_non_trovato": "Non trovo un task aperto che assomigli a {titolo}.",
        "archiviato": "Archiviato {nome}. Non lo segnalo più.",
        "riaperto": "{nome} torna attivo.",
        "progetto_non_trovato": "Non trovo un progetto che si chiami {nome}.",
        "niente_proposte": "Non ho niente in sospeso da proporti. Chiedimi il riepilogo.",
        "mandato": "Mando {chi} a vedere: {cosa}. Ti dico com'è andata.",
        "mandato_esegui": "Mando {chi} a farlo davvero: {cosa}.",
        "fatto_proposta": "Fatto.",
        "nessun_task": "Non hai task aperti.",
        "non_capito": "Non ho capito.",
    },
    "en": {
        "vai": "Opening {vista}.",
        "aggiorna": "Re-reading the sources.",
        "ferma": "All right.",
        "niente_da_ripetere": "I have not said anything yet.",
        "piu_piano": "Slowing down.",
        "piu_veloce": "Speeding up.",
        "niente_da_fermare": "There is nothing running to stop.",
        "fermato": "Stopped. Runs interrupted: {n}.",
        "task_add": "Noted: {titolo}.",
        "task_done": "Closed: {titolo}.",
        "task_non_trovato": "I cannot find an open task like {titolo}.",
        "archiviato": "Archived {nome}. I will stop bringing it up.",
        "riaperto": "{nome} is active again.",
        "progetto_non_trovato": "I cannot find a project called {nome}.",
        "niente_proposte": "Nothing pending to suggest. Ask me for the recap.",
        "mandato": "Sending {chi} to look at: {cosa}. I will tell you how it went.",
        "mandato_esegui": "Sending {chi} to actually do it: {cosa}.",
        "fatto_proposta": "Done.",
        "nessun_task": "You have no open tasks.",
        "non_capito": "I did not catch that.",
    },
    "es": {
        "vai": "Abro {vista}.",
        "aggiorna": "Releo las fuentes.",
        "ferma": "Vale.",
        "niente_da_ripetere": "Todavía no he dicho nada.",
        "piu_piano": "Voy más despacio.",
        "piu_veloce": "Voy más rápido.",
        "niente_da_fermare": "No hay nada en marcha que parar.",
        "fermato": "Parado. Trabajos interrumpidos: {n}.",
        "task_add": "Apuntado: {titolo}.",
        "task_done": "Cerrado: {titolo}.",
        "task_non_trovato": "No encuentro una tarea abierta parecida a {titolo}.",
        "archiviato": "Archivado {nome}. No lo vuelvo a mencionar.",
        "riaperto": "{nome} vuelve a estar activo.",
        "progetto_non_trovato": "No encuentro un proyecto que se llame {nome}.",
        "niente_proposte": "No tengo nada pendiente que proponerte. Pídeme el resumen.",
        "mandato": "Mando a {chi} a mirar: {cosa}. Te digo cómo ha ido.",
        "mandato_esegui": "Mando a {chi} a hacerlo de verdad: {cosa}.",
        "fatto_proposta": "Hecho.",
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


def _esegui_proposta(conn, scelta, d, lang, forza_esecuzione=False) -> dict:
    """Trasforma una proposta in un fatto.

    Il modo resta quello scritto nella proposta, cioè proposta, a meno che tu
    non abbia detto esplicitamente di eseguire. Una frase come "fallo" non deve
    mai finire per modificare file da sola.
    """
    a = scelta.get("azione") or {}
    tipo = a.get("tipo")

    if tipo == "vai":
        return {"tipo": "vai", "risposta": d["vai"].format(vista=a.get("vista", "")),
                "azione": {"tipo": "vai", "vista": a.get("vista", "oggi")}}

    if tipo == "rilancia":
        r = conn.execute("SELECT prompt, agente, modo, cwd, task_id FROM runs WHERE id=?",
                         (a.get("run"),)).fetchone()
        if not r:
            return {"tipo": "proposta", "risposta": d["niente_proposte"]}
        modo = "esegui" if forza_esecuzione else r["modo"]
        esito = cantiere.avvia(conn, r["prompt"][:200], agente=r["agente"], modo=modo,
                               cwd=r["cwd"], task_id=r["task_id"], lingua=lang)
        return {"tipo": "cantiere",
                "risposta": d["mandato"].format(chi=r["agente"], cosa=scelta["testo"][:60]),
                "azione": {"tipo": "vai", "vista": "oggi"}, "run": esito["run"]}

    if tipo == "manda":
        modo = "esegui" if forza_esecuzione else a.get("modo", "proposta")
        agente_scelto = a.get("agente", "claude")
        esito = cantiere.avvia(conn, a.get("titolo", scelta["testo"])[:200],
                               progetto=a.get("progetto"), agente=agente_scelto,
                               modo=modo, task_id=a.get("task_id"), lingua=lang)
        chiave = "mandato_esegui" if modo == "esegui" else "mandato"
        return {"tipo": "cantiere",
                "risposta": d[chiave].format(chi=agente_scelto,
                                             cosa=a.get("titolo", "")[:70]),
                "azione": {"tipo": "vai", "vista": "oggi"}, "run": esito["run"]}

    return {"tipo": "proposta", "risposta": d["fatto_proposta"]}


# --------------------------------------------------------------------------
# ingresso unico
# --------------------------------------------------------------------------

def esegui(testo: str, lang=None, conn=None) -> dict:
    esito = _esegui(testo, lang, conn)
    # L'ultima cosa detta si tiene da parte qui e non nella rotta HTTP: da
    # terminale, dall'app e da MCP "ripeti" deve rispondere alla stessa cosa.
    try:
        if esito.get("risposta") and not esito.get("ripetuta"):
            c = conn or store.connect()
            store.set_meta(c, "ultima_risposta", esito["risposta"])
            c.commit()
            if conn is None:
                c.close()
    except Exception:
        pass
    return esito


def _esegui(testo: str, lang=None, conn=None) -> dict:
    lang = recap.lang_or_default(lang)
    d = _dizionario(lang)
    chiudi = False
    if conn is None:
        conn = store.connect()
        store.init_db(conn)
        chiudi = True
    try:
        comando, arg = riconosci(testo)

        if comando == "ripeti":
            ultima = store.get_meta(conn, "ultima_risposta") or ""
            if not ultima:
                return {"tipo": "ripeti", "risposta": d["niente_da_ripetere"], "via": "comando"}
            # Si rimanda lo stesso testo: chi non ha sentito vuole quello, non
            # una riformulazione che lo confonde ancora di più.
            return {"tipo": "ripeti", "risposta": ultima, "via": "comando", "ripetuta": True}

        if comando == "velocita":
            giu = bool(re.search(r"piano|lent|slow|despacio", testo, re.I))
            passo = -0.06 if giu else 0.06
            return {"tipo": "velocita",
                    "risposta": d["piu_piano"] if giu else d["piu_veloce"],
                    "azione": {"tipo": "velocita", "passo": passo}, "via": "comando"}

        if comando == "annulla":
            from . import cantiere
            attivi = [r for r in cantiere.elenco(conn, limite=5)
                      if r["stato"] in ("in coda", "in corso")]
            if not attivi:
                return {"tipo": "annulla", "risposta": d["niente_da_fermare"], "via": "comando"}
            for r in attivi:
                cantiere.annulla(conn, r["id"])
            return {"tipo": "annulla",
                    "risposta": d["fermato"].format(n=len(attivi)), "via": "comando"}

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

        if comando in ("fallo", "eseguilo"):
            scelta = proposte.scegli(conn, arg or None)
            if not scelta:
                return {"tipo": "proposta", "risposta": d["niente_proposte"]}
            return _esegui_proposta(conn, scelta, d, lang,
                                    forza_esecuzione=(comando == "eseguilo"))

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
