import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import requests
import time
from datetime import datetime, timedelta

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="SempreChat CRM", page_icon="💬", layout="wide")

# --- ESTILO ---
st.markdown("""
<style>
    .stApp { background-color: #efeae2; }
    .chat-bubble-cliente {
        background-color: #ffffff; color: #000; padding: 10px 15px;
        border-radius: 0px 15px 15px 15px; margin: 5px 0; max-width: 75%;
        float: left; clear: both; box-shadow: 0 1px 1px rgba(0,0,0,0.1);
    }
    .chat-bubble-empresa {
        background-color: #dcf8c6; color: #000; padding: 10px 15px;
        border-radius: 15px 0px 15px 15px; margin: 5px 0; max-width: 75%;
        float: right; clear: both; box-shadow: 0 1px 1px rgba(0,0,0,0.1); text-align: left;
    }
    .chat-time { display: block; font-size: 11px; color: #999; margin-top: 4px; text-align: right; }
    .stChatInputContainer { padding-bottom: 20px !important; }
    textarea[data-testid="stChatInputTextArea"] {
        min-height: 50px !important; height: auto !important;
        font-size: 16px !important; align-content: center !important;
    }
    div[data-testid="stExpander"] { border: none; box-shadow: none; background-color: transparent; }
</style>
""", unsafe_allow_html=True)

# --- BANCO ---
try:
    if "DATABASE_URL" in st.secrets:
        db_url = st.secrets["DATABASE_URL"].replace("postgres://", "postgresql://")
        engine = create_engine(db_url)
    else: st.error("⚠️ Configure DATABASE_URL nos Secrets."); st.stop()
except Exception as e: st.error(f"Erro Conexão DB: {e}"); st.stop()

# --- FUNÇÕES ---

@st.cache_data(ttl=60) 
def listar_todos_usuarios():
    with engine.connect() as conn: return pd.read_sql(text("SELECT id, nome, email, funcao, ativo FROM usuarios ORDER BY id"), conn)

@st.cache_data(ttl=60)
def listar_usuarios_ativos():
    with engine.connect() as conn: return pd.read_sql(text("SELECT id, nome FROM usuarios WHERE ativo=TRUE ORDER BY nome"), conn)

@st.cache_data(ttl=2) 
def carregar_fila(admin=False, usuario_id=None):
    with engine.connect() as conn:
        filtro = "" if admin else f"AND (c.vendedora_id = {usuario_id} OR c.vendedora_id IS NULL)"
        query = text(f"""
            SELECT c.id, c.nome, c.whatsapp_id, c.status_atendimento, u.nome as vendedora, c.codigo_cliente
            FROM contatos c
            LEFT JOIN usuarios u ON c.vendedora_id = u.id
            WHERE c.status_atendimento != 'encerrado' {filtro}
            ORDER BY c.ultima_interacao DESC
        """)
        return pd.read_sql(query, conn)

def carregar_mensagens(cid):
    with engine.connect() as conn: return pd.read_sql(text("SELECT id, remetente, texto, tipo, url_media, data_envio FROM mensagens WHERE contato_id = :cid ORDER BY data_envio ASC"), conn, params={"cid":cid})

def carregar_info_cliente(cid):
    with engine.connect() as conn: return conn.execute(text("SELECT nome, whatsapp_id, codigo_cliente, cpf_cnpj, notas_internas FROM contatos WHERE id=:id"), {"id":cid}).fetchone()

def gerar_relatorio_custos(dias=30):
    try:
        with engine.connect() as conn:
            query = text("""
                SELECT 
                    u.nome AS Vendedora,
                    COUNT(m.id) AS Qtd_Mensagens,
                    SUM(m.custo) AS Custo_Total
                FROM mensagens m
                JOIN contatos c ON m.contato_id = c.id
                LEFT JOIN usuarios u ON c.vendedora_id = u.id
                WHERE m.remetente = 'empresa' 
                  AND m.data_envio >= CURRENT_DATE - INTERVAL :d DAY
                  AND m.custo > 0
                GROUP BY u.nome
                ORDER BY Custo_Total DESC
            """)
            return pd.read_sql(query, conn, params={"d": f"{dias} days"})
    except: return pd.DataFrame()

