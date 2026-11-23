// Reference shown next to the formula editor. Keep in sync with backend/app/semantic/formula.py FUNCTIONS.
export const FUNCTION_GROUPS: { name: string; items: [string, string][] }[] = [
  {
    name: "Aggregate",
    items: [
      ["SUM(number)", "Total"],
      ["AVG(number)", "Average"],
      ["COUNT(value)", "Number of non-empty values"],
      ["COUNTD(value)", "Number of different values"],
      ["MIN(value) / MAX(value)", "Smallest / largest"],
      ["MEDIAN(number)", "Middle value"],
      ["PERCENTILE(number, 0.9)", "Value below which 90% fall"],
      ["STDEV(number) / VAR(number)", "Spread"],
    ],
  },
  {
    name: "Logic",
    items: [
      ["IF cond THEN a ELSEIF cond THEN b ELSE c END", "Choose a value"],
      ["CASE [Field] WHEN 'x' THEN a ELSE b END", "Match values"],
      ["IIF(cond, a, b)", "Short IF"],
      ["IFNULL(value, fallback)", "Replace empty values"],
      ["ZN(number)", "Empty becomes 0"],
      ["ISNULL(value)", "Is it empty?"],
      ["value IN ('a', 'b')", "One of a list"],
    ],
  },
  {
    name: "Number",
    items: [
      ["ROUND(number, digits)", "Round"],
      ["ABS(number)", "Without sign"],
      ["CEILING(n) / FLOOR(n)", "Round up / down"],
      ["POWER(n, p) / SQRT(n)", "Powers"],
      ["LOG(n) / LN(n) / EXP(n)", "Logarithms"],
      ["DIV(a, b)", "Whole-number division"],
      ["MIN(a, b) / MAX(a, b)", "Smaller / larger of two"],
    ],
  },
  {
    name: "Text",
    items: [
      ["'a' + 'b'", "Join text"],
      ["STR(value)", "As text"],
      ["LEN(text)", "Length"],
      ["UPPER(text) / LOWER(text)", "Change case"],
      ["TRIM(text)", "Remove spaces"],
      ["LEFT(text, n) / RIGHT(text, n)", "First / last characters"],
      ["MID(text, start, length)", "Part of the text"],
      ["CONTAINS(text, 'x')", "Contains?"],
      ["STARTSWITH(text, 'x') / ENDSWITH", "Starts / ends with?"],
      ["REPLACE(text, 'old', 'new')", "Replace"],
      ["SPLIT(text, '-', 2)", "Part between separators"],
      ["FIND(text, 'x')", "Position of x"],
    ],
  },
  {
    name: "Date",
    items: [
      ["YEAR / QUARTER / MONTH / WEEK / DAY(date)", "Part as a number"],
      ["DATETRUNC('month', date)", "Start of the month"],
      ["DATEADD('day', 7, date)", "Add time"],
      ["DATEDIFF('day', start, end)", "Time between"],
      ["DATEPART('quarter', date)", "Any part as a number"],
      ["WEEKDAY(date)", "Day of the week (1 = Monday)"],
      ["TODAY() / NOW()", "Current date / time"],
      ["MAKEDATE(2026, 1, 31)", "Build a date"],
      ["#2026-01-31#", "A fixed date"],
    ],
  },
  {
    name: "Convert",
    items: [
      ["INT(value)", "Whole number"],
      ["FLOAT(value)", "Decimal number"],
      ["DATE(value) / DATETIME(value)", "Date / date and time"],
    ],
  },
];
