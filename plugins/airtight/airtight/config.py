"""Loading and validating pipeline.yaml — the single source of truth.

Everything the tools enforce is declared in that file: the phases, the
thresholds, the duties, and the rules carried on each vocabulary value. This
module knows the *shape* of the file; it holds none of its content.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent          # the plugin root
DEFAULT_CONFIG = ROOT / "defaults" / "pipeline.yaml"   # copied into each project by setup
DEFAULT_SCHEMA = ROOT / "defaults" / "schema.sql"      # code, not config: never copied
PROJECT_DIR = Path(".airtight")
PROJECT_CONFIG = PROJECT_DIR / "pipeline.yaml"


def config_path():
    """The project's own copy of pipeline.yaml once setup has made one, else the default."""
    return PROJECT_CONFIG if PROJECT_CONFIG.exists() else DEFAULT_CONFIG


def plugin_version():
    """The installed plugin's version, from its manifest."""
    import json
    try:
        return json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"]
    except (OSError, ValueError, KeyError):
        return "unknown"

HUMAN_ID = 1

# vocabulary name -> the attribute columns its values carry, beyond
# value/description/seq. These are the rules that belong to each value.
VOCABULARIES = {
    "statement_kinds": ["label", "level", "guarded_by", "requires_link", "writable",
                        "document_section"],
    "statement_statuses": ["live"],
    "link_relations": ["from_kinds", "to_kinds"],
    "concern_kinds": ["must_address", "via_tool"],
    "concern_statuses": [],
    "answer_kinds": ["judged"],
    "verdicts": ["closes", "needs_reply", "human_only"],
    "concern_outcomes": [],
    "deliverable_statuses": ["live"],
    "milestone_statuses": [],
    "turn_statuses": [],
    "project_statuses": [],
}

BOOL_ATTRS = {"live", "writable", "judged", "closes", "needs_reply", "human_only"}

# attributes that must name a role in the roster
ROLE_ATTRS = {"must_address", "guarded_by"}

# attributes that may not be left out
REQUIRED_ATTRS = {"statement_kinds": ["level"]}

# attributes whose values the tools interpret, and the values they understand
ENUM_ATTRS = {"level": ("project", "deliverable")}

# Values the tools refer to by name. Renaming one in the YAML must be matched
# in the code, so the validator says so instead of letting a tool fail later.
REQUIRED_VALUES = {
    "statement_statuses": ["pending", "agreed", "superseded", "cancelled"],
    "link_relations": ["supersedes"],
    "concern_statuses": ["open", "closed"],
    "answer_kinds": ["answer", "reassign"],
    "concern_outcomes": ["changed", "kept"],
    "deliverable_statuses": ["live", "superseded", "cancelled"],
    "milestone_statuses": ["planned", "building", "blocked", "in_review", "merged", "accepted"],
    "turn_statuses": ["running", "done", "skipped"],
    "project_statuses": ["running", "paused", "converged", "done"],
}

# top-level vocabularies that are plain value/description lists
SIMPLE_LISTS = {"duties": "duties", "duty_relations": "duty_relations"}

DUTY_RELATIONS = ("owns", "rules_on", "reviews", "checks")

AGENT_REQUIRED = ("id", "name", "role", "motivation", "phases", "joins_at_phase")
AGENT_FIELDS = AGENT_REQUIRED + (
    "charter", "seq", "approves", "join_trigger", "join_rationale", "auto_staff", "active",
    "joined_round",
) + DUTY_RELATIONS


def set_policy(path, key, value):
    """Change one policy value in a pipeline.yaml in place, keeping its comments."""
    import re
    text = Path(path).read_text()
    pattern = re.compile(rf"(- key: {re.escape(key)}\n\s+value: )\S+")
    if pattern.search(text):
        Path(path).write_text(pattern.sub(lambda m: m.group(1) + str(value), text, count=1))
        return
    # an older copy without the key: add it at the top of the policy list
    entry = (f"  - key: {key}\n    value: {value}\n    description: >\n"
             f"      Added by airtight; see the plugin's default pipeline.yaml.\n")
    Path(path).write_text(text.replace("policy:\n", "policy:\n" + entry, 1))


