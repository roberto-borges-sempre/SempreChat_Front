import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import requests
import time

# --- CONFIGURAÇÃO DA PÁGINA ---
st.set_page_config(page_title="SempreChat CRM", page_icon="💬", layout="wide")

# --- ESTILO VISUAL ---
st.markdown("""
<style>
    .stApp { background-color: #efeae2; }
    
    /* Balões de Chat */
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
    
    /* === CAIXA DE TEXTO MAIOR === */
    .stChatInputContainer { padding-bottom: 20px !important; }
    textarea[data-testid="stChatInputTextArea"] {
        min-height: 50px !important;
        height: auto !important;
        font-size: 16px !important;
        align-content: center !important;
    }
</style>
""", unsafe_allow_html=True)

# --- CONEXÃO BANCO ---
try:
    if "DATABASE_URL" in st.secrets:
        db_url = st.secrets["DATABASE_URL"].replace("postgres://", "postgresql://")
        engine = create_engine(db_url)
    else:
        st.error("⚠️ Configure DATABASE_URL nos Secrets."); st.stop()
except Exception as e:
    st.error(f"Erro Conexão DB: {e}"); st.stop()

# =======================
# 🛠️ FUNÇÕES DE BANCO
# =======================

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
    with engine.connect() as conn: return pd.read_sql(text("SELECT remetente, texto, tipo, url_media, data_envio FROM mensagens WHERE contato_id = :cid ORDER BY data_envio ASC"), conn, params={"cid":cid})

def carregar_info_cliente(cid):
    with engine.connect() as conn: return conn.execute(text("SELECT nome, whatsapp_id, codigo_cliente, cpf_cnpj, notas_internas FROM contatos WHERE id=:id"), {"id":cid}).fetchone()

# --- AÇÕES ---
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

# --- UPDATE CLIENTE ---
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

# --- CONFIGURAÇÃO ROBÔ ---
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
    except Exception as e:
        return False, f"Erro Banco: {e}"

# --- META API ---
def get_media_bytes(media_id):
    try:
        url = f"https://graph.facebook.com/v18.0/{media_id}"
        headers = {"Authorization": f"Bearer {st.secrets['META_TOKEN']}"}
        r = requests.get(url, headers=headers).json()
        if 'url' in r: return requests.get(r['url'], headers=headers).content
    except: return None

def enviar_mensagem_api(telefone, texto, tipo="text", template_name=None):
    tel = ''.join(filter(str.isdigit, str(telefone)))
    if len(tel) == 13 and tel.startswith("55"): tel = tel[:4] + tel[5:]
    url = f"https://graph.facebook.com/v18.0/{st.secrets['META_PHONE_ID']}/messages"
    headers = {"Authorization": f"Bearer {st.secrets['META_TOKEN']}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": tel, "type": tipo}
    cost = 0.0
    if tipo == 'text': payload['text'] = {"body": texto}
    elif tipo == 'template': 
        payload['template'] = {"name": template_name, "language": {"code": "pt_BR"}}; cost = 0.05
    try:
        resp = requests.post(url, headers=headers, json=payload)
        return resp.status_code, resp.json(), cost
    except Exception as e: return 500, str(e), 0.0

# --- RESPOSTAS RÁPIDAS ---
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

