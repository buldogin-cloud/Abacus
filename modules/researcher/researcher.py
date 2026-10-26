"""Модуль Дослідник (Researcher).

Клас :class:`Researcher` моніторить офіційні джерела — МОЗ України, НСЗУ —
та профільні накази/новини (за ключовими словами), формуючи короткий дайджест.

ВАЖЛИВО про доступ до джерел
----------------------------
Сайти ``moz.gov.ua`` та ``nszu.gov.ua`` захищені Cloudflare (JS-challenge),
тому прямий HTTP-запит повертає **HTTP 403** і жодного контенту. Через це
попередня версія модуля щоразу мовчки повертала порожній список — і важливі
накази (напр. № 1675 «Про систему анестезіологічної та інтенсивної допомоги»)
не потрапляли у брифінг.

Нова стратегія збору (стійка до Cloudflare, придатна для CI):

1. **Google News RSS** — основне джерело. Запит іде до ``news.google.com``,
   а не напряму до moz.gov.ua, тому Cloudflare не блокує. Дає свіжі новини
   з фільтром ``site:moz.gov.ua`` / ``site:nszu.gov.ua``.
2. **aaukr.org/mozcat/nakazy-moz** — профільне дзеркало наказів МОЗ з
   анестезіології та інтенсивної терапії (віддає HTTP 200). Ідеальне для
   профілю користувача (заступник з анестезіологічної допомоги).
3. **Прямий HTTP** до офіційних сайтів — як остання спроба з реалістичним
   User-Agent. Якщо повертається 403 — джерело позначається як
   ``blocked`` (а не «0 записів = все добре»), і статус видно у дайджесті.

Статус кожного джерела зберігається у ``self.source_status`` для того, щоб
оркестратор (collector) міг відрізнити «джерело недоступне» від
«справді немає нових записів».
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime
from typing import Any

import feedparser
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Офіційні джерела та ендпоінти
# ---------------------------------------------------------------------------
MOZ_NEWS_URL = "https://moz.gov.ua/uk/news"
NSZU_NEWS_URL = "https://nszu.gov.ua/novini"
# Урядовий портал (Кабінет Міністрів) — постанови/розпорядження КМУ.
# НЕ за Cloudflare (Apache), тому доступний напряму + через Google News.
KMU_NPAS_URL = "https://www.kmu.gov.ua/npas"

# Профільне дзеркало наказів МОЗ (анестезіологія та інтенсивна терапія).
AAUKR_ORDERS_URL = "https://aaukr.org/mozcat/nakazy-moz/"

# Шаблон Google News RSS (обходить Cloudflare).
GOOGLE_NEWS_RSS = (
    "https://news.google.com/rss/search?q={query}&hl=uk&gl=UA&ceid=UA:uk"
)

# Таймаут мережевих запитів (секунди).
REQUEST_TIMEOUT = 20

# Реалістичний User-Agent — деякі сайти блокують «ботоподібні» рядки.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Рядок дати у форматі DD.MM.YYYY на початку заголовка наказу з aaukr.
_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
# Номер наказу у заголовку («№ 1675», «№1328»).
_ORDER_NO_RE = re.compile(r"№\s?(\d+)")


class Researcher:
    """Моніторинг оновлень МОЗ, НСЗУ та профільних наказів/новин."""

    def __init__(self, keywords: list[str] | None = None,
                 rss_sources: list[dict[str, Any]] | None = None) -> None:
        """Ініціалізує Дослідника.

        :param keywords: ключові слова для фільтрації (напр. «анестезіолог»,
            «закупівл», «стандарт», «наказ»). Якщо None або порожній —
            повертаються всі знайдені записи.
        """
        self.keywords = [k.lower() for k in (keywords or [])]
        # Статус останнього збору по кожному джерелу:
        #   "ok"        — успішно отримано контент
        #   "blocked"   — джерело віддало 403/Cloudflare-challenge
        #   "error"     — мережева/парсинг-помилка
        #   "empty"     — контент отримано, але релевантних записів немає
        self.source_status: dict[str, str] = {}
        self.source_meta: dict[str, dict[str, Any]] = {}
        self.rss_sources = self._validate_rss_sources(rss_sources)

    @staticmethod
    def _validate_rss_sources(sources):
        """Validate explicit configured feeds; never infer official status."""
        if sources is None:
            return []
        if not isinstance(sources, list):
            raise ValueError("rss_sources_must_be_list")
        checked, ids, labels = [], set(), set()
        reserved = {"Накази МОЗ (aaukr)", "МОЗ (новини)", "НСЗУ", "КМУ (постанови)"}
        for source in sources:
            if not isinstance(source, dict):
                raise ValueError("invalid_rss_source")
            if not isinstance(source.get("enabled", True), bool):
                raise ValueError("rss_enabled_must_be_boolean")
            if source.get("enabled", True) is False:
                continue
            source_id, label, url = (source.get(key, "") for key in ("id", "label", "url"))
            if not all(isinstance(x, str) and x.strip() for x in (source_id, label, url)):
                raise ValueError("rss_identity_required")
            parsed = urllib.parse.urlsplit(url)
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("rss_https_url_required")
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", source_id):
                raise ValueError("invalid_rss_id")
            if source_id in ids or source_id in {"moz_ukraine", "nszu", "kmu_cabinet", "gmail", "calendar", "tasks_registry"} or label in labels or label in reserved:
                raise ValueError("duplicate_rss_source")
            kind = source.get("kind", "research")
            if kind not in ("research", "clinical"):
                raise ValueError("invalid_rss_kind")
            keywords = source.get("keywords")
            if keywords is not None and (not isinstance(keywords, list) or
                                        not all(isinstance(k, str) for k in keywords)):
                raise ValueError("invalid_rss_keywords")
            ids.add(source_id)
            labels.add(label)
            checked.append(dict(source, id=source_id, label=label, url=url, kind=kind))
        return checked

    # ------------------------------------------------------------------ #
    # Внутрішні допоміжні методи
    # ------------------------------------------------------------------ #
    def _matches_keywords(self, text: str) -> bool:
        """Перевіряє, чи містить текст хоча б одне ключове слово."""
        if not self.keywords:
            return True
        low = text.lower()
        return any(k in low for k in self.keywords)

    def _fetch(self, url: str) -> tuple[str | None, str]:
        """Завантажує сторінку.

        :returns: кортеж ``(text, status)``, де ``status`` ∈
            {"ok", "blocked", "error"}.
        """
        try:
            resp = requests.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers={
                    "User-Agent": BROWSER_UA,
                    "Accept-Language": "uk,en;q=0.8",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
        except requests.RequestException:
            return None, "error"

        if resp.status_code == 403 or "Just a moment" in resp.text[:600]:
            return None, "blocked"
        if not resp.ok:
            return None, "error"
        return resp.text, "ok"

    @staticmethod
    def _clean_title(title: str) -> str:
        """Прибирає суфікс джерела Google News («… - МОЗ»)."""
        return re.sub(r"\s+-\s+[^-]+$", "", title).strip() or title.strip()

    # ------------------------------------------------------------------ #
    # Джерело 1: Google News RSS (обходить Cloudflare)
    # ------------------------------------------------------------------ #
    def search_google_news(
        self, query: str, source: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Пошук через Google News RSS.

        :param query: пошуковий запит (можна з ``site:moz.gov.ua``).
        :param source: людська назва джерела для дайджесту.
        """
        url = GOOGLE_NEWS_RSS.format(query=urllib.parse.quote(query))
        try:
            feed = feedparser.parse(url)
        except Exception:  # noqa: BLE001 — feedparser рідко, але кидає
            self.source_status[source] = "error"
            return []

        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in feed.entries:
            title = self._clean_title(getattr(entry, "title", ""))
            # Відсікаємо надто короткі/беззмістовні заголовки («Наказ», «602»).
            if not title or len(title) < 20 or title in seen:
                continue
            if not self._matches_keywords(title):
                continue
            seen.add(title)
            items.append(
                {
                    "source": source,
                    "title": title,
                    "url": getattr(entry, "link", ""),
                    "published": getattr(entry, "published", ""),
                    "via": "google_news",
                }
            )
            if len(items) >= limit:
                break

        self.source_status[source] = "ok" if feed.entries else "empty"
        return items

    # ------------------------------------------------------------------ #
    # Джерело 2: aaukr.org — накази МОЗ (анестезіологія/ІТ)
    # ------------------------------------------------------------------ #
    def search_moz_orders(self, limit: int = 15) -> list[dict[str, Any]]:
        """Парсить перелік наказів МОЗ з профільного дзеркала aaukr.org.

        Заголовки мають вигляд «03.11.2025№ 1675 "Про систему …"».
        """
        text, status = self._fetch(AAUKR_ORDERS_URL)
        if status != "ok" or not text:
            self.source_status["Накази МОЗ (aaukr)"] = status
            return []

        soup = BeautifulSoup(text, "html.parser")
        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        for link in soup.find_all("a", href=True):
            raw = link.get_text(" ", strip=True)
            href = link["href"]
            if not raw or len(raw) < 20:
                continue
            date_m = _DATE_RE.search(raw)
            no_m = _ORDER_NO_RE.search(raw)
            # Нас цікавлять лише рядки, що виглядають як накази (є дата + №).
            if not (date_m and no_m):
                continue
            if href in seen:
                continue
            if not self._matches_keywords(raw):
                continue

            dd, mm, yyyy = date_m.groups()
            try:
                order_date = datetime(int(yyyy), int(mm), int(dd))
            except ValueError:
                order_date = None

            # Заголовок без дати на початку.
            title = raw[date_m.end():].lstrip(" —-") if date_m else raw
            seen.add(href)
            items.append(
                {
                    "source": "Накази МОЗ (aaukr)",
                    "title": title.strip(),
                    "order_no": no_m.group(1),
                    "date": order_date.isoformat()[:10] if order_date else "",
                    "_date_obj": order_date,
                    "url": href,
                    "via": "aaukr",
                }
            )

        # Сортуємо за датою (найновіші зверху) і обрізаємо.
        items.sort(key=lambda x: x["_date_obj"] or datetime.min, reverse=True)
        items = items[:limit]
        for it in items:
            it.pop("_date_obj", None)

        self.source_status["Накази МОЗ (aaukr)"] = "ok" if items else "empty"
        return items

    # ------------------------------------------------------------------ #
    # Публічний інтерфейс (зворотно сумісний)
    # ------------------------------------------------------------------ #
    def search_moz_updates(self, limit: int = 10) -> list[dict[str, Any]]:
        """Останні оновлення МОЗ: накази (aaukr) + новини (Google News).

        Прямий сайт МОЗ за Cloudflare — не перевіряємо, використовуємо
        тільки джерела що реально доступні (aaukr + Google News).
        """
        results = self.search_moz_orders(limit=limit)
        news = self.search_google_news(
            "site:moz.gov.ua", source="МОЗ (новини)", limit=limit
        )
        # Об'єднуємо, уникаючи дублів за URL.
        seen = {r["url"] for r in results}
        for n in news:
            if n["url"] not in seen:
                results.append(n)
                seen.add(n["url"])
        return results[: limit * 2]

    def search_nszu_updates(self, limit: int = 10) -> list[dict[str, Any]]:
        """Останні оновлення НСЗУ через Google News (сайт за Cloudflare)."""
        return self.search_google_news(
            "site:nszu.gov.ua", source="НСЗУ", limit=limit
        )

    def search_kmu_updates(self, limit: int = 10) -> list[dict[str, Any]]:
        """Останні постанови/розпорядження КМУ (Урядовий портал).

        Урядовий портал не за Cloudflare, тож використовуємо Google News
        (site:kmu.gov.ua) як стабільний канал зі свіжими постановами.
        Фільтруємо за медичними ключовими словами, щоб не тонути у всіх
        актах уряду.
        """
        # Google News з медичним фокусом.
        med_query = (
            "site:kmu.gov.ua постанова (охорона здоров'я OR медичн OR "
            "лікарн OR госпіталіз OR повітрян)"
        )
        return self.search_google_news(
            med_query, source="КМУ (постанови)", limit=limit
        )

    def search_rss(
        self, feed_url: str, source: str, limit: int = 10,
        keywords: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Read a bounded feed snapshot, not a claim of complete history.

        Empty means a valid feed with zero matching entries; HTTP/parse failures
        are errors. No article body is fetched or clinically verified here.
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("rss_positive_limit_required")
        text, status = self._fetch(feed_url)
        self.source_meta[source] = {"feed_url": feed_url, "coverage": "feed_snapshot"}
        if status != "ok" or not text:
            self.source_status[source] = status if status != "ok" else "error"
            return []
        try:
            feed = feedparser.parse(text)
        except Exception:
            self.source_status[source] = "error"
            return []
        if feed.get("bozo") or not feed.get("version"):
            self.source_status[source] = "error"
            return []
        selected = self.keywords if keywords is None else [k.lower() for k in keywords]
        items, seen = [], set()
        invalid = 0
        for entry in feed.entries:
            title = str(entry.get("title", "")).strip()
            url = str(entry.get("link", "")).strip()
            try:
                parsed = urllib.parse.urlsplit(url)
                valid_url = (parsed.scheme in ("http", "https") and parsed.hostname
                             and not parsed.username and not parsed.password)
            except ValueError:
                valid_url = False
            if not title or not valid_url:
                invalid += 1
                continue
            if selected and not any(k in title.lower() for k in selected):
                continue
            if url in seen:
                continue
            seen.add(url)
            items.append({"source": source, "title": title, "url": url,
                          "published": entry.get("published") or entry.get("updated", ""),
                          "via": "rss"})
        truncated = len(items) > limit
        self.source_meta[source].update(entries_seen=len(feed.entries),
                                        matching_entries=len(items), invalid_entries=invalid,
                                        truncated=truncated)
        self.source_status[source] = (
            "partial" if truncated or invalid else "ok" if items else "empty"
        )
        return items[:limit]

    def collect(self, limit_per_source: int = 5) -> dict[str, Any]:
        """Збирає все та повертає структурований результат зі статусами.

        Використовується оркестратором (collector.py) замість «сирого»
        дайджесту, щоб мати доступ до статусів джерел.
        """
        self.source_status.clear()
        self.source_meta.clear()
        moz = self.search_moz_updates(limit=limit_per_source)
        nszu = self.search_nszu_updates(limit=limit_per_source)
        kmu = self.search_kmu_updates(limit=limit_per_source)
        all_records = moz + nszu + kmu
        for source in self.rss_sources:
            rows = self.search_rss(source["url"], source["label"], limit_per_source,
                                   keywords=source.get("keywords"))
            for row in rows:
                row.update(source_code=source["id"], research_kind=source["kind"])
            all_records.extend(rows)

        blocked = [s for s, st in self.source_status.items() if st == "blocked"]
        errored = [s for s, st in self.source_status.items() if st == "error"]

        return {
            "records": all_records,
            "count": len(all_records),
            "source_status": dict(self.source_status),
            "blocked_sources": blocked,
            "errored_sources": errored,
            "partial_sources": [s for s, st in self.source_status.items() if st == "partial"],
            "source_meta": dict(self.source_meta),
            "collected_at": datetime.now().isoformat(timespec="seconds"),
        }

    def generate_digest(self, limit_per_source: int = 5) -> str:
        """Формує короткий дайджест оновлень у markdown-форматі."""
        collected = self.collect(limit_per_source)
        rows = collected["records"]
        moz = [r for r in rows if r["source"] in ("Накази МОЗ (aaukr)", "МОЗ (новини)")]
        nszu = [r for r in rows if r["source"] == "НСЗУ"]
        kmu = [r for r in rows if r["source"] == "КМУ (постанови)"]

        lines = [f"# 📰 Дайджест Дослідника — {datetime.now():%d.%m.%Y}", ""]

        def _section(title: str, items: list[dict[str, Any]]) -> None:
            lines.append(f"## {title}")
            if not items:
                lines.append("_Немає нових записів або джерело недоступне._")
            else:
                for it in items:
                    prefix = ""
                    if it.get("order_no"):
                        prefix = f"№{it['order_no']} ({it.get('date','')}) — "
                    lines.append(f"- {prefix}[{it['title']}]({it['url']})")
            lines.append("")

        _section("Накази та оновлення МОЗ", moz)
        _section("Постанови КМУ (Урядовий портал)", kmu)
        _section("НСЗУ", nszu)
        for source in self.rss_sources:
            _section(source["label"], [r for r in rows if r.get("source_code") == source["id"]])

        # Блок статусів джерел — щоб блокування було видно, а не приховане.
        lines.append("## Статус джерел")
        status_icon = {"ok": "✅", "blocked": "🚫 (Cloudflare)", "error": "⚠️", "empty": "➖", "partial": "⚠️ (часткове охоплення)"}
        for src, st in self.source_status.items():
            lines.append(f"- {src}: {status_icon.get(st, st)}")
        lines.append("")

        return "\n".join(lines)


if __name__ == "__main__":
    # Проста ручна перевірка роботи Дослідника.
    researcher = Researcher(
        keywords=["анестезіолог", "інтенсивн", "закупівл", "стандарт",
                  "наказ", "оснащенн", "протокол", "госпіталіз"]
    )
    print(researcher.generate_digest())
