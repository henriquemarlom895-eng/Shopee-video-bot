"""
Busca informações de produto (nome, preço, imagem) via API interna da Shopee.
"""
import re
import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

API_HEADERS = {
    **HEADERS,
    "Referer": "https://shopee.com.br/",
    "X-API-SOURCE": "pc",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json",
}


def resolver_url(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, allow_redirects=True, timeout=15)
    return resp.url


def extrair_ids(url: str):
    match = re.search(r"/(\d+)/(\d+)(?:[?#]|$)", url)
    if match:
        return match.group(1), match.group(2)
    return None, None


def get_product_info(shopee_url: str) -> dict | None:
    """
    Retorna dict com: name, price (float R$), image_url, item_id, shop_id
    ou None se não conseguir obter.
    """
    try:
        url_real = resolver_url(shopee_url)
        shop_id, item_id = extrair_ids(url_real)
        if not shop_id or not item_id:
            print(f"  ⚠️  Não encontrei shop_id/item_id em: {url_real}")
            return None

        api_url = (
            f"https://shopee.com.br/api/v4/item/get"
            f"?itemid={item_id}&shopid={shop_id}"
        )
        resp = requests.get(api_url, headers=API_HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"  ⚠️  API retornou {resp.status_code}")
            return None

        data = resp.json()
        item = data.get("data") or data.get("item") or {}

        name = item.get("name") or "Produto Shopee"

        # Preço em centavos × 100000 → divide por 100000 para R$
        price_raw = item.get("price") or item.get("price_min") or 0
        price_brl = price_raw / 100000

        # Imagem: pega a primeira da lista
        images = item.get("images") or []
        image_url = None
        if images:
            image_url = f"https://down-f.svr.shopee.com.br/file/{images[0]}"

        return {
            "name": name,
            "price": price_brl,
            "image_url": image_url,
            "item_id": item_id,
            "shop_id": shop_id,
        }

    except Exception as e:
        print(f"  ❌ Erro ao buscar produto: {e}")
        return None