# --- AÇÕES CRUD ---
def criar_usuario(n, e, s, f):
    try:
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO usuarios (nome, email, senha, funcao, ativo) VALUES (:n, :e, :s, :f, TRUE)"), {"n":n, "e":e, "s":s, "f":f})
            conn.commit()
        listar_todos_usuarios.clear(); return True, "Criado!"
    except Exception as er: return False, str(er)

def editar_usuario(uid, nn, ns=None):
    with engine.connect() as conn:
        conn.execute(text("UPDATE usuarios SET nome = :n WHERE id = :id"), {"n":nn, "id":uid})
        if ns: conn.execute(text("UPDATE usuarios SET senha = :s WHERE id = :id"), {"s":ns, "id":uid})
        conn.commit()
    listar_todos_usuarios.clear(); listar_usuarios_ativos.clear()

def excluir_usuario(uid):
    with engine.connect() as conn:
        conn.execute(text("UPDATE contatos SET vendedora_id = NULL WHERE vendedora_id = :id"), {"id":uid})
        conn.execute(text("DELETE FROM usuarios WHERE id = :id"), {"id":uid})
        conn.commit()
    listar_todos_usuarios.clear()

def atualizar_cliente_completo(cid, nome, codigo, notas):
    with engine.connect() as conn:
        conn.execute(text("UPDATE contatos SET nome=:nm, codigo_cliente=:c, notas_internas=:n WHERE id=:id"), {"nm":nome, "c":codigo, "n":notas, "id":cid})
        conn.commit()
    carregar_fila.clear()

def transferir_atendimento(cid, vid):
    with engine.connect() as conn:
        conn.execute(text("UPDATE contatos SET vendedora_id=:vid, status_atendimento='em_andamento' WHERE id=:cid"), {"vid":vid, "cid":cid})
        conn.commit()
    carregar_fila.clear()

def encerrar_atendimento(cid):
    with engine.connect() as conn:
        conn.execute(text("UPDATE contatos SET status_atendimento='encerrado' WHERE id=:cid"), {"cid":cid})
        conn.commit()
    carregar_fila.clear()

def pegar_msg_boas_vindas():
    try:
        with engine.connect() as conn:
            res = conn.execute(text("SELECT valor FROM configuracoes WHERE chave='msg_boas_vindas'")).fetchone()
            return res[0] if res else ""
    except Exception: return "" 

def salvar_msg_boas_vindas(txt):
    try:
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO configuracoes (chave, valor) VALUES ('msg_boas_vindas', :v) ON CONFLICT (chave) DO UPDATE SET valor = :v"), {"v":txt})
            conn.commit()
        return True, "Salvo!"
    except Exception as e: return False, f"Erro: {e}"

# --- TEMPLATES (COM CUSTO) ---
def criar_template(nome_tecnico, custo):
    try:
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO templates (nome_tecnico, idioma, custo_estimado) VALUES (:n, 'pt_BR', :c)"), {"n":nome_tecnico, "c":custo})
            conn.commit()
        return True, "Cadastrado!"
    except Exception as e: return False, str(e)

def listar_templates():
    try:
        with engine.connect() as conn: return pd.read_sql(text("SELECT * FROM templates ORDER BY nome_tecnico"), conn)
    except: return pd.DataFrame()

def excluir_template(tid):
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM templates WHERE id=:id"), {"id":tid})
        conn.commit()

