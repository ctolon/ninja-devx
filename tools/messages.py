"""Keep the translation catalogs in ``src/ninja_devx/locale`` in sync with the code.

::

    uv run python tools/messages.py            # update .po files and compile .mo files
    uv run python tools/messages.py --language tr
    uv run python tools/messages.py --check    # CI: fail when catalogs are stale or incomplete

Messages are the string literals passed to ``gettext``, ``gettext_lazy``, ``gettext_noop``
or ``_``. No GNU gettext binaries are needed: extraction uses the AST and ``.mo`` files are
written directly. Translators edit ``msgstr`` lines in the ``.po`` files, then run the tool.

Languages come from ``--language`` (repeatable) or, without it, from the catalogs already
present under ``src/ninja_devx/locale``. The package ships English defaults and no catalog.
"""

from __future__ import annotations

import argparse
import ast
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "ninja_devx"
LOCALE = PACKAGE / "locale"
# Languages with a shipped catalog. Add your language code here and run the tool; the
# package itself ships English defaults only.
LANGUAGES: tuple[str, ...] = ()
FUNCTIONS = frozenset({"_", "gettext", "gettext_lazy", "gettext_noop"})
HEADERS = (
    "Project-Id-Version: ninja-devx",
    "MIME-Version: 1.0",
    "Content-Type: text/plain; charset=UTF-8",
    "Content-Transfer-Encoding: 8bit",
)


@dataclass
class Message:
    msgid: str
    locations: list[tuple[str, int]] = field(default_factory=list)


def extract() -> dict[str, Message]:
    messages: dict[str, Message] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if "migrations" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id not in FUNCTIONS or not node.args:
                continue
            argument = node.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                entry = messages.setdefault(argument.value, Message(argument.value))
                entry.locations.append((path.relative_to(PACKAGE).as_posix(), node.lineno))
    for message in messages.values():
        message.locations.sort()
    return messages


def _unquote(text: str) -> str:
    value: object = ast.literal_eval(text)
    assert isinstance(value, str)
    return value


def read_po(path: Path) -> dict[str, str]:
    """``{msgid: msgstr}`` of a ``.po`` file (header excluded)."""
    if not path.exists():
        return {}
    entries: dict[str, str] = {}
    msgid: list[str] = []
    msgstr: list[str] = []
    current: list[str] | None = None
    for raw in [*path.read_text().splitlines(), 'msgid ""']:
        line = raw.strip()
        if line.startswith("msgid "):
            if "".join(msgid):
                entries["".join(msgid)] = "".join(msgstr)
            msgid, msgstr = [_unquote(line[6:])], []
            current = msgid
        elif line.startswith("msgstr "):
            msgstr = [_unquote(line[7:])]
            current = msgstr
        elif line.startswith('"') and current is not None:
            current.append(_unquote(line))
    return entries


def _quote(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def render_po(language: str, messages: dict[str, Message], translations: dict[str, str]) -> str:
    lines = [
        f"# ninja-devx translations ({language}): edit msgstr, then run tools/messages.py.",
        'msgid ""',
        'msgstr ""',
        *(_quote(header + "\n") for header in (f"Language: {language}", *HEADERS)),
        "",
    ]
    for msgid in sorted(messages, key=lambda key: messages[key].locations[0]):
        message = messages[msgid]
        lines += [f"#: {path}" for path in dict.fromkeys(path for path, _ in message.locations)]
        if "%(" in msgid:
            lines.append("#, python-format")
        lines += [f"msgid {_quote(msgid)}", f"msgstr {_quote(translations.get(msgid, ''))}", ""]
    return "\n".join(lines)


def compile_mo(language: str, translations: dict[str, str]) -> bytes:
    """GNU ``.mo`` bytes: little endian, keys sorted, no hash table."""
    header = "".join(f"{line}\n" for line in (f"Language: {language}", *HEADERS))
    catalog = {"": header, **{key: value for key, value in translations.items() if value}}
    keys = sorted(catalog)
    originals = [key.encode() for key in keys]
    translated = [catalog[key].encode() for key in keys]
    count = len(keys)
    data_start = 7 * 4 + 16 * count
    table: list[int] = []
    blob = b""
    for strings in (originals, translated):
        for item in strings:
            table += [len(item), data_start + len(blob)]
            blob += item + b"\0"
    head = struct.pack("<7I", 0x950412DE, 0, count, 7 * 4, 7 * 4 + 8 * count, 0, 0)
    return head + struct.pack(f"<{len(table)}I", *table) + blob


def discover_languages() -> tuple[str, ...]:
    """Language codes with a ``django.po`` under ``src/ninja_devx/locale``."""
    if not LOCALE.exists():
        return ()
    return tuple(
        sorted(
            path.name for path in LOCALE.iterdir() if (path / "LC_MESSAGES" / "django.po").exists()
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="only report stale catalogs")
    parser.add_argument(
        "--language",
        action="append",
        default=[],
        metavar="CODE",
        help="language to update (repeatable); default: existing catalogs",
    )
    arguments = parser.parse_args(argv)
    languages = tuple(dict.fromkeys(arguments.language)) or LANGUAGES or discover_languages()
    messages = extract()
    problems: list[str] = []
    for language in languages:
        directory = LOCALE / language / "LC_MESSAGES"
        po_path, mo_path = directory / "django.po", directory / "django.mo"
        translations = {key: value for key, value in read_po(po_path).items() if key in messages}
        po = render_po(language, messages, translations)
        mo = compile_mo(language, translations)
        problems += [
            f"{language}: untranslated {key!r}" for key in messages if not translations.get(key)
        ]
        if arguments.check:
            if not po_path.exists() or po_path.read_text() != po:
                problems.append(f"{po_path.relative_to(ROOT)} is out of date")
            if not mo_path.exists() or mo_path.read_bytes() != mo:
                problems.append(f"{mo_path.relative_to(ROOT)} is out of date")
            continue
        directory.mkdir(parents=True, exist_ok=True)
        po_path.write_text(po)
        mo_path.write_bytes(mo)
        done = sum(1 for key in messages if translations.get(key))
        print(f"{language}: {done}/{len(messages)} messages translated")
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
