"""Controle Logístico — verificação de equipamentos e histórico"""
import base64, io, json
import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

st.set_page_config(page_title="Controle Logístico", page_icon="📦", layout="wide")

# ── Conexão ─────────────────────────────────────────────────────────────────

@st.cache_resource
def _conn():
    return psycopg2.connect(st.secrets["DATABASE_URL"], connect_timeout=10,
                            cursor_factory=RealDictCursor)

def _query(sql, params=None):
    try:
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute(sql, params or [])
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        st.cache_resource.clear()
        st.error(f"Erro de banco: {e}")
        return []

def _execute(sql, params=None):
    try:
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute(sql, params or [])
            conn.commit()
        return True
    except Exception as e:
        st.cache_resource.clear()
        st.error(f"Erro ao salvar: {e}")
        return False

# ── Cache de dados ──────────────────────────────────────────────────────────

@st.cache_data(ttl=300)
def _supervisores():
    rows = _query("SELECT DISTINCT supervisor FROM tecnicos WHERE supervisor IS NOT NULL ORDER BY supervisor")
    return [r["supervisor"] for r in rows]

@st.cache_data(ttl=300)
def _tecnicos(supervisor=None):
    if supervisor:
        return _query("SELECT tr, tt, nome, tipo, operadora, supervisor, setor FROM tecnicos WHERE supervisor = %s ORDER BY nome", [supervisor])
    return _query("SELECT tr, tt, nome, tipo, operadora, supervisor, setor FROM tecnicos ORDER BY nome")

@st.cache_data(ttl=60)
def _historico(supervisor=None, tr=None):
    filters, params = [], []
    if supervisor:
        filters.append("supervisor = %s"); params.append(supervisor)
    if tr:
        filters.append("tr = %s"); params.append(tr)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    rows = _query(
        f"SELECT id, tr, tecnico_nome, supervisor, setor, conferente, data_conf, itens, criado_em"
        f" FROM conferencias_logisticas {where} ORDER BY data_conf DESC LIMIT 300",
        params,
    )
    return pd.DataFrame(rows) if rows else pd.DataFrame()

# ── Abas principais ─────────────────────────────────────────────────────────

tab_nova, tab_hist = st.tabs(["📋 Nova Conferência", "📊 Histórico"])

