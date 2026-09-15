"""agents.txt is the machine-readable twin of README's setup path.

It is downloaded on its own, away from this repository, so nothing around it
corrects a command that has gone stale: the version it stamps, the two plugin
install commands, the marketplace slug and the CLI package name all have to be
the ones the plugin actually ships. These checks are what "stays in sync with
the README" means in practice, and they are the reason no generator is needed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
AGENTS_TXT = PLUGIN_ROOT / "agents.txt"
README = PLUGIN_ROOT / "README.md"
PLUGIN_MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
SETUP_STEPS = PLUGIN_ROOT / "lib" / "setup-steps.sh"
SKILLS_DIR = PLUGIN_ROOT / "skills"


def _agents_text() -> str:
    assert AGENTS_TXT.is_file(), (
        f"{AGENTS_TXT.name} must sit at the repository root: it is served from "
        "there to agents that have no checkout."
    )
    return AGENTS_TXT.read_text(encoding="utf-8")


def _manifest() -> dict:
    return json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))


def test_version_stamp_matches_the_plugin_manifest():
    text = _agents_text()
    m = re.search(r"^LOCI version:\s*(\S+)\s*$", text, flags=re.MULTILINE)
    assert m, "agents.txt must open with a `LOCI version: <x.y.z>` line."
    assert m.group(1) == _manifest()["version"], (
        f"agents.txt stamps LOCI {m.group(1)}, the manifest says "
        f"{_manifest()['version']}. The stamp is how a downloaded copy says "
        "which release it describes, so a release bumps both."
    )


def test_readme_install_commands_all_appear():
    readme = README.read_text(encoding="utf-8")
    block = re.search(r"^## Install\s*\n+```\n(.*?)```", readme,
                      flags=re.MULTILINE | re.DOTALL)
    assert block, "README lost its fenced `## Install` block."
    commands = [ln.strip() for ln in block.group(1).splitlines() if ln.strip()]
    assert commands, "README's Install block is empty."

    text = _agents_text()
    missing = [c for c in commands if c not in text]
    assert not missing, (
        "README's install commands are absent from agents.txt, so the two "
        f"surfaces now tell users to run different things: {missing}"
    )


def test_marketplace_slug_matches_the_repository():
    repo = _manifest()["repository"]
    slug = re.sub(r"^https?://github\.com/", "", repo).removesuffix(".git")
    assert f"marketplace add {slug}" in _agents_text(), (
        f"agents.txt must add the marketplace from `{slug}`, the repository "
        "the manifest declares."
    )


def test_cli_package_name_matches_the_installer():
    m = re.search(r'^LOCI_CLI_PACKAGE="([^"]+)"',
                  SETUP_STEPS.read_text(encoding="utf-8"), flags=re.MULTILINE)
    assert m, "lib/setup-steps.sh no longer declares LOCI_CLI_PACKAGE."
    assert f"uv tool install -p 3.12 {m.group(1)}" in _agents_text(), (
        f"agents.txt's manual CLI install must name `{m.group(1)}`, the "
        "package the plugin's own installer fetches."
    )


def test_every_skill_it_routes_to_exists():
    named = set(re.findall(r"/loci:([a-z][a-z-]*)", _agents_text()))
    assert named, "agents.txt names no skills at all."
    missing = sorted(n for n in named
                     if not (SKILLS_DIR / n / "SKILL.md").is_file())
    assert not missing, (
        "agents.txt routes failures to skills that do not exist, so an agent "
        f"following it would invoke nothing: {missing}"
    )