# --- META API ---
def upload_para_meta(uploaded_file, mime_type):
    url = f"https://graph.facebook.com/v18.0/{st.secrets['META_PHONE_ID']}/media"
    headers = {"Authorization": f"Bearer {st.secrets['META_TOKEN']}"}
    files = {'file': (uploaded_file.name, uploaded_file.getvalue(), mime_type)}
    data = {'messaging_product': 'whatsapp'}
    try:
        response = requests.post(url, headers=headers, files=files, data=data)
        if response.status_code == 200: return response.json()['id']
        else: return None
    except: return None

def get_media_bytes(media_id):
    try:
        url = f"https://graph.facebook.com/v18.0/{media_id}"
        headers = {"Authorization": f"Bearer {st.secrets['META_TOKEN']}"}
        r = requests.get(url, headers=headers).json()
        if 'url' in r: return requests.get(r['url'], headers=headers).content
    except: return None

def enviar_mensagem_api(telefone, conteudo, tipo="text", template_name=None):
    tel = ''.join(filter(str.isdigit, str(telefone)))
    if len(tel) == 13 and tel.startswith("55"): tel = tel[:4] + tel[5:]
    url = f"https://graph.facebook.com/v18.0/{st.secrets['META_PHONE_ID']}/messages"
    headers = {"Authorization": f"Bearer {st.secrets['META_TOKEN']}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": tel, "type": tipo}
    
    if tipo == 'text': payload['text'] = {"body": conteudo}
    elif tipo == 'template': payload['template'] = {"name": template_name, "language": {"code": "pt_BR"}}
    elif tipo == 'image': payload['image'] = {"id": conteudo}
    elif tipo == 'document': payload['document'] = {"id": conteudo, "filename": "Anexo"}
    elif tipo == 'audio': payload['audio'] = {"id": conteudo}
        
    try:
        resp = requests.post(url, headers=headers, json=payload)
        return resp.status_code, resp.json()
    except Exception as e: return 500, str(e)

# --- RR ---
def criar_rr(t, tx, uid):
    with engine.connect() as conn:
        conn.execute(text("INSERT INTO respostas_rapidas (titulo, texto, criado_por) VALUES (:t, :tx, :u)"), {"t":t, "tx":tx, "u":uid})
        conn.commit()
def listar_rr():
    with engine.connect() as conn: return pd.read_sql(text("SELECT * FROM respostas_rapidas"), conn)
def excluir_rr(rid):
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM respostas_rapidas WHERE id=:id"), {"id":rid})
        conn.commit()

# =======================
# 🖥️ INTERFACE
# =======================

if "usuario" not in st.session_state: st.session_state.usuario = None
if "pagina" not in st.session_state: st.session_state.pagina = "chat"

if st.session_state.usuario is None:
    c1,c2,c3 = st.columns([1,2,1])
    with c2:
        st.title("🔐 SempreChat")
        with st.form("login"):
            email = st.text_input("Login")
            senha = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar"):
                def verif(e, s):
                    with engine.connect() as conn: return conn.execute(text("SELECT id, nome, funcao FROM usuarios WHERE email=:e AND senha=:s AND ativo=TRUE"), {"e":e,"s":s}).fetchone()
                try:
                    u = verif(email, senha)
                    if u: st.session_state.usuario = {"id":u[0], "nome":u[1], "funcao":u[2]}; st.rerun()
                    else: st.error("Login inválido")
                except Exception as e: st.error("Erro Conexão Banco")
