import logging
import os
import re
import subprocess
import tempfile
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
    return bool(re.search(
        r"(shopee\.[a-z.]+|shope\.ee|s\.shopee|shp\.ee|br\.shp\.ee)",
        text,
        re.IGNORECASE,
    ))


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
    import json as _json

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

    video_pattern = re.compile(
        r"https?://[^\s\"'<>]*\.(mp4|mov|webm)[^\s\"'<>]*",
        re.IGNORECASE,
    )

    for script in soup.find_all("script"):
        script_text = script.string or script.get_text() or ""
        if not script_text:
            continue

        # Priority 1: originVideoUrl (sem marca d'água)
        origin_match = re.search(r'"originVideoUrl"\s*:\s*"([^"]+)"', script_text)
        if origin_match:
            video_url = origin_match.group(1).replace("\\u002F", "/")
            logger.info("Found originVideoUrl (sem marca): %s", video_url)
            return video_url

        # Priority 2: videoUrl genérico (geralmente sem marca)
        plain_match = re.search(r'"videoUrl"\s*:\s*"([^"]+)"', script_text)
        if plain_match:
            video_url = plain_match.group(1).replace("\\u002F", "/")
            logger.info("Found videoUrl: %s", video_url)
            return video_url

        # Priority 3: watermarkVideoUrl (com marca d'água, último recurso)
        wm_match = re.search(r'"watermarkVideoUrl"\s*:\s*"([^"]+)"', script_text)
        if wm_match:
            video_url = wm_match.group(1).replace("\\u002F", "/")
            logger.info("Found watermarkVideoUrl (com marca): %s", video_url)
            return video_url

        # Generic mp4/webm URL search
        match = video_pattern.search(script_text)
        if match:
            return match.group(0).rstrip(",;}")

    return None


def remover_marca_dagua(video_url: str) -> bytes | None:
    """
    Baixa o vídeo e aplica desfoque nos cantos onde ficam as marcas d'água
    (logo ShopeeVideo no canto superior esquerdo e @usuário no inferior esquerdo).
    Retorna os bytes do vídeo processado, ou None em caso de falha.
    """
    tmp_in_path = None
    tmp_out_path = None
    try:
        logger.info("Baixando vídeo para remover marca d'água...")
        resp = requests.get(video_url, headers=HEADERS, timeout=60, stream=True)
        if resp.status_code != 200:
            logger.warning("Falha ao baixar vídeo: status %s", resp.status_code)
            return None

        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp_in:
            for chunk in resp.iter_content(chunk_size=65536):
                tmp_in.write(chunk)
            tmp_in_path = tmp_in.name

        tmp_out_path = tmp_in_path[:-4] + '_out.mp4'

        # Desfoca: canto superior esquerdo (logo Shopee) e inferior esquerdo (usuário)
        vf = (
            "split=3[a][b][c];"
            "[b]crop=iw*0.55:ih*0.13:0:0,boxblur=20:5[top];"
            "[c]crop=iw*0.55:ih*0.13:0:ih-ih*0.13,boxblur=20:5[bot];"
            "[a][top]overlay=0:0[tmp];"
            "[tmp][bot]overlay=0:H-h"
        )

        cmd = [
            "ffmpeg", "-y", "-i", tmp_in_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "copy",
            tmp_out_path,
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=180)
        if result.returncode != 0:
            logger.error("FFmpeg falhou: %s", result.stderr.decode()[-600:])
            return None

        with open(tmp_out_path, "rb") as f:
            return f.read()

    except Exception:
        logger.exception("Erro ao remover marca d'água")
        return None
    finally:
        for path in [tmp_in_path, tmp_out_path]:
            if path:
                try:
                    os.unlink(path)
                except Exception:
                    pass


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


def extrair_urls_da_mensagem(message) -> list[str]:
    """
    Extract all URLs from a Telegram message, including:
    - Plain text
    - Clickable link entities (url / text_link)
    """
    urls = []
    text = message.text or message.caption or ""

    # URLs embedded as entities (forwarded messages, inline links, etc.)
    entities = message.entities or message.caption_entities or []
    for entity in entities:
        if entity.type == "url":
            # Slice the raw URL from the message text
            url = text[entity.offset: entity.offset + entity.length]
            urls.append(url)
        elif entity.type == "text_link" and entity.url:
            urls.append(entity.url)

    # Also add the full plain text (catches links typed as plain text)
    if text:
        urls.append(text)

    return urls


async def processar_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    todas_urls = extrair_urls_da_mensagem(message)
    logger.info("URLs extraídas da mensagem: %s", todas_urls)

    # Find the first Shopee URL among all extracted URLs
    url_shopee = next((u for u in todas_urls if is_shopee_url(u)), None)

    if not url_shopee:
        await message.reply_text(
            "❌ Por favor, envie um link válido da Shopee.\n"
            "Exemplos: https://shope.ee/... ou https://shopee.com.br/..."
        )
        return

    texto = url_shopee

    status_msg = await update.message.reply_text("⏳ Processando o link, aguarde...")

    link_video = extrair_video_shopee(texto)

    if link_video:
        try:
            await status_msg.edit_text("🎞️ Removendo marca d'água, aguarde...")
            video_bytes = remover_marca_dagua(link_video)

            await status_msg.edit_text("📤 Enviando o vídeo...")
            if video_bytes:
                from telegram import InputFile
                import io
                await update.message.reply_video(
                    video=InputFile(io.BytesIO(video_bytes), filename="video.mp4"),
                    caption="🎥 Aqui está o seu vídeo da Shopee!",
                )
            else:
                # Fallback: envia o link direto se o processamento falhar
                await update.message.reply_video(
                    video=link_video,
                    caption="🎥 Aqui está o seu vídeo da Shopee! (sem remoção de marca)",
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

    webhook_url = os.environ.get("WEBHOOK_URL", "").rstrip("/")
    port = int(os.environ.get("PORT", 8443))

    if webhook_url:
        # Production: webhook mode (used on Render, Railway, etc.)
        logger.info("Iniciando em modo webhook na porta %d...", port)
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=TOKEN,
            webhook_url=f"{webhook_url}/{TOKEN}",
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        # Development: polling mode (used on Replit locally)
        logger.info("Iniciando em modo polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
