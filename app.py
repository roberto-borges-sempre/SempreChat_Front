import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import requests
import time

# --- CONFIGURAÇÃO INICIAL DA PÁGINA ---
st.set_page_config(page_title="SempreChat", page_icon="💬", layout="wide")

# --- CONEXÃO COM O BANCO DE DADOS ---
# O Streamlit busca a senha segura nos "Secrets"
try:
    if "DATABASE_URL" in st.secrets:
        # Corrige o prefixo se necessário (compatibilidade Render/Neon)
        db_url = st.secrets["DATABASE_URL"].replace("postgres://", "postgresql://")
        engine = create_engine(db_url)
    else:
        st.error("⚠️ Configuração DATABASE_URL não encontrada nos Secrets.")
        st.stop()
except Exception as e:
    st.error(f"Erro ao conectar no banco: {e}")
    st.stop()

# --- FUNÇÕES DO SISTEMA ---

def verificar_login(email, senha):
    """Verifica se o usuário existe no banco de dados"""
    try:
        with engine.connect() as conn:
            # Busca usuário ativo com esse email e senha
            query = text("SELECT id, nome, funcao FROM usuarios WHERE email = :e AND senha = :s AND ativo = TRUE")
            result = conn.execute(query, {"e": email, "s": senha}).fetchone()
            return result
    except Exception as e:
        st.error(f"Erro no login: {e}")
        return None

def carregar_fila(admin=False, usuario_id=None):
    """Carrega lista de clientes para atendimento"""
    with engine.connect() as conn:
        if admin:
            # Admin vê tudo
            query = text("""
                SELECT c.id, c.nome, c.whatsapp_id, c.status_atendimento, u.nome as vendedora
                FROM contatos c
                LEFT JOIN usuarios u ON c.vendedora_id = u.id
                ORDER BY c.ultima_interacao DESC
            """)
            return pd.read_sql(query, conn)
        else:
            # Vendedor vê seus clientes + Fila livre
            query = text("""
                SELECT c.id, c.nome, c.whatsapp_id, c.status_atendimento
                FROM contatos c
                WHERE c.vendedora_id = :vid OR c.vendedora_id IS NULL
                ORDER BY c.ultima_interacao DESC
            """)
            return pd.read_sql(query, conn, params={"vid": usuario_id})

def carregar_mensagens(contato_id):
    """Busca o histórico de conversa de um cliente"""
    with engine.connect() as conn:
        query = text("""
            SELECT remetente, texto, data_envio 
            FROM mensagens 
            WHERE contato_id = :cid 
            ORDER BY data_envio ASC
        """)
        return pd.read_sql(query, conn, params={"cid": contato_id})

def enviar_resposta_meta(telefone, texto):
    """Envia a mensagem para a API oficial do WhatsApp"""
    
    # 1. Limpeza do número (remove caracteres não numéricos)
    telefone_limpo = ''.join(filter(str.isdigit, str(telefone)))
    
    # 2. TRUQUE DO 9 DÍGITO
    # A API da Meta geralmente usa números antigos sem o 9.
    # Se o número tiver 13 dígitos (55 31 9 8888-8888), removemos o 9.
    if len(telefone_limpo) == 13 and telefone_limpo.startswith("55"):
        telefone_limpo = telefone_limpo[:4] + telefone_limpo[5:]
    
    # URL e Cabeçalhos da Meta
    url = f"https://graph.facebook.com/v18.0/{st.secrets['META_PHONE_ID']}/messages"
    headers = {
        "Authorization": f"Bearer {st.secrets['META_TOKEN']}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": telefone_limpo,
        "type": "text",
        "text": {"body": texto}
    }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        return response.status_code, response.json()
    except Exception as e:
        return 500, str(e)

# --- LÓGICA DE SESSÃO (Mantém logado) ---
if "usuario" not in st.session_state:
    st.session_state.usuario = None

