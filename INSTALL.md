# Install and update pai

pai is a small command-line tool. It reads project files you register and
prints the context a coding agent needs for a task. At the end of the task it
writes a draft handoff note into your notes folder.

**Requirements:** Linux or macOS with Python 3.11 or newer (`python3
--version`). Nothing else is installed: there are no runtime dependencies and
no background service.

**Not included (external prerequisites):**
- your coding agent (for example Claude Code) and its login;
- your Markdown vault (for example Obsidian);
- file synchronization (for example Syncthing);
- your project repositories.

pai never installs, configures, launches or logs in to any of these.

## Install

```sh
# 1. Verify the download (Linux: sha256sum; macOS: shasum -a 256).
sha256sum -c pai-workbench-0.1.0.tar.gz.sha256     # or: shasum -a 256 -c …
# 2. Extract anywhere; the path may contain spaces.
tar -xzf pai-workbench-0.1.0.tar.gz -C "/path/of/your/choice"
cd "/path/of/your/choice/pai-workbench-0.1.0"
# 3. Check the bundle and Python (it reports "no configuration" until step 4).
bin/pai doctor --agents
```

## Configure

`pai init` writes nothing until you add `--write`, and never overwrites a
file. Paths after `--vault` are relative to the vault. The inbox folder must
already exist; pai does not create folders in your vault.

```sh
bin/pai init flh --repo "/abs/path/to/repo" --vault "/abs/path/to/vault" \
  --memory "Agent Inbox/PAI-001-…" --inbox "Agent Inbox" \
  --markdown-root "30 Projects" --project-note "30 Projects/Project.md" \
  --repo-doc "docs/SOME_DOC.md"                        # preview only
bin/pai init flh …same arguments… --write               # creates pai.local.toml (mode 0600)
bin/pai doctor flh
```

By default the configuration is `pai.local.toml` inside the bundle directory.
To keep it across updates, store it elsewhere and point pai at it with
`--config FILE` or the `PAI_CONFIG` environment variable, for example
`export PAI_CONFIG="$HOME/.config/pai/config.toml"`. The directory must
already exist.

## Use

```sh
bin/pai context flh --task "what you are about to do"
bin/pai handoff new flh --task-id TASK-1 --summary "…" --evidence "…"
```

See `SESSION_START.md` for the text to paste at the start of an agent session.

## Update

1. Verify and extract the new version into a **new** directory. Do not
   extract over the old one.
2. Reuse your configuration:
   - if it lives outside the bundle (`PAI_CONFIG`), there is nothing to do;
   - otherwise copy the old `pai.local.toml` into the new directory.
3. Run `bin/pai doctor`. It must report the new version and the bundle as
   intact.
4. Delete the old directory when satisfied.

Handoffs and notes live in your vault, not in the bundle, so updating never
touches them.

## Move to another machine

1. Extract the same archive there.
2. Run `bin/pai init` with that machine's own paths. Configurations are
   per-machine and are not copied blindly.

`bin/pai doctor` reports missing roots.
