import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import requests
import time

# --- CONFIGURAÇÃO ---
st.set_page_config(page_title="SempreChat CRM", page_icon="📶", layout="wide")

# --- CONEXÃO BANCO ---
try:
    if "DATABASE_URL" in st.secrets:
        db_url = st.secrets["DATABASE_URL"].replace("postgres://", "postgresql://")
        engine = create_engine(db_url)
    else:
        st.error("Configure DATABASE_URL nos Secrets.")
        st.stop()
except Exception as e:
    st.error(f"Erro Conexão: {e}")
    st.stop()

# ==========================================
# 🛠️ FUNÇÕES DE BACKEND
# ==========================================

# --- USUÁRIOS (ADMIN) ---
def verificar_login(email, senha):
    with engine.connect() as conn:
        return conn.execute(text("SELECT id, nome, funcao FROM usuarios WHERE email=:e AND senha=:s AND ativo=TRUE"), {"e":email, "s":senha}).fetchone()

def listar_usuarios_ativos():
    with engine.connect() as conn:
        return pd.read_sql(text("SELECT id, nome FROM usuarios WHERE ativo=TRUE ORDER BY nome"), conn)

def listar_todos_usuarios():
    with engine.connect() as conn:
        return pd.read_sql(text("SELECT id, nome, email, funcao, ativo FROM usuarios ORDER BY id"), conn)

def criar_usuario(nome, email, senha, funcao):
    try:
        with engine.connect() as conn:
            conn.execute(text("INSERT INTO usuarios (nome, email, senha, funcao, ativo) VALUES (:n, :e, :s, :f, TRUE)"), {"n": nome, "e": email, "s": senha, "f": funcao})
            conn.commit()
        return True, "Criado com sucesso!"
    except Exception as e:
        return False, f"Erro: {e}"

def alterar_senha(id_usuario, nova_senha):
    with engine.connect() as conn:
        conn.execute(text("UPDATE usuarios SET senha = :s WHERE id = :id"), {"s": nova_senha, "id": id_usuario})
        conn.commit()

def excluir_usuario(id_usuario):
    with engine.connect() as conn:
        conn.execute(text("UPDATE contatos SET vendedora_id = NULL WHERE vendedora_id = :id"), {"id": id_usuario})
        conn.execute(text("DELETE FROM usuarios WHERE id = :id"), {"id": id_usuario})
        conn.commit()

# --- CHAT & ATENDIMENTO ---
def carregar_fila(admin=False, usuario_id=None):
    with engine.connect() as conn:
        filtro_vendedor = "" if admin else f"AND (c.vendedora_id = {usuario_id} OR c.vendedora_id IS NULL)"
        # AQUI OCORRE O ERRO SE O BANCO NÃO TIVER AS COLUNAS NOVAS
        query = text(f"""
            SELECT c.id, c.nome, c.whatsapp_id, c.status_atendimento, u.nome as vendedora, c.codigo_cliente
            FROM contatos c
            LEFT JOIN usuarios u ON c.vendedora_id = u.id
            WHERE c.status_atendimento != 'encerrado' {filtro_vendedor}
            ORDER BY c.ultima_interacao DESC
        """)
        return pd.read_sql(query, conn)

def carregar_mensagens(contato_id):
    with engine.connect() as conn:
        return pd.read_sql(text("SELECT remetente, texto, tipo, url_media, data_envio FROM mensagens WHERE contato_id = :cid ORDER BY data_envio ASC"), conn, params={"cid": contato_id})

def carregar_info_cliente(contato_id):
    with engine.connect() as conn:
        return conn.execute(text("SELECT nome, whatsapp_id, codigo_cliente, cpf_cnpj, notas_internas FROM contatos WHERE id=:id"), {"id":contato_id}).fetchone()

def atualizar_cliente(contato_id, codigo, notas):
    with engine.connect() as conn:
        conn.execute(text("UPDATE contatos SET codigo_cliente=:c, notas_internas=:n WHERE id=:id"), {"c":codigo, "n":notas, "id":contato_id})
        conn.commit()