# ==========================================
# 🔐 TELA DE LOGIN
# ==========================================
if st.session_state.usuario is None:
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        st.title("🔐 SempreChat - Acesso")
        with st.form("login_form"):
            email = st.text_input("E-mail")
            senha = st.text_input("Senha", type="password")
            entrar = st.form_submit_button("Entrar no Sistema")
            
            if entrar:
                user = verificar_login(email, senha)
                if user:
                    # Salva dados na sessão (id, nome, funcao)
                    st.session_state.usuario = {"id": user[0], "nome": user[1], "funcao": user[2]}
                    st.rerun()
                else:
                    st.error("Usuário ou senha incorretos.")
                    st.info("Dica: O padrão é admin@sempre.com / 123")

# ==========================================
# 💬 TELA DO SISTEMA (CHAT)
# ==========================================
else:
    # --- BARRA LATERAL (LISTA DE CLIENTES) ---
    with st.sidebar:
        st.write(f"👤 **{st.session_state.usuario['nome']}**")
        if st.button("Sair"):
            st.session_state.usuario = None
            st.rerun()
        
        st.divider()
        st.subheader("📥 Clientes")
        
        is_admin = (st.session_state.usuario['funcao'] == 'admin')
        df_fila = carregar_fila(is_admin, st.session_state.usuario['id'])
        
        if df_fila.empty:
            st.info("Nenhum cliente na fila.")
        
        for index, row in df_fila.iterrows():
            # Ícone muda se já tiver dono ou não
            nome_botao = f"🟢 {row['nome']}"
            if is_admin and row['vendedora']:
                nome_botao = f"🔒 {row['nome']} ({row['vendedora']})"
            
            if st.button(nome_botao, key=row['id'], use_container_width=True):
                st.session_state.chat_ativo = row['id']
                st.session_state.chat_nome = row['nome']
                st.session_state.chat_tel = row['whatsapp_id']
                st.rerun()

    # --- ÁREA PRINCIPAL (MENSAGENS) ---
    if "chat_ativo" in st.session_state:
        st.header(f"💬 Conversando com {st.session_state.chat_nome}")
        
        # Carrega histórico
        msgs = carregar_mensagens(st.session_state.chat_ativo)
        
        # Container de rolagem para as mensagens
        chat_container = st.container(height=500)
        with chat_container:
            if msgs.empty:
                st.info("Nenhuma mensagem trocada ainda.")
            else:
                for idx, row in msgs.iterrows():
                    # Diferencia quem mandou (cliente x empresa)
                    tipo = row['remetente']
                    if tipo == 'cliente':
                        with st.chat_message("user", avatar="👤"):
                            st.write(row['texto'])
                            st.caption(f"{row['data_envio'].strftime('%H:%M')}")
                    else:
                        with st.chat_message("assistant", avatar="🏢"):
                            st.write(row['texto'])
                            st.caption(f"Enviado às {row['data_envio'].strftime('%H:%M')}")

        # --- CAMPO DE ENVIAR MENSAGEM ---
        texto = st.chat_input("Digite sua resposta aqui...")
        
        if texto:
            # 1. Enviar para a Meta (WhatsApp Real)
            code, resp = enviar_resposta_meta(st.session_state.chat_tel, texto)
            
            if code in [200, 201]:
                # 2. Se deu certo, salva no Banco
                with engine.connect() as conn:
                    # Vincula cliente a este vendedor (se ainda não tiver)
                    conn.execute(text("""
                        UPDATE contatos SET vendedora_id = :vid, status_atendimento = 'em_andamento' 
                        WHERE id = :cid AND vendedora_id IS NULL
                    """), {"vid": st.session_state.usuario['id'], "cid": st.session_state.chat_ativo})
                    
                    # Insere a mensagem
                    conn.execute(text("""
                        INSERT INTO mensagens (contato_id, remetente, texto, mensagem_id_meta)
                        VALUES (:cid, 'empresa', :txt, 'web_panel')
                    """), {"cid": st.session_state.chat_ativo, "txt": texto})
                    
                    conn.commit()
                
                st.success("Enviado!")
                time.sleep(0.5) # Dá um tempinho pro usuário ver o sucesso
                st.rerun()      # Recarrega a tela pra mostrar a msg nova
            else:
                st.error(f"Erro ao enviar: {resp}")

    else:
        st.info("👈 Selecione um cliente na barra lateral para começar.")