else:
    # --- SIDEBAR ---
    with st.sidebar:
        st.write(f"👤 **{st.session_state.usuario['nome']}**")
        st.caption(st.session_state.usuario['funcao'])
        if st.button("💬 Chat", use_container_width=True): st.session_state.pagina = "chat"; st.rerun()
        if st.button("⚡ Respostas", use_container_width=True): st.session_state.pagina = "respostas"; st.rerun()
        if st.session_state.usuario['funcao']=='admin':
            if st.button("⚙️ Admin", use_container_width=True): st.session_state.pagina = "admin"; st.rerun()
        if st.button("Sair", type="primary"): st.session_state.usuario = None; st.rerun()
        st.divider()
        if st.session_state.pagina == "chat":
            st.subheader("📥 Fila")
            is_adm = st.session_state.usuario['funcao']=='admin'
            try:
                df = carregar_fila(is_adm, st.session_state.usuario['id'])
                if df.empty: st.info("Vazia")
                for _, r in df.iterrows():
                    d = f"🟢 {r['nome']}"
                    if is_adm and r['vendedora']: d = f"🔒 {r['vendedora']} | {r['nome']}"
                    if r['codigo_cliente']: d += f" ({r['codigo_cliente']})"
                    if st.button(d, key=f"c_{r['id']}", use_container_width=True):
                        st.session_state.chat_ativo = r['id']; st.rerun()
            except Exception as e: st.error("Erro Fila")

    # --- CHAT ---
    if st.session_state.pagina == "chat":
        if "chat_ativo" in st.session_state:
            cli = carregar_info_cliente(st.session_state.chat_ativo)
            if not cli: st.warning("Cliente sumiu"); st.stop()
            
            c1, c2, c3 = st.columns([3, 1, 1])
            with c1: st.title(cli[0]); st.code(cli[1], language="text")
            with c2: 
                us = listar_usuarios_ativos()
                ud = {u[1]:u[0] for _,u in us.iterrows()}
                d = st.selectbox("Transferir", ["--"]+list(ud.keys()), label_visibility="collapsed")
                if d!="--": 
                    if st.button("Ok", key="tf"): transferir_atendimento(st.session_state.chat_ativo, ud[d]); st.success("Foi!"); time.sleep(0.5); st.rerun()
            with c3:
                if st.button("🔴 Fim", use_container_width=True): 
                    encerrar_atendimento(st.session_state.chat_ativo); del st.session_state['chat_ativo']; st.success("Fim"); st.rerun()

            with st.expander(f"📝 Perfil (Cód: {cli[2] if cli[2] else '--'})"):
                with st.form("fc"):
                    ncli = st.text_input("Nome", value=cli[0])
                    nc = st.text_input("Código/CPF", value=cli[2] if cli[2] else "")
                    nn = st.text_area("Notas", value=cli[4] if cli[4] else "")
                    if st.form_submit_button("Salvar"): atualizar_cliente_completo(st.session_state.chat_ativo, ncli, nc, nn); st.success("Salvo!"); st.rerun()

            st.divider()

            msgs = carregar_mensagens(st.session_state.chat_ativo)
            with st.container(height=400):
                if msgs.empty: st.info("Início da conversa.")
                for _, r in msgs.iterrows():
                    cls = "chat-bubble-cliente" if r['remetente']=='cliente' else "chat-bubble-empresa"
                    h = r['data_envio'].strftime('%H:%M')
                    cnt = f"<span>{r['texto']}</span>" if r['texto'] and r['texto']!="None" else ""
                    st.markdown(f"""<div class="{cls}">{cnt}<span class="chat-time">{h}</span></div>""", unsafe_allow_html=True)
                    if r['tipo'] in ['image','audio','voice','document'] and r['url_media']:
                        dt = get_media_bytes(r['url_media'])
                        if dt:
                            cl_a, cl_b, cl_c = st.columns([1,2,1])
                            tc = cl_c if r['remetente']=='empresa' else cl_a
                            with tc:
                                if r['tipo']=='image': st.image(dt, width=200)
                                elif r['tipo']=='audio': st.audio(dt)
                                elif r['tipo']=='document': st.download_button("📄 Baixar", dt, file_name="anexo.pdf", key=f"f_{r['id']}")

            with st.expander("📎 Anexar"):
                uploaded_file = st.file_uploader("Arquivo", type=['png', 'jpg', 'pdf', 'mp3', 'ogg', 'wav'])
                if uploaded_file and st.button("Enviar Arq"):
                    with st.spinner("Enviando..."):
                        mime = uploaded_file.type
                        tmsg = "document"
                        if "image" in mime: tmsg = "image"
                        elif "audio" in mime: tmsg = "audio"
                        mid = upload_para_meta(uploaded_file, mime)
                        if mid:
                            c, r = enviar_mensagem_api(cli[1], mid, tipo=tmsg)
                            if c in [200, 201]:
                                with engine.connect() as conn:
                                    conn.execute(text("INSERT INTO mensagens (contato_id, remetente, texto, tipo, url_media) VALUES (:cid, 'empresa', :txt, :tipo, :url)"), {"cid":st.session_state.chat_ativo, "txt":f"Arq: {uploaded_file.name}", "tipo":tmsg, "url":mid})
                                    conn.commit()
                                st.rerun()
                            else: st.error(f"Erro Meta: {r}")

            rr = listar_rr(); rrd = {r[1]:r[2] for _,r in rr.iterrows()}
            rr_sel = st.selectbox("⚡ Rápida", ["--"]+list(rrd.keys()))
            if rr_sel != "--":
                tr = rrd[rr_sel]; st.info(f"Enviar: {tr}")
                if st.button("🚀 Enviar"):
                    c,r = enviar_mensagem_api(cli[1], tr)
                    if c in [200,201]:
                        with engine.connect() as conn:
                            conn.execute(text("INSERT INTO mensagens (contato_id, remetente, texto, tipo, custo) VALUES (:cid,'empresa',:t,'text',0)"), {"cid":st.session_state.chat_ativo, "t":tr})
                            conn.commit()
                        st.rerun()
            
            if prompt := st.chat_input("Mensagem..."):
                c,r = enviar_mensagem_api(cli[1], prompt)
                if c in [200,201]:
                    with engine.connect() as conn:
                        conn.execute(text("INSERT INTO mensagens (contato_id, remetente, texto, tipo, custo) VALUES (:cid,'empresa',:t,'text',0)"), {"cid":st.session_state.chat_ativo, "t":prompt})
                        conn.commit()
                    st.rerun()

            with st.expander("📢 Enviar Template"):
                df_tpl = listar_templates()
                if not df_tpl.empty:
                    # Cria lista de templates (nome) e um dicionário de custos
                    tpl_list = df_tpl['nome_tecnico'].tolist()
                    tpl_costs = {row['nome_tecnico']: row['custo_estimado'] for _, row in df_tpl.iterrows()}
                    
                    tpl_sel = st.selectbox("Selecione", ["--"] + tpl_list)
                    if tpl_sel != "--":
                        custo_tpl = tpl_costs.get(tpl_sel, 0.0)
                        st.info(f"Custo Estimado: R$ {custo_tpl:.2f}")
                        
                        if st.button(f"Enviar '{tpl_sel}'"):
                            c,r = enviar_mensagem_api(cli[1], "", "template", tpl_sel)
                            if c in [200,201]:
                                with engine.connect() as conn:
                                    conn.execute(text("INSERT INTO mensagens (contato_id, remetente, texto, tipo, custo) VALUES (:cid,'empresa',:t,'template',:c)"), {"cid":st.session_state.chat_ativo, "t":f"[TPL: {tpl_sel}]", "c":custo_tpl})
                                    conn.commit()
                                st.success("Enviado"); st.rerun()
                            else: st.error(f"Erro: {r}")
                else: st.warning("Sem templates.")
        else: st.info("👈 Selecione um cliente.")

    elif st.session_state.pagina == "respostas":
        st.header("⚡ Gerenciar Respostas")
        with st.form("nrr"):
            t = st.text_input("Título"); tx = st.text_area("Texto")
            if st.form_submit_button("Criar"): criar_rr(t, tx, st.session_state.usuario['id']); st.rerun()
        df = listar_rr()
        for _,r in df.iterrows():
            c1,c2,c3 = st.columns([1,4,1])
            c1.write(f"**{r['titulo']}**"); c2.text(r['texto'])
            if c3.button("🗑️", key=f"dr_{r['id']}"): excluir_rr(r['id']); st.rerun()
        if st.button("Voltar"): st.session_state.pagina="chat"; st.rerun()

    elif st.session_state.pagina == "admin":
        st.header("⚙️ Admin")
        tab1, tab2, tab3, tab4, tab5 = st.tabs(["➕ Usuários", "📝 Editar/Listar", "🤖 Config Robô", "📢 Templates", "💰 Custos"])
        
        with tab1:
            with st.form("nu"):
                n = st.text_input("Nome"); e = st.text_input("Login"); s = st.text_input("Senha"); f = st.selectbox("Função", ["vendedor","admin"])
                if st.form_submit_button("Cadastrar"): 
                    b,m = criar_usuario(n,e,s,f); 
                    if b: st.success(m)
                    else: st.error(m)
        with tab2:
            dfu = listar_todos_usuarios(); st.dataframe(dfu)
            u_ids = dfu['id'].tolist()
            sel_uid = st.selectbox("Selecione", u_ids, format_func=lambda x: dfu[dfu['id']==x]['nome'].values[0])
            with st.form("edit_user"):
                nn = st.text_input("Novo Nome"); ns = st.text_input("Nova Senha", type="password")
                if st.form_submit_button("Salvar"): editar_usuario(sel_uid, nn, ns if ns else None); st.success("Salvo!"); time.sleep(1); st.rerun()
            if st.button("Excluir", type="primary"): excluir_usuario(sel_uid); st.rerun()
        with tab3:
            msg = pegar_msg_boas_vindas()
            with st.form("cr"):
                txt = st.text_area("Saudação Automática", value=msg)
                if st.form_submit_button("Salvar"): 
                    sucesso, retorno = salvar_msg_boas_vindas(txt)
                    if sucesso: st.success("Ok")
                    else: st.error(f"Erro: {retorno}")
        
        with tab4:
            st.info("Cadastre o nome TÉCNICO e o CUSTO do template.")
            with st.form("ntpl"):
                nt = st.text_input("Nome Técnico (ex: contato_inicial)")
                ce = st.number_input("Custo Estimado (R$)", min_value=0.0, max_value=5.0, value=0.05, step=0.01)
                if st.form_submit_button("Cadastrar"):
                    b, m = criar_template(nt, ce)
                    if b: st.success(m); st.rerun()
                    else: st.error(m)
            st.divider()
            dft = listar_templates()
            if not dft.empty:
                # Mostra tabela bonitinha com custo
                st.dataframe(dft.style.format({"custo_estimado": "R$ {:.2f}"}), use_container_width=True)
                # Opção de exclusão
                for _, row in dft.iterrows():
                    if st.button(f"🗑️ Excluir {row['nome_tecnico']}", key=f"dt_{row['id']}"): 
                        excluir_template(row['id']); st.rerun()
        
        with tab5:
            st.subheader("💰 Relatório de Custos (Templates)")
            dias = st.slider("Período (dias)", 1, 90, 30)
            df_fin = gerar_relatorio_custos(dias)
            if not df_fin.empty:
                t1 = df_fin['custo_total'].sum()
                t2 = df_fin['qtd_mensagens'].sum()
                m1, m2 = st.columns(2)
                m1.metric("Custo Total", f"R$ {t1:.2f}")
                m2.metric("Qtd Mensagens", t2)
                st.dataframe(df_fin.style.format({"custo_total": "R$ {:.2f}"}), use_container_width=True)
                st.bar_chart(df_fin, x="vendedora", y="custo_total")
            else: st.info("Sem custos no período.")

        if st.button("Voltar"): st.session_state.pagina="chat"; st.rerun()
