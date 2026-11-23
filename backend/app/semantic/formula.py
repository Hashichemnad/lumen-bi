"""The calculated-field formula language.

Tableau-like syntax:
    [Sales] - [Cost]
    IF [Region] = 'UAE' THEN 'Home' ELSEIF [Region] IN ('KSA', 'QAT') THEN 'GCC' ELSE 'Other' END
    SUM([Profit]) / SUM([Sales])
    DATETRUNC('month', [Order Date])
    [Amount] * @Exchange Rate        (@name refers to a parameter; spaces allowed up to an operator)

A formula is tokenised, parsed into a tree, type-checked and compiled to DuckDB SQL. The SQL is built only
from the tree: field references become quoted column expressions supplied by the caller, literals are
escaped, and only whitelisted functions exist. User text is never pasted into SQL.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

NUMERIC = {"integer", "number"}
TEMPORAL = {"date", "datetime"}
DATE_PARTS = {"year", "quarter", "month", "week", "day", "hour", "minute", "second"}


class FormulaError(Exception):
    def __init__(self, message: str, pos: int | None = None):
        super().__init__(message)
        self.message = message
        self.pos = pos


# ===================================================================== tokens
@dataclass
class Tok:
    kind: str  # num str field param date ident op eof
    value: Any
    pos: int


TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<comment>//[^\n]*)
  | (?P<num>\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?
  | (?P<op><=|>=|==|!=|<>|[-+*/%=<>(),])
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.VERBOSE,
)


def tokenize(text: str) -> list[Tok]:
    tokens: list[Tok] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "'\"":  # string, doubled quote escapes it
            j, buf = i + 1, []
            while True:
                if j >= n:
                    raise FormulaError("Text is missing its closing quote", i)
                if text[j] == ch:
                    if j + 1 < n and text[j + 1] == ch:
                        buf.append(ch)
                        j += 2
                        continue
                    break
                buf.append(text[j])
                j += 1
            tokens.append(Tok("str", "".join(buf), i))
            i = j + 1
            continue
        if ch == "[":  # field reference, ]] escapes ]
            j, buf = i + 1, []
            while True:
                if j >= n:
                    raise FormulaError("A field name is missing its closing ]", i)
                if text[j] == "]":
                    if j + 1 < n and text[j + 1] == "]":
                        buf.append("]")
                        j += 2
                        continue
                    break
                buf.append(text[j])
                j += 1
            name = "".join(buf).strip()
            if not name:
                raise FormulaError("Empty field name []", i)
            tokens.append(Tok("field", name, i))
            i = j + 1
            continue
        if ch == "#":  # date literal #2026-01-31# or #2026-01-31 08:00#
            j = text.find("#", i + 1)
            if j < 0:
                raise FormulaError("A date is missing its closing #", i)
            raw = text[i + 1 : j].strip()
            try:
                value: date | datetime = (
                    datetime.fromisoformat(raw) if (" " in raw or "T" in raw) else date.fromisoformat(raw)
                )
            except ValueError as exc:
                raise FormulaError(f"Not a valid date: #{raw}# (use #2026-01-31#)", i) from exc
            tokens.append(Tok("date", value, i))
            i = j + 1
            continue
        if ch == "@":  # parameter: @Name or @[Name with spaces]
            if i + 1 < n and text[i + 1] == "[":
                j = text.find("]", i + 2)
                if j < 0:
                    raise FormulaError("A parameter name is missing its closing ]", i)
                tokens.append(Tok("param", text[i + 2 : j].strip(), i))
                i = j + 1
            else:
                m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[i + 1 :])
                if not m:
                    raise FormulaError("Expected a parameter name after @", i)
                tokens.append(Tok("param", m.group(0), i))
                i += 1 + len(m.group(0))
            continue
        m = TOKEN_RE.match(text, i)
        if not m:
            raise FormulaError(f"Unexpected character {ch!r}", i)
        kind = m.lastgroup
        if kind == "num":
            raw = m.group(0)
            tokens.append(Tok("num", float(raw) if any(c in raw for c in ".eE") else int(raw), i))
        elif kind == "op":
            tokens.append(Tok("op", m.group(0), i))
        elif kind == "ident":
            tokens.append(Tok("ident", m.group(0), i))
        i = m.end()
    tokens.append(Tok("eof", None, n))
    return tokens


# ===================================================================== syntax tree
@dataclass
class Node:
    pos: int


@dataclass
class Lit(Node):
    value: Any  # int float str bool None date datetime


@dataclass
class FieldRef(Node):
    name: str


@dataclass
class ParamRef(Node):
    name: str


@dataclass
class Unary(Node):
    op: str
    operand: Node


@dataclass
class Binary(Node):
    op: str
    left: Node
    right: Node


@dataclass
class Call(Node):
    name: str
    args: list[Node]


@dataclass
class IfExpr(Node):
    branches: list[tuple[Node, Node]]
    otherwise: Node | None


@dataclass
class CaseExpr(Node):
    subject: Node
    whens: list[tuple[Node, Node]]
    otherwise: Node | None


@dataclass
class InExpr(Node):
    subject: Node
    items: list[Node]
    negate: bool


@dataclass
class IsNull(Node):
    subject: Node
    negate: bool


KEYWORDS = {
    "IF",
    "THEN",
    "ELSEIF",
    "ELSE",
    "END",
    "CASE",
    "WHEN",
    "AND",
    "OR",
    "NOT",
    "IN",
    "IS",
    "NULL",
    "TRUE",
    "FALSE",
}


class Parser:
    def __init__(self, text: str):
        self.toks = tokenize(text)
        self.i = 0

    @property
    def tok(self) -> Tok:
        return self.toks[self.i]

    def kw(self, word: str) -> bool:
        return self.tok.kind == "ident" and self.tok.value.upper() == word

    def op(self, value: str) -> bool:
        return self.tok.kind == "op" and self.tok.value == value

    def advance(self) -> Tok:
        t = self.tok
        self.i += 1
        return t

    def expect_kw(self, word: str) -> None:
        if not self.kw(word):
            raise FormulaError(f"Expected {word}", self.tok.pos)
        self.advance()

    def expect_op(self, value: str) -> None:
        if not self.op(value):
            found = "the end of the formula" if self.tok.kind == "eof" else repr(str(self.tok.value))
            raise FormulaError(f"Expected '{value}' but found {found}", self.tok.pos)
        self.advance()

    def parse(self) -> Node:
        if self.tok.kind == "eof":
            raise FormulaError("The formula is empty", 0)
        node = self.or_()
        if self.tok.kind != "eof":
            raise FormulaError(f"Unexpected {str(self.tok.value)!r}", self.tok.pos)
        return node

    def or_(self) -> Node:
        node = self.and_()
        while self.kw("OR"):
            pos = self.advance().pos
            node = Binary(pos, "OR", node, self.and_())
        return node

    def and_(self) -> Node:
        node = self.not_()
        while self.kw("AND"):
            pos = self.advance().pos
            node = Binary(pos, "AND", node, self.not_())
        return node

    def not_(self) -> Node:
        if self.kw("NOT"):
            pos = self.advance().pos
            return Unary(pos, "NOT", self.not_())
        return self.comparison()

    def comparison(self) -> Node:
        node = self.additive()
        pos = self.tok.pos
        if self.tok.kind == "op" and self.tok.value in {"=", "==", "!=", "<>", "<", "<=", ">", ">="}:
            op = self.advance().value
            op = {"==": "=", "!=": "<>"}.get(op, op)
            return Binary(pos, op, node, self.additive())
        negate = False
        if (
            self.kw("NOT")
            and self.toks[self.i + 1].kind == "ident"
            and self.toks[self.i + 1].value.upper() == "IN"
        ):
            self.advance()
            negate = True
        if self.kw("IN"):
            self.advance()
            self.expect_op("(")
            items = [self.additive()]
            while self.op(","):
                self.advance()
                items.append(self.additive())
            self.expect_op(")")
            return InExpr(pos, node, items, negate)
        if self.kw("IS"):
            self.advance()
            neg = False
            if self.kw("NOT"):
                self.advance()
                neg = True
            self.expect_kw("NULL")
            return IsNull(pos, node, neg)
        return node

    def additive(self) -> Node:
        node = self.term()
        while self.op("+") or self.op("-"):
            t = self.advance()
            node = Binary(t.pos, t.value, node, self.term())
        return node

    def term(self) -> Node:
        node = self.unary()
        while self.op("*") or self.op("/") or self.op("%"):
            t = self.advance()
            node = Binary(t.pos, t.value, node, self.unary())
        return node

    def unary(self) -> Node:
        if self.op("-"):
            pos = self.advance().pos
            return Unary(pos, "-", self.unary())
        if self.op("+"):
            self.advance()
            return self.unary()
        return self.primary()

    def primary(self) -> Node:
        t = self.tok
        if t.kind in {"num", "str", "date"}:
            self.advance()
            return Lit(t.pos, t.value)
        if t.kind == "field":
            self.advance()
            return FieldRef(t.pos, t.value)
        if t.kind == "param":
            self.advance()
            return ParamRef(t.pos, t.value)
        if self.op("("):
            self.advance()
            node = self.or_()
            self.expect_op(")")
            return node
        if t.kind == "ident":
            word = t.value.upper()
            if word in {"TRUE", "FALSE"}:
                self.advance()
                return Lit(t.pos, word == "TRUE")
            if word == "NULL":
                self.advance()
                return Lit(t.pos, None)
            if word == "IF":
                return self.if_()
            if word == "CASE":
                return self.case_()
            if word in KEYWORDS:
                raise FormulaError(f"Unexpected {word}", t.pos)
            self.advance()
            if not self.op("("):
                raise FormulaError(
                    f"Unknown name {t.value!r}. Put field names in brackets, e.g. [{t.value}]", t.pos
                )
            self.advance()
            args: list[Node] = []
            if not self.op(")"):
                args.append(self.or_())
                while self.op(","):
                    self.advance()
                    args.append(self.or_())
            self.expect_op(")")
            return Call(t.pos, word, args)
        if t.kind == "eof":
            raise FormulaError("The formula ends too early", t.pos)
        raise FormulaError(f"Unexpected {str(t.value)!r}", t.pos)

    def if_(self) -> Node:
        pos = self.advance().pos
        branches = []
        cond = self.or_()
        self.expect_kw("THEN")
        branches.append((cond, self.or_()))
        otherwise = None
        while True:
            if self.kw("ELSEIF"):
                self.advance()
                cond = self.or_()
                self.expect_kw("THEN")
                branches.append((cond, self.or_()))
            elif self.kw("ELSE"):
                self.advance()
                otherwise = self.or_()
                self.expect_kw("END")
                break
            elif self.kw("END"):
                self.advance()
                break
            else:
                raise FormulaError("Expected ELSEIF, ELSE or END to close the IF", self.tok.pos)
        return IfExpr(pos, branches, otherwise)

    def case_(self) -> Node:
        pos = self.advance().pos
        subject = self.or_()
        whens = []
        while self.kw("WHEN"):
            self.advance()
            value = self.or_()
            self.expect_kw("THEN")
            whens.append((value, self.or_()))
        if not whens:
            raise FormulaError("CASE needs at least one WHEN … THEN", self.tok.pos)
        otherwise = None
        if self.kw("ELSE"):
            self.advance()
            otherwise = self.or_()
        self.expect_kw("END")
        return CaseExpr(pos, subject, whens, otherwise)


def parse(text: str) -> Node:
    return Parser(text).parse()


# ===================================================================== compiling
@dataclass
class Compiled:
    sql: str
    type: str  # integer number string boolean date datetime null
    level: str = "row"  # row | agg | const


@dataclass
class FieldSource:
    """What the compiler needs to know about a referenceable field."""

    name: str
    type: str
    sql: str | None = None  # expression for a column, e.g. "t"."Amount"
    formula: str | None = None  # for calculated fields (compiled on demand)
    level: str = "row"
    custom_sql: Callable[[], Compiled] | None = None  # group/bin fields


@dataclass
class Context:
    fields: dict[str, FieldSource]
    params: dict[str, tuple[str, Any]] = field(default_factory=dict)  # name -> (type, value)
    _cache: dict[str, Compiled] = field(default_factory=dict)
    _visiting: set[str] = field(default_factory=set)

    def lookup(self, name: str) -> FieldSource | None:
        if name in self.fields:
            return self.fields[name]
        lowered = name.lower()
        return next((f for k, f in self.fields.items() if k.lower() == lowered), None)


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FormulaError("Numbers must be finite")
        return repr(value)
    if isinstance(value, datetime):
        return f"TIMESTAMP '{value.isoformat(sep=' ')}'"
    if isinstance(value, date):
        return f"DATE '{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


def literal_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, datetime):
        return "datetime"
    if isinstance(value, date):
        return "date"
    return "string"


def _level(*parts: Compiled, pos: int) -> str:
    levels = {p.level for p in parts} - {"const"}
    if levels == {"row", "agg"}:
        raise FormulaError(
            "This mixes aggregated values (SUM, AVG…) with row values. Aggregate both sides, e.g. "
            "SUM([Profit]) / SUM([Sales])",
            pos,
        )
    return levels.pop() if levels else "const"


def _unify(types: list[str], pos: int, what: str = "All results") -> str:
    real = [t for t in types if t != "null"]
    if not real:
        return "null"
    if all(t in NUMERIC for t in real):
        return "integer" if all(t == "integer" for t in real) else "number"
    if all(t in TEMPORAL for t in real):
        return "date" if all(t == "date" for t in real) else "datetime"
    if len(set(real)) == 1:
        return real[0]
    raise FormulaError(f"{what} must have the same type (found {', '.join(sorted(set(real)))})", pos)


def _comparable(a: str, b: str) -> bool:
    if "null" in {a, b}:
        return True
    return a == b or {a, b} <= NUMERIC or {a, b} <= TEMPORAL


def _shift(d: Compiled, days: Compiled, op: str, level: str) -> Compiled:
    """date ± number of days (stays a date for dates)."""
    expr = f"({d.sql} {op} to_days(CAST({days.sql} AS INTEGER)))"
    if d.type == "date":
        return Compiled(f"CAST({expr} AS DATE)", "date", level)
    return Compiled(expr, d.type, level)


class Compiler:
    def __init__(self, ctx: Context):
        self.ctx = ctx

    # ---- entry
    def compile(self, node: Node) -> Compiled:
        method = getattr(self, f"c_{type(node).__name__}")
        return method(node)

    def field_compiled(self, name: str, pos: int) -> Compiled:
        src = self.ctx.lookup(name)
        if src is None:
            raise FormulaError(f"Unknown field [{name}]", pos)
        key = src.name
        if key in self.ctx._cache:
            return self.ctx._cache[key]
        if src.custom_sql is not None:
            result = src.custom_sql()
        elif src.formula is not None:
            if key in self.ctx._visiting:
                raise FormulaError(f"[{key}] refers to itself (directly or through another field)", pos)
            self.ctx._visiting.add(key)
            try:
                inner = Compiler(self.ctx).compile(parse(src.formula))
            except FormulaError as exc:
                raise FormulaError(f"In [{key}]: {exc.message}", pos) from exc
            finally:
                self.ctx._visiting.discard(key)
            result = Compiled(f"({inner.sql})", inner.type, inner.level)
        else:
            result = Compiled(src.sql or quote_ident(key), src.type, src.level)
        self.ctx._cache[key] = result
        return result

    # ---- leaves
    def c_Lit(self, n: Lit) -> Compiled:
        return Compiled(sql_literal(n.value), literal_type(n.value), "const")

    def c_FieldRef(self, n: FieldRef) -> Compiled:
        return self.field_compiled(n.name, n.pos)

    def c_ParamRef(self, n: ParamRef) -> Compiled:
        match = next((v for k, v in self.ctx.params.items() if k.lower() == n.name.lower()), None)
        if match is None:
            raise FormulaError(f"Unknown parameter @{n.name}", n.pos)
        ptype, value = match
        return Compiled(sql_literal(value), ptype if value is not None else "null", "const")

    # ---- operators
    def c_Unary(self, n: Unary) -> Compiled:
        x = self.compile(n.operand)
        if n.op == "NOT":
            if x.type not in {"boolean", "null"}:
                raise FormulaError("NOT needs a true/false value", n.pos)
            return Compiled(f"(NOT {x.sql})", "boolean", x.level)
        if x.type not in NUMERIC | {"null"}:
            raise FormulaError("Minus needs a number", n.pos)
        return Compiled(f"(-{x.sql})", x.type, x.level)

    def c_Binary(self, n: Binary) -> Compiled:
        a, b = self.compile(n.left), self.compile(n.right)
        level = _level(a, b, pos=n.pos)
        op = n.op
        if op in {"AND", "OR"}:
            for side in (a, b):
                if side.type not in {"boolean", "null"}:
                    raise FormulaError(f"{op} needs true/false values on both sides", n.pos)
            return Compiled(f"({a.sql} {op} {b.sql})", "boolean", level)
        if op in {"=", "<>", "<", "<=", ">", ">="}:
            if not _comparable(a.type, b.type):
                raise FormulaError(f"Cannot compare {a.type} with {b.type}", n.pos)
            return Compiled(f"({a.sql} {op} {b.sql})", "boolean", level)
        if op == "+":
            if a.type == "string" or b.type == "string":
                if {a.type, b.type} - {"string", "null"}:
                    raise FormulaError(
                        "To join text with a number or date, wrap it in STR(), e.g. 'Q' + STR([Quarter])",
                        n.pos,
                    )
                return Compiled(f"({a.sql} || {b.sql})", "string", level)
            if a.type in TEMPORAL and b.type in NUMERIC:
                return _shift(a, b, "+", level)
            if b.type in TEMPORAL and a.type in NUMERIC:
                return _shift(b, a, "+", level)
        if op == "-":
            if a.type in TEMPORAL and b.type in TEMPORAL:
                return Compiled(f"date_diff('day', {b.sql}, {a.sql})", "integer", level)
            if a.type in TEMPORAL and b.type in NUMERIC:
                return _shift(a, b, "-", level)
        for side in (a, b):
            if side.type not in NUMERIC | {"null"}:
                raise FormulaError(f"'{op}' needs numbers (found {side.type})", n.pos)
        if op == "/":  # division by zero gives empty (NULL) instead of an error
            return Compiled(f"(CAST({a.sql} AS DOUBLE) / NULLIF({b.sql}, 0))", "number", level)
        if op == "%":
            return Compiled(f"({a.sql} % NULLIF({b.sql}, 0))", _unify([a.type, b.type], n.pos), level)
        return Compiled(f"({a.sql} {op} {b.sql})", _unify([a.type, b.type], n.pos), level)

    def c_InExpr(self, n: InExpr) -> Compiled:
        x = self.compile(n.subject)
        items = [self.compile(i) for i in n.items]
        for item in items:
            if not _comparable(x.type, item.type):
                raise FormulaError(f"IN list values must match the type of the value ({x.type})", n.pos)
        level = _level(x, *items, pos=n.pos)
        neg = "NOT " if n.negate else ""
        return Compiled(f"({x.sql} {neg}IN ({', '.join(i.sql for i in items)}))", "boolean", level)

    def c_IsNull(self, n: IsNull) -> Compiled:
        x = self.compile(n.subject)
        return Compiled(f"({x.sql} IS {'NOT ' if n.negate else ''}NULL)", "boolean", x.level)

    def c_IfExpr(self, n: IfExpr) -> Compiled:
        parts, results = [], []
        for cond, value in n.branches:
            c, v = self.compile(cond), self.compile(value)
            if c.type not in {"boolean", "null"}:
                raise FormulaError("IF conditions must be true/false, e.g. [Sales] > 100", cond.pos)
            parts.append((c, v))
            results.append(v)
        other = self.compile(n.otherwise) if n.otherwise else None
        all_parts = [p for pair in parts for p in pair] + ([other] if other else [])
        level = _level(*all_parts, pos=n.pos)
        rtype = _unify([r.type for r in results] + ([other.type] if other else []), n.pos, "All IF results")
        sql = "CASE " + " ".join(f"WHEN {c.sql} THEN {v.sql}" for c, v in parts)
        sql += f" ELSE {other.sql} END" if other else " END"
        return Compiled(f"({sql})", rtype, level)

    def c_CaseExpr(self, n: CaseExpr) -> Compiled:
        subject = self.compile(n.subject)
        pairs = [(self.compile(w), self.compile(r)) for w, r in n.whens]
        for w, _ in pairs:
            if not _comparable(subject.type, w.type):
                raise FormulaError(f"WHEN values must match the CASE value type ({subject.type})", n.pos)
        other = self.compile(n.otherwise) if n.otherwise else None
        level = _level(subject, *[p for pair in pairs for p in pair], *([other] if other else []), pos=n.pos)
        rtype = _unify(
            [r.type for _, r in pairs] + ([other.type] if other else []), n.pos, "All CASE results"
        )
        sql = f"CASE {subject.sql} " + " ".join(f"WHEN {w.sql} THEN {r.sql}" for w, r in pairs)
        sql += f" ELSE {other.sql} END" if other else " END"
        return Compiled(f"({sql})", rtype, level)

    # ---- functions
    def c_Call(self, n: Call) -> Compiled:
        spec = FUNCTIONS.get(n.name)
        if spec is None:
            raise FormulaError(
                f"Unknown function {n.name}(). See the function list for what is available.", n.pos
            )
        lo, hi, handler = spec
        if not lo <= len(n.args) <= hi:
            expected = str(lo) if lo == hi else f"{lo} to {hi}"
            raise FormulaError(f"{n.name}() takes {expected} argument(s), got {len(n.args)}", n.pos)
        return handler(self, n)


# ---- function helpers
def _args(c: Compiler, n: Call) -> list[Compiled]:
    return [c.compile(a) for a in n.args]


def _need(arg: Compiled, allowed: set[str], fn: str, pos: int, what: str) -> None:
    if arg.type not in allowed | {"null"}:
        raise FormulaError(f"{fn}() needs {what} (found {arg.type})", pos)


def _part(c: Compiler, n: Call, index: int) -> str:
    node = n.args[index]
    if not (isinstance(node, Lit) and isinstance(node.value, str) and node.value.lower() in DATE_PARTS):
        raise FormulaError(
            f"{n.name}() needs a date part such as 'month' ({', '.join(sorted(DATE_PARTS))})", n.pos
        )
    return node.value.lower()


def _const_number(n: Call, index: int) -> float:
    node = n.args[index]
    if isinstance(node, Unary) and node.op == "-" and isinstance(node.operand, Lit):
        node = Lit(node.pos, -node.operand.value)
    if not (
        isinstance(node, Lit) and isinstance(node.value, int | float) and not isinstance(node.value, bool)
    ):
        raise FormulaError(f"{n.name}() needs a fixed number here", n.pos)
    return node.value


def agg(sql_fn: str, out: str | None = None, numeric: bool = True, distinct: bool = False):
    def handler(c: Compiler, n: Call) -> Compiled:
        (x,) = _args(c, n)
        if x.level == "agg":
            raise FormulaError(f"{n.name}() cannot contain another aggregation", n.pos)
        if numeric:
            _need(x, NUMERIC, n.name, n.pos, "a number")
        rtype = out or x.type
        inner = f"DISTINCT {x.sql}" if distinct else x.sql
        return Compiled(f"{sql_fn}({inner})", rtype, "agg")

    return handler


def row(
    sql_fn: str, out: str | Callable[[list[Compiled]], str], needs: list[set[str] | None], what: str = ""
):
    """A row-level function whose arguments must have the given types (None = any)."""

    def handler(c: Compiler, n: Call) -> Compiled:
        args = _args(c, n)
        for arg, allowed in zip(args, needs, strict=False):
            if allowed is not None:
                _need(arg, allowed, n.name, n.pos, what or "/".join(sorted(allowed)))
        rtype = out(args) if callable(out) else out
        return Compiled(f"{sql_fn}({', '.join(a.sql for a in args)})", rtype, _level(*args, pos=n.pos))

    return handler


def _min_max(fn: str):
    def handler(c: Compiler, n: Call) -> Compiled:
        if len(n.args) == 1:
            (x,) = _args(c, n)
            if x.level == "agg":
                raise FormulaError(f"{n.name}() cannot contain another aggregation", n.pos)
            return Compiled(f"{fn}({x.sql})", x.type, "agg")
        a, b = _args(c, n)
        t = _unify([a.type, b.type], n.pos)
        return Compiled(
            f"{'least' if fn == 'min' else 'greatest'}({a.sql}, {b.sql})", t, _level(a, b, pos=n.pos)
        )

    return handler


def _count(c: Compiler, n: Call) -> Compiled:
    (x,) = _args(c, n)
    if x.level == "agg":
        raise FormulaError("COUNT() cannot contain another aggregation", n.pos)
    return Compiled(f"count({x.sql})", "integer", "agg")


def _countd(c: Compiler, n: Call) -> Compiled:
    (x,) = _args(c, n)
    if x.level == "agg":
        raise FormulaError("COUNTD() cannot contain another aggregation", n.pos)
    return Compiled(f"count(DISTINCT {x.sql})", "integer", "agg")


def _percentile(c: Compiler, n: Call) -> Compiled:
    x = c.compile(n.args[0])
    _need(x, NUMERIC, n.name, n.pos, "a number")
    p = _const_number(n, 1)
    if not 0 <= p <= 1:
        raise FormulaError("PERCENTILE() needs a fraction between 0 and 1, e.g. 0.9", n.pos)
    return Compiled(f"quantile_cont({x.sql}, {p!r})", "number", "agg")


def _round(c: Compiler, n: Call) -> Compiled:
    args = _args(c, n)
    _need(args[0], NUMERIC, n.name, n.pos, "a number")
    digits = int(_const_number(n, 1)) if len(args) == 2 else 0
    return Compiled(f"round({args[0].sql}, {digits})", "integer" if digits <= 0 else "number", args[0].level)


def _log(c: Compiler, n: Call) -> Compiled:
    args = _args(c, n)
    for a in args:
        _need(a, NUMERIC, n.name, n.pos, "numbers")
    if len(args) == 1:
        return Compiled(f"log10({args[0].sql})", "number", args[0].level)
    return Compiled(f"(ln({args[0].sql}) / NULLIF(ln({args[1].sql}), 0))", "number", _level(*args, pos=n.pos))


def _iif(c: Compiler, n: Call) -> Compiled:
    cond, a, b = _args(c, n)
    _need(cond, {"boolean"}, n.name, n.pos, "a true/false condition first")
    t = _unify([a.type, b.type], n.pos, "Both IIF results")
    return Compiled(f"(CASE WHEN {cond.sql} THEN {a.sql} ELSE {b.sql} END)", t, _level(cond, a, b, pos=n.pos))


def _coalesce(c: Compiler, n: Call) -> Compiled:
    args = _args(c, n)
    t = _unify([a.type for a in args], n.pos, f"All {n.name}() values")
    return Compiled(f"coalesce({', '.join(a.sql for a in args)})", t, _level(*args, pos=n.pos))


def _zn(c: Compiler, n: Call) -> Compiled:
    (x,) = _args(c, n)
    _need(x, NUMERIC, n.name, n.pos, "a number")
    return Compiled(f"coalesce({x.sql}, 0)", x.type if x.type != "null" else "integer", x.level)


def _isnull(c: Compiler, n: Call) -> Compiled:
    (x,) = _args(c, n)
    return Compiled(f"({x.sql} IS NULL)", "boolean", x.level)


def _mid(c: Compiler, n: Call) -> Compiled:
    args = _args(c, n)
    _need(args[0], {"string"}, n.name, n.pos, "text")
    for a in args[1:]:
        _need(a, NUMERIC, n.name, n.pos, "numbers for start and length")
    return Compiled(f"substring({', '.join(a.sql for a in args)})", "string", _level(*args, pos=n.pos))


def _str(c: Compiler, n: Call) -> Compiled:
    (x,) = _args(c, n)
    return Compiled(f"CAST({x.sql} AS VARCHAR)", "string", x.level)


def _cast(target: str, out: str):
    def handler(c: Compiler, n: Call) -> Compiled:
        (x,) = _args(c, n)
        if out == "integer" and x.type in NUMERIC:  # INT(3.7) = 3, like Tableau (truncates)
            return Compiled(f"CAST(trunc({x.sql}) AS BIGINT)", "integer", x.level)
        return Compiled(f"TRY_CAST({x.sql} AS {target})", out, x.level)

    return handler


def _date_part_fn(part: str):
    def handler(c: Compiler, n: Call) -> Compiled:
        (x,) = _args(c, n)
        _need(x, TEMPORAL, n.name, n.pos, "a date")
        return Compiled(f"{part}({x.sql})", "integer", x.level)

    return handler


def _datetrunc(c: Compiler, n: Call) -> Compiled:
    part = _part(c, n, 0)
    d = c.compile(n.args[1])
    _need(d, TEMPORAL, n.name, n.pos, "a date")
    if d.type == "date" and part in {"year", "quarter", "month", "week", "day"}:
        return Compiled(f"CAST(date_trunc('{part}', {d.sql}) AS DATE)", "date", d.level)
    return Compiled(f"date_trunc('{part}', {d.sql})", "datetime", d.level)


def _datepart(c: Compiler, n: Call) -> Compiled:
    part = _part(c, n, 0)
    d = c.compile(n.args[1])
    _need(d, TEMPORAL, n.name, n.pos, "a date")
    return Compiled(f"date_part('{part}', {d.sql})", "integer", d.level)


def _dateadd(c: Compiler, n: Call) -> Compiled:
    part = _part(c, n, 0)
    amount, d = c.compile(n.args[1]), c.compile(n.args[2])
    _need(amount, NUMERIC, n.name, n.pos, "a number of units")
    _need(d, TEMPORAL, n.name, n.pos, "a date")
    amt = f"CAST({amount.sql} AS INTEGER)"
    interval = f"to_months(3 * {amt})" if part == "quarter" else f"to_{part}s({amt})"
    expr = f"({d.sql} + {interval})"
    if d.type == "date" and part in {"year", "quarter", "month", "week", "day"}:
        return Compiled(f"CAST({expr} AS DATE)", "date", _level(amount, d, pos=n.pos))
    return Compiled(expr, "datetime", _level(amount, d, pos=n.pos))


def _datediff(c: Compiler, n: Call) -> Compiled:
    part = _part(c, n, 0)
    a, b = c.compile(n.args[1]), c.compile(n.args[2])
    _need(a, TEMPORAL, n.name, n.pos, "dates")
    _need(b, TEMPORAL, n.name, n.pos, "dates")
    return Compiled(f"date_diff('{part}', {a.sql}, {b.sql})", "integer", _level(a, b, pos=n.pos))


def _today(c: Compiler, n: Call) -> Compiled:
    return Compiled("current_date", "date", "const")


def _now(c: Compiler, n: Call) -> Compiled:
    return Compiled("CAST(now() AS TIMESTAMP)", "datetime", "const")


S, N, ANY = {"string"}, NUMERIC, None
FUNCTIONS: dict[str, tuple[int, int, Callable[[Compiler, Call], Compiled]]] = {
    # aggregates
    "SUM": (1, 1, agg("sum")),
    "AVG": (1, 1, agg("avg", "number")),
    "MEDIAN": (1, 1, agg("median", "number")),
    "STDEV": (1, 1, agg("stddev_samp", "number")),
    "VAR": (1, 1, agg("var_samp", "number")),
    "COUNT": (1, 1, _count),
    "COUNTD": (1, 1, _countd),
    "MIN": (1, 2, _min_max("min")),
    "MAX": (1, 2, _min_max("max")),
    "PERCENTILE": (2, 2, _percentile),
    # numbers
    "ABS": (1, 1, row("abs", lambda a: a[0].type, [N], "a number")),
    "ROUND": (1, 2, _round),
    "CEILING": (1, 1, row("ceil", "integer", [N], "a number")),
    "FLOOR": (1, 1, row("floor", "integer", [N], "a number")),
    "SQRT": (1, 1, row("sqrt", "number", [N], "a number")),
    "POWER": (2, 2, row("pow", "number", [N, N], "numbers")),
    "EXP": (1, 1, row("exp", "number", [N], "a number")),
    "LN": (1, 1, row("ln", "number", [N], "a number")),
    "LOG": (1, 2, _log),
    "SIGN": (1, 1, row("sign", "integer", [N], "a number")),
    "DIV": (2, 2, row("divide", "integer", [N, N], "numbers")),
    # logic and empties
    "IIF": (3, 3, _iif),
    "IFNULL": (2, 2, _coalesce),
    "COALESCE": (1, 10, _coalesce),
    "ZN": (1, 1, _zn),
    "ISNULL": (1, 1, _isnull),
    # text
    "LEN": (1, 1, row("length", "integer", [S], "text")),
    "LOWER": (1, 1, row("lower", "string", [S], "text")),
    "UPPER": (1, 1, row("upper", "string", [S], "text")),
    "TRIM": (1, 1, row("trim", "string", [S], "text")),
    "LTRIM": (1, 1, row("ltrim", "string", [S], "text")),
    "RTRIM": (1, 1, row("rtrim", "string", [S], "text")),
    "LEFT": (2, 2, row("left", "string", [S, N], "text and a number")),
    "RIGHT": (2, 2, row("right", "string", [S, N], "text and a number")),
    "MID": (2, 3, _mid),
    "CONTAINS": (2, 2, row("contains", "boolean", [S, S], "text")),
    "STARTSWITH": (2, 2, row("starts_with", "boolean", [S, S], "text")),
    "ENDSWITH": (2, 2, row("ends_with", "boolean", [S, S], "text")),
    "REPLACE": (3, 3, row("replace", "string", [S, S, S], "text")),
    "SPLIT": (3, 3, row("split_part", "string", [S, S, N], "text, a separator and a position")),
    "FIND": (2, 2, row("strpos", "integer", [S, S], "text")),
    "STR": (1, 1, _str),
    # conversion
    "INT": (1, 1, _cast("BIGINT", "integer")),
    "FLOAT": (1, 1, _cast("DOUBLE", "number")),
    "DATE": (1, 1, _cast("DATE", "date")),
    "DATETIME": (1, 1, _cast("TIMESTAMP", "datetime")),
    # dates
    "YEAR": (1, 1, _date_part_fn("year")),
    "QUARTER": (1, 1, _date_part_fn("quarter")),
    "MONTH": (1, 1, _date_part_fn("month")),
    "WEEK": (1, 1, _date_part_fn("week")),
    "DAY": (1, 1, _date_part_fn("day")),
    "WEEKDAY": (1, 1, _date_part_fn("isodow")),
    "DATETRUNC": (2, 2, _datetrunc),
    "DATEPART": (2, 2, _datepart),
    "DATEADD": (3, 3, _dateadd),
    "DATEDIFF": (3, 3, _datediff),
    "MAKEDATE": (3, 3, row("make_date", "date", [N, N, N], "year, month and day numbers")),
    "TODAY": (0, 0, _today),
    "NOW": (0, 0, _now),
}


def referenced_fields(text: str) -> set[str]:
    """Field names a formula refers to (for dependency checks); empty if it does not parse."""
    try:
        return {t.value for t in tokenize(text) if t.kind == "field"}
    except FormulaError:
        return set()


def compile_formula(text: str, ctx: Context) -> Compiled:
    return Compiler(ctx).compile(parse(text))
