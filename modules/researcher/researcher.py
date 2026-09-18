"""Модуль Дослідник (Researcher).

Клас :class:`Researcher` моніторить офіційні джерела — МОЗ України, НСЗУ —
та профільні новини (за ключовими словами), формуючи короткий дайджест.

Джерела зчитуються через RSS/HTML. За відсутності мережі або зміни
структури сайтів методи повертають порожні списки, не перериваючи роботу
щоденного брифінгу.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import feedparser
import requests
from bs4 import BeautifulSoup

# Офіційні джерела за замовчуванням.
MOZ_NEWS_URL = "https://moz.gov.ua/uk/news"
NSZU_NEWS_URL = "https://nszu.gov.ua/novini"

# Таймаут мережевих запитів (секунди).
REQUEST_TIMEOUT = 15
USER_AGENT = "CommandCenter-Researcher/1.0 (+https://github.com/buldogin-cloud/Abacus)"


class Researcher:
    """Моніторинг оновлень МОЗ, НСЗУ та профільних новин."""

    def __init__(self, keywords: list[str] | None = None) -> None:
        """Ініціалізує Дослідника.

        :param keywords: ключові слова для фільтрації (напр. «анестезіологія»,
            «закупівлі», «стандарт»). Якщо None — повертаються всі знайдені записи.
        """
        self.keywords = [k.lower() for k in (keywords or [])]

    # ------------------------------------------------------------------ #
    # Внутрішні допоміжні методи
    # ------------------------------------------------------------------ #
    def _matches_keywords(self, text: str) -> bool:
        """Перевіряє, чи містить текст хоча б одне ключове слово."""
        if not self.keywords:
            return True
        low = text.lower()
        return any(k in low for k in self.keywords)

    def _fetch_html(self, url: str) -> str | None:
        """Завантажує HTML-сторінку; повертає None у разі помилки."""
        try:
            resp = requests.get(
                url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": USER_AGENT}
            )
            resp.raise_for_status()
            return resp.text
        except requests.RequestException:
            return None

    def _parse_news_page(self, url: str, source: str, limit: int = 10) -> list[dict[str, Any]]:
        """Універсальний парсер сторінки новин (заголовки + посилання)."""
        html = self._fetch_html(url)
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        # Збираємо посилання, що ведуть на сторінки новин.
        for link in soup.find_all("a", href=True):
            title = link.get_text(strip=True)
            href = link["href"]
            if not title or len(title) < 15:
                continue
            if href in seen:
                continue
            if not self._matches_keywords(title):
                continue
            # Відносні посилання доповнюємо доменом.
            if href.startswith("/"):
                base = url.split("/uk/")[0].split("/novini")[0].rstrip("/")
                href = base + href
            seen.add(href)
            items.append(
                {
                    "source": source,
                    "title": title,
                    "url": href,
                    "fetched_at": datetime.now().isoformat(timespec="minutes"),
                }
            )
            if len(items) >= limit:
                break
        return items

    # ------------------------------------------------------------------ #
    # Публічний інтерфейс
    # ------------------------------------------------------------------ #
    def search_moz_updates(self, limit: int = 10) -> list[dict[str, Any]]:
        """Повертає останні оновлення з сайту МОЗ України."""
        return self._parse_news_page(MOZ_NEWS_URL, source="МОЗ", limit=limit)

    def search_nszu_updates(self, limit: int = 10) -> list[dict[str, Any]]:
        """Повертає останні оновлення з сайту НСЗУ."""
        return self._parse_news_page(NSZU_NEWS_URL, source="НСЗУ", limit=limit)

    def search_rss(self, feed_url: str, source: str, limit: int = 10) -> list[dict[str, Any]]:
        """Повертає записи з RSS-стрічки, відфільтровані за ключовими словами."""
        feed = feedparser.parse(feed_url)
        items = []
        for entry in feed.entries:
            title = getattr(entry, "title", "")
            if not self._matches_keywords(title):
                continue
            items.append(
                {
                    "source": source,
                    "title": title,
                    "url": getattr(entry, "link", ""),
                    "published": getattr(entry, "published", ""),
                }
            )
            if len(items) >= limit:
                break
        return items

    def generate_digest(self, limit_per_source: int = 5) -> str:
        """Формує короткий дайджест оновлень у markdown-форматі."""
        moz = self.search_moz_updates(limit=limit_per_source)
        nszu = self.search_nszu_updates(limit=limit_per_source)

        lines = [f"# 📰 Дайджест Дослідника — {datetime.now():%d.%m.%Y}", ""]

        def _section(title: str, items: list[dict[str, Any]]) -> None:
            lines.append(f"## {title}")
            if not items:
                lines.append("_Немає нових записів або джерело недоступне._")
            else:
                for it in items:
                    lines.append(f"- [{it['title']}]({it['url']})")
            lines.append("")

        _section("МОЗ України", moz)
        _section("НСЗУ", nszu)
        return "\n".join(lines)


if __name__ == "__main__":
    # Проста ручна перевірка роботи Дослідника.
    researcher = Researcher(keywords=["анестезіолог", "закупівл", "стандарт", "наказ"])
    print(researcher.generate_digest())
