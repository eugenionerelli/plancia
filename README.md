# Plancia

A control room for the work you do with AI. Plancia reads what already happened
inside Claude Code, keeps it in one place, and gives it back two ways: a native
macOS app for you, and an MCP server plus an automatic briefing for Claude.

It also tells you how the day went, out loud, in your language.

Local-first. Nothing leaves the machine. No dependencies to install: Python 3 and
its standard library, Swift for the app.

[Italiano](README.it.md)

![The dashboard](docs/dashboard.png)

## What it reads

| source | where | what it gets |
|---|---|---|
| Claude Code sessions | `~/.claude/projects/**/*.jsonl` | date, project, opening prompt, turns, tools, tokens |
| Claude memory | `~/.claude/projects/*/memory/*.md` | project descriptions, `[[wiki]]` links |
| skills, plugins, routines | `~/.claude/skills`, `plugins`, `scheduled-tasks` | what your Claude Code can do |
| GitHub | `gh repo list`, recent commits | repos, commits, real material for posts |
| local git | your code roots | branch, uncommitted changes |
| session hooks | `SessionStart`, `SessionEnd` | which sessions are open right now |

Sources are never modified. Plancia reads them and stays out of the way.

## Install

```bash
git clone https://github.com/eugenionerelli/plancia.git ~/dev/plancia
cd ~/dev/plancia
./bin/plancia install      # command, MCP server, hooks, skills, autostart
./bin/plancia init         # builds your project map from repos, folders, memory
./mac/build.sh --install   # builds Plancia.app into /Applications
```

`plancia uninstall` puts everything back. Your data stays in `~/.plancia/`.

## The three ways in

**The app.** A native window, a menu bar item, and the voice. It supervises the
backend, so there is nothing to start by hand. `plancia://recap`,
`plancia://ask?q=…`, `plancia://open?view=projects`, `plancia://screenshot` are
URL actions you can bind to a system shortcut, Raycast or Shortcuts.

**Claude Code.** Sixteen `plancia_*` MCP tools in every session, a `SessionStart`
hook that hands Claude your current state as opening context, and two skills that
tell it when to read from Plancia and when to write back.

**The terminal.** `plancia recap --speak`, `plancia ask "what did I ship this
week?"`, `plancia task add`, `plancia search`, `plancia projects`.

## The daily recap

![The recap](docs/recap.png)

Plancia collects the day from real data — sessions, commits, tasks opened and
closed, posts, what each project is waiting on — and turns it into something
written to be heard: short sentences, no lists, no markdown, no file paths read
out loud.

Two engines for the text. The template one is deterministic, costs nothing and
always works. The other passes the same data to Claude Code in headless mode
(`claude -p`) and gets a better told version in about eight seconds. If Claude
does not answer in time, the template takes over and you never notice.

Two engines for the voice. [Voicebox](https://github.com/jamiepine/voicebox) if
its local backend is up, so you get your own cloned voice. Otherwise the macOS
system voices, which are always there, need no setup and start instantly. Both
handle Italian, English, Spanish, French, German and Portuguese.

```bash
plancia recap --speak            # today, out loud
plancia recap --lang en          # in English
plancia ask "where did I leave the transcription pipeline?" --speak
plancia daily on 08:45           # every morning, as a notification
plancia daily on 08:45 --voce    # every morning, out loud
```

Asking a question goes through Claude Code with your Plancia context attached, so
the answer is grounded in what actually happened, not in a guess.

## Projects

![Projects](docs/projects.png)

A project is whatever you say it is: a GitHub repo, a folder, a memory note, or
all three. `plancia init` proposes a map from what it finds; you correct it in
`~/.plancia/seed.json`. Sessions that run from a generic folder get attributed by
keyword, and re-attributed on every sync as you refine the keywords.

## Two decisions worth knowing about

**Transcripts are read by byte offset, not by line.** They are hundreds of
megabytes and they grow. Plancia keeps the offset of every file and only reads
the new tail; lines over 256 KB (tool results) are never parsed, only probed. A
full re-read of 60 sessions costs about seven seconds.

**A record's type is matched in full.** Inside `message.content` there are other
`type` fields (`text`, `tool_use`, `tool_result`) that come before the real one,
so searching for `"type":"` gives you the wrong answer. Plancia searches for
`"type":"assistant"` and `"type":"user"` whole.

## Layout

```
bin/plancia            command
bin/plancia-mcp        MCP server (stdio)
bin/plancia-hook       session hook, 20 ms
plancia/store.py       schema and data access
plancia/ingest.py      reading the sources
plancia/recap.py       the daily recap
plancia/voice.py       speech, playback, listening
plancia/briefing.py    what Claude sees
plancia/actions.py     writes, shared by HTTP and MCP
plancia/api.py         local server and REST
plancia/mcp.py         JSON-RPC over stdio
mac/Sources/main.swift the macOS app
web/                   dashboard, no framework, no build step
```

Data lives in `~/.plancia/`: `plancia.db` (SQLite), `seed.json`, `token`,
`briefing.md`, `audio/`. Keep it out of any synced folder: a SQLite file inside
Dropbox or Drive will corrupt.

## Requirements

macOS 13 or later, Python 3.9+, Claude Code. Xcode command line tools only if you
want to build the app. `gh` is optional and only used to read your repos.

## Security

The server listens on loopback only. HTTP writes require the token in
`~/.plancia/token`; the dashboard receives it from the server inside the page.
Reads are open: it is your data, already on your disk.

## Licence

MIT. See [LICENSE](LICENSE).
