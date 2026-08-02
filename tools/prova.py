#!/usr/bin/env python3
"""Il collaudo: gira tutto su un archivio finto e non tocca il tuo.

    python3 tools/prova.py

Non è una suite di test completa e non pretende di esserlo. È la lista delle
cose che si sono rotte almeno una volta, messe in fila, così prima di una
release si sa in dieci secondi se una di quelle è tornata a rompersi.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

RADICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RADICE))

CASA = Path(tempfile.mkdtemp(prefix="plancia-prova-"))
os.environ["PLANCIA_HOME"] = str(CASA)

falliti = []
passati = 0


def prova(nome, condizione, dettaglio=""):
    global passati
    if condizione:
        passati += 1
        print(f"  ok   {nome}")
    else:
        falliti.append(nome)
        print(f"  NO   {nome} {dettaglio}")


def main():
    print(f"archivio di prova: {CASA}\n")

    from plancia import (config, eventi, lavagna, proposte, recap,  # noqa: E402
                         store)

    # ---------------------------------------------------------------- schema
    conn = store.connect()
    store.init_db(conn)
    store.migrate(conn)
    tabelle = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    prova("le tabelle ci sono tutte",
          {"projects", "sessions", "tasks", "posts", "agenda", "runs",
           "events", "knowledge"} <= tabelle,
          str(sorted(tabelle)))

    # migrate deve poter girare due volte di fila senza lamentarsi
    store.migrate(conn)
    colonne = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
    prova("migrate è ripetibile e aggiunge le colonne nuove",
          {"agent", "prompt", "cwd", "run_id"} <= colonne, str(sorted(colonne)))

    # ------------------------------------------------------------ dati finti
    subprocess.run([sys.executable, str(RADICE / "tools" / "demo-data.py")],
                   check=True, capture_output=True, env=os.environ)
    conn = store.connect()
    n_prog = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    prova("i dati dimostrativi entrano", n_prog >= 7, f"progetti: {n_prog}")

    # ---------------------------------------------------------------- lavagna
    conteggi = lavagna.conteggi(conn)
    prova("la lavagna vede tutte e tre le fonti",
          {"claude", "codex", "plancia"} <= set(conteggi), str(conteggi))
    voci = lavagna.elenco(conn, "aperti")
    prova("le voci aperte hanno stato e fonte",
          bool(voci) and all(v.get("stato") and v.get("fonte") for v in voci))

    # in modalità dimostrativa non deve andare a leggere la macchina vera
    prova("la lavagna dimostrativa non tocca le fonti vere",
          lavagna.sync(conn) == 0)

    # una fonte che non risponde non deve far sparire le sue voci: si simula
    # facendo fallire la lettura di Codex e controllando che restino lì
    prima = conn.execute("SELECT COUNT(*) FROM agenda WHERE fonte='codex'").fetchone()[0]
    store.set_meta(conn, "demo", "0")
    conn.commit()
    vero_codex, vero_claude = lavagna.da_codex, lavagna.da_claude

    def codex_rotto(esito=None):
        if esito is not None:
            esito["ok"] = False
        return []

    lavagna.da_codex = codex_rotto
    lavagna.da_claude = lambda esito=None: []
    lavagna.sync(conn)
    lavagna.da_codex, lavagna.da_claude = vero_codex, vero_claude
    dopo = conn.execute("SELECT COUNT(*) FROM agenda WHERE fonte='codex'").fetchone()[0]
    store.set_meta(conn, "demo", "1")
    conn.commit()
    prova("una fonte muta non cancella le sue voci", dopo == prima,
          f"prima {prima}, dopo {dopo}")

    # --------------------------------------------------------------- proposte
    for lingua in ("it", "en", "es"):
        p = proposte.calcola(conn, lingua)
        prova(f"le proposte escono in {lingua}", bool(p))
        testo = " ".join(x["testo"] for x in p)
        if lingua == "en":
            prova("le proposte in inglese non hanno pezzi in italiano",
                  "quota" not in testo or "ha finito" not in testo, testo[:80])
        prova(f"ogni proposta in {lingua} ha un'azione",
              all(x.get("azione", {}).get("tipo") for x in p))

    # l'ordine è per urgenza: un lancio fallito prima di una spesa alta
    p = proposte.calcola(conn, "it")
    prova("le proposte sono ordinate per urgenza",
          [x["urgenza"] for x in p] == sorted(x["urgenza"] for x in p))

    proposte.salva(conn, p)
    prova("«la seconda» sceglie la seconda",
          proposte.scegli(conn, "la seconda") == p[1] if len(p) > 1 else True)
    prova("«fallo» sceglie la prima", proposte.scegli(conn) == p[0])
    # Se nessuno le ha ancora chieste, "fallo" non deve dire che non c'è niente
    store.set_meta(conn, "proposte", "")
    conn.commit()
    prova("«fallo» funziona anche a memoria vuota",
          (proposte.scegli(conn, None, "it") or {}).get("testo") == p[0]["testo"])

    # --------------------------------------------------------------- riepilogo
    dati = recap.collect(conn)
    prova("il riepilogo raccoglie la giornata", "sessioni" in dati)
    for lingua in ("it", "en", "es", "fr", "de", "pt"):
        testo = recap.render_template(dati, lingua)
        prova(f"il riepilogo a modelli parla {lingua}",
              len(testo) > 40 and "None" not in testo, testo[:60])

    r = recap.build(conn, lang="en", engine="template", cache=True)
    prova("il riepilogo in cache torna fresco",
          recap.solo_cache(conn, "en").get("fresco") is True)
    prova("il riepilogo in cache non torna in un'altra lingua",
          recap.solo_cache(conn, "it").get("testo") is None,
          "la cache tiene una lingua sola")

    # ------------------------------------------------------------------ lanci
    from plancia import cantiere  # noqa: E402
    conn.execute("INSERT INTO runs(agente, modo, prompt, cwd, stato, inizio, pid) "
                 "VALUES('claude','proposta','appeso','/tmp','in corso',?,999999)",
                 (store.now(),))
    conn.commit()
    prova("un lancio appeso viene chiuso al riavvio", cantiere.riconcilia(conn) >= 1)
    prova("e non resta in corso",
          conn.execute("SELECT COUNT(*) FROM runs WHERE stato='in corso' "
                       "AND prompt='appeso'").fetchone()[0] == 0)

    conn.execute("INSERT INTO runs(agente, modo, prompt, cwd, stato, inizio) "
                 "VALUES('claude','proposta','senza pid','/tmp','in coda',?)", (store.now(),))
    conn.commit()
    rid = conn.execute("SELECT id FROM runs WHERE prompt='senza pid'").fetchone()[0]
    prova("si annulla anche un lancio senza processo", cantiere.annulla(conn, rid) is True)

    # ------------------------------------------------------------ comandi voce
    from plancia import jarvis as _j  # noqa: E402
    coppie = [("vai più veloce", "velocita"), ("parla più piano", "velocita"),
              ("vai su progetti", "vai"), ("apri la lavagna", "vai"),
              ("ripeti", "ripeti"), ("non ho capito", "ripeti"),
              ("basta", "ferma"), ("annulla", "annulla"),
              ("slow down", "velocita"), ("say that again", "ripeti")]
    sbagliate = [f"{f} -> {_j.riconosci(f)[0]}" for f, atteso in coppie
                 if _j.riconosci(f)[0] != atteso]
    prova("i comandi vocali non si rubano la frase", not sbagliate, str(sbagliate))

    # ------------------------------------------------------- testo per la voce
    from plancia.voce_testo import per_voce  # noqa: E402
    prova("gli indirizzi non si leggono lettera per lettera",
          "GitHub" in per_voce("pubblicato su github.com/tizio/repo, poi si vede", "it")
          and "github.com" not in per_voce("pubblicato su github.com/tizio/repo", "it"))
    prova("di un percorso resta l'ultimo pezzo",
          per_voce("sta in ~/.plancia/eventi.jsonl", "it") == "sta in eventi.jsonl")
    prova("gli sha non si dicono",
          "4f1a2c9e8b7d" not in per_voce("il commit 4f1a2c9e8b7d6543 è a posto", "it"))
    prova("la punteggiatura della frase resta",
          per_voce("vedi github.com/a/b, poi torna", "it").endswith("poi torna")
          and "," in per_voce("vedi github.com/a/b, poi torna", "it"))
    prova("le date con le barre non diventano numeri",
          per_voce("la riunione è il 12/08/2026 alle nove", "it")
          == "la riunione è il 12/08/2026 alle nove")
    prova("ripeti risponde uguale da qualsiasi porta",
          _j.esegui("quanti progetti attivi ho", "it", conn)["risposta"]
          == _j.esegui("ripeti", "it", conn)["risposta"])
    prova("una frase normale non viene toccata",
          per_voce("Oggi tre sessioni e due commit, niente di strano.", "it")
          == "Oggi tre sessioni e due commit, niente di strano.")

    # --------------------------------------------------- risposte senza modello
    from plancia import risposte  # noqa: E402
    domande = {
        "it": ["quanti task aperti ho", "cosa c'è aperto sulla lavagna",
               "come sono andati i lanci", "quanto ho speso oggi",
               "cosa ho fatto oggi", "come va con codex"],
        "en": ["how many open tasks do i have", "what is open on the board",
               "how did the runs go", "how much did i spend today",
               "what did i do today", "how is codex doing"],
        "es": ["cuántas tareas abiertas tengo", "qué hay abierto en la pizarra",
               "cómo han ido los lanzamientos", "cuánto he gastado hoy"],
    }
    for lingua, elenco_domande in domande.items():
        mancanti = [d for d in elenco_domande if not risposte.prova(conn, d, lingua)]
        prova(f"le domande sui dati rispondono in {lingua} senza modello",
              not mancanti, str(mancanti))

    # una domanda che i dati non sanno deve passare al modello, non inventare
    prova("una domanda che i dati non sanno passa oltre",
          risposte.prova(conn, "scrivimi una poesia sul mare", "it") is None)

    # ------------------------------------------------------ commit e sessioni
    from plancia import ingest  # noqa: E402
    n = ingest.attribuisci_commit(conn)
    prova("l'attribuzione dei commit gira", isinstance(n, int))
    sbagliati = conn.execute(
        "SELECT COUNT(*) FROM commits c JOIN repos r ON r.name=c.repo "
        "JOIN sessions s ON s.session_id=c.session_id "
        "WHERE COALESCE(c.session_id,'')<>'' AND r.project_id IS NOT NULL "
        "AND s.project_id IS NOT NULL AND s.project_id <> r.project_id").fetchone()[0]
    prova("nessun commit finisce nella sessione di un altro progetto",
          sbagliati == 0, f"sbagliati: {sbagliati}")

    # ---------------------------------------------------------------- eventi
    e = eventi.scrivi("lavoro.completato", "prova", progetto="lumen",
                      dati={"agente": "claude"})
    letti = eventi.leggi(limite=5)
    prova("l'evento si scrive e si rilegge",
          bool(letti) and letti[-1]["id"] == e["id"])
    prova("il segnalibro non torna indietro", eventi.leggi(dopo=e["id"]) == [])
    prova("lo schema è dichiarato", e["schema"] == "plancia.evento/1")

    # ------------------------------------------------------------------- HTTP
    from plancia import api  # noqa: E402
    import threading

    porta = 7791
    filo = threading.Thread(target=api.serve, kwargs={"port": porta,
                                                      "sync_first": False},
                            daemon=True)
    filo.start()
    import time
    time.sleep(1.5)

    def prendi(percorso):
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}{percorso}",
                                    timeout=10) as r:
            return json.loads(r.read())

    try:
        o = prendi("/api/overview?lang=en")
        prova("/api/overview risponde", "stats" in o)
        prova("l'overview porta le proposte", isinstance(o.get("proposte"), list))
        prova("l'overview conta la lavagna", "lavagna_aperti" in o["stats"])
        prova("le proposte seguono la lingua chiesta",
              not any("Ci mando" in x["testo"] for x in o["proposte"]))
        prova("/api/lavagna risponde", "voci" in prendi("/api/lavagna"))
        prova("/api/runs risponde", isinstance(prendi("/api/runs?limite=3"), list))
        prova("/api/eventi risponde", isinstance(prendi("/api/eventi").get("eventi"), list))
        prova("/api/proposte risponde", isinstance(prendi("/api/proposte?lang=it"), list))
        with urllib.request.urlopen(f"http://127.0.0.1:{porta}/", timeout=10) as r:
            pagina = r.read().decode()
        prova("la pagina si serve", "<title>" in pagina)
    except Exception as errore:            # noqa: BLE001
        prova("il server HTTP risponde", False, str(errore))

    # --------------------------------------------------------------- scrittura
    # senza token le scritture devono essere rifiutate
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{porta}/api/tasks",
                                     data=b'{"title":"abusivo"}',
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        urllib.request.urlopen(req, timeout=10)
        prova("le scritture senza token sono rifiutate", False, "è passata")
    except urllib.error.HTTPError as errore:
        prova("le scritture senza token sono rifiutate", errore.code in (401, 403),
              str(errore.code))
    except Exception as errore:            # noqa: BLE001
        prova("le scritture senza token sono rifiutate", False, str(errore))

    # ------------------------------------------------------------------ front
    app_js = (RADICE / "web" / "app.js").read_text(encoding="utf-8")
    prova("app.js non legge il riepilogo prima di averlo",
          app_js.index("const d = r.data;") > app_js.index("solo_cache=1"))
    if shutil.which("node"):
        esito = subprocess.run(["node", "--check", str(RADICE / "web" / "app.js")],
                               capture_output=True)
        prova("app.js si compila", esito.returncode == 0,
              esito.stderr.decode()[:200])

    # ------------------------------------------------------------------- stile
    # la regola di casa: niente em dash nei testi che legge una persona
    fuori = []
    for percorso in [RADICE / "README.md", RADICE / "README.it.md",
                     RADICE / "site" / "index.html"]:
        if percorso.exists() and "—" in percorso.read_text(encoding="utf-8"):
            fuori.append(percorso.name)
    prova("niente em dash nei testi pubblici", not fuori, str(fuori))

    print()
    print(f"{passati} passate, {len(falliti)} fallite")
    if falliti:
        print("fallite: " + ", ".join(falliti))
    shutil.rmtree(CASA, ignore_errors=True)
    return 1 if falliti else 0


if __name__ == "__main__":
    sys.exit(main())
