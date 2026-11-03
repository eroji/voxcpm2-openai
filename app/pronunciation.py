"""Pronunciation: respellings applied to the text before it is spoken (adapted from
qwen3-tts-openai's Voice Studio).

One rule set serves every language: whichever voice speaks the text says the respelling in
its own language. Rules apply to every synthesis request (``/v1/audio/speech`` and Speak),
never to Design or Clone, whose transcript must match the audio. The rules live in
``DATA_DIR/pronunciation.json``.
"""

from __future__ import annotations

import functools
import json
import logging
import re
import secrets
import threading
from typing import Any

from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.errors import BadRequest

logger = logging.getLogger(__name__)

MAX_RULES = 2000


class PronRule(BaseModel):
    """A respelling: text matching ``match`` is spoken as ``replace``."""

    match: str
    replace: str
    whole_word: bool = False
    case_sensitive: bool = False
    after_number: bool = False  # units: only right after a number ("24 GB", "24GB")


def rule_key(r: PronRule) -> tuple[str, bool, bool]:
    """Two rules are "the same rule" when they match the same text the same way."""
    return (r.match if r.case_sensitive else r.match.casefold(), r.case_sensitive, r.after_number)


# "Word" characters for whole-word edges: Latin letters (with accents), digits, "_".
# Not \w: it includes kana, Hangul and Han, and CJK text has no spaces, so "nginxを使う"
# or "用nginx部署" would never match. A term ends where the Latin script ends.
_W = r"[0-9A-Za-z_\u00C0-\u024F]"


def _rule_pattern(match: str, whole_word: bool, case_sensitive: bool, after_number: bool) -> str:
    m = re.escape(match)
    if after_number:  # a unit: right after a digit, with or without one space between
        core = rf"(?:(?<=\d)|(?<=\d )){m}(?!{_W})"
    elif whole_word:
        # lookarounds, not \b: \b needs a word character at the edge, so it could never
        # match "e.g." or "etc.". The dotted-name guard keeps nginx.conf / foo.nginx intact.
        core = rf"(?<!{_W})(?<!{_W}\.){m}(?!{_W})(?!\.{_W})"
    else:
        core = m
    return core if case_sensitive else f"(?i:{core})"


@functools.lru_cache(maxsize=8)
def _compile(rules: tuple[tuple[str, bool, bool, bool], ...]) -> re.Pattern[str] | None:
    """One alternation, longest match first: at any spot the longest rule wins
    (vLLM-Omni before vLLM), whatever order the rules were written in."""
    order = sorted((i for i, r in enumerate(rules) if r[0]), key=lambda i: -len(rules[i][0]))
    pattern = "|".join(f"(?P<r{i}>{_rule_pattern(*rules[i])})" for i in order)
    return re.compile(pattern) if pattern else None


def apply_pronunciation(text: str, rules: list[PronRule]) -> str:
    """Rewrite text with the respellings, in a single pass.

    Each part of the text is rewritten at most once, so one rule's output is never
    rewritten by another (k8s -> Kubernetes stays "Kubernetes").
    """
    rx = _compile(tuple((r.match, r.whole_word, r.case_sensitive, r.after_number) for r in rules))
    if rx is None:
        return text

    def sub(m: re.Match[str]) -> str:
        r = rules[int(str(m.lastgroup)[1:])]
        if r.after_number and text[m.start() - 1].isdigit():
            return " " + r.replace  # "24GB" -> "24 gigabytes"
        return r.replace

    return rx.sub(sub, text)


def _errs(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in e['loc']) or 'rule'}: {e['msg']}" for e in exc.errors()]


def parse_rule_file(data: Any) -> tuple[list[PronRule], list[str]]:
    """Every shape a rules file comes in -> (rules, errors).

    - {"terms": [...], "units": [...]}: terms are whole words, units only follow a
      number; entries are {"match", "say"} plus optional case_sensitive / whole_word.
      Keys starting with "_" (comments) are ignored. This is also what export writes.
    - a plain list of rules ({"match", "replace" or "say", flags}).
    - a qwen3-tts-openai voices.json / backup ({"pronunciation": ...}), including its
      older per-language form {lang: [rules]}, merged into one set.
    """
    if isinstance(data, dict) and "pronunciation" in data:
        data = data["pronunciation"]
    groups: list[tuple[str, Any, dict[str, bool]]] = []  # (where, entries, defaults)
    if isinstance(data, list):
        groups.append(("rules", data, {}))
    elif isinstance(data, dict) and ("terms" in data or "units" in data):
        groups += [
            ("terms", data.get("terms") or [], {"whole_word": True}),
            ("units", data.get("units") or [], {"after_number": True}),
        ]
        unknown = [k for k in data if not k.startswith("_") and k not in ("terms", "units")]
        if unknown:
            return [], [f"unknown section(s) {unknown}: expected terms and units"]
    elif isinstance(data, dict) and data and all(isinstance(v, list) for v in data.values()):
        groups += [(k, v, {}) for k, v in data.items()]  # old per-language export
    else:
        return [], [
            'the file must hold {"terms": [...], "units": [...]}, a list of rules, or a voices.json'
        ]
    rules: list[PronRule] = []
    errors: list[str] = []
    seen: set[tuple[str, bool, bool]] = set()
    for where, entries, defaults in groups:
        if not isinstance(entries, list):
            errors.append(f"{where}: expected a list")
            continue
        for i, entry in enumerate(entries):
            if not isinstance(entry, dict):
                errors.append(f"{where}[{i}]: expected an object")
                continue
            e = {**defaults, **entry}
            if "say" in e and "replace" not in e:
                e["replace"] = e.pop("say")
            try:
                rule = PronRule.model_validate(e)
            except ValidationError as exc:
                errors.extend(f"{where}[{i}].{m}" for m in _errs(exc))
                continue
            if not rule.match:
                errors.append(f"{where}[{i}]: match is empty")
                continue
            if rule_key(rule) in seen:  # the per-language form repeats rules: first one wins
                continue
            seen.add(rule_key(rule))
            rules.append(rule)
    return rules, errors


