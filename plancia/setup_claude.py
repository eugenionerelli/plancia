"""Aggancio a Claude Code: server MCP registrato e hook di sessione.

Ogni scrittura in ~/.claude tiene una copia di sicurezza accanto all'originale,
e si può disfare con `plancia uninstall`.
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import config

BIN = config.ROOT / "bin"
HOOK_CMD = str(BIN / "plancia-hook")
MCP_CMD = str(BIN / "plancia-mcp")
HOOK_EVENTS = ["SessionStart", "SessionEnd"]
SKILL_DIR = config.CLAUDE_DIR / "skills" / "plancia"


def backup(path: Path) -> Path:
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_suffix(path.suffix + f".plancia-backup-{stamp}")
    shutil.copy2(path, dest)
    return dest


# --------------------------------------------------------------------------
# hook
# --------------------------------------------------------------------------

def _hook_entry() -> dict:
    return {"hooks": [{"type": "command", "command": HOOK_CMD, "timeout": 5}]}


def install_hooks() -> str:
    path = config.CLAUDE_SETTINGS
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception as exc:
            return f"settings.json illeggibile ({exc}): hook non installati"
        backup(path)
    hooks = data.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        entries = hooks.setdefault(event, [])
        entries = [e for e in entries
                   if not any((h.get("command") or "").endswith("plancia-hook")
                              for h in (e.get("hooks") or []))]
        entries.append(_hook_entry())
        hooks[event] = entries
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    return f"hook installati in {path}"


def remove_hooks() -> str:
    path = config.CLAUDE_SETTINGS
    if not path.exists():
        return "nessun settings.json"
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return "settings.json illeggibile"
    backup(path)
    hooks = data.get("hooks", {})
    for event in HOOK_EVENTS:
        if event in hooks:
            hooks[event] = [e for e in hooks[event]
                            if not any((h.get("command") or "").endswith("plancia-hook")
                                       for h in (e.get("hooks") or []))]
            if not hooks[event]:
                del hooks[event]
    if not hooks:
        data.pop("hooks", None)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    return "hook rimossi"


def hooks_installed() -> bool:
    try:
        data = json.loads(config.CLAUDE_SETTINGS.read_text("utf-8"))
    except Exception:
        return False
    for event in HOOK_EVENTS:
        for entry in data.get("hooks", {}).get(event, []):
            for hook in entry.get("hooks", []):
                if (hook.get("command") or "").endswith("plancia-hook"):
                    return True
    return False


# --------------------------------------------------------------------------
# MCP
# --------------------------------------------------------------------------

def install_mcp() -> str:
    claude = shutil.which("claude")
    if claude:
        subprocess.run([claude, "mcp", "remove", "plancia", "--scope", "user"],
                       capture_output=True, text=True)
        res = subprocess.run(
            [claude, "mcp", "add", "plancia", "--scope", "user", "--", MCP_CMD],
            capture_output=True, text=True)
        if res.returncode == 0:
            return "server MCP registrato con `claude mcp add` (scope utente)"
    # ripiego: scrittura diretta in ~/.claude.json
    path = config.CLAUDE_JSON
    if not path.exists():
        return "impossibile registrare l'MCP: manca ~/.claude.json"
    backup(path)
    data = json.loads(path.read_text("utf-8"))
    servers = data.setdefault("mcpServers", {})
    if isinstance(servers, list):
        servers = data["mcpServers"] = {}
    servers["plancia"] = {"type": "stdio", "command": MCP_CMD, "args": [], "env": {}}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    return "server MCP registrato in ~/.claude.json"


def remove_mcp() -> str:
    claude = shutil.which("claude")
    if claude:
        subprocess.run([claude, "mcp", "remove", "plancia", "--scope", "user"],
                       capture_output=True, text=True)
    path = config.CLAUDE_JSON
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8"))
            if isinstance(data.get("mcpServers"), dict) and "plancia" in data["mcpServers"]:
                backup(path)
                del data["mcpServers"]["plancia"]
                path.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        except Exception:
            pass
    return "server MCP rimosso"


def mcp_installed() -> bool:
    try:
        data = json.loads(config.CLAUDE_JSON.read_text("utf-8"))
    except Exception:
        return False
    servers = data.get("mcpServers")
    return isinstance(servers, dict) and "plancia" in servers


# --------------------------------------------------------------------------
# skill e comando
# --------------------------------------------------------------------------

SKILL = """---
name: plancia
description: >-
  Consulta e aggiorna Plancia, il centro di controllo del lavoro dell'utente con
  l'IA: progetti, task, post sociali, sessioni passate, memoria. Usala quando
  chiede "a che punto sono", "cosa avevo lasciato", "cosa devo fare oggi",
  "dove l'avevamo fatto", quando apre un progetto suo, quando un lavoro finisce
  e va registrato, e prima di scrivere post sociali. Anche per "aggiorna
  plancia", "segna questo", "apri la dashboard".