def transferir_atendimento(contato_id, novo_vendedor_id):
    with engine.connect() as conn:
        conn.execute(text("UPDATE contatos SET vendedora_id=:vid, status_atendimento='em_andamento' WHERE id=:cid"), {"vid":novo_vendedor_id, "cid":contato_id})
        conn.commit()

def encerrar_atendimento(contato_id):
    with engine.connect() as conn:
        conn.execute(text("UPDATE contatos SET status_atendimento='encerrado' WHERE id=:cid"), {"cid":contato_id})
        conn.commit()

# --- RESPOSTAS RÁPIDAS ---
def listar_respostas_rapidas():
    with engine.connect() as conn:
        return pd.read_sql(text("SELECT id, titulo, texto FROM respostas_rapidas ORDER BY titulo"), conn)

def criar_resposta_rapida(titulo, texto, user_id):
    with engine.connect() as conn:
        conn.execute(text("INSERT INTO respostas_rapidas (titulo, texto, criado_por) VALUES (:t, :txt, :u)"), {"t":titulo, "txt":texto, "u":user_id})
        conn.commit()

def excluir_resposta_rapida(id_resp):
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM respostas_rapidas WHERE id=:id"), {"id":id_resp})
        conn.commit()

# --- META API ---
def get_media_bytes(media_id):
    try:
        url_info = f"https://graph.facebook.com/v18.0/{media_id}"
        headers = {"Authorization": f"Bearer {st.secrets['META_TOKEN']}"}
        r_info = requests.get(url_info, headers=headers).json()
        if 'url' in r_info:
            return requests.get(r_info['url'], headers=headers).content
        return None
    except:
        return None

def enviar_mensagem(telefone, texto, tipo="text", template_name=None):
    tel = ''.join(filter(str.isdigit, str(telefone)))
    if len(tel) == 13 and tel.startswith("55"): tel = tel[:4] + tel[5:]
    
    url = f"https://graph.facebook.com/v18.0/{st.secrets['META_PHONE_ID']}/messages"
    headers = {"Authorization": f"Bearer {st.secrets['META_TOKEN']}", "Content-Type": "application/json"}
    
    payload = {"messaging_product": "whatsapp", "to": tel, "type": tipo}
    cost = 0.0
    
    if tipo == 'text':
        payload['text'] = {"body": texto}
    elif tipo == 'template':
        payload['template'] = {"name": template_name, "language": {"code": "pt_BR"}}
        cost = 0.05 

    try:
        resp = requests.post(url, headers=headers, json=payload)
        return resp.status_code, resp.json(), cost
    except Exception as e:
        return 500, str(e), 0.0

# ==========================================
# 🖥️ INTERFACE
# ==========================================

if "usuario" not in st.session_state: st.session_state.usuario = None
if "pagina" not in st.session_state: st.session_state.pagina = "chat"

# --- LOGIN ---
if st.session_state.usuario is None:
    c1,c2,c3 = st.columns([1,2,1])
    with c2:
        st.title("📶 SempreChat CRM")
        with st.form("login"):
            email = st.text_input("Email")
            senha = st.text_input("Senha", type="password")
            if st.form_submit_button("Entrar"):
                u = verificar_login(email, senha)
                if u:
                    st.session_state.usuario = {"id":u[0], "nome":u[1], "funcao":u[2]}
                    st.rerun()
                else:
                    st.error("Login inválido")

