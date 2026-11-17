# PAI Markdown server (EXPERIMENTAL, optional)

A remote Nano with a Markdown preview: a file picker for the Markdown folders you approve,
a plain-text editor, a rendered preview, and a save that shows the exact path and diff and
needs your confirmation for that revision. Nothing else: no task dashboard, chat, agent
controls, shell, Git or PR operations. PAI's CLI and SSH workflow do not need it.

## Install, start, stop, remove

```sh
pai setup --experimental server --root ~/Vault/Tasks --dry-run      # what it would do; writes nothing
pai setup --experimental server --root ~/Vault/Tasks                # asks before installing
pai setup --experimental server --root ~/Vault/Tasks --accept-experimental   # non-interactive
pai server start          # http://127.0.0.1:8765/ (loopback only)
pai server token          # the sign-in token; type it into the page, never into a chat
pai server status
pai server stop
pai server uninstall      # stops it and removes its configuration, token and log; your documents stay
```

Installing downloads nothing: the pack is Python's standard library plus the files in this
folder. It does not start the server. `--root` must be the Markdown folder itself (not `/`
or your home directory); only `.md` files are listed, hidden folders and symbolic links are
never followed. `--port` changes the port (default 8765).

## Reaching it from another machine

It only ever listens on 127.0.0.1. Use one of these; neither opens a port to the internet.

**SSH tunnel** (from your laptop):

```sh
ssh -N -L 8765:127.0.0.1:8765 you@your-server
# then open http://127.0.0.1:8765/ on the laptop
```

**Tailscale, tailnet only** (on the server; check `tailscale serve --help` for your
version's exact syntax):

```sh
pai setup --experimental server --root ~/Vault/Tasks --allowed-host your-server.your-tailnet.ts.net --accept-experimental
tailscale serve --bg 8765        # https://your-server.your-tailnet.ts.net/ for your tailnet's devices only
tailscale serve status
tailscale serve reset            # stop serving
```

Never use `tailscale funnel` for this: Funnel publishes to the internet. pai never runs
`tailscale` for you.

## How it stays safe

- Sign-in: a random token in `~/.local/share/pai-workbench/server/token` (mode 0600). The
  session cookie is HttpOnly and SameSite=Strict.
- Writes need the session's CSRF header, and every request needs a Host (and, when sent,
  an Origin) of this server, which also stops DNS rebinding.
- Reads, previews and diffs never write. A save sends the SHA-256 of the exact text you
  confirmed and the SHA-256 of the file you opened. If the file changed since (SSH, an
  agent, another browser), nothing is written, your draft stays in the editor, and you can
  copy it or reload. Writes are atomic (temporary file and rename), and keep UTF-8, a BOM,
  CRLF line endings and front matter exactly as they were.
- Rendering is inert: all source text is HTML-escaped (raw HTML shows as text), links
  keep only `http`, `https`, `mailto`, in-page and relative targets, images are never
  loaded (they become a labelled link), code blocks are shown and never run. The page's
  Content-Security-Policy allows only this server's own script and styles, and no images.

## Renderer research and choice

Candidates looked at (from their projects' documentation; versions were not re-checked
for this release):

| Project | License | Tables, task lists | Raw HTML / sanitizing | Offline |
| --- | --- | --- | --- | --- |
| [markdown-it](https://github.com/markdown-it/markdown-it) (JavaScript) | MIT | tables built in; task lists by plugin | raw HTML off by default (`html: false`); unsafe link schemes rejected | yes, one file |
| [marked](https://github.com/markedjs/marked) (JavaScript) | MIT | both, with GFM | does not sanitize; needs a sanitizer such as DOMPurify | yes |
| [mistune](https://github.com/lepture/mistune) (Python) | BSD-3-Clause | both, as plugins | escapes HTML by default in v3 | yes, but a pip install |

This first version uses none of them: `render.py` is a small built-in renderer for the
GitHub-style subset PAI notes use (front matter, headings, emphasis, code, lists, task
lists, tables, links, quotes), written so that unsafe output cannot be produced (every
character is escaped first). That keeps the pack dependency-free, with nothing to download.
If fuller rendering is needed later, markdown-it with `html: false`, vendored into
`static/`, is the recommended next step.

## Theme

`static/app.css` adapts the Endfield-inspired theme of French Learning Hub's opt-in
`web/src/theme-endfield.css` (itself original CSS inspired by public design notes): paper
and ink, one yellow signal used only as a fill, cut-corner actions, hairline panels with
an ink tab, a three-colour strip, and an ink-and-yellow keyboard focus ring. No game art,
logos or fonts. It is local to this pack, so the server runs offline without an FLH
checkout. Diff lines carry `+` and `−` symbols as well as colour; the EXPERIMENTAL label,
"Unsaved changes" and the stale-file warning stay visible. Below 760 px the file list
stacks above the editor, with Edit/Preview tabs.

Screenshots: not included. This development host has no browser (no Chromium, Firefox or
Playwright) and installing one was out of scope; take them with `pai server start` on a
machine that has one.
