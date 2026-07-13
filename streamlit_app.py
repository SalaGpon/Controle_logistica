"""Controle Logístico — verificação de equipamentos e histórico"""
import base64, hashlib, io, json, os
import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from PIL import Image as PILImage

st.set_page_config(page_title="Controle Logístico", page_icon="📦", layout="wide")

# ── JS eval (scanner) ────────────────────────────────────────────────────────
try:
    from streamlit_js_eval import streamlit_js_eval as _js_eval
    _HAS_JS = True
except ImportError:
    _HAS_JS = False
    def _js_eval(*a, **kw): return None

# ── Scanner JS ───────────────────────────────────────────────────────────────
# Fluxo: câmera ao vivo → usuário enquadra serial no retângulo → toca "Ler Serial"
# → frame é congelado → Tesseract lê a área recortada → preenche serial.
# Tudo no iframe (sem CSP do Streamlit Cloud).
_SCANNER_JS = """
new Promise(function(RESOLVE){
  var PD=window.parent.document;
  var old=PD.getElementById('_bscan'); if(old)old.remove();
  var ROOT=PD.createElement('div');
  ROOT.id='_bscan';
  ROOT.style.cssText='position:fixed;top:0;left:0;width:100%;height:100%;background:#000;z-index:2147483647;display:flex;flex-direction:column;font-family:-apple-system,sans-serif;';
  var VWRAP=PD.createElement('div');
  VWRAP.style.cssText='position:relative;flex:1;min-height:0;overflow:hidden;';
  var VID=PD.createElement('video');
  VID.autoplay=true; VID.playsInline=true; VID.muted=true;
  VID.style.cssText='width:100%;height:100%;object-fit:cover;display:block;';
  var OV=PD.createElement('canvas');
  OV.style.cssText='position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;';
  VWRAP.appendChild(VID); VWRAP.appendChild(OV);
  var ST=PD.createElement('div');
  ST.style.cssText='padding:10px 14px;text-align:center;font-size:13px;color:#94a3b8;background:rgba(0,0,0,.92);min-height:42px;display:flex;align-items:center;justify-content:center;';
  ST.textContent='Enquadre o serial no retangulo e toque em Ler Serial';
  var BTNS=PD.createElement('div');
  BTNS.style.display='flex';
  var BOCR=PD.createElement('button');
  BOCR.textContent='Ler Serial';
  BOCR.style.cssText='flex:2;padding:16px;background:#059669;color:#fff;border:none;font-size:16px;font-weight:700;cursor:pointer;';
  var BCAN=PD.createElement('button');
  BCAN.textContent='Cancelar';
  BCAN.style.cssText='flex:1;padding:16px;background:#374151;color:#fff;border:none;font-size:14px;cursor:pointer;';
  BTNS.appendChild(BOCR); BTNS.appendChild(BCAN);
  var ZBAR=PD.createElement('div');
  ZBAR.style.cssText='background:#111;padding:5px 12px;display:flex;align-items:center;gap:8px;';
  ZBAR.innerHTML='<span style="color:#64748b;font-size:11px;white-space:nowrap;">Zoom −</span><input id="_bzmr" type="range" min="1" max="100" value="20" style="flex:1;accent-color:#10b981;"><span style="color:#64748b;font-size:11px;">+</span>';
  ROOT.appendChild(VWRAP); ROOT.appendChild(ZBAR); ROOT.appendChild(ST); ROOT.appendChild(BTNS);
  PD.body.appendChild(ROOT);

  var stream,scanT,animT,GR={},found=false,LY=0,LD=1,BDinst=null,qrFn=null;

  function DONE(v){
    found=true; clearInterval(scanT); clearInterval(animT);
    if(stream) stream.getTracks().forEach(function(t){t.stop();});
    ROOT.remove();
    window.postMessage({_bscanRes:v},'*');
  }
  window.addEventListener('message',function H(e){
    if(e.data&&e.data._bscanRes!==undefined){window.removeEventListener('message',H); RESOLVE(e.data._bscanRes);}
  });

  navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}}})
  .then(function(s){stream=s; VID.srcObject=s; return VID.play();})
  .then(function(){
    setTimeout(RZ,200); animT=setInterval(DRAW,30);
    // BarcodeDetector passivo (detecta QR/barcode sem CDN)
    var BDC=window.BarcodeDetector||window.parent.BarcodeDetector;
    if(BDC){try{BDinst=new BDC();}catch(e){}}
    // jsQR como fallback para QR codes
    var s2=document.createElement('script');
    s2.src='https://unpkg.com/jsqr@1.4.0/dist/jsQR.min.js';
    s2.onload=function(){qrFn=window.jsQR||null;};
    document.head.appendChild(s2);
    // Scan passivo — sem mensagem de status
    scanT=setInterval(function(){
      if(found||!VID.videoWidth||!GR.vw) return;
      var cv=CROP_GUIDE();
      if(qrFn){var d=cv.getContext('2d').getImageData(0,0,cv.width,cv.height); var q=qrFn(d.data,d.width,d.height,{inversionAttempts:'dontInvert'}); if(q&&!found){DONE({type:'barcode',value:q.data}); return;}}
      if(BDinst){BDinst.detect(cv).then(function(cs){if(cs.length&&!found)DONE({type:'barcode',value:cs[0].rawValue});}).catch(function(){});}
    },300);
  }).catch(function(e){ST.textContent='Erro camera: '+e.message; ST.style.color='#ef4444';});

  function RZ(){
    var r=VID.getBoundingClientRect(); if(!r.width)return;
    OV.width=r.width; OV.height=r.height;
    // Retangulo largo (88% da largura) e alto o suficiente para etiqueta de serial
    var gw=Math.round(r.width*.88), gh=Math.round(r.height*.24);
    GR={x:Math.round((r.width-gw)/2),y:Math.round((r.height-gh)/2),w:gw,h:gh,vw:r.width,vh:r.height};
  }

  function DRAW(){
    if(!GR.vw)return;
    var c=OV.getContext('2d');
    c.clearRect(0,0,OV.width,OV.height);
    c.fillStyle='rgba(0,0,0,.55)'; c.fillRect(0,0,GR.vw,GR.vh);
    c.clearRect(GR.x,GR.y,GR.w,GR.h);
    c.strokeStyle='#10b981'; c.lineWidth=2; c.strokeRect(GR.x,GR.y,GR.w,GR.h);
    var cs=18; c.lineWidth=3.5;
    [[GR.x,GR.y,1,1],[GR.x+GR.w,GR.y,-1,1],[GR.x,GR.y+GR.h,1,-1],[GR.x+GR.w,GR.y+GR.h,-1,-1]].forEach(function(p){
      c.beginPath(); c.moveTo(p[0]+p[2]*cs,p[1]); c.lineTo(p[0],p[1]); c.lineTo(p[0],p[1]+p[3]*cs); c.stroke();
    });
    if(!found){
      var ly=GR.y+LY, g2=c.createLinearGradient(0,ly-2,0,ly+2);
      g2.addColorStop(0,'transparent'); g2.addColorStop(.5,'rgba(16,185,129,.9)'); g2.addColorStop(1,'transparent');
      c.fillStyle=g2; c.fillRect(GR.x+1,ly-2,GR.w-2,4);
      LY+=LD*2; if(LY>=GR.h)LD=-1; if(LY<=0)LD=1;
    }
    // Label acima do retangulo
    c.fillStyle='rgba(16,185,129,.95)'; c.font='bold 12px sans-serif'; c.textAlign='center';
    c.fillText('Posicione o SERIAL aqui',GR.vw/2,GR.y-7);
  }

  // Recorta area do guia (para BarcodeDetector/jsQR)
  function CROP_GUIDE(){
    var cv=document.createElement('canvas');
    var sx=VID.videoWidth/GR.vw, sy=VID.videoHeight/GR.vh;
    cv.width=Math.round(GR.w*sx); cv.height=Math.round(GR.h*sy);
    cv.getContext('2d').drawImage(VID,Math.round(GR.x*sx),Math.round(GR.y*sy),cv.width,cv.height,0,0,cv.width,cv.height);
    return cv;
  }

  // Recorta e pre-processa para OCR (grayscale + contraste alto)
  function CROP_OCR(){
    var sx=VID.videoWidth/GR.vw, sy=VID.videoHeight/GR.vh;
    var srcW=Math.round(GR.w*sx), srcH=Math.round(GR.h*sy);
    // Escala para pelo menos 800px de largura (Tesseract precisa de resolucao)
    var scale=Math.max(1, 800/srcW);
    var cv=document.createElement('canvas');
    cv.width=Math.round(srcW*scale); cv.height=Math.round(srcH*scale);
    var ctx=cv.getContext('2d');
    ctx.imageSmoothingEnabled=true; ctx.imageSmoothingQuality='high';
    ctx.drawImage(VID,Math.round(GR.x*sx),Math.round(GR.y*sy),srcW,srcH,0,0,cv.width,cv.height);
    // Pre-processamento: grayscale + boost contraste 2x
    var id=ctx.getImageData(0,0,cv.width,cv.height), d=id.data;
    for(var i=0;i<d.length;i+=4){
      var g=0.299*d[i]+0.587*d[i+1]+0.114*d[i+2];
      g=Math.min(255,Math.max(0,(g-128)*2+128));
      d[i]=d[i+1]=d[i+2]=g; // d[i+3] mantido (alpha)
    }
    ctx.putImageData(id,0,0);
    return cv;
  }

  // Botao Ler Serial: congela camera + OCR no recorte pre-processado
  BOCR.addEventListener('click',async function(){
    if(found||BOCR.disabled) return;
    BOCR.disabled=true; BOCR.textContent='Aguarde...';
    clearInterval(scanT); clearInterval(animT);
    VID.pause(); // congela ultimo frame
    ST.textContent='Carregando OCR...'; ST.style.color='#10b981';
    var Tes=window.Tesseract;
    if(!Tes){
      try{
        await new Promise(function(res,rej){
          var s=document.createElement('script'); // iframe — sem restricao CSP
          s.src='https://unpkg.com/tesseract.js@5/dist/tesseract.min.js';
          s.onload=res; s.onerror=function(){rej(new Error('CDN indisponivel'));};
          document.head.appendChild(s);
        });
        Tes=window.Tesseract;
      }catch(le){ST.textContent='Erro: '+le.message; ST.style.color='#ef4444'; resetCam(); return;}
    }
    if(!Tes){ST.textContent='OCR indisponivel'; ST.style.color='#ef4444'; resetCam(); return;}
    ST.textContent='Lendo serial...';
    try{
      var cv=CROP_OCR();
      var dataUrl=cv.toDataURL('image/png'); // PNG evita artefatos JPEG
      var w=await Tes.createWorker('eng');
      await w.setParameters({tessedit_pageseg_mode:'6'}); // bloco uniforme de texto
      var r=await w.recognize(dataUrl);
      await w.terminate();
      // Limpa resultado: mantém alfanumerico + separadores comuns
      var txt=r.data.text.replace(/[^A-Za-z0-9\-_\/\. ]/g,' ').replace(/\s+/g,' ').trim();
      if(txt.length>2){
        DONE({type:'ocr',value:txt});
      }else{
        ST.textContent='Nao foi possivel ler. Melhore o enquadramento e tente de novo.';
        ST.style.color='#f59e0b';
        resetCam();
      }
    }catch(e){ST.textContent='Erro OCR: '+e.message; ST.style.color='#ef4444'; resetCam();}
  });

  function resetCam(){
    VID.play(); BOCR.disabled=false; BOCR.textContent='Ler Serial';
    ST.textContent='Enquadre o serial no retangulo e toque em Ler Serial'; ST.style.color='#94a3b8';
    animT=setInterval(DRAW,30);
    scanT=setInterval(function(){
      if(found||!VID.videoWidth||!GR.vw) return;
      var cv=CROP_GUIDE();
      if(qrFn){var d=cv.getContext('2d').getImageData(0,0,cv.width,cv.height); var q=qrFn(d.data,d.width,d.height,{inversionAttempts:'dontInvert'}); if(q&&!found){DONE({type:'barcode',value:q.data}); return;}}
      if(BDinst){BDinst.detect(cv).then(function(cs){if(cs.length&&!found)DONE({type:'barcode',value:cs[0].rawValue});}).catch(function(){});}
    },300);
  }

  PD.getElementById('_bzmr').addEventListener('input',async function(){
    if(!stream)return;
    var track=stream.getVideoTracks()[0];
    var cap=track.getCapabilities?track.getCapabilities():{};
    if(cap.zoom){var z=cap.zoom; await track.applyConstraints({advanced:[{zoom:z.min+(z.max-z.min)*(this.value/100)}]}).catch(function(){});}
  });
  BCAN.addEventListener('click',function(){DONE({type:'cancel'});});
  window.addEventListener('resize',RZ);
})
"""

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
        st.session_state.setdefault(f"scan_cnt_{i}", 0)
        st.session_state.setdefault(f"foto_b64_{i}", None)
        st.session_state.setdefault(f"foto_hash_{i}", "")

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
                if not st.session_state[f"scan_on_{i}"]:
                    st.session_state[f"scan_cnt_{i}"] += 1
                st.session_state[f"scan_on_{i}"] = not st.session_state[f"scan_on_{i}"]
                st.rerun()

            if st.session_state[f"scan_on_{i}"] and _HAS_JS:
                scan_key = f"scan_{i}_{st.session_state[f'scan_cnt_{i}']}"
                result = _js_eval(js_expressions=_SCANNER_JS, key=scan_key)
                if result is not None:
                    st.session_state[f"scan_on_{i}"] = False
                    rtype = (result.get("type") or "") if isinstance(result, dict) else ""
                    rval  = (result.get("value") or "").strip() if isinstance(result, dict) else ""
                    if rtype in ("barcode", "ocr") and rval:
                        st.session_state[f"serial_{i}"] = rval
                    st.rerun()

            # ── Foto: somente upload ───────────────────────────────────────
            st.caption("Foto do equipamento (opcional)")
            upload = st.file_uploader(
                "Selecionar foto",
                type=["jpg", "jpeg", "png"],
                key=f"fotoup_{i}",
                label_visibility="collapsed",
            )
            if upload:
                raw_foto = upload.read()
                fh = hashlib.md5(raw_foto).hexdigest()[:10]
                if fh != st.session_state[f"foto_hash_{i}"]:
                    st.session_state[f"foto_b64_{i}"] = _img_to_b64(raw_foto)
                    st.session_state[f"foto_hash_{i}"] = fh

            if st.session_state[f"foto_b64_{i}"]:
                st.image(st.session_state[f"foto_b64_{i}"], width=180)

            verificado = st.checkbox("✅ Item verificado", key=f"veri_{i}")
            itens_state.append({
                "id": i,
                "serial": st.session_state[f"serial_{i}"],
                "verificado": verificado,
                "foto_b64": st.session_state[f"foto_b64_{i}"],
            })

    # ── Assinaturas ─────────────────────────────────────────────────────────
    st.divider()
    st.subheader("Assinaturas")
    assin_tec = assin_conf = None
    try:
        from streamlit_drawable_canvas import st_canvas
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
                for k in (f"serial_{i}", f"foto_b64_{i}", f"foto_hash_{i}"):
                    st.session_state.pop(k, None)
                st.session_state[f"scan_on_{i}"] = False
                st.session_state[f"scan_cnt_{i}"] = 0
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
                    rows_it.append({
                        "#": it.get("id"),
                        "Serial": it.get("serial") or "—",
                        "Verificado": "✅" if it.get("verificado") else "❌",
                    })
                df_it = pd.DataFrame(rows_it)
                ok_n = (df_it["Verificado"] == "✅").sum()
                st.markdown(f"**{ok_n}/{len(df_it)} itens verificados**")
                st.dataframe(df_it, use_container_width=True, hide_index=True)
            except Exception:
                st.warning("Não foi possível carregar os itens.")
