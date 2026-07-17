# 📢 Bot de Promoções — Facebook, Instagram e Threads

Lê links da Shopee de uma planilha Excel, busca foto/nome/preço automaticamente e publica nas redes sociais.

---

## 📋 Como configurar (passo a passo)

### 1. Crie o arquivo `.env`

Copie o `.env.example` e renomeie para `.env`:

```
FB_PAGE_ID=
FB_PAGE_TOKEN=
IG_USER_ID=
THREADS_USER_ID=
PLANILHA=planilha.xlsx
```

### 2. Obtenha os tokens da Meta

**Para conseguir o FB_PAGE_TOKEN (Token da Página):**
1. Acesse: https://developers.facebook.com/tools/explorer/
2. Selecione seu app (ou crie um em developers.facebook.com)
3. Clique em **"Gerar Token de Acesso"**
4. Marque as permissões: `pages_manage_posts`, `pages_read_engagement`, `instagram_basic`, `instagram_content_publish`
5. Copie o token gerado
6. Para torná-lo permanente (60 dias), acesse:  
   `https://graph.facebook.com/v19.0/oauth/access_token?grant_type=fb_exchange_token&client_id=SEU_APP_ID&client_secret=SEU_APP_SECRET&fb_exchange_token=SEU_TOKEN`

**Para conseguir o FB_PAGE_ID:**
1. Acesse sua Página do Facebook
2. Vá em Configurações → Informações da Página
3. Role até o final — o ID da Página estará lá

**Para conseguir o IG_USER_ID:**
```
https://graph.facebook.com/v19.0/me/accounts?access_token=SEU_TOKEN
```
Retorna a lista de páginas. Use o `id` da página que está conectada ao Instagram.
Depois:
```
https://graph.facebook.com/v19.0/SEU_PAGE_ID?fields=instagram_business_account&access_token=SEU_TOKEN
```
O `id` dentro de `instagram_business_account` é o IG_USER_ID.

**Para conseguir o THREADS_USER_ID:**
```
https://graph.threads.net/v1.0/me?access_token=SEU_TOKEN
```

---

### 3. Crie a planilha

```bash
python criar_planilha.py
```

Isso cria o arquivo `planilha.xlsx`. Abra e cole seus links afiliados na **coluna A**.

| Link Shopee (afiliado) | Texto extra (opcional) | Status |
|---|---|---|
| https://shope.ee/... | | |
| https://shope.ee/... | Última unidade! 🔥 | |

---

### 4. Execute o bot

```bash
python main.py
```

O bot vai:
1. Ler cada link ainda não publicado
2. Buscar nome, preço e foto na Shopee
3. Montar o anúncio automaticamente
4. Publicar no Facebook, Instagram (feed + story) e Threads
5. Marcar como "Publicado" na planilha

---

## 📝 Modelo do anúncio

```
🔥 Nome do Produto

💰 Por apenas R$ 49,90

🛒 Compre agora: https://shope.ee/...

#shopee #promoção #oferta #desconto
```

Para personalizar, edite o `TEMPLATE_TEXTO` no `.env`.

---

## ❓ Dúvidas frequentes

**O bot publica tudo de uma vez?**  
Sim, mas com uma pausa de 10 segundos entre cada produto para não ser bloqueado.

**E se um produto falhar?**  
O status na planilha vai mostrar "Erro". Você pode corrigir e rodar novamente — ele pula os que já foram publicados.

**Posso agendar para rodar automaticamente?**  
Sim! Me peça e configuro um horário automático para rodar todo dia.
