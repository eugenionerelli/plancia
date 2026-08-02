Plancia reads what Claude Code and Codex already write on your disk and puts it
on one board. This is the first release under the GPL.

## What is in it

**One board for every agent.** Claude Code keeps its task list in one folder,
Codex keeps its goals in a different database, Plancia has its own. None of the
three knows the other two exist. The board reads all of them, normalises the
states, and shows one list.

**Dispatch from any row.** Write how you want the work done, pick a project and
an agent, and send it. The default mode reads and reports without touching a
file; letting an agent write is a separate choice you make every time.

**A recap that ends with a decision.** Your day told in a paragraph, out loud if
you want, in six languages. Then proposals, each with an action already prepared,
and each one born from a signal in the data: a failed run, a Codex goal out of
quota, files uncommitted since yesterday, an approved post that never went out.
No signal, no proposal.

**Hands-free voice.** ⌥Space anywhere. The microphone stays open while it
answers, so you can cut it off by speaking again. Questions about your own data
answer in under a millisecond with no model involved. Say "do it" to run a
proposal, "cancel" to stop a running dispatch, "repeat" if you missed it.

**An event log other tools can follow.** `~/.plancia/eventi.jsonl`, one JSON line
per event, schema `plancia.evento/1`, append only. Keep a bookmark, ask for what
came after.

**A guide on first run**, five steps, with your own numbers in it.

## Installing

```bash
git clone https://github.com/nerln/plancia.git ~/dev/plancia
cd ~/dev/plancia
./bin/plancia install
./bin/plancia init
./mac/build.sh --install
```

Python 3.9+ and macOS 13 or later. No dependencies to install, no account, no
server. Everything lives in `~/.plancia/`.

## About the .dmg attached here

**It is not signed or notarised.** macOS will refuse to open it on the first try
and offer only "Move to Trash" or "Done". To open it anyway you have to go to
System Settings, Privacy and Security, Security, and press "Open Anyway". Since
macOS Sequoia the old Control-click shortcut no longer works.

Building from source, with the commands above, avoids all of that and gives you
the same program.

A signed build that opens with a double click is what the paid version will be:
see the [website](https://nerln.github.io/plancia/#prezzo). The source
stays free and complete, always. What the price covers is the Apple certificate,
the notarisation, and the maintenance. It is the model Ardour and Krita have used
for years.

## Licence

GPL-3.0-or-later. Versions up to 0.2.0 were MIT and stay MIT. No licence keys and
no activation: under the GPL those would be a further restriction, which is not
allowed.

sha256 of the disk image is in the `.sha256` file next to it.
