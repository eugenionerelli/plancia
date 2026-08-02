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
`plancia://jarvis`, `plancia://ask?q=…`, `plancia://open?view=projects` and
`plancia://pdf` are
URL actions you can bind to a system shortcut, Raycast or Shortcuts.

**Claude Code and Codex.** Sixteen `plancia_*` MCP tools in every session of both, a `SessionStart`
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

## Jarvis

Hold nothing, press nothing. `⌥Space` anywhere, or `plancia://jarvis`, opens a
panel that listens continuously and works out you have finished speaking from the
silence, not from a key you keep held down.

What it hears goes two ways. Phrases it can recognise with certainty — open a
view, note a task, close one, re-read the sources, read me the recap — run
locally in a tenth of a second. Everything else goes to Claude Code in headless
mode with the `plancia_*` tools open, so it can actually add the task, update the
project or search the archive, not just answer about it.

```bash
plancia jarvis "remind me to write the migration note"   # same thing, typed
```

Claude Code has had [voice input since March 2026](https://claudefa.st/blog/guide/mechanics/voice-mode):
you hold the spacebar and dictate. It is input only, and by design there is no
hands-free mode. This is the other half: it speaks back, and it acts.

## Two agents, one archive

Plancia reads Codex sessions from `~/.codex/sessions` alongside Claude Code's,
and registers its own MCP server inside `~/.codex/config.toml`. Both agents see
the same projects, the same tasks, the same sixteen tools. The Agents view shows
who worked on what and when the two handed work to each other, inside the
Archive.

## Where the time goes

Every `claude -p` costs about five seconds of startup before it even thinks. In a
spoken conversation that is five seconds of silence per question. Plancia takes
three routes, in this order:

| route | when | cost |
|---|---|---|
| commands | open a view, note a task, close one, archive a project | 0.1 s |
| data answers | how many tasks, what should I pick up, how much did I work | 0.1 s |
| Claude, kept warm | anything else, with the `plancia_*` tools open | 2.7 s |

The Claude process stays alive between questions instead of being restarted, so
only the first one pays the startup, and the panel warms it up the moment you
open it. The daily recap is precomputed at the end of every cold pass: asking for
it costs 20 ms instead of ten seconds.

## The data flow

```
sources ──▶ sync ──▶ SQLite ──▶ briefing.md · recap · REST · voice
```

Two rhythms, because reading twenty repos to find out you just opened a session
is a waste:

- **hot**, every two minutes, ~0.01 s: the hook queue and the new tail of the
  transcripts. What you are doing right now.
- **cold**, every thirty minutes, ~1.3 s: memory, skills, repos, local git,
  project housekeeping, search index, recap.

`plancia flusso` prints every source, where it comes from, which pass reads it
and how fresh it is.

## Projects end

A project born from a folder you worked in once, three weeks ago, is not an
active project: it is a memory. Plancia archives it on its own after two weeks
if it has no repo, no memory note and fewer than three sessions. Anything you
declared yourself is never touched. By voice: "archive the video project", or
"the Ard footage is finished".


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

## Four surfaces

Today (the recap, the rhythm, the tasks), Projects, Social, Archive (sessions,
agents, memory, skills). Everything else goes through ⌘K.

## Requirements

macOS 13 or later, Python 3.9+, Claude Code. Xcode command line tools only if you
want to build the app. `gh` is optional and only used to read your repos.

## Security

The server listens on loopback only. HTTP writes require the token in
`~/.plancia/token`; the dashboard receives it from the server inside the page.
Reads are open: it is your data, already on your disk.

## Licence

MIT. See [LICENSE](LICENSE).
