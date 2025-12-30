import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import requests
import time

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="SempreChat CRM", page_icon="📶", layout="wide")

# --- BANCO ---
try:
    if "DATABASE_URL" in st.secrets:
        db_url = st.secrets["DATABASE_URL"].replace("postgres://", "postgresql://")
        engine = create_engine(db_url)
    else:
        st.error("Configure DATABASE_URL.")
        st.stop()
except Exception as e:
    st.error(f"Erro DB: {e}")
    st.stop()

# =======================
# 🛠️ FUNÇÕES BACKEND
# =======================

@st.cache_data(ttl=60) 
def listar_todos_usuarios():
    with engine.connect() as conn:
        return pd.read_sql(text("SELECT id, nome, email, funcao, ativo FROM usuarios ORDER BY id"), conn)

@st.cache_data(ttl=60)
def listar_usuarios_ativos():
    with engine.connect() as conn:
        return pd.read_sql(text("SELECT id, nome FROM usuarios WHERE ativo=TRUE ORDER BY nome"), conn)

@st.cache_data(ttl=5)
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
    with engine.connect() as conn:
        return pd.read_sql(text("SELECT remetente, texto, tipo, url_media, data_envio FROM mensagens WHERE contato_id = :cid ORDER BY data_envio ASC"), conn, params={"cid":cid})

def carregar_info_cliente(cid):
    with engine.connect() as conn:
        return conn.execute(text("SELECT nome, whatsapp_id, codigo_cliente, cpf_cnpj, notas_internas FROM contatos WHERE id=:id"), {"id":cid}).fetchone()

# --- FUNÇÕES DE EDIÇÃO ---
def criar_usuario(nome, email, senha, funcao):
    try:
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO usuarios (nome, email, senha, funcao, ativo) VALUES (:n, :e, :s, :f, TRUE)"), {"n":nome, "e":email, "s":senha, "f":funcao})
            conn.commit()
        listar_todos_usuarios.clear()
        return True, "Criado!"
    except Exception as e: return False, str(e)

def editar_usuario(user_id, novo_nome, nova_senha=None):
    with engine.connect() as conn:
        conn.execute(text("UPDATE usuarios SET nome = :n WHERE id = :id"), {"n":novo_nome, "id":user_id})
        if nova_senha:
            conn.execute(text("UPDATE usuarios SET senha = :s WHERE id = :id"), {"s":nova_senha, "id":user_id})
        conn.commit()
    listar_todos_usuarios.clear()
    listar_usuarios_ativos.clear()

def excluir_usuario(id_u):
    with engine.connect() as conn:
        conn.execute(text("UPDATE contatos SET vendedora_id = NULL WHERE vendedora_id = :id"), {"id":id_u})
        conn.execute(text("DELETE FROM usuarios WHERE id = :id"), {"id":id_u})
        conn.commit()
    listar_todos_usuarios.clear()

# --- CONFIGURAÇÃO ROBÔ ---
def pegar_msg_boas_vindas():
    with engine.connect() as conn:
        res = conn.execute(text("SELECT valor FROM configuracoes WHERE chave='msg_boas_vindas'")).fetchone()
        return res[0] if res else ""

def salvar_msg_boas_vindas(texto):
    with engine.connect() as conn:
        conn.execute(text("INSERT INTO configuracoes (chave, valor) VALUES ('msg_boas_vindas', :v) ON CONFLICT (chave) DO UPDATE SET valor = :v"), {"v":texto})
        conn.commit()

# --- META API ---
def enviar_mensagem_api(telefone, texto, tipo="text", template_name=None):
    tel = ''.join(filter(str.isdigit, str(telefone)))
    if len(tel) == 13 and tel.startswith("55"): tel = tel[:4] + tel[5:]
    url = f"https://graph.facebook.com/v18.0/{st.secrets['META_PHONE_ID']}/messages"
    headers = {"Authorization": f"Bearer {st.secrets['META_TOKEN']}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": tel, "type": tipo}
    cost = 0.0
    if tipo == 'text': payload['text'] = {"body": texto}
    elif tipo == 'template': 
        payload['template'] = {"name": template_name, "language": {"code": "pt_BR"}}
        cost = 0.05
    try:
        resp = requests.post(url, headers=headers, json=payload)
        return resp.status_code, resp.json(), cost
    except Exception as e: return 500, str(e), 0.0