# ════════════════════════════════════════════════════════════════════════════
# ABA 1 — NOVA CONFERÊNCIA
# ════════════════════════════════════════════════════════════════════════════
with tab_nova:
    st.title("📋 Nova Conferência")
    st.caption("Preencha os dados, verifique os itens e salve.")

    # ── Seleção de técnico ──────────────────────────────────────────────────
    sups = _supervisores()
    sup_sel = st.selectbox("Supervisor", [""] + sups, key="nova_sup")

    techs = _tecnicos(sup_sel) if sup_sel else []
    tech_map = {f"{r['nome']} ({r['tr']})": r for r in techs}
    tech_label = st.selectbox("Técnico", [""] + list(tech_map.keys()), key="nova_tec")

    tech = tech_map.get(tech_label)

    if tech:
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(f"**TR:** {tech['tr']}")
        c2.markdown(f"**Função:** {tech.get('tipo','')}")
        c3.markdown(f"**Operadora:** {tech.get('operadora','')}")
        c4.markdown(f"**Setor:** {tech.get('setor','')}")

        st.divider()
        conferente = st.text_input("Nome do conferente *", key="nova_conf")

        # ── Itens ───────────────────────────────────────────────────────────
        st.subheader("Itens verificados")
        n_itens = st.number_input("Quantidade de itens", min_value=1, max_value=10, value=1, step=1)

        itens_state = []
        for i in range(1, int(n_itens) + 1):
            with st.expander(f"Item {i}", expanded=True):
                col_s, col_v = st.columns([4, 1])
                serial = col_s.text_input("Serial / código", key=f"serial_{i}", placeholder="Digite ou escaneie")
                verificado = col_v.checkbox("✅ OK", key=f"veri_{i}")
                foto_file = st.camera_input("Foto do equipamento (opcional)", key=f"foto_{i}")
                foto_b64 = None
                if foto_file:
                    foto_b64 = "data:image/jpeg;base64," + base64.b64encode(foto_file.read()).decode()
                itens_state.append({
                    "id": i,
                    "serial": serial.strip() if serial else "",
                    "verificado": verificado,
                    "foto_base64": foto_b64,
                })

        # ── Assinaturas ─────────────────────────────────────────────────────
        st.divider()
        st.subheader("Assinaturas")
        assin_tec = assin_conf = None
        try:
            from streamlit_drawable_canvas import st_canvas
            col_a, col_b = st.columns(2)
            with col_a:
                st.caption("Técnico — assine abaixo")
                canvas_t = st_canvas(stroke_width=2, height=130, background_color="#fff",
                                     key="canvas_tec", update_streamlit=False)
            with col_b:
                st.caption("Conferente — assine abaixo")
                canvas_c = st_canvas(stroke_width=2, height=130, background_color="#fff",
                                     key="canvas_conf", update_streamlit=False)
            from PIL import Image
            if canvas_t is not None and canvas_t.image_data is not None:
                img = Image.fromarray(canvas_t.image_data.astype("uint8"), "RGBA")
                buf = io.BytesIO(); img.save(buf, format="PNG")
                assin_tec = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
            if canvas_c is not None and canvas_c.image_data is not None:
                img = Image.fromarray(canvas_c.image_data.astype("uint8"), "RGBA")
                buf = io.BytesIO(); img.save(buf, format="PNG")
                assin_conf = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        except ImportError:
            st.info("Assinaturas indisponíveis — instale streamlit-drawable-canvas.")

        # ── Salvar ──────────────────────────────────────────────────────────
        st.divider()
        if st.button("💾 Salvar Conferência", type="primary", use_container_width=True,
                     disabled=not conferente.strip()):
            ok = _execute(
                "INSERT INTO conferencias_logisticas"
                " (tr, tecnico_nome, supervisor, setor, conferente, itens, assin_tecnico, assin_conferente)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (tech["tr"], tech["nome"], tech.get("supervisor"), tech.get("setor"),
                 conferente.strip(), json.dumps(itens_state), assin_tec, assin_conf),
            )
            if ok:
                st.success("✅ Conferência salva com sucesso!")
                st.cache_data.clear()
    else:
        st.info("Selecione um supervisor e um técnico para iniciar.")


# ════════════════════════════════════════════════════════════════════════════
# ABA 2 — HISTÓRICO
# ════════════════════════════════════════════════════════════════════════════
with tab_hist:
    st.title("📊 Histórico de Conferências")

    with st.sidebar:
        st.header("Filtros")
        sups_h = ["Todos"] + _supervisores()
        sup_h = st.selectbox("Supervisor", sups_h, key="hist_sup")
        techs_h = _tecnicos(sup_h if sup_h != "Todos" else None)
        tec_map_h = {"Todos": None} | {f"{r['nome']} ({r['tr']})": r["tr"] for r in techs_h}
        tec_lbl_h = st.selectbox("Técnico", list(tec_map_h.keys()), key="hist_tec")
        tec_tr_h = tec_map_h.get(tec_lbl_h)
        st.divider()
        if st.button("🔄 Atualizar"):
            st.cache_data.clear()
            st.rerun()

    df = _historico(
        supervisor=sup_h if sup_h != "Todos" else None,
        tr=tec_tr_h,
    )

    if df.empty:
        st.info("Nenhuma conferência registrada. Use a aba 'Nova Conferência' para criar.")
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

    sel = st.dataframe(df_show, use_container_width=True, hide_index=True,
                       on_select="rerun", selection_mode="single-row")

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
                df_it = pd.DataFrame(itens)
                cols_d = [c for c in ["id", "serial", "verificado"] if c in df_it.columns]
                df_it = df_it[cols_d].copy()
                df_it.columns = ["#", "Serial", "Verificado"][:len(cols_d)]
                if "Verificado" in df_it.columns:
                    df_it["Verificado"] = df_it["Verificado"].map({True: "✅", False: "❌"})
                ok_n = (df_it.get("Verificado", pd.Series()) == "✅").sum()
                st.markdown(f"**{ok_n}/{len(df_it)} itens verificados**")
                st.dataframe(df_it, use_container_width=True, hide_index=True)
            except Exception:
                st.warning("Não foi possível carregar os itens.")
