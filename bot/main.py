import io
import logging
import os
import re
import subprocess
import tempfile

import requests
from bs4 import BeautifulSoup
from telegram import InputFile, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

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


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def is_shopee_url(text: str) -> bool:
    return bool(re.search(
        r"(shopee\.[a-z.]+|shope\.ee|s\.shopee|shp\.ee|br\.shp\.ee)",
        text,
        re.IGNORECASE,
    ))


def resolver_url(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, allow_redirects=True, timeout=15)
    return resp.url


def extrair_ids_da_url(url: str):
    match = re.search(r"/(\d+)/(\d+)(?:[?#]|$)", url)
    if match:
        return match.group(1), match.group(2)
    return None, None


# ---------------------------------------------------------------------------
# Video extraction
# ---------------------------------------------------------------------------

def extrair_video_via_api(shop_id: str, item_id: str) -> str | None:
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
            item_data = data.get("data") or data.get("item") or {}
            for vid in item_data.get("video_info_list") or []:
                fmt = vid.get("default_format") or {}
                video_url = fmt.get("video_url") or fmt.get("url") or vid.get("video_url") or vid.get("url")
                if video_url:
                    logger.info("Found video via API: %s", video_url)
                    return video_url
        except Exception:
            logger.exception("API call failed for %s", url)
    return None


def extrair_video_via_html(url_real: str) -> str | None:
    html = requests.get(url_real, headers=HEADERS, timeout=15).text
    soup = BeautifulSoup(html, "lxml")

    video_tag = soup.find("video")
    if video_tag:
        src = video_tag.get("src")
        if src:
            return src
        source_tag = video_tag.find("source")
        if source_tag and source_tag.get("src"):
            return source_tag["src"]

    video_pattern = re.compile(
        r"https?://[^\s\"'<>]*\.(mp4|mov|webm)[^\s\"'<>]*", re.IGNORECASE
    )

    for script in soup.find_all("script"):
        script_text = script.string or script.get_text() or ""
        if not script_text:
            continue

        # Sem marca d'água (prioridade máxima)
        for field in ("originVideoUrl", "videoUrl"):
            m = re.search(rf'"{field}"\s*:\s*"([^"]+)"', script_text)
            if m:
                video_url = m.group(1).replace("\\u002F", "/")
                logger.info("Found %s: %s", field, video_url)
                return video_url

        # Com marca d'água (último recurso)
        m = re.search(r'"watermarkVideoUrl"\s*:\s*"([^"]+)"', script_text)
        if m:
            video_url = m.group(1).replace("\\u002F", "/")
            logger.info("Found watermarkVideoUrl (com marca): %s", video_url)
            return video_url

        m = video_pattern.search(script_text)
        if m:
            return m.group(0).rstrip(",;}")

    return None


def extrair_video_shopee(url_compartilhada: str) -> str | None:
    try:
        url_real = resolver_url(url_compartilhada)
        logger.info("Resolved URL: %s", url_real)

        shop_id, item_id = extrair_ids_da_url(url_real)
        logger.info("IDs — shop_id: %s, item_id: %s", shop_id, item_id)

        if shop_id and item_id:
            video_url = extrair_video_via_api(shop_id, item_id)
            if video_url:
                return video_url

        return extrair_video_via_html(url_real)

    except Exception:
        logger.exception("Erro ao extrair vídeo")
        return None


# ---------------------------------------------------------------------------
# Watermark removal
# ---------------------------------------------------------------------------

def url_sem_marca(video_url: str) -> str | None:
    """
    O CDN da Shopee guarda o vídeo original em {base}.mp4
    enquanto a versão com marca fica em {base}.{timestamp}.{id}.mp4
    Tenta acessar a versão original sem marca.
    """
    nova_url = re.sub(r'\.\d+\.\d+(\.mp4)$', r'\1', video_url, flags=re.IGNORECASE)
    if nova_url == video_url:
        return None
    try:
        r = requests.head(nova_url, headers=HEADERS, timeout=10, allow_redirects=True)
        if r.status_code == 200 and "video" in r.headers.get("Content-Type", ""):
            logger.info("URL sem marca encontrada: %s", nova_url)
            return nova_url
    except Exception:
        pass
    return None