# --- LOGIN (ALTERADO PARA "LOGIN") ---
if st.session_state.usuario is None:
    c1,c2,c3 = st.columns([1,2,1])
    with c2:
        st.title("🔐 SempreChat")
        with st.form("login"):
            # AQUI: Mudei o label para "Login"
            email = st.text_input("Login")
            senha = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar"):
                def verif(e, s):
                    with engine.connect() as conn: return conn.execute(text("SELECT id, nome, funcao FROM usuarios WHERE email=:e AND senha=:s AND ativo=TRUE"), {"e":e,"s":s}).fetchone()
                try:
                    u = verif(email, senha)
                    if u: st.session_state.usuario = {"id":u[0], "nome":u[1], "funcao":u[2]}; st.rerun()
                    else: st.error("Login inválido")
                except Exception as e:
                    st.error("Erro: Banco desconectado!")
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
            except Exception as e:
                st.error("Erro Fila"); st.code(str(e))

    # --- CHAT ---
    if st.session_state.pagina == "chat":
        if "chat_ativo" in st.session_state:
            cli = carregar_info_cliente(st.session_state.chat_ativo)
            if not cli: st.warning("Cliente sumiu"); st.stop()
            
            # HEADER
            c1, c2, c3 = st.columns([3, 1, 1])
            with c1: 
                st.title(cli[0])
                st.code(cli[1], language="text")
            
            with c2: 
                us = listar_usuarios_ativos()
                ud = {u[1]:u[0] for _,u in us.iterrows()}
                d = st.selectbox("Transferir", ["--"]+list(ud.keys()), label_visibility="collapsed")
                if d!="--": 
                    if st.button("Ok", key="tf"): transferir_atendimento(st.session_state.chat_ativo, ud[d]); st.success("Foi!"); time.sleep(0.5); st.rerun()
            with c3:
                if st.button("🔴 Fim", use_container_width=True): 
                    encerrar_atendimento(st.session_state.chat_ativo); del st.session_state['chat_ativo']; st.success("Fim"); st.rerun()

            # AQUI: Mudei para "Perfil do Cliente"
            with st.expander(f"📝 Perfil do Cliente (Cód: {cli[2] if cli[2] else '--'})"):
                with st.form("fc"):
                    novo_nome_cliente = st.text_input("Nome do Cliente", value=cli[0])
                    nc = st.text_input("Código / CPF / CNPJ", value=cli[2] if cli[2] else "")
                    nn = st.text_area("Notas", value=cli[4] if cli[4] else "")
                    if st.form_submit_button("💾 Salvar Dados"): 
                        atualizar_cliente_completo(st.session_state.chat_ativo, novo_nome_cliente, nc, nn)
                        st.success("Atualizado!"); st.rerun()

            st.divider()

            # MENSAGENS
            msgs = carregar_mensagens(st.session_state.chat_ativo)
            with st.container(height=450):
                if msgs.empty: st.info("Início da conversa.")
                for _, r in msgs.iterrows():
                    cls = "chat-bubble-cliente" if r['remetente']=='cliente' else "chat-bubble-empresa"
                    h = r['data_envio'].strftime('%H:%M')
                    cnt = f"<span>{r['texto']}</span>" if r['texto'] and r['texto']!="None" else ""
                    st.markdown(f"""<div class="{cls}">{cnt}<span class="chat-time">{h}</span></div>""", unsafe_allow_html=True)
                    if r['tipo'] in ['image','audio','voice'] and r['url_media']:
                        dt = get_media_bytes(r['url_media'])
                        if dt:
                            cl_a, cl_b, cl_c = st.columns([1,2,1])
                            tc = cl_c if r['remetente']=='empresa' else cl_a
                            with tc:
                                if r['tipo']=='image': st.image(dt, width=150)
                                else: st.audio(dt)

            # INPUT AREA
            rr = listar_rr(); rrd = {r[1]:r[2] for _,r in rr.iterrows()}
            rr_sel = st.selectbox("⚡ Usar Resposta Rápida", ["--"]+list(rrd.keys()))
            
            if rr_sel != "--":
                txt_rr = rrd[rr_sel]
                st.info(f"Enviar: {txt_rr}")
                if st.button("🚀 Enviar Rápida"):
                    c,r,co = enviar_mensagem_api(cli[1], txt_rr)
                    if c in [200,201]:
                        with engine.connect() as conn:
                            conn.execute(text("INSERT INTO mensagens (contato_id, remetente, texto, tipo, custo) VALUES (:cid,'empresa',:t,'text',0)"), {"cid":st.session_state.chat_ativo, "t":txt_rr})
                            conn.commit()
                        st.rerun()
            
            if prompt := st.chat_input("Digite sua mensagem..."):
                c,r,co = enviar_mensagem_api(cli[1], prompt)
                if c in [200,201]:
                    with engine.connect() as conn:
                        conn.execute(text("INSERT INTO mensagens (contato_id, remetente, texto, tipo, custo) VALUES (:cid,'empresa',:t,'text',0)"), {"cid":st.session_state.chat_ativo, "t":prompt})
                        conn.commit()
                    st.rerun()
                else: st.error(f"Erro: {r}")

            with st.expander("📢 Template"):
                tp = st.text_input("Nome Template")
                if st.button("Enviar Tpl"):
                    c,r,co = enviar_mensagem_api(cli[1], "", "template", tp)
                    if c in [200,201]:
                        with engine.connect() as conn:
                            conn.execute(text("INSERT INTO mensagens (contato_id, remetente, texto, tipo, custo) VALUES (:cid,'empresa',:t,'template',:c)"), {"cid":st.session_state.chat_ativo, "t":f"[TPL: {tp}]", "c":co})
                            conn.commit()
                        st.success("Enviado"); st.rerun()
        else: st.info("👈 Selecione um cliente.")

    # --- RESPOSTAS ---
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

    # --- ADMIN ---
    elif st.session_state.pagina == "admin":
        st.header("⚙️ Admin")
        tab1, tab2, tab3 = st.tabs(["➕ Usuários", "📝 Editar/Listar", "🤖 Config Robô"])
        with tab1:
            with st.form("nu"):
                # AQUI: Mudei para Login também
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
                txt = st.text_area("Saudação Automática (Robô)", value=msg)
                if st.form_submit_button("Salvar"): 
                    sucesso, retorno = salvar_msg_boas_vindas(txt)
                    if sucesso: st.success("Ok")
                    else: st.error(f"Erro: {retorno}")
        if st.button("Voltar"): st.session_state.pagina="chat"; st.rerun()
