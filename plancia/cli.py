"""Riga di comando di Plancia."""

import argparse
import json
import sys
import webbrowser

from . import actions, briefing, config, store


def cmd_serve(args):
    from . import api
    api.serve(port=args.port, open_browser=args.open, sync_first=not args.no_sync)


def cmd_sync(args):
    from . import ingest
    last = [""]

    def progress(msg):
        if msg != last[0]:
            last[0] = msg
            sys.stderr.write(f"\r\x1b[K{msg}")
            sys.stderr.flush()

    res = ingest.sync(full=args.full, progress=progress, skip_git=args.skip_git)
    sys.stderr.write("\r\x1b[K")
    for key, value in res.items():
        print(f"{key}: {value}")


def cmd_mcp(args):
    from . import mcp
    return mcp.main()


def cmd_briefing(args):
    print(briefing.build(project=args.project) if args.project else briefing.write_cache())


def cmd_recap(args):
    from . import recap, voice
    data = recap.build(day=args.day, lang=args.lang, engine=args.engine)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return
    print(data["testo"])
    if args.notify:
        cmd_notifica("Plancia", data["testo"])
    if args.speak or (args.daily and config.load_config().get("riepilogo_voce")):
        info = voice.parla(data["testo"], data["lingua"], args.voce, attendi=not args.background)
        print(f"\n[voce: {info['motore']} · {info['file']}]", file=sys.stderr)


def cmd_notifica(titolo, testo):
    import subprocess
    testo = testo.replace('"', "'")[:220]
    subprocess.run(["osascript", "-e",
                    f'display notification "{testo}" with title "{titolo}"'],
                   capture_output=True, timeout=20)


def cmd_ask(args):
    from . import recap, voice
    domanda = " ".join(args.domanda)
    risposta = recap.answer(domanda, args.lang)
    print(risposta)
    if args.speak:
        voice.parla(risposta, recap.lang_or_default(args.lang), args.voce,
                    attendi=not args.background)


def cmd_say(args):
    from . import recap, voice
    testo = " ".join(args.testo)
    info = voice.parla(testo, recap.lang_or_default(args.lang), args.voce, attendi=True)
    print(f"[{info['motore']}] {info['file']}")


def cmd_voice(args):
    from . import voice
    if args.azione == "stato":
        print(json.dumps(voice.stato(), ensure_ascii=False, indent=2))
    elif args.azione == "voci":
        for nome, loc in voice.voci_sistema():
            print(f"{loc}  {nome}")
    elif args.azione == "prova":
        from . import recap
        lang = recap.lang_or_default(args.lang)
        frase = {"it": "Plancia è pronta. Ti leggo il riepilogo quando vuoi.",
                 "en": "Plancia is ready. I can read you the recap whenever you want.",
                 "es": "Plancia está lista. Te leo el resumen cuando quieras."}.get(
                     lang, "Plancia is ready.")
        info = voice.parla(frase, lang, args.voce, attendi=True)
        print(f"[{info['motore']} · {voice.voce_per(lang)}] ok")


def cmd_task(args):
    conn = store.connect()
    store.init_db(conn)
    try:
        if args.azione == "add":
            task = actions.task_add(conn, " ".join(args.testo), project=args.project,
                                    priority=args.priority, due=args.due)
            print(f"#{task['id']} {task['title']}")
        elif args.azione == "done":
            task = actions.task_update(conn, int(args.testo[0]), status="fatto")
            print(f"chiuso #{task['id']} {task['title']}")
        else:
            rows = actions.tasks_list(conn, args.status, args.project, 100)
            if not rows:
                print("nessun task")
            for t in rows:
                mark = {"fatto": "×", "in corso": "»", "bloccato": "!"}.get(t["status"], "·")
                proj = f"  [{t['project']}]" if t["project"] else ""
                due = f"  scade {t['due']}" if t["due"] else ""
                print(f"{mark} #{t['id']:<4} {t['title']}{proj}{due}")
    finally:
        conn.close()


def cmd_projects(args):
    conn = store.connect()
    store.init_db(conn)
    try:
        rows = conn.execute(
            "SELECT p.*, (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id "
            "AND t.status IN ('aperto','in corso','bloccato')) AS task_aperti "
            "FROM projects p WHERE hidden=0 ORDER BY pinned DESC, priority, last_activity DESC"
        ).fetchall()
        for p in rows:
            print(f"{p['status'][:8]:<9} {p['key']:<22} {p['name'][:38]:<39} "
                  f"{p['task_aperti'] or '':<3} {(p['last_activity'] or '')[:10]}")
    finally:
        conn.close()


def cmd_search(args):
    conn = store.connect()
    store.init_db(conn)
    try:
        for hit in store.search(conn, " ".join(args.query), 25):
            print(f"{hit['kind']:<10} {(hit['title'] or '')[:70]}")
            if hit.get("snip"):
                print(f"           {hit['snip'][:100]}")
    finally:
        conn.close()


def cmd_init(args):
    from . import init_seed, ingest
    progetti = init_seed.raccogli()
    for p in sorted(progetti.values(), key=lambda x: x["key"]):
        pezzi = ", ".join(f"{k}: {len(v)}" for k, v in p["links"].items())
        print(f"  {p['key']:<28} {pezzi}")
    print(init_seed.scrivi(progetti, forza=args.force))
    if not args.no_sync:
        print("rileggo le fonti…")
        res = ingest.sync()
        print(", ".join(f"{k} {v}" for k, v in res.items()))


