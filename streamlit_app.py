"""Controle Logístico — verificação de equipamentos e histórico"""
import base64, hashlib, io, json, os
import streamlit as st
import streamlit.components.v1 as stc
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor

st.set_page_config(page_title="Controle Logístico", page_icon="📦", layout="wide")

# ── Componente de scanner (câmera ao vivo + BarcodeDetector + jsQR + OCR) ───
_COMP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "components", "barcode_scanner")
_scanner_component = stc.declare_component("barcode_scanner", path=_COMP_DIR)

def barcode_scanner(key=None):
    """Retorna dict {type, value} ou None."""
    return _scanner_component(key=key, default=None)

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
        return _query(
            "SELECT tr, tt, nome, tipo, operadora, supervisor, setor FROM tecnicos WHERE supervisor = %s ORDER BY nome",
            [supervisor],
        )
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

# ── Helpers ──────────────────────────────────────────────────────────────────

def _img_to_b64(raw: bytes, mime="image/jpeg"):
    return f"data:{mime};base64," + base64.b64encode(raw).decode()

try:
    from streamlit_js_eval import streamlit_js_eval as _js_eval
    _HAS_GEO = True
except ImportError:
    _HAS_GEO = False

def _get_geo(suffix: str):
    if not _HAS_GEO:
        return None
    return _js_eval(
        js_expressions=(
            "new Promise(r => navigator.geolocation.getCurrentPosition("
            "p => r({lat:p.coords.latitude.toFixed(6),"
            "        lon:p.coords.longitude.toFixed(6),"
            "        acc:Math.round(p.coords.accuracy),"
            "        ts:new Date().toLocaleString('pt-BR')}),"
            "() => r(null),{timeout:8000,enableHighAccuracy:true}))"
        ),
        key=f"geo_{suffix}",
    )

# ── Abas ────────────────────────────────────────────────────────────────────

tab_nova, tab_hist = st.tabs(["📋 Nova Conferência", "📊 Histórico"])