---

# Plancia

Plancia è l'archivio unico del suo lavoro con l'IA. Sta in `~/dev/plancia`,
i dati in `~/.plancia/plancia.db`, la dashboard su http://127.0.0.1:7773.
I tool `plancia_*` arrivano dal server MCP: se non li vedi, il server non è
collegato e lo si registra con `plancia install`.

## All'inizio

Se la conversazione riguarda un suo progetto, chiama `plancia_briefing` prima di
rispondere. Restituisce progetti attivi, task aperti, post in coda e ultima
attività. Costa poco ed evita di chiedergli cose che sono già scritte.

Se dice "ne avevamo già parlato" o cerchi un lavoro passato, usa `plancia_search`:
indicizza le sessioni di Claude Code, la memoria, i task, i post e i commit.
`plancia_sessions` dà l'elenco con il comando per riprendere la conversazione.

## Durante

- Lavoro individuato ma non fatto: `plancia_task_add`. Un task registrato
  sopravvive alla fine della conversazione, una promessa in chat no.
- Decisione presa, strada abbandonata, traguardo raggiunto: `plancia_log`.
- Cambio di stato di un progetto o prossimo passo chiaro:
  `plancia_project_update` con `next_action`. È la prima cosa che leggerà la
  sessione dopo questa.

## Alla fine

Prima di chiudere un lavoro sostanziale: aggiorna `next_action` del progetto e
chiudi i task fatti con `plancia_task_update`. Non serve chiedere il permesso per
scrivere in Plancia: è il suo archivio, non un'azione verso l'esterno.

## La lavagna e i lanci

`plancia_lavagna` è la lista unica di quello che è aperto adesso, di tutti e tre:
le liste di task di Claude Code, gli obiettivi di Codex, i task di Plancia. Usala
quando chiede "cosa c'è aperto", "su cosa siamo fermi", "cosa sta facendo Codex".
Gli stati sono riportati agli stessi cinque: aperto, in corso, bloccato, fatto,
sparito.

`plancia_manda` fa partire un agente su un lavoro. Due modi, e il predefinito è
`proposta`: l'agente legge e riferisce senza toccare un file. `esegui` lo lascia
scrivere, e va scelto esplicitamente ogni volta, mai per iniziativa tua. Se lui
non ha detto di eseguire, manda in proposta.

`plancia_lanci` dice com'è andata: esito, token, costo. `plancia_eventi` legge il
registro in append, utile a chi deve reagire a un lavoro finito.

Se ti chiede di lanciare un lavoro mentre sei già dentro Claude Code, di solito
conviene farlo tu invece di passare da `plancia_manda`: il lancio serve quando il
lavoro deve andare a un altro agente o in un'altra cartella.

## Le proposte

Il riepilogo finisce con le cose che converrebbe fare, calcolate dai segnali nei
dati e mai inventate. Se ti chiede "cosa dovrei fare adesso", `plancia_recap` le
contiene già: non aggiungerne di tue sopra quelle, semmai spiega perché una è la
prima.

## Social

`plancia_post_add` salva una bozza, non pubblica niente. Il campo `source_ref`
deve puntare al lavoro reale che sta dietro al post: sha di un commit, nome di un
repo, id di una sessione. La regola dell'account è che ogni post nasce da qualcosa
che è successo davvero.