def cmd_install(args):
    from . import setup_claude
    for line in setup_claude.install_all():
        print("·", line)
    print("\nOra: `plancia sync` e poi `plancia serve --open`.")
    print("Le sessioni di Claude Code già aperte vanno riavviate per vedere i tool plancia_*.")


def cmd_uninstall(args):
    from . import setup_claude
    for line in setup_claude.uninstall_all():
        print("·", line)


def cmd_autostart(args):
    from . import setup_claude
    print(setup_claude.autostart_on() if args.stato == "on" else setup_claude.autostart_off())


def cmd_daily(args):
    from . import setup_claude
    if args.stato == "off":
        print(setup_claude.recap_daily_off())
        return
    print(setup_claude.recap_daily_on(args.ora, voce=args.voce))


def cmd_doctor(args):
    from . import setup_claude
    for line in setup_claude.doctor():
        print(line)


def cmd_open(args):
    port = config.load_config().get("port", config.DEFAULT_PORT)
    webbrowser.open(f"http://127.0.0.1:{port}")


def cmd_config(args):
    cfg = config.load_config()
    if args.chiave:
        value = args.valore
        if value is not None:
            try:
                value = json.loads(value)
            except Exception:
                pass
            cfg[args.chiave] = value
            config.save_config(cfg)
        print(f"{args.chiave} = {cfg.get(args.chiave)}")
    else:
        print(json.dumps(cfg, indent=2, ensure_ascii=False))


def build_parser():
    p = argparse.ArgumentParser(prog="plancia", description="Centro di controllo del lavoro con l'IA")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="avvia la dashboard locale")
    s.add_argument("--port", type=int)
    s.add_argument("--open", action="store_true", help="apre il browser")
    s.add_argument("--no-sync", action="store_true", help="non aggiornare all'avvio")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("sync", help="rilegge sessioni, memoria, repo")
    s.add_argument("--full", action="store_true", help="rilegge i transcript da capo")
    s.add_argument("--skip-git", action="store_true", help="salta GitHub e git locale")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("mcp", help="server MCP su stdio (lo lancia Claude Code)")
    s.set_defaults(func=cmd_mcp)

    s = sub.add_parser("briefing", help="stampa il briefing")
    s.add_argument("--project")
    s.set_defaults(func=cmd_briefing)

    s = sub.add_parser("task", help="task da terminale")
    s.add_argument("azione", nargs="?", default="list", choices=["list", "add", "done"])
    s.add_argument("testo", nargs="*")
    s.add_argument("--project")
    s.add_argument("--priority", type=int, default=2)
    s.add_argument("--due")
    s.add_argument("--status", default="aperti")
    s.set_defaults(func=cmd_task)

    s = sub.add_parser("recap", help="riepilogo della giornata, anche a voce")
    s.add_argument("--lang")
    s.add_argument("--day", help="AAAA-MM-GG, default oggi")
    s.add_argument("--engine", choices=["claude", "template"])
    s.add_argument("--speak", action="store_true", help="leggilo ad alta voce")
    s.add_argument("--notify", action="store_true", help="mandalo come notifica")
    s.add_argument("--daily", action="store_true", help="modalità automatica")
    s.add_argument("--voce", choices=["auto", "voicebox", "say"])
    s.add_argument("--background", action="store_true", help="non aspettare la fine")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_recap)

    s = sub.add_parser("ask", help="domanda sul tuo lavoro, risposta da Claude")
    s.add_argument("domanda", nargs="+")
    s.add_argument("--lang")
    s.add_argument("--speak", action="store_true")
    s.add_argument("--voce", choices=["auto", "voicebox", "say"])
    s.add_argument("--background", action="store_true")
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("say", help="leggi una frase con la voce configurata")
    s.add_argument("testo", nargs="+")
    s.add_argument("--lang")
    s.add_argument("--voce", choices=["auto", "voicebox", "say"])
    s.set_defaults(func=cmd_say)

    s = sub.add_parser("voice", help="stato della voce, elenco voci, prova")
    s.add_argument("azione", nargs="?", default="stato", choices=["stato", "voci", "prova"])
    s.add_argument("--lang")
    s.add_argument("--voce", choices=["auto", "voicebox", "say"])
    s.set_defaults(func=cmd_voice)

    s = sub.add_parser("projects", help="elenco progetti")
    s.set_defaults(func=cmd_projects)

    s = sub.add_parser("search", help="cerca in tutto")
    s.add_argument("query", nargs="+")
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("init", help="costruisce la mappa dei progetti dai tuoi dati")
    s.add_argument("--force", action="store_true", help="riscrive il seed esistente")
    s.add_argument("--no-sync", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("install", help="collega MCP, hook, skill e comando")
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("uninstall", help="scollega tutto (i dati restano)")
    s.set_defaults(func=cmd_uninstall)

    s = sub.add_parser("autostart", help="avvia la dashboard a ogni accesso")
    s.add_argument("stato", choices=["on", "off"])
    s.set_defaults(func=cmd_autostart)

    s = sub.add_parser("daily", help="riepilogo automatico ogni giorno")
    s.add_argument("stato", choices=["on", "off"])
    s.add_argument("ora", nargs="?", default="08:45", help="HH:MM, default 08:45")
    s.add_argument("--voce", action="store_true", help="leggilo anche ad alta voce")
    s.set_defaults(func=cmd_daily)

    s = sub.add_parser("doctor", help="controlla lo stato")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("open", help="apre la dashboard nel browser")
    s.set_defaults(func=cmd_open)

    s = sub.add_parser("config", help="legge o scrive la configurazione")
    s.add_argument("chiave", nargs="?")
    s.add_argument("valore", nargs="?")
    s.set_defaults(func=cmd_config)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    config.ensure_dirs()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
