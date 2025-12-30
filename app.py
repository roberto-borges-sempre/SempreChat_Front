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
# 🛠️ FUNÇÕES DE BACKEND (DB & API)
# ==========================================

# --- USUÁRIOS ---
def verificar_login(email, senha):
    with engine.connect() as conn:
        return conn.execute(text("SELECT id, nome, funcao FROM usuarios WHERE email=:e AND senha=:s AND ativo=TRUE"), {"e":email, "s":senha}).fetchone()

def listar_usuarios_ativos():
    with engine.connect() as conn:
        return pd.read_sql(text("SELECT id, nome FROM usuarios WHERE ativo=TRUE ORDER BY nome"), conn)

# --- CHAT & ATENDIMENTO ---
def carregar_fila(admin=False, usuario_id=None):
    with engine.connect() as conn:
        # Traz contatos que NÃO estão encerrados
        filtro_vendedor = "" if admin else f"AND (c.vendedora_id = {usuario_id} OR c.vendedora_id IS NULL)"
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
        # Encerra e remove o vendedor? Não, mantemos o vendedor para histórico, mas status muda.
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

# --- META API (ENVIO & MÍDIA) ---
def get_media_bytes(media_id):
    """Baixa a imagem/áudio da Meta usando o Token"""
    try:
        # 1. Pega a URL
        url_info = f"https://graph.facebook.com/v18.0/{media_id}"
        headers = {"Authorization": f"Bearer {st.secrets['META_TOKEN']}"}
        r_info = requests.get(url_info, headers=headers).json()
        
        if 'url' in r_info:
            # 2. Baixa o binário
            media_url = r_info['url']
            r_bin = requests.get(media_url, headers=headers)
            return r_bin.content
        return None
    except:
        return None

def enviar_mensagem(telefone, texto, tipo="text", template_name=None):
    # Limpa telefone
    tel = ''.join(filter(str.isdigit, str(telefone)))
    if len(tel) == 13 and tel.startswith("55"): tel = tel[:4] + tel[5:]
    
    url = f"https://graph.facebook.com/v18.0/{st.secrets['META_PHONE_ID']}/messages"
    headers = {"Authorization": f"Bearer {st.secrets['META_TOKEN']}", "Content-Type": "application/json"}
    
    payload = {
        "messaging_product": "whatsapp",
        "to": tel,
        "type": tipo
    }
    
    cost = 0.0
    if tipo == 'text':
        payload['text'] = {"body": texto}
    elif tipo == 'template':
        payload['template'] = {"name": template_name, "language": {"code": "pt_BR"}}
        cost = 0.05 # Custo estimado de template (exemplo)

    try:
        resp = requests.post(url, headers=headers, json=payload)
        return resp.status_code, resp.json(), cost
    except Exception as e:
        return 500, str(e), 0.0

# ==========================================
# 🖥️ INTERFACE (FRONTEND)
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

