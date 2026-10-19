"""`pai init`: build a local configuration from explicit paths.

- Without --write it only previews. It prints the TOML and the validation
  result and writes nothing.
- With --write it creates the file exclusively (O_EXCL). It refuses if the
  file exists. On POSIX systems the file gets mode 0600 (owner read/write).
  Windows ignores POSIX modes: the file inherits the folder's ACLs, so keep it
  in a folder only you can read.
- It looks only at the paths it is given: no scanning of home, vault or
  credential stores. It creates no vault folder and edits no note.
"""

import json
import os
import re
import tomllib

from pai import config

PROJECT_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _q(value):
    # JSON string escapes (\" \\ \n \uXXXX) are valid TOML basic strings.
    return json.dumps(value, ensure_ascii=False)


def _list(values):
    return "[" + ", ".join(_q(v) for v in values) + "]"


def render(key, a):
    lines = [
        "# pai local configuration, created by `pai init`. It holds this machine's paths: keep it private.",
        "# Edit by hand to add more projects; `pai init` never overwrites it.",
        "",
        f"[projects.{key}]",
        f"name = {_q(a['name'] or key)}",
        f"repo = {_q(a['repo'])}",
        f"instructions = {_list(a['instructions'])}",
        f"repo_docs = {_list(a['repo_docs'])}",
        f"instruction_always = {_list(a['instruction_always'])}",
        f"vault = {_q(a['vault'])}",
        f"memory = {_q(a['memory'])}",
        f"inbox = {_q(a['inbox'])}",
        f"markdown_roots = {_list(a['markdown_roots'])}",
        f"project_notes = {_list(a['project_notes'])}",
        f"proposal_files = {_list(a['proposal_files'])}",
        "exclude = []",
        f"budget_chars = {int(a['budget'])}",
        "max_section_chars = 2500",
        "required_section_chars = 1500",
        "",
    ]
    return "\n".join(lines)


def prepare(key, a):
    """(toml_text, project) after full validation; raises ConfigError with an actionable message."""
    if not PROJECT_KEY.match(key or ""):
        raise config.ConfigError("project key must be 1-32 characters of [a-z0-9_-], starting with a letter or digit")
    for name in ("repo", "vault"):
        if not config.is_absolute(a.get(name)):
            raise config.ConfigError(f"--{name} must be an absolute path to an existing directory")
    if not a.get("memory") or not a.get("inbox"):
        raise config.ConfigError("--memory and --inbox are required (paths relative to the vault)")
    text = render(key, a)
    data = tomllib.loads(text)  # the file we would write must parse
    project = config.from_dict(key, data["projects"][key])
    return text, project


def write(path, text):
    parent = os.path.dirname(os.path.abspath(path)) or "."
    if not os.path.isdir(parent):
        raise config.ConfigError(f"directory for the configuration does not exist: {parent} (create it first)")
    if os.path.lexists(path):
        raise config.ConfigError(f"{path} already exists; pai init never overwrites it. Edit it by hand "
                                 "(append the previewed [projects.…] table) or pass --config NEW_PATH")
    try:  # O_EXCL also covers a file created between the check above and this call
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise config.ConfigError(f"{path} appeared while writing; pai init never overwrites it")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def permission_note():
    """What protects the written file on this platform, stated without overclaiming."""
    if os.name == "nt":
        return ("POSIX mode 0600 does not apply on Windows; the file inherits the folder's ACLs, "
                "so keep it in a folder only you can read")
    return "mode 0600: readable and writable by the owner only"
