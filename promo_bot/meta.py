"""
Publicação nas redes sociais via Meta Graph API e Threads API.
"""
import time
import requests

FB_GRAPH = "https://graph.facebook.com/v19.0"
THREADS_GRAPH = "https://graph.threads.net/v1.0"


class MetaPoster:
    def __init__(self, page_id: str, page_token: str, ig_user_id: str, threads_user_id: str):
        self.page_id = page_id
        self.page_token = page_token
        self.ig_user_id = ig_user_id
        self.threads_user_id = threads_user_id

    # ── Facebook ──────────────────────────────────────────────────────────

    def post_facebook(self, text: str, image_url: str) -> dict:
        """Posta foto com legenda na página do Facebook."""
        url = f"{FB_GRAPH}/{self.page_id}/photos"
        resp = requests.post(url, data={
            "url": image_url,
            "caption": text,
            "access_token": self.page_token,
        }, timeout=30)
        return resp.json()

    # ── Instagram ─────────────────────────────────────────────────────────

    def _ig_create_container(self, image_url: str, caption: str, media_type: str = "IMAGE") -> str | None:
        url = f"{FB_GRAPH}/{self.ig_user_id}/media"
        data = {
            "image_url": image_url,
            "caption": caption,
            "media_type": media_type,
            "access_token": self.page_token,
        }
        resp = requests.post(url, data=data, timeout=30)
        return resp.json().get("id")

    def _ig_publish(self, container_id: str) -> dict:
        url = f"{FB_GRAPH}/{self.ig_user_id}/media_publish"
        resp = requests.post(url, data={
            "creation_id": container_id,
            "access_token": self.page_token,
        }, timeout=30)
        return resp.json()

    def post_instagram_feed(self, text: str, image_url: str) -> dict:
        """Posta no feed do Instagram."""
        container_id = self._ig_create_container(image_url, text)
        if not container_id:
            return {"error": "Não foi possível criar container"}
        time.sleep(5)
        return self._ig_publish(container_id)

    def post_instagram_story(self, image_url: str) -> dict:
        """Posta nos Stories do Instagram (sem legenda — limitação da API)."""
        url = f"{FB_GRAPH}/{self.ig_user_id}/media"
        resp = requests.post(url, data={
            "image_url": image_url,
            "media_type": "STORIES",
            "access_token": self.page_token,
        }, timeout=30)
        container_id = resp.json().get("id")
        if not container_id:
            return resp.json()
        time.sleep(5)
        return self._ig_publish(container_id)

    # ── Threads ───────────────────────────────────────────────────────────

    def post_threads(self, text: str, image_url: str) -> dict:
        """Posta no Threads com imagem."""
        # Passo 1: cria container
        url = f"{THREADS_GRAPH}/{self.threads_user_id}/threads"
        resp = requests.post(url, data={
            "media_type": "IMAGE",
            "image_url": image_url,
            "text": text,
            "access_token": self.page_token,
        }, timeout=30)
        container_id = resp.json().get("id")
        if not container_id:
            return resp.json()

        # Passo 2: aguarda e publica (Threads exige ~30s)
        time.sleep(32)
        url = f"{THREADS_GRAPH}/{self.threads_user_id}/threads_publish"
        resp = requests.post(url, data={
            "creation_id": container_id,
            "access_token": self.page_token,
        }, timeout=30)
        return resp.json()