# --- SISTEMA LOGADO ---
else:
    # SIDEBAR
    with st.sidebar:
        st.write(f"👤 **{st.session_state.usuario['nome']}** ({st.session_state.usuario['funcao']})")
        
        if st.button("💬 Atendimento", use_container_width=True): 
            st.session_state.pagina = "chat"
            st.rerun()

        # Botão Respostas Rápidas (Novo)
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
        
        # FILA DE ATENDIMENTO
        if st.session_state.pagina == "chat":
            st.subheader("📥 Em Atendimento")
            is_admin = st.session_state.usuario['funcao'] == 'admin'
            df_fila = carregar_fila(is_admin, st.session_state.usuario['id'])
            
            if df_fila.empty: st.info("Fila vazia.")
            
            for _, row in df_fila.iterrows():
                icon = "🟢"
                if is_admin and row['vendedora']: icon = f"🔒 {row['vendedora'][:10]}"
                # Mostra código se tiver
                display_name = row['nome']
                if row['codigo_cliente']: display_name += f" ({row['codigo_cliente']})"

                if st.button(f"{icon} {display_name}", key=f"chat_{row['id']}", use_container_width=True):
                    st.session_state.chat_ativo = row['id']
                    st.session_state.chat_nome = row['nome']
                    st.session_state.chat_tel = row['whatsapp_id']
                    st.rerun()

    # --- PÁGINA: CHAT ---
    if st.session_state.pagina == "chat":
        if "chat_ativo" in st.session_state:
            # HEADER DO CLIENTE
            cli = carregar_info_cliente(st.session_state.chat_ativo)
            
            c1, c2, c3 = st.columns([3, 1, 1])
            with c1:
                st.markdown(f"### 💬 {cli[0]}")
                st.caption(f"Tel: {cli[1]} | Cód: {cli[2] if cli[2] else '--'}")
            
            with c2:
                # TRANSFERIR
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
                # ENCERRAR
                if st.button("🔴 Encerrar", use_container_width=True):
                    encerrar_atendimento(st.session_state.chat_ativo)
                    del st.session_state['chat_ativo']
                    st.success("Atendimento Finalizado!")
                    time.sleep(1)
                    st.rerun()

            # NOTAS INTERNAS (EXPANDER)
            with st.expander("📝 Notas Internas & Cadastro (Clique para editar)"):
                with st.form("form_notas"):
                    novo_cod = st.text_input("Código Cliente / CNPJ", value=cli[2] if cli[2] else "")
                    novas_notas = st.text_area("Notas sobre o cliente", value=cli[4] if cli[4] else "")
                    if st.form_submit_button("Salvar Notas"):
                        atualizar_cliente(st.session_state.chat_ativo, novo_cod, novas_notas)
                        st.success("Salvo!")
                        st.rerun()

            st.divider()

            # MENSAGENS
            msgs = carregar_mensagens(st.session_state.chat_ativo)
            container = st.container(height=450)
            with container:
                if msgs.empty: st.info("Nenhuma mensagem.")
                for _, row in msgs.iterrows():
                    tipo = row['tipo']
                    texto = row['texto']
                    media_id = row['url_media']
                    
                    with st.chat_message(row['remetente'], avatar="👤" if row['remetente']=='cliente' else "🏢"):
                        # Renderiza texto
                        if texto and texto != "None": 
                            st.write(texto)
                        
                        # Renderiza Mídia
                        if tipo in ['image', 'audio', 'voice'] and media_id:
                            # Tenta baixar
                            media_data = get_media_bytes(media_id)
                            if media_data:
                                if tipo == 'image': st.image(media_data, width=300)
                                elif tipo in ['audio', 'voice']: st.audio(media_data)
                            else:
                                st.warning(f"Erro ao carregar mídia (ID: {media_id})")

                        st.caption(f"{row['data_envio'].strftime('%H:%M')}")

            # INPUT AREA
            st.divider()
            
            # RESPOSTAS RÁPIDAS
            resps = listar_respostas_rapidas()
            opcoes_rr = {r[1]: r[2] for _, r in resps.iterrows()}
            rr_selecionada = st.selectbox("⚡ Mensagem Rápida", ["-- Selecione --"] + list(opcoes_rr.keys()))
            
            msg_inicial = ""
            if rr_selecionada != "-- Selecione --":
                msg_inicial = opcoes_rr[rr_selecionada]

            # ENVIO DE TEXTO
            col_txt, col_send = st.columns([5, 1])
            with col_txt:
                txt = st.text_input("Mensagem", value=msg_inicial, key="input_msg")
            
            with col_send:
                st.write("")
                st.write("")
                if st.button("Enviar ➤"):
                    if txt:
                        code, r, c = enviar_mensagem(st.session_state.chat_tel, txt)
                        if code in [200, 201]:
                            with engine.connect() as conn:
                                conn.execute(text("INSERT INTO mensagens (contato_id, remetente, texto, tipo, custo) VALUES (:cid, 'empresa', :t, 'text', 0)"), {"cid":st.session_state.chat_ativo, "t":txt})
                                conn.commit()
                            st.rerun()
                        else:
                            st.error(f"Erro: {r}")

            # ENVIO DE TEMPLATE (24H)
            with st.expander("📢 Enviar Template (Furar 24h)"):
                nome_tmpl = st.text_input("Nome do Template (Ex: hello_world)")
                if st.button("Enviar Template"):
                    code, r, custo = enviar_mensagem(st.session_state.chat_tel, "", "template", nome_tmpl)
                    if code in [200, 201]:
                        with engine.connect() as conn:
                            conn.execute(text("INSERT INTO mensagens (contato_id, remetente, texto, tipo, custo) VALUES (:cid, 'empresa', :t, 'template', :c)"), {"cid":st.session_state.chat_ativo, "t":f"[Template: {nome_tmpl}]", "c":custo})
                            conn.commit()
                        st.success("Template enviado!")
                        st.rerun()
                    else:
                        st.error(f"Erro Template: {r}")

        else:
            st.info("👈 Selecione um cliente.")

    # --- PÁGINA: GESTÃO RESPOSTAS RÁPIDAS ---
    elif st.session_state.pagina == "respostas":
        st.header("⚡ Gerenciar Respostas Rápidas")
        
        with st.form("nova_rr"):
            c1, c2 = st.columns([1, 2])
            t = c1.text_input("Título (Curto)")
            tx = c2.text_input("Mensagem Completa")
            if st.form_submit_button("➕ Criar"):
                criar_resposta_rapida(t, tx, st.session_state.usuario['id'])
                st.success("Criado!")
                st.rerun()
        
        st.divider()
        df_rr = listar_respostas_rapidas()
        for _, row in df_rr.iterrows():
            c1, c2, c3 = st.columns([1, 4, 1])
            c1.markdown(f"**{row['titulo']}**")
            c2.write(row['texto'])
            if c3.button("🗑️", key=f"del_rr_{row['id']}"):
                excluir_resposta_rapida(row['id'])
                st.rerun()
        
        if st.button("🔙 Voltar"):
            st.session_state.pagina = "chat"
            st.rerun()

    # --- PÁGINA: ADMIN (Mantida simples) ---
    elif st.session_state.pagina == "admin":
        st.header("⚙️ Equipe")
        # (Código de usuários mantido igual ao anterior, resumido aqui para caber)
        st.info("Painel de Usuários (Use o código anterior ou copie se precisar alterar)")
        if st.button("🔙 Voltar"):
            st.session_state.pagina = "chat"
            st.rerun()
