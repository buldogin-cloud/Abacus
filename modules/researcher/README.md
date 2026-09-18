# Модуль «Дослідник» (Researcher)

Моніторить офіційні джерела — **МОЗ України** та **НСЗУ** — а також
профільні новини за ключовими словами, формуючи короткий дайджест.

## Використання

```python
from modules.researcher.researcher import Researcher

researcher = Researcher(keywords=["анестезіолог", "закупівл", "стандарт"])

researcher.search_moz_updates(limit=10)    # оновлення МОЗ
researcher.search_nszu_updates(limit=10)   # оновлення НСЗУ
print(researcher.generate_digest())        # markdown-дайджест
```

## Методи

| Метод | Опис |
|-------|------|
| `search_moz_updates(limit)` | Останні новини з сайту МОЗ. |
| `search_nszu_updates(limit)` | Останні новини з сайту НСЗУ. |
| `search_rss(feed_url, source, limit)` | Записи з довільної RSS-стрічки. |
| `generate_digest(limit_per_source)` | Зведений дайджест у markdown. |

## Примітки

- Джерела зчитуються через HTTP/HTML (та RSS для `search_rss`).
- За відсутності мережі або зміни структури сайтів методи повертають
  порожні списки, **не перериваючи** роботу щоденного брифінгу.
- Ключові слова фільтрують заголовки без урахування регістру.
