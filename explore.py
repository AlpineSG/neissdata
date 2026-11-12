"""
Quick explorer for neiss_kids.duckdb.

    pip install streamlit duckdb pandas
    streamlit run explore.py
"""
import duckdb
import streamlit as st

DB = "neiss_kids.duckdb"
AGE_ORDER = ["0-5 months", "6-11 months", "12-17 months", "18-23 months", "2 years"]

st.set_page_config(page_title="NEISS kids explorer", layout="wide")


@st.cache_resource
def connect():
    return duckdb.connect(DB, read_only=True)


con = connect()
has_codes = bool(con.execute(
    "SELECT count(*) FROM information_schema.tables WHERE table_name = 'codes'").fetchone()[0])


def q(sql, params=None):
    return con.execute(sql, params or []).df()


def label(variable, expr):
    """SQL that turns a coded column into its label, falling back to the raw code."""
    if has_codes:
        return (f"coalesce((SELECT label FROM codes WHERE variable = '{variable}' "
                f"AND code = {expr}), {expr}::VARCHAR)")
    return f"{expr}::VARCHAR"


# All products on a case, in recorded order, as one readable string.
PRODUCTS_FOR_CASE = f"""(
    SELECT string_agg({label('product', 'cp.product')}, ' | ' ORDER BY cp.position)
    FROM case_products cp WHERE cp.case_id = c.case_id)"""


@st.cache_data
def product_options():
    """Every product code in the data with its label, most common first."""
    df = q(f"""SELECT cp.product AS code, {label('product', 'cp.product')} AS label, count(*) AS n
               FROM case_products cp GROUP BY 1, 2 ORDER BY n DESC""")
    return {int(r.code): f"{r.label} ({r.code}) · {r.n:,}" for r in df.itertuples()}


# ---- Filters ----------------------------------------------------------------
st.sidebar.header("Filters")
years = q("SELECT DISTINCT year(treatment_date) AS y FROM cases ORDER BY 1")["y"].tolist()
year_range = st.sidebar.select_slider("Years", options=years, value=(years[0], years[-1]))
ages = st.sidebar.multiselect("Age groups", AGE_ORDER, default=AGE_ORDER)
keyword = st.sidebar.text_input("Narrative contains", placeholder="e.g. SWING").strip().upper()
prod_opts = product_options()
codes = st.sidebar.multiselect("Products", list(prod_opts), format_func=prod_opts.get,
                               placeholder="Type to search, e.g. high chair")

where = ["year(c.treatment_date) BETWEEN ? AND ?", "list_contains(?, c.age_group)"]
params = [year_range[0], year_range[1], ages]
if keyword:
    where.append("c.narrative LIKE ?")
    params.append(f"%{keyword}%")
if codes:
    where.append("c.case_id IN (SELECT case_id FROM case_products WHERE list_contains(?, product))")
    params.append(codes)
WHERE = " AND ".join(where)

# ---- Headline numbers -------------------------------------------------------
summary = q(f"SELECT count(*) AS n, coalesce(sum(weight), 0) AS est FROM cases c WHERE {WHERE}",
            params).iloc[0]
a, b = st.columns(2)
a.metric("Sampled cases", f"{int(summary.n):,}")
b.metric("National estimate (sum of weights)", f"{summary.est:,.0f}")
if 0 < summary.n < 20:
    st.warning("Fewer than 20 cases: CPSC treats estimates this small as unstable.")

# ---- Charts -----------------------------------------------------------------
left, right = st.columns(2)

by_year = q(f"""SELECT year(treatment_date)::VARCHAR AS year, sum(weight) AS estimate
               FROM cases c WHERE {WHERE} GROUP BY 1 ORDER BY 1""", params)
left.subheader("Estimate by year")
left.bar_chart(by_year, x="year", y="estimate")

by_age = q(f"""SELECT age_group, sum(weight) AS estimate
              FROM cases c WHERE {WHERE} GROUP BY 1""", params)
right.subheader("Estimate by age group")
right.bar_chart(by_age.set_index("age_group").reindex(AGE_ORDER).dropna(), y="estimate")

st.subheader("Top products")
st.caption("A case can involve up to three products, so these counts add up to more than the case total.")
top = q(f"""SELECT cp.product AS code, {label('product', 'cp.product')} AS product,
                   count(*) AS cases, round(sum(c.weight)) AS estimate
            FROM cases c JOIN case_products cp USING (case_id)
            WHERE {WHERE} GROUP BY 1, 2 ORDER BY estimate DESC LIMIT 15""", params)
st.dataframe(top, hide_index=True, width='stretch')

st.subheader("Narratives")
st.dataframe(q(f"""SELECT c.treatment_date, c.age_group,
                          {PRODUCTS_FOR_CASE} AS products,
                          {label('body_part', 'c.body_part')} AS body_part,
                          {label('diagnosis', 'c.diagnosis')} AS diagnosis,
                          {label('disposition', 'c.disposition')} AS disposition,
                          c.narrative, round(c.weight, 1) AS weight
                   FROM cases c WHERE {WHERE}
                   ORDER BY c.treatment_date DESC LIMIT 500""", params),
             hide_index=True, width='stretch',
             column_config={"narrative": st.column_config.TextColumn(width="large")})

# ---- Free SQL ---------------------------------------------------------------
with st.expander("Run your own SQL"):
    sql = st.text_area("Query", "SELECT * FROM cases LIMIT 20")
    if st.button("Run"):
        try:
            st.dataframe(q(sql), width='stretch')
        except Exception as e:
            st.error(str(e))
