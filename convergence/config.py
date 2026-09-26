"""Loading and validating pipeline.yaml — the single source of truth.

Everything the tools enforce is declared in that file: the phases, the
thresholds, the duties, and the rules carried on each vocabulary value. This
module knows the *shape* of the file; it holds none of its content.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "pipeline.yaml"
DEFAULT_SCHEMA = ROOT / "schema.sql"

HUMAN_ID = 1

# vocabulary name -> the attribute columns its values carry, beyond
# value/description/seq. These are the rules that belong to each value.
VOCABULARIES = {
    "concern_kinds": ["must_address", "via_tool", "about"],
    "concern_statuses": [],
    "answer_kinds": ["requires", "document_section"],
    "requirement_kinds": ["decided_by", "document_section"],
    "requirement_statuses": ["in_document", "document_section"],
    "cost_flags": ["set_by"],
    "link_relations": [],
    "deliverable_statuses": ["in_document"],
    "milestone_statuses": ["in_document"],
}

BOOL_ATTRS = {"in_document"}

# attributes that must name a role in the roster
ROLE_ATTRS = {"must_address", "decided_by", "set_by"}

# attributes that may not be left out
REQUIRED_ATTRS = {"requirement_kinds": ["decided_by"], "concern_kinds": ["about"]}

# attributes whose values the tools interpret, and the values they understand
ENUM_ATTRS = {"about": ("none", "requirement", "concern", "any")}

# top-level vocabularies that are plain value/description lists
SIMPLE_LISTS = {"duties": "duties", "duty_relations": "duty_relations"}

DUTY_RELATIONS = ("owns", "rules_on", "reviews", "checks")

AGENT_REQUIRED = ("id", "name", "role", "motivation", "phases", "joins_at_phase")
AGENT_FIELDS = AGENT_REQUIRED + (
    "charter", "join_trigger", "join_rationale", "auto_staff", "active", "joined_round",
) + DUTY_RELATIONS


def load(path):
    with open(path) as f:
        config = yaml.safe_load(f)
    if not isinstance(config, dict):
        raise ValueError(f"{path}: expected a mapping at the top level")
    return config


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
    relation_values = _entries(problems, config.get("duty_relations"), "duty_relations")
    for missing in sorted(set(DUTY_RELATIONS) - relation_values):
        problems.append(f"duty_relations: '{missing}' is missing")

    # ---- vocabularies
    for name in sorted(set(VOCABULARIES) - set(vocabs)):
        problems.append(f"vocabularies: '{name}' is missing")
    for name in sorted(set(vocabs) - set(VOCABULARIES)):
        problems.append(f"vocabularies: '{name}' is not a vocabulary this schema uses")

    role_refs = []   # (where, role) to check against the roster below
    for name in sorted(set(vocabs) & set(VOCABULARIES)):
        attrs = VOCABULARIES[name]
        _entries(problems, vocabs[name], f"vocabularies.{name}", attrs,
                 REQUIRED_ATTRS.get(name, ()))
        for entry in vocabs[name] or []:
            if not isinstance(entry, dict):
                continue
            for attr in attrs:
                if attr in ENUM_ATTRS and entry.get(attr) not in (None, *ENUM_ATTRS[attr]):
                    problems.append(f"vocabularies.{name}.{entry.get('value')}.{attr}: "
                                    f"'{entry[attr]}' is not one of {', '.join(ENUM_ATTRS[attr])}")
                if attr in ROLE_ATTRS and entry.get(attr):
                    role_refs.append((f"vocabularies.{name}.{entry.get('value')}.{attr}",
                                      entry[attr]))

    # ---- agents
    if not agents:
        problems.append("agents: no agents defined")
    ids, names, roles = set(), set(), set()
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

    return problems
