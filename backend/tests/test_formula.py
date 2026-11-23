from datetime import date

import duckdb
import pytest

from app.semantic.formula import Context, FieldSource, FormulaError, compile_formula, sql_literal

ROWS = """
SELECT * FROM (VALUES
  (1, 'UAE', 100.0, 60.0, 3, DATE '2026-01-15', 'Dubai Mall'),
  (2, 'KSA', 200.0, 150.0, 5, DATE '2026-02-20', 'Riyadh Park'),
  (3, 'QAT', 50.0, 80.0, 1, DATE '2026-02-01', NULL)
) v("Id", "Region", "Sales", "Cost", "Qty", "Order Date", "Store")
"""


@pytest.fixture(scope="module")
def con():
    c = duckdb.connect()
    c.execute(f"CREATE TABLE t AS {ROWS}")
    yield c
    c.close()


def ctx() -> Context:
    fields = {
        n: FieldSource(n, t)
        for n, t in [
            ("Id", "integer"),
            ("Region", "string"),
            ("Sales", "number"),
            ("Cost", "number"),
            ("Qty", "integer"),
            ("Order Date", "date"),
            ("Store", "string"),
        ]
    }
    fields["Profit"] = FieldSource("Profit", "number", formula="[Sales] - [Cost]")
    fields["Margin"] = FieldSource("Margin", "number", formula="SUM([Profit]) / SUM([Sales])")
    return Context(fields=fields, params={"Rate": ("number", 2.0), "Home": ("string", "UAE")})


def run(con, formula: str):
    c = compile_formula(formula, ctx())
    rows = con.execute(
        f'SELECT {c.sql} FROM t ORDER BY "Id"' if c.level != "agg" else f"SELECT {c.sql} FROM t"
    ).fetchall()
    values = [r[0] for r in rows]
    return c, [float(v) if type(v).__name__ == "Decimal" else v for v in values]


@pytest.mark.parametrize(
    "formula,expected,typ",
    [
        ("[Sales] - [Cost]", [40, 50, -30], "number"),
        ("[Profit] * @Rate", [80, 100, -60], "number"),
        ("[Sales] / 0", [None, None, None], "number"),
        ("ROUND([Sales] / 3, 1)", [33.3, 66.7, 16.7], "number"),
        ("[Qty] % 2", [1, 1, 1], "integer"),
        ("-[Qty]", [-3, -5, -1], "integer"),
        (
            "IF [Region] = @Home THEN 'Home' ELSEIF [Region] IN ('KSA', 'QAT') THEN 'GCC' ELSE 'Other' END",
            ["Home", "GCC", "GCC"],
            "string",
        ),
        ("CASE [Region] WHEN 'UAE' THEN 1 WHEN 'KSA' THEN 2 ELSE 0 END", [1, 2, 0], "integer"),
        ("IIF([Sales] > 99, 'big', 'small')", ["big", "big", "small"], "string"),
        ("[Region] NOT IN ('UAE')", [False, True, True], "boolean"),
        ("[Store] IS NULL", [False, False, True], "boolean"),
        ("ISNULL([Store]) OR [Qty] > 4", [False, True, True], "boolean"),
        ("IFNULL([Store], 'Online')", ["Dubai Mall", "Riyadh Park", "Online"], "string"),
        ("ZN(NULL) + [Qty]", [3, 5, 1], "integer"),
        ("UPPER(LEFT([Region], 2)) + '-' + STR([Id])", ["UA-1", "KS-2", "QA-3"], "string"),
        ("LEN([Store])", [10, 11, None], "integer"),
        ("CONTAINS([Store], 'Mall')", [True, False, None], "boolean"),
        ("SPLIT([Store], ' ', 1)", ["Dubai", "Riyadh", None], "string"),
        ("REPLACE([Region], 'A', 'a')", ["UaE", "KSa", "QaT"], "string"),
        ("YEAR([Order Date]) * 100 + MONTH([Order Date])", [202601, 202602, 202602], "integer"),
        ("DATETRUNC('month', [Order Date])", [date(2026, 1, 1), date(2026, 2, 1), date(2026, 2, 1)], "date"),
        (
            "DATEADD('month', 1, [Order Date])",
            [date(2026, 2, 15), date(2026, 3, 20), date(2026, 3, 1)],
            "date",
        ),
        ("[Order Date] + 10", [date(2026, 1, 25), date(2026, 3, 2), date(2026, 2, 11)], "date"),
        ("DATEDIFF('day', [Order Date], #2026-03-01#)", [45, 9, 28], "integer"),
        ("#2026-03-01# - [Order Date]", [45, 9, 28], "integer"),
        ("MAX([Sales], [Cost])", [100, 200, 80], "number"),
        ("INT(3.9) + FLOAT('2.5')", [5.5, 5.5, 5.5], "number"),
        ("SUM([Sales])", [350], "number"),
        ("SUM([Profit]) / SUM([Sales])", [pytest.approx(60 / 350)], "number"),
        ("[Margin] * 100", [pytest.approx(6000 / 350)], "number"),
        ("COUNTD([Region]) + COUNT([Store])", [5], "integer"),
        ("AVG([Qty])", [3], "number"),
        ("MEDIAN([Sales])", [100], "number"),
        ("PERCENTILE([Sales], 0.5)", [100], "number"),
        ("MIN([Order Date])", [date(2026, 1, 15)], "date"),
    ],
)
def test_formulas_evaluate(con, formula, expected, typ):
    c, values = run(con, formula)
    assert c.type == typ
    assert values == expected


