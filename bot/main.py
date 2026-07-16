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
    return bool(re.search(r"(shopee\.[a-z.]+|shope\.ee|s\.shopee)", text, re.IGNORECASE))


def resolver_url(url_compartilhada: str) -> str:
    """Follow redirects and return the final URL."""
    response = requests.get(
        url_compartilhada,
        headers=HEADERS,
        allow_redirects=True,
        timeout=15,
    )
    return response.url


def extrair_ids_da_url(url: str):
    """
    Extract (shop_id, item_id) from a Shopee product URL.
    Supports formats:
      - shopee.com.br/{name}/{shop_id}/{item_id}
      - shopee.com.br/product/{shop_id}/{item_id}
      - shopee.com.br/i/{shop_id}/{item_id}
    Returns (shop_id, item_id) as strings, or (None, None).
    """
    # Pattern: two consecutive numeric segments at the end of the path
    match = re.search(r"/(\d+)/(\d+)(?:[?#]|$)", url)
    if match:
        return match.group(1), match.group(2)
    return None, None


def extrair_video_via_api(shop_id: str, item_id: str) -> str | None:
    """Call Shopee's internal API to get the product video URL."""
    # Try Brazil endpoint first, then fallback to generic
    endpoints = [
        f"https://shopee.com.br/api/v4/item/get?itemid={item_id}&shopid={shop_id}",
        f"https://shopee.com/api/v4/item/get?itemid={item_id}&shopid={shop_id}",
    ]

    api_headers = {
        **HEADERS,
        "Referer": "https://shopee.com.br/",
        "X-API-SOURCE": "pc",
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json",
    }

    for url in endpoints:
        try:
            resp = requests.get(url, headers=api_headers, timeout=15)
            if resp.status_code != 200:
                continue
            data = resp.json()
            # Navigate: data.data.video_info_list[0].default_format.video_url
            item_data = data.get("data") or data.get("item") or {}
            video_list = item_data.get("video_info_list") or []
            for vid in video_list:
                formats = vid.get("default_format") or {}
                video_url = formats.get("video_url") or formats.get("url")
                if video_url:
                    logger.info("Found video via API: %s", video_url)
                    return video_url
                # Some responses use a flat structure
                video_url = vid.get("video_url") or vid.get("url")
                if video_url:
                    logger.info("Found video via API (flat): %s", video_url)
                    return video_url
        except Exception:
            logger.exception("API call failed for %s", url)

    return None


def extrair_video_via_html(url_real: str) -> str | None:
    """Fallback: scrape the HTML page for video URLs."""
    html = requests.get(url_real, headers=HEADERS, timeout=15).text
    soup = BeautifulSoup(html, "lxml")

    # Try <video> and <source> tags
    video_tag = soup.find("video")
    if video_tag:
        src = video_tag.get("src")
        if src:
            return src
        source_tag = video_tag.find("source")
        if source_tag and source_tag.get("src"):
            return source_tag["src"]

    # Search for mp4/webm URLs inside script tags
    video_pattern = re.compile(
        r"https?://[^\s\"'<>]*\.(mp4|mov|webm)[^\s\"'<>]*",
        re.IGNORECASE,
    )
    for script in soup.find_all("script"):
        script_text = script.string or ""
        match = video_pattern.search(script_text)
        if match:
            return match.group(0).rstrip(",;}")

    return None


def extrair_video_shopee(url_compartilhada: str) -> str | None:
    """
    Main extraction function.
    1. Resolve the URL (follows short links).
    2. Try Shopee's internal API (most reliable).
    3. Fall back to HTML scraping.
    """
    try:
        url_real = resolver_url(url_compartilhada)
        logger.info("Resolved URL: %s", url_real)

        shop_id, item_id = extrair_ids_da_url(url_real)
        logger.info("Extracted IDs — shop_id: %s, item_id: %s", shop_id, item_id)

        # Attempt 1 – Shopee API (works even for JS-rendered pages)
        if shop_id and item_id:
            video_url = extrair_video_via_api(shop_id, item_id)
            if video_url:
                return video_url

        # Attempt 2 – HTML scraping fallback
        video_url = extrair_video_via_html(url_real)
        if video_url:
            return video_url

        logger.warning("No video found for: %s", url_real)
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
