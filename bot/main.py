import logging
import os
import re
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}


def is_shopee_url(text: str) -> bool:
    """Return True if the text contains a Shopee link."""
    return bool(re.search(r"(shopee\.[a-z.]+|shope\.ee)", text, re.IGNORECASE))


def extrair_video_shopee(url_compartilhada: str) -> str | None:
    """
    Follow the shared / shortened Shopee URL and attempt to extract
    the direct video source URL from the product page.
    Returns the video URL string, or None if not found.
    """
    try:
        # Step 1 – resolve redirects (short links like shope.ee/xxx)
        response = requests.get(
            url_compartilhada,
            headers=HEADERS,
            allow_redirects=True,
            timeout=15,
        )
        url_real = response.url
        logger.info("Resolved URL: %s", url_real)

        # Step 2 – fetch the real product page
        html = requests.get(url_real, headers=HEADERS, timeout=15).text
        soup = BeautifulSoup(html, "lxml")

        # Attempt 1 – plain <video src="..."> tag
        video_tag = soup.find("video")
        if video_tag:
            src = video_tag.get("src")
            if src:
                logger.info("Found video via <video> tag: %s", src)
                return src

            # Sometimes the src is inside a <source> child
            source_tag = video_tag.find("source")
            if source_tag and source_tag.get("src"):
                logger.info("Found video via <source> tag: %s", source_tag["src"])
                return source_tag["src"]

        # Attempt 2 – look for video URLs inside embedded JSON / script tags
        video_pattern = re.compile(
            r"https?://[^\s\"'<>]*\.(mp4|mov|webm)[^\s\"'<>]*",
            re.IGNORECASE,
        )
        for script in soup.find_all("script"):
            script_text = script.string or ""
            match = video_pattern.search(script_text)
            if match:
                video_url = match.group(0).rstrip(",;}")
                logger.info("Found video via script tag: %s", video_url)
                return video_url

        logger.warning("No video found on page: %s", url_real)
        return None

    except Exception:
        logger.exception("Error while extracting Shopee video")
        return None


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Olá! Sou o bot de download de vídeos da Shopee.\n\n"
        "📤 Envie um link de produto ou vídeo da Shopee e eu tentarei "
        "baixar o vídeo para você!\n\n"
        "Exemplo:\n"
        "https://shope.ee/xxxxxxxx\n"
        "https://shopee.com.br/produto-xxx"
    )


async def processar_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    texto = update.message.text.strip()

    if not is_shopee_url(texto):
        await update.message.reply_text(
            "❌ Por favor, envie um link válido da Shopee.\n"
            "Exemplos: https://shope.ee/... ou https://shopee.com.br/..."
        )
        return

    status_msg = await update.message.reply_text("⏳ Processando o link, aguarde...")

    link_video = extrair_video_shopee(texto)

    if link_video:
        try:
            await status_msg.edit_text("📤 Enviando o vídeo...")
            await update.message.reply_video(
                video=link_video,
                caption="🎥 Aqui está o seu vídeo da Shopee!",
            )
            await status_msg.delete()
        except Exception as e:
            logger.exception("Failed to send video")
            await status_msg.edit_text(
                f"⚠️ Encontrei o vídeo, mas não consegui enviá-lo pelo Telegram.\n\n"
                f"Tente acessar o link diretamente:\n{link_video}\n\n"
                f"Erro técnico: {e}"
            )
    else:
        await status_msg.edit_text(
            "😕 Não consegui encontrar nenhum vídeo nesse link.\n\n"
            "Certifique-se de que:\n"
            "• O link leva a um produto com vídeo\n"
            "• O link é válido e acessível\n\n"
            "Obs: A Shopee pode bloquear ou atualizar a estrutura das páginas, "
            "o que pode afetar a extração."
        )


def main() -> None:
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, processar_mensagem)
    )

    logger.info("Bot iniciado. Aguardando mensagens...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