@pytest.mark.parametrize(
    "formula,fragment",
    [
        ("[Sales] + 'x'", "STR()"),
        ("SUM([Sales]) + [Cost]", "mixes aggregated"),
        ("SUM(SUM([Sales]))", "another aggregation"),
        ("[Nope]", "Unknown field [Nope]"),
        ("Sales + 1", "brackets"),
        ("IF [Sales] THEN 1 END", "true/false"),
        ("IF [Sales] > 1 THEN 'a' ELSE 2 END", "same type"),
        ("[Region] = 5", "Cannot compare"),
        ("LEN([Sales])", "needs text"),
        ("DATETRUNC('fortnight', [Order Date])", "date part"),
        ("DATETRUNC([Region], [Order Date])", "date part"),
        ("PERCENTILE([Sales], 2)", "between 0 and 1"),
        ("ROUND([Sales], [Qty])", "fixed number"),
        ("FOO(1)", "Unknown function"),
        ("EXEC('drop')", "Unknown function"),
        ("'unterminated", "closing quote"),
        ("[Sales] +", "ends too early"),
        ("(1 + 2", "Expected ')'"),
        ("1 2", "Unexpected"),
        ("@Missing", "Unknown parameter"),
        ("#2026-13-45#", "Not a valid date"),
        ("", "empty"),
    ],
)
def test_formula_errors(formula, fragment):
    with pytest.raises(FormulaError) as exc:
        compile_formula(formula, ctx())
    assert fragment in exc.value.message


def test_error_positions_point_at_the_problem():
    with pytest.raises(FormulaError) as exc:
        compile_formula("[Sales] + [Nope]", ctx())
    assert exc.value.pos == 10


def test_cycles_detected():
    c = ctx()
    c.fields["A"] = FieldSource("A", "number", formula="[B] + 1")
    c.fields["B"] = FieldSource("B", "number", formula="[A] + 1")
    with pytest.raises(FormulaError) as exc:
        compile_formula("[A]", c)
    assert "refers to itself" in exc.value.message


def test_injection_attempts_stay_data(con):
    # A string with quotes becomes a literal, never SQL
    c, values = run(con, "'x''); DROP TABLE t; --'")
    assert values == ["x'); DROP TABLE t; --"] * 3
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 3
    # A field name with SQL in it is just an unknown field
    with pytest.raises(FormulaError):
        compile_formula('[Sales"; DROP TABLE t; --]', ctx())
    # Comments are stripped by the tokenizer, not passed through
    c, values = run(con, "[Qty] // + 1000")
    assert values == [3, 5, 1]


def test_sql_literal_escaping():
    assert sql_literal("O'Brien") == "'O''Brien'"
    assert sql_literal(True) == "TRUE" and sql_literal(None) == "NULL"
    assert sql_literal(date(2026, 1, 2)) == "DATE '2026-01-02'"
    with pytest.raises(FormulaError):
        sql_literal(float("inf"))


def test_field_names_case_insensitive_and_escaped_brackets(con):
    c = ctx()
    c.fields["Price [AED]"] = FieldSource("Price [AED]", "number", sql='"Sales"')
    compiled = compile_formula("[price [aed]]] * 2", c)
    assert con.execute(f'SELECT {compiled.sql} FROM t ORDER BY "Id"').fetchall()[0][0] == 200
    assert compile_formula("[sales]", ctx()).type == "number"
