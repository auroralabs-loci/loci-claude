"""agents.txt is the machine-readable twin of README's setup path.

It is downloaded on its own, away from this repository, so nothing around it
corrects a command that has gone stale: the version it stamps, the plugin
install commands for BOTH hosts (Claude Code and GitHub Copilot CLI), the
marketplace slug and the CLI package name all have to be the ones the plugin
actually ships. These checks are what "stays in sync with the README" means in
practice, and they are the reason no generator is needed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
AGENTS_TXT = PLUGIN_ROOT / "agents.txt"
README = PLUGIN_ROOT / "README.md"
PLUGIN_MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = PLUGIN_ROOT / ".claude-plugin" / "marketplace.json"
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


def _marketplace_slug() -> str:
    repo = _manifest()["repository"]
    return re.sub(r"^https?://github\.com/", "", repo).removesuffix(".git")


def _plugin_at_marketplace() -> str:
    """`loci@loci`: the plugin's name in the marketplace manifest at the
    marketplace's own name. Both hosts install by this spelling."""
    doc = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    (plugin,) = doc["plugins"]
    return f"{plugin['name']}@{doc['name']}"


def _readme_install_blocks() -> list[list[str]]:
    """The fenced blocks of README's `## Install` section, as command lists."""
    readme = README.read_text(encoding="utf-8")
    m = re.search(r"^## Install\s*\n(.*?)(?=^## )", readme,
                  flags=re.MULTILINE | re.DOTALL)
    assert m, "README lost its `## Install` section."
    blocks = re.findall(r"^```[^\n]*\n(.*?)^```", m.group(1),
                        flags=re.MULTILINE | re.DOTALL)
    assert blocks, "README's Install section has no fenced block."
    return [[ln.strip() for ln in b.splitlines() if ln.strip()] for b in blocks]


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
    """Every command README's Install section shows, for either host, is in
    agents.txt too — the two surfaces must tell users to run the same things."""
    blocks = _readme_install_blocks()
    text = _agents_text()
    for commands in blocks:
        assert commands, "README's Install section has an empty fenced block."
        missing = [c for c in commands if c not in text]
        assert not missing, (
            "README's install commands are absent from agents.txt, so the two "
            f"surfaces now tell users to run different things: {missing}"
        )


def test_readme_installs_on_both_hosts():
    """README's Install section carries one block per host: the Claude Code
    slash commands and the `copilot plugin …` terminal commands. AAD-7792 added
    the second; losing either silently drops a host from the install path."""
    blocks = _readme_install_blocks()
    claude = [b for b in blocks if any(c.startswith("/plugin ") for c in b)]
    copilot = [b for b in blocks if any(c.startswith("copilot plugin ") for c in b)]
    assert claude, "README's Install section lost the Claude Code block."
    assert copilot, "README's Install section lost the GitHub Copilot CLI block."
    assert not any(c.startswith("copilot ") for b in claude for c in b), (
        "a `copilot` command sits in the Claude Code install block")
    assert not any(c.startswith("/") for b in copilot for c in b), (
        "a slash command sits in the Copilot install block: Copilot's install "
        "is two terminal commands, the slash commands are Claude Code's")


def test_marketplace_slug_matches_the_repository():
    slug = _marketplace_slug()
    assert f"marketplace add {slug}" in _agents_text(), (
        f"agents.txt must add the marketplace from `{slug}`, the repository "
        "the manifest declares."
    )


def test_both_hosts_install_the_same_marketplace_plugin():
    """The three install blocks (Claude Code in-session, Claude Code from a
    terminal, GitHub Copilot CLI) all add the marketplace from the repository
    the manifest declares and install `<plugin>@<marketplace>` as
    marketplace.json names them. Copilot reads the same manifest, so a rename
    there breaks every block at once — and this is where it shows."""
    slug = _marketplace_slug()
    target = _plugin_at_marketplace()
    text = _agents_text()
    expected = [
        f"/plugin marketplace add {slug}",
        f"/plugin install {target}",
        f"claude plugin marketplace add {slug}",
        f"claude plugin install {target} -y",
        f"copilot plugin marketplace add {slug}",
        f"copilot plugin install {target}",
    ]
    missing = [c for c in expected if c not in text]
    assert not missing, (
        f"agents.txt's install blocks no longer carry {missing}; the plugin is "
        f"`{target}` from `{slug}` on both hosts."
    )
    assert f"copilot plugin install {slug}" not in text, (
        "agents.txt tells Copilot users to install straight from the repository, "
        "a form Copilot prints a deprecation warning for; the marketplace install "
        "is the documented one"
    )


def test_cli_package_name_matches_the_installer():
    """The package AND the pin: agents.txt is read away from this repository,
    so an unpinned `uv tool install loci-tools` installed whatever PyPI's newest
    wheel was, not the one this plugin release is proven against (AAD-7835).
    The pin moves with `LOCI_CLI_VERSION` (RELEASING.md step 2)."""
    steps = SETUP_STEPS.read_text(encoding="utf-8")
    m = re.search(r'^LOCI_CLI_PACKAGE="([^"]+)"', steps, flags=re.MULTILINE)
    assert m, "lib/setup-steps.sh no longer declares LOCI_CLI_PACKAGE."
    v = re.search(r'^LOCI_CLI_VERSION="([0-9.]+)"', steps, flags=re.MULTILINE)
    assert v, "lib/setup-steps.sh no longer declares LOCI_CLI_VERSION."
    spec = f"{m.group(1)}=={v.group(1)}"
    installs = re.findall(r"uv tool install -p 3\.12 (\S+)", _agents_text())
    assert installs == [spec], (
        f"agents.txt's manual CLI install must be `{spec}` — the package the "
        f"plugin's own installer fetches, at its pin — and the only one; found {installs}."
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


def test_it_names_both_hosts_as_prerequisites():
    """The first prerequisite names both hosts and, for Copilot on Windows, the
    two things Claude Code never needed spelled out: PowerShell 7 (Copilot's own
    requirement) and Git for Windows' `bash` on PATH (Copilot hands the hook
    command to PowerShell, which resolves `bash` through PATH; Claude Code finds
    Git Bash itself)."""
    text = _agents_text()
    m = re.search(r"^BEFORE YOU START\n-+\n(.*?)^STEP 1", text,
                  flags=re.MULTILINE | re.DOTALL)
    assert m, "agents.txt lost its BEFORE YOU START section."
    prereqs = re.sub(r"\s+", " ", m.group(1))
    for needle in ("Claude Code", "GitHub Copilot CLI", "PowerShell 7",
                   "Git for Windows", "bash"):
        assert needle in prereqs, f"the prerequisites no longer name {needle!r}"
    assert "Claude Code plugin" not in text, (
        "agents.txt again calls LOCI a Claude Code plugin; it serves two hosts")
