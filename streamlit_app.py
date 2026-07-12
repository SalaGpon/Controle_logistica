"""Controle Logístico — histórico de verificações de equipamentos"""
import json
import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

st.set_page_config(
    page_title="Controle Logístico",
    page_icon="📦",
    layout="wide",
)

# ── Conexão PG ────────────────────────────────────────────────────────────────

@st.cache_resource
def _conn():
    url = st.secrets["DATABASE_URL"]
    return psycopg2.connect(url, connect_timeout=10, cursor_factory=RealDictCursor)


def query(sql, params=None):
    try:
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute(sql, params or [])
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        st.cache_resource.clear()
        st.error(f"Erro de banco: {e}")
        return []


# ── Dados ────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def carregar_conferencias(supervisor=None, tr=None, limit=300):
    filters, params = [], []
    if supervisor and supervisor != "Todos":
        filters.append("supervisor = %s")
        params.append(supervisor)
    if tr and tr != "Todos":
        filters.append("tr = %s")
        params.append(tr)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    rows = query(
        f"SELECT id, tr, tecnico_nome, supervisor, setor, conferente,"
        f" data_conf, itens, criado_em"
        f" FROM conferencias_logisticas {where}"
        f" ORDER BY data_conf DESC LIMIT %s",
        params + [limit],
    )
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=300)
def carregar_supervisores():
    rows = query("SELECT DISTINCT supervisor FROM tecnicos WHERE supervisor IS NOT NULL ORDER BY supervisor")
    return ["Todos"] + [r["supervisor"] for r in rows]


@st.cache_data(ttl=300)
def carregar_tecnicos(supervisor=None):
    if supervisor and supervisor != "Todos":
        rows = query(
            "SELECT tr, nome FROM tecnicos WHERE supervisor = %s ORDER BY nome",
            [supervisor]
        )
    else:
        rows = query("SELECT tr, nome FROM tecnicos ORDER BY nome")
    return {"Todos": None} | {f"{r['nome']} ({r['tr']})": r["tr"] for r in rows}


# ── Layout ────────────────────────────────────────────────────────────────────

st.title("📦 Controle Logístico")
st.caption("Verificações de equipamentos dos técnicos")

with st.sidebar:
    st.header("Filtros")
    supervisores = carregar_supervisores()
    sup_sel = st.selectbox("Supervisor", supervisores)

    tec_map = carregar_tecnicos(sup_sel if sup_sel != "Todos" else None)
    tec_sel_label = st.selectbox("Técnico", list(tec_map.keys()))
    tec_tr = tec_map.get(tec_sel_label)

    st.divider()
    if st.button("🔄 Atualizar"):
        st.cache_data.clear()
        st.rerun()

df = carregar_conferencias(
    supervisor=sup_sel if sup_sel != "Todos" else None,
    tr=tec_tr,
)

if df.empty:
    st.info("Nenhuma conferência registrada ainda. Use o app mobile para criar.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total", len(df))
c2.metric("Técnicos", df["tr"].nunique())
c3.metric("Supervisores", df["supervisor"].nunique())
c4.metric("Conferentes", df["conferente"].nunique())

st.divider()

df_show = df[["data_conf", "tr", "tecnico_nome", "supervisor", "setor", "conferente"]].copy()
df_show.columns = ["Data/Hora", "TR", "Técnico", "Supervisor", "Setor", "Conferente"]
df_show["Data/Hora"] = pd.to_datetime(df_show["Data/Hora"]).dt.strftime("%d/%m/%Y %H:%M")

st.subheader("Histórico de conferências")
sel = st.dataframe(
    df_show,
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
)

rows_sel = getattr(getattr(sel, "selection", None), "rows", [])
if rows_sel:
    row = df.iloc[rows_sel[0]]
    st.divider()
    st.subheader(f"Detalhe — {row['tecnico_nome']} ({row['tr']})")

    col_a, col_b = st.columns(2)
    col_a.markdown(f"**Conferente:** {row['conferente']}")
    col_a.markdown(f"**Supervisor:** {row['supervisor']}")
    col_b.markdown(f"**Setor:** {row['setor'] or '—'}")
    col_b.markdown(f"**Data:** {str(row['data_conf'])[:16]}")

    itens_raw = row.get("itens")
    if itens_raw:
        try:
            itens = itens_raw if isinstance(itens_raw, list) else json.loads(itens_raw)
            df_itens = pd.DataFrame(itens)
            cols_disp = [c for c in ["id", "serial", "verificado"] if c in df_itens.columns]
            df_itens = df_itens[cols_disp].copy()
            df_itens.columns = ["#", "Serial", "Verificado"][:len(cols_disp)]
            if "Verificado" in df_itens.columns:
                df_itens["Verificado"] = df_itens["Verificado"].map({True: "✅", False: "❌"})
            ok = (df_itens.get("Verificado", pd.Series()) == "✅").sum()
            st.markdown(f"**Itens verificados: {ok}/{len(df_itens)}**")
            st.dataframe(df_itens, use_container_width=True, hide_index=True)
        except Exception:
            st.warning("Não foi possível carregar os itens.")
