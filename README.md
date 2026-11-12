# NEISS Kids

A small DuckDB database of emergency room visits for children age 2 and under, built from CPSC's [National Electronic Injury Surveillance System (NEISS)](https://www.cpsc.gov/Research--Statistics/NEISS-Injury-Data) data for 2020–2025, plus a Streamlit app for exploring it.

## What's here

| Path | What it is |
| --- | --- |
| `raw/neiss20XX.tsv` | Raw yearly NEISS exports (all ages) |
| `neiss_codes.csv` | Code labels (`variable,code,label`) for product, body part, diagnosis, disposition, location, sex |
| `build.py` | Builds the database from the raw files |
| `neiss_kids.duckdb` | The built database |
| `parquet/` | The same tables as Parquet |
| `explore.py` | Streamlit explorer |

## Setup

Python 3.13 (see `.tool-versions`).

```sh
pip install duckdb streamlit pandas
```

## Rebuild the database

```sh
python build.py raw/*.tsv --lookup neiss_codes.csv
```

This repairs records split by stray line breaks in the narrative, keeps only children age 2 and under, and writes `neiss_kids.duckdb` and `parquet/`.

## Explore

```sh
streamlit run explore.py
```

Filter by year, age group, product and narrative keyword. The app shows national estimates, top products, case narratives, and a box for running your own SQL.

## Data model

- **`cases`**: one row per sampled ER visit. Includes `treatment_date`, `age_months`, `age_group` (`0-5 months` … `2 years`), coded `sex`, `body_part`, `diagnosis`, `disposition`, `location`, `product_1`–`product_3`, the `narrative`, and the survey fields `weight`, `stratum` and `psu`.
- **`case_products`**: one row per case per product (`case_id`, `product`, `position`), so finding every case involving a product is a single join.
- **`codes`**: labels for the coded columns (`variable`, `code`, `label`).

Example:

```sql
SELECT c.age_group, count(*) AS cases, round(sum(c.weight)) AS national_estimate
FROM cases c
JOIN case_products cp USING (case_id)
JOIN codes k ON k.variable = 'product' AND k.code = cp.product
WHERE k.label ILIKE '%high chair%'
GROUP BY 1 ORDER BY 1;
```