La scrittura resta della skill `social-media-manager`, la pubblicazione della
skill `x-account`, che chiede approvazione esplicita. Plancia tiene il conto:
`plancia_posts` per lo stato della pipeline, `plancia_post_update` con l'url
quando un post è davvero online.

## Voce

`plancia_recap` restituisce il riepilogo della giornata scritto per essere
ascoltato. Con `speak=true` lo legge ad alta voce sul suo Mac. `plancia_speak`
legge un testo qualsiasi: usalo solo se lo chiede, e scrivi per l'orecchio, non
per l'occhio.

Le lingue sono it, en, es, fr, de, pt. Se non la specifica, vale quella in
`~/.plancia/config.json`.

## Jarvis

`plancia://jarvis` apre il pannello vocale a mani libere, oppure ⌥Spazio da
qualsiasi app. Ascolta di continuo, capisce dal silenzio quando ha finito di
parlare, esegue e risponde a voce. I comandi che riconosce da solo (aprire una
vista, segnare un task, chiuderlo, rileggere le fonti, il riepilogo) partono
subito; tutto il resto arriva a Claude Code con i tool `plancia_*` aperti, quindi
può agire davvero.

Puoi interromperlo mentre parla: basta ricominciare a parlare, il microfono
resta aperto anche mentre risponde. "Annulla" ferma un lavoro partito, "basta"
chiude il pannello, "ripeti" ridice l'ultima cosa, "più piano" e "più veloce"
cambiano la velocità della voce. Quando un lancio finisce te lo dice a voce anche se nel
frattempo stavi facendo altro.

`plancia jarvis "frase"` fa la stessa cosa da terminale, senza microfono.

Tre strade in ordine: i comandi e le domande sui dati si risolvono in un decimo
di secondo senza chiamare nessun modello; il resto va a un processo Claude tenuto
caldo, circa tre secondi. Il riepilogo è precalcolato, quindi è immediato.

## I due agenti

Plancia legge anche le sessioni di Codex da `~/.codex/sessions` e registra il
proprio server MCP dentro `~/.codex/config.toml`: Codex e Claude vedono lo stesso
archivio e gli stessi tool. La vista Agenti mostra chi ha lavorato su cosa e
quando i due si sono passati il lavoro.

## Comandi

```bash
plancia lavagna          # tutto quello che è aperto, di tutti gli agenti
plancia manda "..." --agente codex --modo proposta
plancia lanci            # com'è andata
plancia eventi --dopo <id>
plancia recap --speak    # riepilogo letto ad alta voce
plancia jarvis "..."     # un comando vocale scritto
plancia ask "..." --speak
plancia daily on 08:45   # riepilogo automatico ogni mattina
plancia flusso           # da dove arrivano i dati e quanto sono freschi
plancia sync --modo caldo   # solo sessioni e hook, un centesimo di secondo
plancia serve --open     # dashboard
plancia sync             # rilegge sessioni, memoria, repo
plancia briefing         # il briefing su stdout
plancia doctor           # controlla i collegamenti
```
"""


RIEPILOGO_SKILL = """---
name: riepilogo
description: >-
  Racconta all'utente com'è andata la giornata di lavoro con l'IA, con i dati
  veri di Plancia, e se vuole gliela legge ad alta voce nella lingua che usa.
  Usala per "com'è andata oggi", "riepilogo", "cosa ho fatto", "leggimi il
  riepilogo", "briefing", "recap", "resumen", "what did I get done".
---

# Riepilogo della giornata

Il riepilogo non si inventa e non si ricostruisce a mano: lo produce Plancia dai
dati reali, con `plancia_recap`.

## Come farlo

1. Chiama `plancia_recap`. Senza argomenti è la giornata di oggi nella sua
   lingua. `day` accetta AAAA-MM-GG per un giorno passato, `lang` cambia lingua.
2. Riporta il testo com'è. È già scritto per essere ascoltato: frasi corte,
   niente elenchi, niente markdown. Non riformattarlo in punti elenco.