# --- LOGADO ---
else:
    with st.sidebar:
        st.write(f"👤 **{st.session_state.usuario['nome']}**")
        
        if st.button("💬 Atendimento", use_container_width=True): 
            st.session_state.pagina = "chat"
            st.rerun()

        if st.button("⚡ Respostas Rápidas", use_container_width=True):
            st.session_state.pagina = "respostas"
            st.rerun()

        if st.session_state.usuario['funcao'] == 'admin':
            if st.button("⚙️ Gerenciar Equipe", use_container_width=True): 
                st.session_state.pagina = "admin"
                st.rerun()

        if st.button("Sair", type="primary"): 
            st.session_state.usuario = None
            st.rerun()
        
        st.divider()
        
        # FILA (Só mostra se estiver no chat)
        if st.session_state.pagina == "chat":
            st.subheader("📥 Em Atendimento")
            is_admin = st.session_state.usuario['funcao'] == 'admin'
            
            # PROTEÇÃO CONTRA ERRO DE BANCO DESATUALIZADO
            try:
                df_fila = carregar_fila(is_admin, st.session_state.usuario['id'])
                if df_fila.empty: st.info("Fila vazia.")
                for _, row in df_fila.iterrows():
                    icon = "🟢"
                    if is_admin and row['vendedora']: icon = f"🔒 {row['vendedora'][:10]}"
                    display = row['nome']
                    if row['codigo_cliente']: display += f" ({row['codigo_cliente']})"
                    if st.button(f"{icon} {display}", key=f"chat_{row['id']}", use_container_width=True):
                        st.session_state.chat_ativo = row['id']
                        st.rerun()
            except Exception as e:
                st.error("⚠️ Erro ao carregar fila. Você rodou o /setup_banco?")
                st.code(str(e))

    # --- PAGINA CHAT ---
    if st.session_state.pagina == "chat":
        if "chat_ativo" in st.session_state:
            cli = carregar_info_cliente(st.session_state.chat_ativo)
            
            c1, c2, c3 = st.columns([3, 1, 1])
            with c1:
                st.markdown(f"### 💬 {cli[0]}")
                st.caption(f"Tel: {cli[1]} | Cód: {cli[2] if cli[2] else '--'}")
            
            with c2:
                users = listar_usuarios_ativos()
                users_dict = {u[1]: u[0] for _, u in users.iterrows()}
                dest = st.selectbox("Transferir", ["--"] + list(users_dict.keys()), label_visibility="collapsed")
                if dest != "--":
                    if st.button("Confirmar", key="bt_transf"):
                        transferir_atendimento(st.session_state.chat_ativo, users_dict[dest])
                        st.success(f"Transferido para {dest}")
                        time.sleep(1)
                        st.rerun()
            
            with c3:
                if st.button("🔴 Encerrar", use_container_width=True):
                    encerrar_atendimento(st.session_state.chat_ativo)
                    del st.session_state['chat_ativo']
                    st.success("Finalizado!")
                    st.rerun()

            with st.expander("📝 Notas & Cadastro"):
                with st.form("form_notas"):
                    novo_cod = st.text_input("Código/CNPJ", value=cli[2] if cli[2] else "")
                    novas_notas = st.text_area("Notas", value=cli[4] if cli[4] else "")
                    if st.form_submit_button("Salvar"):
                        atualizar_cliente(st.session_state.chat_ativo, novo_cod, novas_notas)
                        st.success("Salvo!")
                        st.rerun()

            st.divider()
            msgs = carregar_mensagens(st.session_state.chat_ativo)
            cont = st.container(height=450)
            with cont:
                if msgs.empty: st.info("Sem mensagens.")
                for _, row in msgs.iterrows():
                    media_id = row['url_media']
                    with st.chat_message(row['remetente'], avatar="👤" if row['remetente']=='cliente' else "🏢"):
                        if row['texto'] and row['texto'] != "None": st.write(row['texto'])
                        if row['tipo'] in ['image', 'audio', 'voice'] and media_id:
                            data = get_media_bytes(media_id)
                            if data:
                                if row['tipo'] == 'image': st.image(data, width=300)
                                else: st.audio(data)
                        st.caption(f"{row['data_envio'].strftime('%H:%M')}")

            st.divider()
            resps = listar_respostas_rapidas()
            opcoes_rr = {r[1]: r[2] for _, r in resps.iterrows()}
            rr = st.selectbox("⚡ Rápida", ["--"] + list(opcoes_rr.keys()))
            msg_ini = opcoes_rr[rr] if rr != "--" else ""

            col_txt, col_send = st.columns([5, 1])
            with col_txt:
                txt = st.text_input("Mensagem", value=msg_ini, key="input_msg")
            with col_send:
                st.write("")
                st.write("")
                if st.button("Enviar ➤"):
                    if txt:
                        code, r, c = enviar_mensagem(cli[1], txt)
                        if code in [200, 201]:
                            with engine.connect() as conn:
                                conn.execute(text("INSERT INTO mensagens (contato_id, remetente, texto, tipo, custo) VALUES (:cid, 'empresa', :t, 'text', 0)"), {"cid":st.session_state.chat_ativo, "t":txt})
                                conn.commit()
                            st.rerun()
                        else:
                            st.error(f"Erro: {r}")

            with st.expander("📢 Template (24h)"):
                nm = st.text_input("Nome do Template")
                if st.button("Enviar Template"):
                    code, r, custo = enviar_mensagem(cli[1], "", "template", nm)
                    if code in [200, 201]:
                        with engine.connect() as conn:
                            conn.execute(text("INSERT INTO mensagens (contato_id, remetente, texto, tipo, custo) VALUES (:cid, 'empresa', :t, 'template', :c)"), {"cid":st.session_state.chat_ativo, "t":f"[Template: {nm}]", "c":custo})
                            conn.commit()
                        st.success("Enviado!")
                    else:
                        st.error(f"Erro: {r}")
        else:
            st.info("👈 Selecione um cliente.")

    # --- PAGINA RESPOSTAS RAPIDAS ---
    elif st.session_state.pagina == "respostas":
        st.header("⚡ Respostas Rápidas")
        with st.form("rr_form"):
            t = st.text_input("Título")
            tx = st.text_input("Texto")
            if st.form_submit_button("Criar"):
                criar_resposta_rapida(t, tx, st.session_state.usuario['id'])
                st.rerun()
        st.divider()
        df = listar_respostas_rapidas()
        for _, r in df.iterrows():
            c1, c2, c3 = st.columns([1, 4, 1])
            c1.markdown(f"**{r['titulo']}**")
            c2.write(r['texto'])
            if c3.button("🗑️", key=f"d_{r['id']}"):
                excluir_resposta_rapida(r['id'])
                st.rerun()
        if st.button("🔙 Voltar"):
            st.session_state.pagina = "chat"
            st.rerun()

    # --- PAGINA ADMIN (AQUI ESTAVA O PROBLEMA ANTES) ---
    elif st.session_state.pagina == "admin":
        st.header("⚙️ Gestão de Equipe")
        
        tab1, tab2 = st.tabs(["➕ Novo Usuário", "📋 Lista & Edição"])
        
        with tab1:
            with st.form("novo_user"):
                col1, col2 = st.columns(2)
                nome = col1.text_input("Nome")
                email = col2.text_input("Email (Login)")
                senha = col1.text_input("Senha Inicial")
                funcao = col2.selectbox("Função", ["vendedor", "admin"])
                
                if st.form_submit_button("Cadastrar Usuário"):
                    sucesso, msg = criar_usuario(nome, email, senha, funcao)
                    if sucesso: st.success(msg)
                    else: st.error(msg)

        with tab2:
            st.subheader("Usuários Ativos")
            df_users = listar_todos_usuarios()
            st.dataframe(df_users)
            
            st.divider()
            st.write("🔧 **Ações Rápidas**")
            
            col_sel, col_new_pass, col_btn = st.columns([1, 1, 1])
            with col_sel:
                lista_users = df_users['id'].tolist()
                if lista_users:
                    user_id_sel = st.selectbox("Selecione Usuário", lista_users, format_func=lambda x: df_users[df_users['id'] == x]['nome'].values[0])
                else:
                    user_id_sel = None
            
            with col_new_pass:
                nova_senha = st.text_input("Nova Senha", placeholder="Digite para alterar")
            
            with col_btn:
                if st.button("💾 Atualizar Senha"):
                    if user_id_sel:
                        alterar_senha(user_id_sel, nova_senha)
                        st.success("Senha alterada!")
                
                if st.button("🗑️ Excluir Usuário", type="primary"):
                    if user_id_sel == st.session_state.usuario['id']:
                        st.error("Não pode se excluir!")
                    elif user_id_sel:
                        excluir_usuario(user_id_sel)
                        st.warning("Usuário removido.")
                        time.sleep(1)
                        st.rerun()

        if st.button("🔙 Voltar ao Chat"):
            st.session_state.pagina = "chat"
            st.rerun()
