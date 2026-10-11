"""Keep the plugin's version moving with its contents.

Claude Code caches an installed plugin by version, so a change shipped under the same
version never reaches anyone who already has it. Every commit that touches the plugin must
therefore change plugins/airtight/.claude-plugin/plugin.json's version.

    python3 scripts/plugin_version.py bump-if-needed   the pre-commit hook: bumps the patch
                                                       version and stages it, if needed
    python3 scripts/plugin_version.py check BASE       CI: fails if BASE..HEAD changed the
                                                       plugin but not its version
"""

import json
import subprocess
import sys

PLUGIN = "plugins/airtight"
MANIFEST = f"{PLUGIN}/.claude-plugin/plugin.json"
IGNORED = (":(exclude)*.egg-info/*", ":(exclude)*__pycache__*")


def git(*args, check=True):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=check).stdout


def version_at(ref):
    """The version in the manifest at a ref ('' for the index), or None if it has none."""
    try:
        return json.loads(git("show", f"{ref}:{MANIFEST}"))["version"]
    except (subprocess.CalledProcessError, ValueError, KeyError):
        return None


def plugin_changed(*diff_args):
    out = git("diff", "--name-only", *diff_args, "--", PLUGIN, *IGNORED)
    return [p for p in out.splitlines() if p and p != MANIFEST]


def bump_if_needed():
    changed = plugin_changed("--cached")
    if not changed:
        return 0
    staged, head = version_at(""), version_at("HEAD")
    if staged != head:
        return 0   # already bumped by hand
    if git("diff", "--name-only", "--", MANIFEST).strip():
        print(f"pre-commit: {MANIFEST} has unstaged edits; stage or drop them so the version "
              "can be bumped", file=sys.stderr)
        return 1
    major, minor, patch = (int(x) for x in staged.split("."))
    new = f"{major}.{minor}.{patch + 1}"
    with open(MANIFEST) as f:
        text = f.read()
    with open(MANIFEST, "w") as f:
        f.write(text.replace(f'"version": "{staged}"', f'"version": "{new}"', 1))
    git("add", MANIFEST)
    print(f"pre-commit: the plugin changed ({len(changed)} file(s)); version {staged} -> {new}")
    return 0


def check(base):
    changed = plugin_changed(f"{base}...HEAD")
    if changed and version_at(base) == version_at("HEAD"):
        print(f"{len(changed)} plugin file(s) changed since {base} but {MANIFEST} still says "
              f"{version_at('HEAD')}. Bump it, or run ./setup.sh so the pre-commit hook does.")
        for path in changed[:20]:
            print(f"  {path}")
        return 1
    print(f"plugin version: {version_at(base)} -> {version_at('HEAD')}"
          + ("" if changed else " (plugin unchanged)"))
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["bump-if-needed"]:
        sys.exit(bump_if_needed())
    if sys.argv[1:2] == ["check"] and len(sys.argv) == 3:
        sys.exit(check(sys.argv[2]))
    sys.exit(__doc__)