# ------------------------------------------------------------------ upgrades
#
# A project keeps its own copy of pipeline.yaml, so a newer plugin's rules never reach it
# on their own. merge_defaults() brings in what the plugin added since the copy was made,
# and never changes a value the project already has.

IDENTITY = ("id", "number", "key", "value")


def _identity(item):
    if isinstance(item, dict):
        for key in IDENTITY:
            if key in item:
                return key
    return None


def _label(path, item=None):
    key = _identity(item) if item is not None else None
    return f"{path}: {item[key]}" if key else path


def _merge(project, default, path, added, new_duties):
    if isinstance(project, dict) and isinstance(default, dict):
        for key, value in default.items():
            where = f"{path}.{key}" if path else key
            if key not in project:
                project[key] = value
                added.append(where)
            elif key in DUTY_RELATIONS and isinstance(value, list) and isinstance(project[key], list):
                # A duty the plugin newly declared goes to whoever holds it by default; a duty
                # the project already knew stays wherever the project put it.
                for duty in value:
                    if duty in new_duties and duty not in project[key]:
                        project[key].append(duty)
                        added.append(f"{where}: {duty}")
            else:
                _merge(project[key], value, where, added, new_duties)
    elif isinstance(project, list) and isinstance(default, list):
        key = next((_identity(d) for d in default if _identity(d)), None)
        if key is None:
            return
        mine = {item.get(key): item for item in project if isinstance(item, dict)}
        position = 0
        for item in default:
            ident = item.get(key) if isinstance(item, dict) else None
            if ident in mine:
                _merge(mine[ident], item, _label(path, item), added, new_duties)
                position = project.index(mine[ident]) + 1
            else:
                # Keep the default's order: seq comes from position in the list.
                project.insert(position, item)
                position += 1
                added.append(_label(path, item))


def merge_defaults(project, default):
    """The project's config with everything the default has and it lacks. Returns (merged, added)."""
    import copy
    merged = copy.deepcopy(project)
    known = {d.get("value") for d in project.get("duties") or []}
    new_duties = {d.get("value") for d in default.get("duties") or []} - known
    added = []
    _merge(merged, copy.deepcopy(default), "", added, new_duties)
    return merged, added


def dump(config, header):
    """pipeline.yaml as text: block mappings, short lists inline, long text folded."""

    class Dumper(yaml.SafeDumper):
        pass

    def text(dumper, value):
        style = ">" if len(value) > 80 and "\n" not in value.rstrip("\n") else None
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)

    def seq(dumper, value):
        flow = all(not isinstance(v, (dict, list)) for v in value)
        return dumper.represent_sequence("tag:yaml.org,2002:seq", value, flow_style=flow)

    Dumper.add_representer(str, text)
    Dumper.add_representer(list, seq)
    body = yaml.dump(config, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=92,
                     indent=2)
    return header + body


def tools():
    """The at-* tools the plugin puts on PATH, from its bin/ directory."""
    return {p.name for p in (ROOT / "bin").iterdir() if p.name.startswith("at-")}


def load(path):
    with open(path) as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return config


def kinds_list(text):
    """A comma-separated kinds attribute as a list; empty means any."""
    return [k.strip() for k in (text or "").split(",") if k.strip()]


def _entries(problems, entries, where, attrs=(), required=()):
    """Validate a list of {value, description, ...} and return the values seen."""
    seen = set()
    if not entries:
        problems.append(f"{where}: no values")
        return seen
    for i, entry in enumerate(entries):
        at = f"{where}[{i}]"
        if not isinstance(entry, dict):
            problems.append(f"{at}: expected 'value' and 'description'")
            continue
        value = entry.get("value")
        if not value:
            problems.append(f"{at}: no value")
        elif value in seen:
            problems.append(f"{at}: '{value}' is listed twice")
        else:
            seen.add(value)
        if not entry.get("description"):
            problems.append(f"{at}: '{value}' has no description")
        for field in entry:
            if field not in ("value", "description", *attrs):
                problems.append(f"{at}: '{value}' has unknown field '{field}'")
        for field in required:
            if entry.get(field) in (None, ""):
                problems.append(f"{at}: '{value}' needs '{field}'")
    return seen