def remover_marca_dagua_ffmpeg(video_url: str) -> bytes | None:
    """
    Fallback: baixa o vídeo e desfoca os cantos com as marcas.
    """
    tmp_in_path = None
    tmp_out_path = None
    try:
        logger.info("Baixando vídeo para processar com ffmpeg...")
        resp = requests.get(video_url, headers=HEADERS, timeout=60, stream=True)
        if resp.status_code != 200:
            return None

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_in:
            for chunk in resp.iter_content(chunk_size=65536):
                tmp_in.write(chunk)
            tmp_in_path = tmp_in.name

        tmp_out_path = tmp_in_path[:-4] + "_out.mp4"

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
        logger.exception("Erro no ffmpeg")
        return None
    finally:
        for path in [tmp_in_path, tmp_out_path]:
            if path:
                try:
                    os.unlink(path)
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 Olá! Sou o bot de download de vídeos da Shopee.\n\n"
        "📤 Envie um link de produto ou vídeo da Shopee e eu baixo o vídeo sem marca d'água!\n\n"
        "Exemplo:\n"
        "https://shope.ee/xxxxxxxx\n"
        "https://shopee.com.br/produto-xxx"
    )


def extrair_urls_da_mensagem(message) -> list[str]:
    urls = []
    text = message.text or message.caption or ""
    entities = message.entities or message.caption_entities or []
    for entity in entities:
        if entity.type == "url":
            urls.append(text[entity.offset: entity.offset + entity.length])
        elif entity.type == "text_link" and entity.url:
            urls.append(entity.url)
    if text:
        urls.append(text)
    return urls


async def processar_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    todas_urls = extrair_urls_da_mensagem(message)
    logger.info("URLs extraídas: %s", todas_urls)

    url_shopee = next((u for u in todas_urls if is_shopee_url(u)), None)

    if not url_shopee:
        await message.reply_text(
            "❌ Por favor, envie um link válido da Shopee.\n"
            "Exemplos: https://shope.ee/... ou https://shopee.com.br/..."
        )
        return

    status_msg = await update.message.reply_text("⏳ Processando o link, aguarde...")

    link_video = extrair_video_shopee(url_shopee)

    if link_video:
        try:
            await status_msg.edit_text("🎞️ Removendo marca d'água, aguarde...")

            # Método 1: URL original sem marca via CDN (rápido)
            link_limpo = url_sem_marca(link_video)

            await status_msg.edit_text("📤 Enviando o vídeo...")

            if link_limpo:
                logger.info("Enviando URL sem marca: %s", link_limpo)
                await update.message.reply_video(
                    video=link_limpo,
                    caption="🎥 Aqui está o seu vídeo da Shopee!",
                )
            else:
                # Método 2: ffmpeg desfoca as marcas
                logger.info("Usando ffmpeg para remover marcas...")
                video_bytes = remover_marca_dagua_ffmpeg(link_video)
                if video_bytes:
                    await update.message.reply_video(
                        video=InputFile(io.BytesIO(video_bytes), filename="video.mp4"),
                        caption="🎥 Aqui está o seu vídeo da Shopee!",
                    )
                else:
                    await update.message.reply_video(
                        video=link_video,
                        caption="🎥 Aqui está o seu vídeo da Shopee!",
                    )

            await status_msg.delete()

        except Exception as e:
            logger.exception("Erro ao enviar vídeo")
            await status_msg.edit_text(
                f"⚠️ Encontrei o vídeo, mas não consegui enviá-lo.\n\n"
                f"Acesse diretamente:\n{link_video}\n\n"
                f"Erro: {e}"
            )
    else:
        await status_msg.edit_text(
            "😕 Não consegui encontrar nenhum vídeo nesse link.\n\n"
            "Certifique-se de que:\n"
            "• O link leva a um produto com vídeo\n"
            "• O link é válido e acessível"
        )


def main() -> None:
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processar_mensagem))

    webhook_url = os.environ.get("WEBHOOK_URL", "").rstrip("/")
    port = int(os.environ.get("PORT", 8443))

    if webhook_url:
        logger.info("Iniciando em modo webhook na porta %d...", port)
        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path=TOKEN,
            webhook_url=f"{webhook_url}/{TOKEN}",
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        logger.info("Iniciando em modo polling...")
        application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