# --- LÓGICA DE UPDATE CLIENTE ---
def atualizar_cliente(cid, codigo, notas):
    with engine.connect() as conn:
        conn.execute(text("UPDATE contatos SET codigo_cliente=:c, notas_internas=:n WHERE id=:id"), {"c":codigo, "n":notas, "id":cid})
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
if "chat_input_text" not in st.session_state: st.session_state.chat_input_text = ""

# --- LOGIN ---
if st.session_state.usuario is None:
    c1,c2,c3 = st.columns([1,2,1])
    with c2:
        st.title("📶 SempreChat Login")
        with st.form("login"):
            email = st.text_input("Email")
            senha = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar"):
                def verificar_login(e, s):
                    with engine.connect() as conn: return conn.execute(text("SELECT id, nome, funcao FROM usuarios WHERE email=:e AND senha=:s AND ativo=TRUE"), {"e":e,"s":s}).fetchone()
                u = verificar_login(email, senha)
                if u:
                    st.session_state.usuario = {"id":u[0], "nome":u[1], "funcao":u[2]}
                    st.rerun()
                else: st.error("Erro no login")
else:
    # SIDEBAR
    with st.sidebar:
        st.write(f"👤 **{st.session_state.usuario['nome']}**")
        st.caption(st.session_state.usuario['funcao'])
        if st.button("💬 Chat", use_container_width=True): st.session_state.pagina = "chat"; st.rerun()
        if st.button("⚡ Respostas", use_container_width=True): st.session_state.pagina = "respostas"; st.rerun()
        if st.session_state.usuario['funcao']=='admin':
            if st.button("⚙️ Admin / Equipe", use_container_width=True): st.session_state.pagina = "admin"; st.rerun()
        if st.button("Sair", type="primary"): st.session_state.usuario = None; st.rerun()
        st.divider()
        
        # FILA
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
                        st.session_state.chat_ativo = r['id']
                        st.rerun()
            except: st.error("Erro fila")

    # --- CHAT ---
    if st.session_state.pagina == "chat":
        if "chat_ativo" in st.session_state:
            cli = carregar_info_cliente(st.session_state.chat_ativo)
            if not cli: st.warning("Cliente sumiu"); st.stop()
            
            c1,c2,c3 = st.columns([3,1,1])
            with c1: st.markdown(f"### 💬 {cli[0]}")
            with c2: 
                us = listar_usuarios_ativos()
                ud = {u[1]:u[0] for _,u in us.iterrows()}
                d = st.selectbox("Transf", ["--"]+list(ud.keys()), label_visibility="collapsed")
                if d!="--": 
                    if st.button("Ok", key="tf"): transferir_atendimento(st.session_state.chat_ativo, ud[d]); st.success("Foi!"); time.sleep(1); st.rerun()
            with c3:
                if st.button("🔴 Fim", use_container_width=True): encerrar_atendimento(st.session_state.chat_ativo); del st.session_state['chat_ativo']; st.success("Fim"); st.rerun()

            with st.expander("📝 Cadastro"):
                with st.form("fc"):
                    # LABEL ALTERADA CONFORME PEDIDO
                    nc = st.text_input("Código / CPF / CNPJ", value=cli[2] if cli[2] else "")
                    nn = st.text_area("Notas", value=cli[4] if cli[4] else "")
                    if st.form_submit_button("Salvar"): atualizar_cliente(st.session_state.chat_ativo, nc, nn); st.success("Salvo"); st.rerun()
            
            st.divider()
            # MSG
            msgs = carregar_mensagens(st.session_state.chat_ativo)
            with st.container(height=450):
                if msgs.empty: st.info("Nada aqui.")
                for _,r in msgs.iterrows():
                    av = "👤" if r['remetente']=='cliente' else "🏢"
                    with st.chat_message(r['remetente'], avatar=av):
                        if r['texto'] and r['texto']!="None": st.write(r['texto'])
                        st.caption(r['data_envio'].strftime('%H:%M'))

            # ENVIO
            rr = listar_rr()
            rrd = {r[1]:r[2] for _,r in rr.iterrows()}
            rrs = st.selectbox("⚡ Rápida", ["--"]+list(rrd.keys()))
            if rrs!="--": st.session_state.chat_input_text = rrd[rrs]

            def send_cb():
                txt = st.session_state.chat_input_text
                if txt:
                    c,r,co = enviar_mensagem_api(cli[1], txt)
                    if c in [200,201]:
                        with engine.connect() as conn:
                            conn.execute(text("INSERT INTO mensagens (contato_id, remetente, texto, tipo, custo) VALUES (:cid,'empresa',:t,'text',0)"), {"cid":st.session_state.chat_ativo, "t":txt})
                            conn.commit()
                        st.session_state.chat_input_text = ""
            
            c_in, c_b = st.columns([6,1])
            c_in.text_input("Msg", key="chat_input_text", on_change=send_cb)
            if c_b.button("Enviar"): send_cb(); st.rerun()

            with st.expander("📢 Template"):
                tp = st.text_input("Nome Template")
                if st.button("Enviar Tpl"):
                    c,r,co = enviar_mensagem_api(cli[1], "", "template", tp)
                    if c in [200,201]:
                        with engine.connect() as conn:
                            conn.execute(text("INSERT INTO mensagens (contato_id, remetente, texto, tipo, custo) VALUES (:cid,'empresa',:t,'template',:c)"), {"cid":st.session_state.chat_ativo, "t":f"[TPL: {tp}]", "c":co})
                            conn.commit()
                        st.success("Enviado")
        else: st.info("Escolha um cliente")

    # --- RESPOSTAS ---
    elif st.session_state.pagina == "respostas":
        st.header("⚡ Respostas Rápidas")
        with st.form("nrr"):
            t = st.text_input("Título"); tx = st.text_area("Texto")
            if st.form_submit_button("Criar"): criar_rr(t, tx, st.session_state.usuario['id']); st.rerun()
        df = listar_rr()
        for _,r in df.iterrows():
            c1,c2,c3 = st.columns([1,4,1])
            c1.write(f"**{r['titulo']}**"); c2.text(r['texto'])
            if c3.button("🗑️", key=f"dr_{r['id']}"): excluir_rr(r['id']); st.rerun()
        if st.button("Voltar"): st.session_state.pagina="chat"; st.rerun()

    # --- ADMIN (NOVA VERSÃO COM EDIÇÃO DE NOME E SAUDAÇÃO) ---
    elif st.session_state.pagina == "admin":
        st.header("⚙️ Admin")
        
        tab1, tab2, tab3 = st.tabs(["➕ Usuários", "📝 Editar/Listar", "🤖 Config Robô"])
        
        with tab1:
            with st.form("nu"):
                n = st.text_input("Nome"); e = st.text_input("Login"); s = st.text_input("Senha"); f = st.selectbox("Função", ["vendedor","admin"])
                if st.form_submit_button("Cadastrar"): 
                    b,m = criar_usuario(n,e,s,f)
                    if b: st.success(m)
                    else: st.error(m)

        with tab2:
            st.subheader("Lista de Usuários")
            dfu = listar_todos_usuarios()
            st.dataframe(dfu)
            
            st.divider()
            st.write("✏️ **Editar Usuário**")
            
            # Seletor de usuário
            u_ids = dfu['id'].tolist()
            sel_uid = st.selectbox("Selecione para editar", u_ids, format_func=lambda x: dfu[dfu['id']==x]['nome'].values[0])
            
            # Pega dados atuais
            user_atual = dfu[dfu['id']==sel_uid].iloc[0]
            
            with st.form("edit_user"):
                novo_nome = st.text_input("Nome", value=user_atual['nome'])
                nova_senha = st.text_input("Nova Senha (deixe vazio para manter)", type="password")
                
                if st.form_submit_button("💾 Salvar Alterações"):
                    s_final = nova_senha if nova_senha else None
                    editar_usuario(sel_uid, novo_nome, s_final)
                    st.success("Usuário atualizado!")
                    time.sleep(1)
                    st.rerun()
            
            st.divider()
            if st.button("🗑️ Excluir Selecionado", type="primary"):
                if sel_uid == st.session_state.usuario['id']: st.error("Não se exclua!")
                else: excluir_usuario(sel_uid); st.rerun()

        with tab3:
            st.subheader("🤖 Mensagem de Saudação Automática")
            st.info("Esta mensagem será enviada automaticamente quando um cliente NOVO entrar em contato, ou um cliente ENCERRADO voltar a chamar.")
            
            msg_atual = pegar_msg_boas_vindas()
            with st.form("conf_robo"):
                txt_saudacao = st.text_area("Texto da Saudação", value=msg_atual, height=150)
                if st.form_submit_button("Salvar Saudação"):
                    salvar_msg_boas_vindas(txt_saudacao)
                    st.success("Configuração salva!")

        if st.button("Voltar"): st.session_state.pagina="chat"; st.rerun()
