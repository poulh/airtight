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
    "statement_kinds": ["level", "guarded_by", "requires_link", "writable", "document_section"],
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