def validate(config):
    """Return a list of problems; empty means the config is usable."""
    problems = []
    phases = config.get("phases") or []
    policy = config.get("policy") or []
    vocabs = config.get("vocabularies") or {}
    agents = config.get("agents") or []

    # ---- phases
    numbers = []
    for i, phase in enumerate(phases):
        where = f"phases[{i}]"
        if not isinstance(phase, dict):
            problems.append(f"{where}: expected a mapping")
            continue
        for field in ("number", "key", "name", "ends_when"):
            if not phase.get(field):
                problems.append(f"{where}: '{field}' is required")
        for field in phase:
            if field not in ("number", "key", "name", "ends_when", "needs"):
                problems.append(f"{where}: unknown field '{field}'")
        if not isinstance(phase.get("needs") or [], list):
            problems.append(f"{where}: 'needs' must be a list of duties")
        if phase.get("number") in numbers:
            problems.append(f"{where}: number {phase.get('number')} is used twice")
        numbers.append(phase.get("number"))
    if not phases:
        problems.append("phases: none defined")
    elif sorted(n for n in numbers if isinstance(n, int)) != list(range(1, len(numbers) + 1)):
        problems.append("phases: numbers must run 1..n with no gaps")

    # ---- policy
    keys = set()
    for i, item in enumerate(policy):
        where = f"policy[{i}]"
        if not isinstance(item, dict):
            problems.append(f"{where}: expected a mapping")
            continue
        if not item.get("key"):
            problems.append(f"{where}: 'key' is required")
        elif item["key"] in keys:
            problems.append(f"{where}: '{item['key']}' is listed twice")
        else:
            keys.add(item["key"])
        if item.get("value") is None:
            problems.append(f"{where}: '{item.get('key')}' has no value")
        if not item.get("description"):
            problems.append(f"{where}: '{item.get('key')}' has no description")

    # ---- duties and duty relations
    duty_values = _entries(problems, config.get("duties"), "duties")
    for phase in phases:
        if isinstance(phase, dict) and isinstance(phase.get("needs"), list):
            for duty in phase["needs"]:
                if duty not in duty_values:
                    problems.append(f"phases[{phase.get('number')}].needs: '{duty}' is not a "
                                    "declared duty")
    relation_values = _entries(problems, config.get("duty_relations"), "duty_relations")
    for missing in sorted(set(DUTY_RELATIONS) - relation_values):
        problems.append(f"duty_relations: '{missing}' is missing")

    # ---- vocabularies
    for name in sorted(set(VOCABULARIES) - set(vocabs)):
        problems.append(f"vocabularies: '{name}' is missing")
    for name in sorted(set(vocabs) - set(VOCABULARIES)):
        problems.append(f"vocabularies: '{name}' is not a vocabulary this schema uses")

    values = {}
    role_refs = []   # (where, role) to check against the roster below
    for name in sorted(set(vocabs) & set(VOCABULARIES)):
        attrs = VOCABULARIES[name]
        values[name] = _entries(problems, vocabs[name], f"vocabularies.{name}", attrs,
                                REQUIRED_ATTRS.get(name, ()))
        for missing in REQUIRED_VALUES.get(name, ()):
            if missing not in values[name]:
                problems.append(f"vocabularies.{name}: '{missing}' is required by the tools")
        for entry in vocabs[name] or []:
            if not isinstance(entry, dict):
                continue
            where = f"vocabularies.{name}.{entry.get('value')}"
            for attr in attrs:
                raw = entry.get(attr)
                if attr in ENUM_ATTRS and raw not in (None, *ENUM_ATTRS[attr]):
                    problems.append(f"{where}.{attr}: '{raw}' is not one of "
                                    f"{', '.join(ENUM_ATTRS[attr])}")
                if attr in ROLE_ATTRS and raw:
                    role_refs.append((f"{where}.{attr}", raw))

    # Rules that point from one vocabulary into another
    kinds = values.get("statement_kinds", set())
    relations = values.get("link_relations", set())
    for entry in vocabs.get("statement_kinds") or []:
        link = isinstance(entry, dict) and entry.get("requires_link")
        if link and link not in relations:
            problems.append(f"vocabularies.statement_kinds.{entry.get('value')}.requires_link: "
                            f"'{link}' is not a link relation")
    for entry in vocabs.get("link_relations") or []:
        if not isinstance(entry, dict):
            continue
        for attr in ("from_kinds", "to_kinds"):
            for kind in kinds_list(entry.get(attr)):
                if kind not in kinds:
                    problems.append(f"vocabularies.link_relations.{entry.get('value')}.{attr}: "
                                    f"'{kind}' is not a statement kind")

    # ---- agents
    if not agents:
        problems.append("agents: no agents defined")
    ids, names, roles, seqs = set(), set(), set(), set()
    for i, agent in enumerate(agents):
        where = f"agents[{i}]"
        if not isinstance(agent, dict):
            problems.append(f"{where}: expected a mapping")
            continue
        label = agent.get("name") or where
        for field in AGENT_REQUIRED:
            if not agent.get(field):
                problems.append(f"{label}: '{field}' is required")
        for field in agent:
            if field not in AGENT_FIELDS:
                problems.append(f"{label}: unknown field '{field}'")

        if agent.get("id") in ids:
            problems.append(f"{label}: id {agent.get('id')} is used twice")
        ids.add(agent.get("id"))
        if agent.get("name") in names:
            problems.append(f"{label}: name is used twice")
        names.add(agent.get("name"))
        if agent.get("role") in roles:
            problems.append(f"{label}: role '{agent.get('role')}' is used twice")
        roles.add(agent.get("role"))
        if not isinstance(agent.get("seq"), int):
            problems.append(f"{label}: 'seq' (turn order) must be a number")
        elif agent["seq"] in seqs:
            problems.append(f"{label}: seq {agent['seq']} is used twice")
        seqs.add(agent.get("seq"))

        agent_phases = agent.get("phases") or []
        if not isinstance(agent_phases, list):
            problems.append(f"{label}: 'phases' must be a list of phase numbers")
            agent_phases = []
        for number in agent_phases:
            if number not in numbers:
                problems.append(f"{label}: phase {number} is not a declared phase")
        joins = agent.get("joins_at_phase")
        if joins is not None and joins not in agent_phases:
            problems.append(f"{label}: joins_at_phase {joins} is not in its own phases {agent_phases}")

        for relation in DUTY_RELATIONS:
            for duty in agent.get(relation) or []:
                if duty not in duty_values:
                    problems.append(f"{label}: {relation} '{duty}' is not a declared duty")

        if not agent.get("active") and not agent.get("join_trigger") and agent.get("id") != HUMAN_ID:
            problems.append(f"{label}: needs a join_trigger, or active: true")
        if agent.get("active") and not agent.get("joined_round"):
            problems.append(f"{label}: active agents need a joined_round")

    for where, role in role_refs:
        if role not in roles:
            problems.append(f"{where}: '{role}' is not a role in the roster")

    if HUMAN_ID not in ids:
        problems.append(f"agents: no agent with id {HUMAN_ID} (the human)")
    else:
        human = next(a for a in agents if isinstance(a, dict) and a.get("id") == HUMAN_ID)
        if human.get("role") != "human":
            problems.append(f"agents: id {HUMAN_ID} must be the human (role: human)")
        if human.get("approves"):
            problems.append("agents: the human does not approve statements (approves: false)")

    return problems