3. Se chiede di sentirlo ("leggimelo", "dimmelo", "a voce"), richiama
   `plancia_recap` con `speak=true`, oppure `plancia_speak` se vuoi leggere una
   risposta tua.

Dentro `dati` c'è tutto il dettaglio: sessioni, commit, task chiusi e aperti,
post, progetti fermi. Usalo per rispondere alle domande che fa dopo, senza
rigenerare il riepilogo.

## Il riepilogo finisce con una proposta

Dopo i fatti arriva la cosa che converrebbe fare, e non è un consiglio generico:
nasce da un segnale nei dati. Un lancio fallito, un obiettivo di Codex senza
quota, file non committati da ieri, un post approvato e mai uscito, il prossimo
passo di un progetto fermo.

Se lui risponde "fallo", "la seconda", "eseguilo", quella frase va passata a
Plancia così com'è: `plancia_manda` con quello che dice la proposta, oppure
lasciando fare al pannello vocale. **Non decidere tu di eseguire**: il modo
predefinito guarda e riferisce, e scrivere sui file è una scelta che fa lui ogni
volta.

Se il segnale non c'è, la proposta non c'è, ed è voluto. Non aggiungerne una tua
per riempire il finale.

## Cosa non fare

Non aggiungere risultati che non sono nei dati. Se la giornata è stata vuota, il
riepilogo lo dice in una riga e va bene così: riempirlo di frasi di incoraggiamento
lo rende inutile la volta dopo.

Non leggere ad alta voce senza che lo abbia chiesto. L'audio esce dalle casse del
suo Mac e potrebbe non essere solo.

Non riscrivere il testo per la voce: ci pensa Plancia, che toglie indirizzi,
percorsi e sha prima di dirlo, perché letti ad alta voce sono una filastrocca.

## Ogni mattina

`plancia daily on 08:45` mette un agente launchd che lo prepara e manda la
notifica. Con `--voce` lo legge anche. `plancia daily off` lo toglie.
L'app Plancia ha la stessa cosa nel menu della barra, e `plancia://recap`
lo lancia da una scorciatoia di sistema.
"""


def install_skill() -> str:
    SKILL_DIR.mkdir(parents=True, exist_ok=True)
    (SKILL_DIR / "SKILL.md").write_text(SKILL, "utf-8")
    altra = config.CLAUDE_DIR / "skills" / "riepilogo"
    altra.mkdir(parents=True, exist_ok=True)
    (altra / "SKILL.md").write_text(RIEPILOGO_SKILL, "utf-8")
    return f"skill plancia e riepilogo scritte in {SKILL_DIR.parent}"


def install_command() -> str:
    target = Path.home() / ".local" / "bin"
    target.mkdir(parents=True, exist_ok=True)
    link = target / "plancia"
    src = BIN / "plancia"
    if link.is_symlink() or link.exists():
        link.unlink()
    link.symlink_to(src)
    on_path = str(target) in os.environ.get("PATH", "").split(":")
    return (f"comando `plancia` in {link}" if on_path
            else f"comando in {link} — aggiungi {target} al PATH")


# --------------------------------------------------------------------------
# avvio automatico
# --------------------------------------------------------------------------

AGENT_LABEL = "com.plancia.server"
AGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{AGENT_LABEL}.plist"

PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array><string>{python}</string><string>{cmd}</string><string>serve</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
  <key>ProcessType</key><string>Background</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>{path}</string></dict>
</dict>
</plist>
"""


