# --- ESTILO VISUAL (CORRIGIDO PARA O TEXTO NÃO CORTAR) ---
st.markdown("""
<style>
    .stApp { background-color: #efeae2; }
    
    /* Balões */
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
    
    /* === CAIXA DE TEXTO (CORREÇÃO DE CORTE) === */
    .stChatInputContainer textarea {
        min-height: 60px !important;  /* Altura confortável */
        height: 60px !important;
        font-size: 16px !important;
        padding-top: 10px !important; /* Espaço para o texto não colar no topo */
    }
</style>
""", unsafe_allow_html=True)