def merge_rules(old: list[PronRule], new: list[PronRule]) -> tuple[list[PronRule], int, int]:
    """Imported rules update same-match rules in place (order kept) and append the rest."""
    out = list(old)
    at = {rule_key(r): i for i, r in enumerate(out)}
    added = updated = 0
    for r in new:
        k = rule_key(r)
        if k in at:
            if out[at[k]] != r:
                out[at[k]] = r
                updated += 1
        else:
            at[k] = len(out)
            out.append(r)
            added += 1
    return out, added, updated


def export_rules(rules: list[PronRule]) -> dict[str, Any]:
    """The rules as {"terms", "units"}: the same format import reads, defaults left out."""
    terms: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    for r in rules:
        e: dict[str, Any] = {"match": r.match, "say": r.replace}
        if r.case_sensitive:
            e["case_sensitive"] = True
        if r.after_number:
            units.append(e)
            continue
        if not r.whole_word:
            e["whole_word"] = False
        terms.append(e)
    return {
        "_comment": "Respellings applied to TTS input before synthesis, for every language. "
        "'terms': whole-word matches (case-insensitive unless case_sensitive is true; "
        "\"whole_word\": false matches inside words too). 'units': only after a number. "
        "Longest match wins.",
        "terms": terms,
        "units": units,
    }


class PronunciationStore:
    """The rule set, backed by ``DATA_DIR/pronunciation.json``."""

    def __init__(self, settings: Settings) -> None:
        self.path = settings.data_dir / "pronunciation.json"
        self._lock = threading.RLock()
        self._rules = self._load()

    def _load(self) -> list[PronRule]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            return [PronRule.model_validate(r) for r in raw.get("rules", [])]
        except (OSError, json.JSONDecodeError, ValidationError, AttributeError):
            logger.warning("%s is unreadable; starting with no pronunciation rules", self.path)
            return []

    def _write(self) -> None:
        # unique tmp name, like LanguageStore: two stores may share a data dir (app + tests)
        tmp = self.path.with_name(f"{self.path.name}.{secrets.token_hex(4)}.tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        data = {"rules": [r.model_dump() for r in self._rules]}
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self.path)

    def rules(self) -> list[PronRule]:
        with self._lock:
            return list(self._rules)

    def apply(self, text: str) -> str:
        return apply_pronunciation(text, self.rules())

    def set_rules(self, rules: list[PronRule]) -> list[PronRule]:
        empty = [i + 1 for i, r in enumerate(rules) if not r.match]
        if empty:
            raise BadRequest(
                f"Pronunciation rule {empty[0]} has nothing to match.",
                param="rules",
                code="invalid_pronunciation",
            )
        if len(rules) > MAX_RULES:
            raise BadRequest(
                f"{len(rules)} pronunciation rules; at most {MAX_RULES}.",
                param="rules",
                code="invalid_pronunciation",
            )
        with self._lock:
            self._rules = list(rules)
            self._write()
            return list(self._rules)

    def import_rules(self, data: Any, *, mode: str, dry_run: bool) -> dict[str, Any]:
        """Merge the file's rules into the set, or replace it; ``dry_run`` only reports."""
        if mode not in ("merge", "replace"):
            raise BadRequest("mode must be merge or replace.", param="mode", code="invalid_mode")
        incoming, errors = parse_rule_file(data)
        if errors:
            raise BadRequest(
                "; ".join(errors[:20]), param="rules", code="invalid_pronunciation_file"
            )
        with self._lock:
            old = list(self._rules)
            if mode == "replace":
                new, added, updated = incoming, len(incoming), 0
            else:
                new, added, updated = merge_rules(old, incoming)
            summary = {
                "added": added,
                "updated": updated,
                "removed": len(old) if mode == "replace" else 0,
                "total": len(new),
                "in_file": len(incoming),
            }
            if not dry_run:
                self.set_rules(new)
            return summary