def autostart_on() -> str:
    AGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
    # L'interprete va fissato: launchd non ha il PATH della shell e "python3"
    # gli risolve nel 3.9 di Xcode invece che in quello con cui gira il resto.
    percorso = ":".join([str(Path.home() / ".local/bin"), "/opt/homebrew/bin",
                         "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
    AGENT_PLIST.write_text(PLIST.format(label=AGENT_LABEL, python=sys.executable,
                                        cmd=str(BIN / "plancia"), path=percorso,
                                        log=str(config.LOG_FILE)), "utf-8")
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{AGENT_LABEL}"], capture_output=True)
    res = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(AGENT_PLIST)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        return f"plist scritto in {AGENT_PLIST}, ma launchctl ha risposto: {res.stderr.strip()[:120]}"
    return "avvio automatico attivo: la dashboard riparte a ogni accesso"


def autostart_off() -> str:
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{AGENT_LABEL}"], capture_output=True)
    if AGENT_PLIST.exists():
        AGENT_PLIST.unlink()
    return "avvio automatico disattivato"


def autostart_installed() -> bool:
    return AGENT_PLIST.exists()


# --------------------------------------------------------------------------
# riepilogo automatico
# --------------------------------------------------------------------------

RECAP_LABEL = "com.plancia.recap"
RECAP_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{RECAP_LABEL}.plist"

RECAP_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{label}</string>
  <key>ProgramArguments</key>
  <array><string>{python}</string><string>{cmd}</string><string>recap</string>
    <string>--daily</string><string>--notify</string></array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>{ora}</integer><key>Minute</key><integer>{minuto}</integer></dict>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>{path}</string></dict>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
"""


def recap_daily_on(ora: str = "08:45", voce: bool = False) -> str:
    try:
        h, m = [int(x) for x in ora.split(":")]
        assert 0 <= h < 24 and 0 <= m < 60
    except Exception:
        return f"ora non valida: {ora}. Serve HH:MM."
    cfg = config.load_config()
    cfg["riepilogo_ora"] = f"{h:02d}:{m:02d}"
    cfg["riepilogo_voce"] = bool(voce)
    config.save_config(cfg)

    percorso = ":".join([str(Path.home() / ".local/bin"), "/opt/homebrew/bin",
                         "/usr/local/bin", "/usr/bin", "/bin"])
    RECAP_PLIST.parent.mkdir(parents=True, exist_ok=True)
    RECAP_PLIST.write_text(RECAP_TEMPLATE.format(
        label=RECAP_LABEL, python=sys.executable, cmd=str(BIN / "plancia"),
        ora=h, minuto=m, log=str(config.DATA_DIR / "recap.log"), path=percorso), "utf-8")
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{RECAP_LABEL}"], capture_output=True)
    res = subprocess.run(["launchctl", "bootstrap", f"gui/{uid}", str(RECAP_PLIST)],
                         capture_output=True, text=True)
    if res.returncode != 0:
        return f"plist scritto, launchctl ha risposto: {res.stderr.strip()[:120]}"
    return (f"riepilogo automatico alle {h:02d}:{m:02d}"
            + (" con la voce" if voce else " come notifica"))


def recap_daily_off() -> str:
    uid = os.getuid()
    subprocess.run(["launchctl", "bootout", f"gui/{uid}/{RECAP_LABEL}"], capture_output=True)
    if RECAP_PLIST.exists():
        RECAP_PLIST.unlink()
    cfg = config.load_config()
    cfg.pop("riepilogo_ora", None)
    config.save_config(cfg)
    return "riepilogo automatico disattivato"


def recap_daily_installed() -> bool:
    return RECAP_PLIST.exists()


def install_all() -> list:
    for script in ("plancia", "plancia-mcp", "plancia-hook"):
        path = BIN / script
        if path.exists():
            path.chmod(0o755)
    from . import codex
    return [install_command(), install_mcp(), codex.registra_mcp(), install_hooks(),
            install_skill(), autostart_on()]


def uninstall_all() -> list:
    from . import codex
    out = [recap_daily_off(), autostart_off(), remove_hooks(), remove_mcp(),
           codex.rimuovi_mcp()]
    link = Path.home() / ".local" / "bin" / "plancia"
    if link.is_symlink():
        link.unlink()
        out.append("comando rimosso")
    for d in (SKILL_DIR, config.CLAUDE_DIR / "skills" / "riepilogo"):
        if d.exists():
            shutil.rmtree(d)
    out.append("skill rimosse")
    out.append(f"i dati restano in {config.DATA_DIR}")
    return out


def doctor() -> list:
    from . import store
    lines = []
    ok = lambda cond: "ok  " if cond else "no  "
    lines.append(f"{ok(config.DB_PATH.exists())}database {config.DB_PATH}")
    if config.DB_PATH.exists():
        conn = store.connect()
        try:
            for table in ("projects", "sessions", "tasks", "posts", "knowledge", "events"):
                n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                lines.append(f"    {table}: {n}")
            lines.append(f"    ultimo sync: {store.get_meta(conn, 'last_sync_end', 'mai')}")

            # La lavagna: quante voci per fonte, e se una fonte è muta si vede
            # subito invece di scoprirlo dal fatto che manca roba.
            from . import lavagna as _lav
            per_fonte = _lav.conteggi(conn)
            pezzi = ", ".join(f"{k} {v.get('aperti', 0)}" for k, v in sorted(per_fonte.items()))
            lines.append(f"{ok(any(v.get('aperti') for v in per_fonte.values()))}"
                         f"lavagna: {pezzi or 'vuota'}")
            esiti = {"claude": {"ok": True}, "codex": {"ok": True}}
            _lav.da_claude(esiti["claude"])
            _lav.da_codex(esiti["codex"])
            for fonte, e in esiti.items():
                if not e.get("ok", True):
                    lines.append(f"no  la lavagna non riesce a leggere {fonte}")

            # Lanci appesi: se ce ne sono, la lavagna sta raccontando un lavoro
            # che non sta lavorando.
            from . import cantiere as _cant
            appesi = [r for r in conn.execute(
                "SELECT id, pid FROM runs WHERE stato IN ('in coda','in corso')")
                if not _cant._vivo(r["pid"])]
            lines.append(f"{ok(not appesi)}lanci appesi: {len(appesi)}"
                         + ("  (si chiudono al prossimo giro freddo)" if appesi else ""))
        except Exception as exc:
            lines.append(f"    errore: {exc}")
        finally:
            conn.close()
    lines.append(f"{ok(mcp_installed())}server MCP registrato in ~/.claude.json")
    lines.append(f"{ok(hooks_installed())}hook SessionStart/SessionEnd")
    from . import codex
    cx = codex.stato()
    lines.append(f"{ok(cx['installato'])}Codex trovato ({cx['sessioni']} sessioni)")
    lines.append(f"{ok(cx['mcp'])}server MCP registrato anche in Codex")
    lines.append(f"{ok((SKILL_DIR / 'SKILL.md').exists())}skill plancia")
    lines.append(f"{ok(autostart_installed())}avvio automatico (launchd)")
    ora = config.load_config().get("riepilogo_ora")
    lines.append(f"{ok(recap_daily_installed())}riepilogo automatico"
                 + (f" alle {ora}" if ora else "  (`plancia daily on 08:45`)"))
    try:
        from . import voice
        v = voice.stato()
        lines.append(f"ok  voce: {v['motore']} · {v['voce_attuale']} · "
                     f"{'Voicebox attivo' if v['voicebox_vivo'] else 'voci di sistema'}")
    except Exception as exc:
        lines.append(f"no  voce: {exc}")
    try:
        from . import eventi as _ev
        st = _ev.stato()
        lines.append(f"{ok(st['eventi'] >= 0)}registro eventi: {st['eventi']} righe, "
                     f"{round(st['byte'] / 1024)} KB, schema {st['schema']}")
    except Exception as exc:
        lines.append(f"no  registro eventi: {exc}")
    from . import recap as _recap
    lines.append(f"{ok(bool(_recap.claude_bin()))}claude per il riepilogo: {_recap.claude_bin() or 'non trovato'}")
    app = Path("/Applications/Plancia.app")
    lines.append(f"{ok(app.exists())}app macOS in {app}"
                 + ("" if app.exists() else "  (`./mac/build.sh --install`)"))
    link = Path.home() / ".local" / "bin" / "plancia"
    lines.append(f"{ok(link.exists())}comando {link}")
    port = config.load_config().get("port", config.DEFAULT_PORT)
    import socket
    with socket.socket() as s:
        s.settimeout(0.4)
        alive = s.connect_ex(("127.0.0.1", port)) == 0
    lines.append(f"{ok(alive)}dashboard su http://127.0.0.1:{port}"
                 + ("" if alive else "  (avviala con `plancia serve`)"))
    lines.append(f"    python: {sys.executable}")
    return lines