# ════════════════════════════════════════════════════════════════════════════
# ABA 1 — NOVA CONFERÊNCIA
# ════════════════════════════════════════════════════════════════════════════
with tab_nova:
    st.title("📋 Nova Conferência")

    sups = _supervisores()
    sup_sel = st.selectbox("Supervisor", [""] + sups, key="nova_sup")
    techs = _tecnicos(sup_sel) if sup_sel else []
    tech_map = {f"{r['nome']} ({r['tr']})": r for r in techs}
    tech_label = st.selectbox("Técnico", [""] + list(tech_map.keys()), key="nova_tec")
    tech = tech_map.get(tech_label)

    if not tech:
        st.info("Selecione supervisor e técnico para iniciar.")
        st.stop()

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(f"**TR:** {tech['tr']}")
    c2.markdown(f"**Função:** {tech.get('tipo','')}")
    c3.markdown(f"**Operadora:** {tech.get('operadora','')}")
    c4.markdown(f"**Setor:** {tech.get('setor','')}")

    st.divider()
    conferente = st.text_input("Nome do conferente *", key="nova_conf")
    n_itens = st.number_input("Quantidade de itens", min_value=1, max_value=10, value=1, step=1)

    # ── Inicializa estado dos itens ─────────────────────────────────────────
    for i in range(1, 11):
        st.session_state.setdefault(f"serial_{i}", "")
        st.session_state.setdefault(f"scan_on_{i}", False)
        st.session_state.setdefault(f"foto_b64_{i}", None)
        st.session_state.setdefault(f"foto_hash_{i}", "")
        st.session_state.setdefault(f"geo_{i}", None)

    st.subheader("Itens")
    itens_state = []

    for i in range(1, int(n_itens) + 1):
        with st.expander(f"Item {i}", expanded=True):

            # ── Serial + botão scan ────────────────────────────────────────
            col_s, col_btn = st.columns([5, 1])
            serial_val = col_s.text_input(
                "Serial / código",
                value=st.session_state[f"serial_{i}"],
                key=f"si_{i}",
                placeholder="Digite ou use 📷 para escanear",
            )
            st.session_state[f"serial_{i}"] = serial_val

            btn_lbl = "✖ Fechar" if st.session_state[f"scan_on_{i}"] else "📷 Scan"
            if col_btn.button(btn_lbl, key=f"scanbtn_{i}"):
                st.session_state[f"scan_on_{i}"] = not st.session_state[f"scan_on_{i}"]
                st.rerun()

            if st.session_state[f"scan_on_{i}"]:
                result = barcode_scanner(key=f"scanner_{i}")
                if result is not None:
                    st.session_state[f"scan_on_{i}"] = False
                    rtype = result.get("type", "")
                    rval  = (result.get("value") or "").strip()
                    if rtype in ("barcode", "ocr") and rval:
                        st.session_state[f"serial_{i}"] = rval
                    st.rerun()

            # ── Foto: câmera ou upload ────────────────────────────────────
            st.caption("Foto do equipamento")
            foto_src = st.radio(
                "Fonte",
                ["📷 Câmera", "⬆️ Upload", "— Nenhuma"],
                key=f"fotosrc_{i}",
                horizontal=True,
                label_visibility="collapsed",
            )

            if "Câmera" in foto_src:
                foto_cam = st.camera_input(
                    "Foto do equipamento",
                    key=f"fotocam_{i}",
                    label_visibility="collapsed",
                )
                if foto_cam:
                    raw_foto = foto_cam.read()
                    fh = hashlib.md5(raw_foto).hexdigest()[:10]
                    if fh != st.session_state[f"foto_hash_{i}"]:
                        st.session_state[f"foto_b64_{i}"] = _img_to_b64(raw_foto)
                        st.session_state[f"foto_hash_{i}"] = fh
                        st.session_state[f"geo_{i}"] = None
                    if st.session_state[f"foto_hash_{i}"]:
                        geo = _get_geo(f"{i}_{st.session_state[f'foto_hash_{i}']}")
                        if geo and "lat" in geo:
                            st.session_state[f"geo_{i}"] = geo

            elif "Upload" in foto_src:
                upload = st.file_uploader(
                    "Escolher imagem", type=["jpg", "jpeg", "png"],
                    key=f"fotoup_{i}", label_visibility="collapsed",
                )
                if upload:
                    st.session_state[f"foto_b64_{i}"] = _img_to_b64(upload.read())
                    st.session_state[f"geo_{i}"] = None
            else:
                st.session_state[f"foto_b64_{i}"] = None
                st.session_state[f"geo_{i}"] = None

            if st.session_state[f"foto_b64_{i}"]:
                st.image(st.session_state[f"foto_b64_{i}"], width=180)
                g = st.session_state[f"geo_{i}"]
                if g and "lat" in g:
                    maps = f"https://www.google.com/maps?q={g['lat']},{g['lon']}"
                    st.caption(f"📍 {g['lat']}, {g['lon']} · {g['ts']} · [ver mapa]({maps})")
                elif "Câmera" in foto_src:
                    st.caption("⏳ Obtendo localização…")

            verificado = st.checkbox("✅ Item verificado", key=f"veri_{i}")
            itens_state.append({
                "id": i,
                "serial": st.session_state[f"serial_{i}"],
                "verificado": verificado,
                "foto_b64": st.session_state[f"foto_b64_{i}"],
                "geo": st.session_state[f"geo_{i}"],
            })

    # ── Assinaturas ─────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Assinaturas")
    assin_tec = assin_conf = None
    try:
        from streamlit_drawable_canvas import st_canvas
        from PIL import Image as PILImage
        col_a, col_b = st.columns(2)
        with col_a:
            st.caption("Técnico")
            cv_t = st_canvas(stroke_width=2, height=130, background_color="#fff",
                             key="sig_tec", update_streamlit=False)
        with col_b:
            st.caption("Conferente")
            cv_c = st_canvas(stroke_width=2, height=130, background_color="#fff",
                             key="sig_conf", update_streamlit=False)

        def _c2b64(cv):
            if cv is None or cv.image_data is None:
                return None
            buf = io.BytesIO()
            PILImage.fromarray(cv.image_data.astype("uint8"), "RGBA").save(buf, format="PNG")
            return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

        assin_tec  = _c2b64(cv_t)
        assin_conf = _c2b64(cv_c)
    except ImportError:
        st.info("Assinaturas indisponíveis (streamlit-drawable-canvas não instalado).")

    # ── Salvar ──────────────────────────────────────────────────────────────
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
            st.success("✅ Conferência salva!")
            for i in range(1, 11):
                for k in (f"serial_{i}", f"foto_b64_{i}", f"geo_{i}", f"foto_hash_{i}"):
                    st.session_state.pop(k, None)
                st.session_state[f"scan_on_{i}"] = False
            st.cache_data.clear()


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

    df = _historico(supervisor=sup_h if sup_h != "Todos" else None, tr=tec_tr_h)

    if df.empty:
        st.info("Nenhuma conferência ainda. Use a aba 'Nova Conferência' para criar.")
        st.stop()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total", len(df))
    c2.metric("Técnicos", df["tr"].nunique())
    c3.metric("Supervisores", df["supervisor"].nunique())
    c4.metric("Conferentes", df["conferente"].nunique())

    st.divider()
    df_show = df[["data_conf","tr","tecnico_nome","supervisor","setor","conferente"]].copy()
    df_show.columns = ["Data/Hora","TR","Técnico","Supervisor","Setor","Conferente"]
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
                rows_it = []
                for it in itens:
                    g = it.get("geo") or {}
                    rows_it.append({
                        "#": it.get("id"),
                        "Serial": it.get("serial") or "—",
                        "Verificado": "✅" if it.get("verificado") else "❌",
                        "Localização": f"{g['lat']}, {g['lon']}" if g.get("lat") else "—",
                        "Data/Hora foto": g.get("ts", "—"),
                    })
                df_it = pd.DataFrame(rows_it)
                ok_n = (df_it["Verificado"] == "✅").sum()
                st.markdown(f"**{ok_n}/{len(df_it)} itens verificados**")
                st.dataframe(df_it, use_container_width=True, hide_index=True)
            except Exception:
                st.warning("Não foi possível carregar os itens.")
