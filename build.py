"""
Build a small, candidate-ready DuckDB database of NEISS cases for children age 2 and under.

Usage:
    pip install duckdb
    python build_neiss_kids.py neiss2021.tsv neiss2022.tsv neiss2023.tsv neiss2024.tsv neiss2025.tsv
    python build_neiss_kids.py data/*.tsv --lookup neiss_codes.csv   # optional code labels

Optional --lookup CSV has three columns: variable,code,label
    e.g.  product,1555,<label from the coding manual>
          body_part,75,Head
          diagnosis,57,Fracture
          disposition,4,Treated and admitted
Variables used: product, body_part, diagnosis, disposition, location, sex

Outputs:
    neiss_kids.duckdb          tables: cases, case_products, (codes if --lookup given)
    parquet/cases.parquet      same data for anyone who prefers Parquet
    parquet/case_products.parquet
"""
import argparse
import csv
import sys
import tempfile
from pathlib import Path

import duckdb

csv.field_size_limit(sys.maxsize)


def repair_tsv(src, outdir):
    """Rejoin records whose narrative contains a stray line break.

    A few NEISS rows have a CRLF inside the narrative, which splits one record
    across two lines (e.g. 22 fields + 4 fields). Merge short rows with the
    following line until the row has as many fields as the header.
    """
    dst = outdir / Path(src).name
    fixed = 0
    with open(src, newline="", encoding="utf-8", errors="replace") as fin, \
         open(dst, "w", newline="", encoding="utf-8") as fout:
        reader = csv.reader(fin, delimiter="\t")
        writer = csv.writer(fout, delimiter="\t", lineterminator="\n")
        header = next(reader)
        writer.writerow(header)
        n = len(header)
        for row in reader:
            while len(row) < n:
                nxt = next(reader, None)
                if nxt is None:
                    break
                row[-1] = f"{row[-1]} {nxt[0]}"
                row.extend(nxt[1:])
                fixed += 1
            if len(row) != n:
                print(f"warning: {src}: case {row[0]} has {len(row)} fields, expected {n}",
                      file=sys.stderr)
            writer.writerow(row)
    if fixed:
        print(f"{src}: rejoined {fixed} split record(s)", file=sys.stderr)
    return str(dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--lookup", help="CSV of variable,code,label for readable code names")
    ap.add_argument("--out", default="neiss_kids.duckdb")
    args = ap.parse_args()

    Path(args.out).unlink(missing_ok=True)
    con = duckdb.connect(args.out)

    tmpdir = tempfile.TemporaryDirectory()
    cleaned = [repair_tsv(f, Path(tmpdir.name)) for f in args.files]

    # Read every file as text so nothing is silently mis-typed; union_by_name
    # handles years whose column order or names differ slightly.
    con.execute("""
        CREATE TEMP TABLE raw AS
        SELECT * FROM read_csv(?, delim='\t', quote='"', escape='"', header=true,
                               all_varchar=true, union_by_name=true, filename=true)
    """, [cleaned])
    tmpdir.cleanup()

    cols = {d[0].lower(): d[0] for d in con.execute("SELECT * FROM raw LIMIT 0").description}

    def col(*names, required=True):
        for n in names:
            if n in cols:
                return f'"{cols[n]}"'
        if required:
            raise SystemExit(f"Missing column {names}. Found: {sorted(cols)}")
        return "NULL"

    # Some years have one Narrative column, others split it into Narrative_1 and _2.
    # Files are unioned by name, so a mix of years can have both forms.
    parts = []
    if "narrative" in cols:
        parts.append(f"nullif(trim({col('narrative')}), '')")
    if "narrative_1" in cols:
        parts.append(f"nullif(trim(coalesce({col('narrative_1')}, '') || ' ' || "
                     f"coalesce({col('narrative_2', required=False)}, '')), '')")
    if not parts:
        raise SystemExit(f"No narrative column. Found: {sorted(cols)}")
    narrative = f"coalesce({', '.join(parts)})"

    age = f"try_cast({col('age')} AS INTEGER)"

    con.execute(f"""
        CREATE TABLE cases AS
        SELECT
            {col('cpsc_case_number')}                                   AS case_id,
            coalesce(try_strptime({col('treatment_date')}, '%m/%d/%Y'),
                     try_strptime({col('treatment_date')}, '%Y-%m-%d'))::DATE AS treatment_date,
            -- NEISS codes ages under 2 as 200 + months (206 = 6 months).
            CASE WHEN {age} >= 200 THEN {age} - 200 END                AS age_months,
            CASE WHEN {age} >= 200 THEN 0 ELSE {age} END                AS age_years,
            CASE
                WHEN {age} BETWEEN 200 AND 205 THEN '0-5 months'
                WHEN {age} BETWEEN 206 AND 211 THEN '6-11 months'
                WHEN {age} BETWEEN 212 AND 217 THEN '12-17 months'
                WHEN {age} BETWEEN 218 AND 223 THEN '18-23 months'
                WHEN {age} = 2                 THEN '2 years'
            END                                                         AS age_group,
            try_cast({col('sex')} AS INTEGER)                           AS sex,
            try_cast({col('body_part')} AS INTEGER)                     AS body_part,
            try_cast({col('diagnosis')} AS INTEGER)                     AS diagnosis,
            try_cast({col('disposition')} AS INTEGER)                   AS disposition,
            try_cast({col('location')} AS INTEGER)                      AS location,
            try_cast({col('product_1')} AS INTEGER)                     AS product_1,
            try_cast({col('product_2', required=False)} AS INTEGER)     AS product_2,
            try_cast({col('product_3', required=False)} AS INTEGER)     AS product_3,
            upper({narrative})                                          AS narrative,
            try_cast({col('weight')} AS DOUBLE)                         AS weight,
            {col('stratum', required=False)}                            AS stratum,
            {col('psu', required=False)}                                AS psu
        FROM raw
        WHERE {age} >= 200 OR {age} = 2
    """)

    # One row per case per product, so "all swing cases" is a single join.
    con.execute("""
        CREATE TABLE case_products AS
        SELECT case_id, product_1 AS product, 1 AS position FROM cases WHERE product_1 > 0
        UNION ALL
        SELECT case_id, product_2, 2 FROM cases WHERE product_2 > 0
        UNION ALL
        SELECT case_id, product_3, 3 FROM cases WHERE product_3 > 0
    """)

    if args.lookup:
        con.execute("""
            CREATE TABLE codes AS
            SELECT lower(variable) AS variable, try_cast(code AS INTEGER) AS code, label
            FROM read_csv(?, header=true, all_varchar=true)
        """, [args.lookup])

    con.execute("""
        COMMENT ON TABLE cases IS 'NEISS ER visits, children age 2 and under. One row is one sampled case. Use stratum and psu for confidence intervals.
CPSC treats estimates from fewer than 20 cases as unstable.'
    """)

    Path("parquet").mkdir(exist_ok=True)
    con.execute("COPY cases TO 'parquet/cases.parquet' (FORMAT parquet)")
    con.execute("COPY case_products TO 'parquet/case_products.parquet' (FORMAT parquet)")

    con.sql("""
        SELECT year(treatment_date) AS year, count(*) AS cases,
               round(sum(weight)) AS national_estimate
        FROM cases GROUP BY 1 ORDER BY 1
    """).show()
    con.sql("""
        SELECT age_group, count(*) AS cases FROM cases GROUP BY 1 ORDER BY 1
    """).show()
    con.close()
    print(f"\nWrote {args.out} and parquet/")


if __name__ == "__main__":
    main()