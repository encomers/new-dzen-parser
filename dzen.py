import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from datetime import time as dt_time

import openai
import requests
import schedule
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# -------------------- Конфигурация логирования --------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)


# -------------------- Pydantic модели --------------------
class NewsArticle(BaseModel):
    title: str = Field(
        default="Не удалось сгененировать заголовок",
        description="Заголовок новости до 110 символов",
    )
    text: str = Field(
        default="Нет текста",
        description="Текст новости без заголовка, каждый абзац обернут в <p></p>",
    )
    subtitle: str = Field(default="Не удалось сгенерировать")
    meta_title: str = Field(
        default="Не удалось сгенерировать", description="SEO-заголовок до 80 символов"
    )
    meta_description: str = Field(
        default="Не удалось сгенерировать", description="SEO-описание до 100 символов"
    )
    source: int = Field(default=3, description="Источник новости")
    author: str = Field(default="New Dzen")


class AdditionalSizesSquare(BaseModel):
    src: str
    srcSet: str | None = None


class AdditionalSizes(BaseModel):
    square_big: AdditionalSizesSquare = Field(alias="square-big")
    square_small: AdditionalSizesSquare = Field(alias="square-small")

    model_config = ConfigDict(populate_by_name=True)


class MediaContent(BaseModel):
    lazy: bool
    alt: str
    type: str
    src: str
    bgColor: str
    additionalSizes: AdditionalSizes


class DzenPublication(BaseModel):
    dzenId: str
    publisherId: str


class StudioDocumentStats(BaseModel):
    docAgencyId: str
    docId: str
    docUrl: str
    docTitle: str
    docPubDate: int


class Story(BaseModel):
    id: str
    persistentId: str
    title: str
    titleUrl: str
    annotation: str
    mediaContent: MediaContent
    source: str
    sourceIcon: str
    agencyId: int
    time: str
    url: str
    target: str
    isTragic: bool
    bestRubric: int
    relatedRubricIds: list[int]
    rubricId: str
    documentUrlId: str
    dzenPublication: DzenPublication
    studioDocumentStats: StudioDocumentStats
    alreadyRead: bool
    timestampInTop: int
    cardMode: str
    bestRubricName: str


class Names(BaseModel):
    ru: str
    ru_genitive: str
    ru_genitive_full: str
    seo_body_description: str
    seo_body_title: str
    seo_description: str
    seo_title: str
    sub_title: str
    title: str


class Rubric(BaseModel):
    alias: str
    id: int
    is_region: bool
    is_sport: bool
    name: str
    names: Names
    type: str
    url: str


class DataModel(BaseModel):
    nextPage: str
    prevPage: str
    rubric: Rubric
    stories: list[Story]
    totalFresh: int


class RootResponse(BaseModel):
    data: DataModel


class ArticleContent(BaseModel):
    text: str
    url: str


# -------------------- Конфигурация --------------------
@dataclass(frozen=True)
class DzenApiConfig:
    base_url: str = "https://dzen.ru/news/rubric/chronologic"
    headers: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.headers:
            object.__setattr__(
                self,
                "headers",
                {
                    "Host": "dzen.ru",
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:152.0) Gecko/20100101 Firefox/152.0",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Referer": "https://dzen.ru/news/rubric/chronologic",
                    "Connection": "keep-alive",
                    "Sec-Fetch-Dest": "empty",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Site": "same-origin",
                    "DNT": "1",
                    "Sec-GPC": "1",
                },
            )

    @property
    def url(self) -> str:
        # Убираем neo_parent_id – теперь каждый запрос как новый
        return f"{self.base_url}?ajax=1"


# -------------------- Основной класс приложения --------------------
class DzenScraper:
    def __init__(self, config: DzenApiConfig | None = None):
        self.config = config or DzenApiConfig()
        self._init_openai()

    def _init_openai(self):
        api_key = os.getenv("YANDEX_API_KEY")
        if not api_key:
            raise ValueError("Переменная окружения YANDEX_API_KEY не установлена")
        base_url = os.getenv("YANDEX_BASE_URL", "https://ai.api.cloud.yandex.net/v1")
        project = os.getenv("YANDEX_PROJECT_ID", "b1g7e364b5giim9tajta")
        if not project:
            raise ValueError("Переменная окружения YANDEX_PROJECT_ID не установлена")
        self.openai_client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            project=project,
        )
        self.prompt_id = os.getenv("YANDEX_PROMPT_ID", "fvt3l28i6c6okkqodqk7")

    # -------------------- Генерация случайных идентификаторов --------------------
    @staticmethod
    def _random_id(length: int = 32) -> str:
        """Генерирует случайную строку из hex-символов."""
        return uuid.uuid4().hex[:length]

    # -------------------- Генерация динамической строки Cookie --------------------
    @staticmethod
    def _get_cookie_string() -> str:
        now_utc = datetime.now(UTC)
        # Устанавливаем story-last-date-count на час назад – чтобы сервер думал, что мы давно не заходили
        past_time = now_utc - timedelta(hours=1)
        story_date = past_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        # Генерируем новые сессионные идентификаторы
        zen_session = DzenScraper._random_id() + "." + str(int(time.time() * 1000))
        dzen_sess = "y0__" + DzenScraper._random_id(30)

        cookies = {
            "_yasc": "+W/3AMJqWzr4a/tHysPOwh3f0t1HVSqhN7oOsKhlZFHvINuHYDGG6ZCBik7zXSABHsJh3/ysMsDk+xE3I7334h5b35MSNzyIah/b3sEWpAKJTA==",
            "addruid": "1b7r6BK0c6T80TU8k8C47b2dK8",
            "ancQTZw": "1",
            "article-view-count": "1",
            "cmtchd": "MTc4NTc2MzI0MTQxNg==",
            "compliance-alert": "true",
            "count_news_trap_city": "0",
            "crookie": "wIIZ7OXDlv9EqyL7i5BK+u+jQV3PlGGEwtRx2tHeqiA/2yoRP24rUJQ4GA9lg8JTDZ1FQFo3YLT1IHxtOt2kCvePSSg=",
            "cryproxy_sync_ok": "1",
            "dzen_sess_id": dzen_sess,  # динамическая
            "has_stable_city": "true",
            "is_auth_through_phone": "true",
            "is_online_stat": "false",
            "is-news-fullscreen-waterfall-ended": "false",
            "KIykI": "1",
            "logical-device-height": "619",
            "logical-device-width": "1899",
            "mda2_beacon": str(int(time.time() * 1000)),  # тоже обновляем
            "nc": "opertopTooltipWasShown=true#web2appAdvertQrBannerLastShow=1764566522170",
            "news_cryproxy_sync_ok": "1",
            "news_lang": "ru",
            "news-fullscreen-current-position": "0",  # сбрасываем
            "news-fullscreen-showings-period-start-date": "2026-08-03T13:05:31.047Z",
            "sessionid2": "3:1768204235.5.1.1759731815900:ud60Xg:8a8b.1.2:1|1333380151.-1.2.3:1758782120.6:2265003531.7:1758964050|1977948841.-1.2.2:181930.3:1758964050.6:2265003531.7:1758964050|64:11602278.913709.fakesign0000000000000000000",
            "skip_glif_onboarding": "true",
            "skip_story_comments_onboarding": "true",
            "sso_status": "sso.passport.yandex.ru:synchronized",
            "stable_city": "2",
            "story-last-date-count": story_date,  # час назад
            "story-view-count": "0",  # сбрасываем
            "yandex_login": "a.m.gafarov",
            "yandexuid": "5847431971758782095",
            "ys": "udn.cDrQkNGA0YLQtdC8#c_chck.2893939542",
            "zen_gid": "43",
            "zen_has_vk_auth_after_sso": "1",
            "zen_session_id": zen_session,  # динамическая
            "zen_sso_checked": "1",
            "zen_vk_gid": "5534",
            "Zen-authorization": zen_session,  # используем тот же, что и zen_session_id
            "Zen-User-Data": '{"zen-theme":"light","zen-theme-setting":"light"}',
            "zencookie": "8711689031759731815",
        }

        return "; ".join(f"{k}={v}" for k, v in cookies.items())

    # -------------------- Работа с API Дзена --------------------
    def fetch_news(
        self, timestamp: int | None = None, timeout: int = 10
    ) -> dict | None:
        # timestamp ставим на минуту назад, чтобы запросить новости за последнюю минуту
        if timestamp is None:
            timestamp = int(time.time()) - 60

        headers = self.config.headers.copy()
        headers["Cookie"] = self._get_cookie_string()

        try:
            logger.info(
                "Отправка запроса к %s с timestamp=%d", self.config.url, timestamp
            )
            resp = requests.post(
                url=self.config.url,
                headers=headers,
                json={"timestamp": timestamp},
                timeout=timeout,
            )
            resp.raise_for_status()
            logger.info("Получен ответ, статус %d", resp.status_code)
            return resp.json()
        except requests.exceptions.RequestException as e:
            logger.error("Ошибка при выполнении запроса: %s", e)
            return None
        except json.JSONDecodeError as e:
            logger.error("Ошибка декодирования JSON: %s", e)
            return None

    @staticmethod
    def parse_news(raw_data: dict) -> RootResponse | None:
        try:
            return RootResponse(**raw_data)
        except Exception as e:
            logger.error("Ошибка валидации Pydantic: %s", e)
            return None

    @staticmethod
    def filter_recent_stories(stories: list[Story], minutes: int = 30) -> list[Story]:
        now = int(time.time())
        limit = now - minutes * 60
        return [s for s in stories if s.timestampInTop >= limit]

    # -------------------- Selenium парсинг --------------------
    @staticmethod
    def create_driver() -> webdriver.Chrome:
        options = Options()

        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        # Явно указываем путь к бинарнику Chrome
        options.binary_location = "/usr/bin/chromium"
        return webdriver.Chrome(options=options)

    def fetch_article_content_selenium(
        self, story: Story, driver: webdriver.Chrome
    ) -> ArticleContent | None:
        """
        Загружает страницу по URL из Story через Selenium, извлекает заголовок и все абзацы.
        """
        try:
            logger.debug("Загрузка статьи через Selenium: %s", story.url)
            driver.get(story.url)

            # Ждём заголовок
            title_selectors = [
                "h1[itemprop='headline']",
                "h1[nq1e3kzynmvmcll0='article-title']",
                "h1.content--nRkI3IdK_MQeeIlhDzHU__title-3r",
            ]
            title_elem = None
            for selector in title_selectors:
                try:
                    title_elem = WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                    if title_elem and title_elem.text.strip():
                        break
                except Exception:
                    continue

            if not title_elem or not title_elem.text.strip():
                logger.warning("Не удалось найти заголовок статьи: %s", story.url)
                return None

            title = title_elem.text.strip()

            # Ждём контейнер с текстом
            body_selectors = [
                "div[itemprop='articleBody']",
                "div[nq1e3kzynmvmcll0='article-body']",
                "div[class*='article-render__container']",
            ]
            body_container = None
            for selector in body_selectors:
                try:
                    body_container = WebDriverWait(driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                    )
                    if body_container:
                        break
                except Exception:
                    continue

            if not body_container:
                logger.warning("Не найден контейнер с текстом статьи: %s", story.url)
                return None

            # Извлекаем абзацы
            paragraphs = body_container.find_elements(By.TAG_NAME, "p")
            if not paragraphs:
                rich_divs = body_container.find_elements(
                    By.CSS_SELECTOR, "div.content--rich-text__richText-2C"
                )
                if rich_divs:
                    text_parts = []
                    for div in rich_divs:
                        spans = div.find_elements(
                            By.CSS_SELECTOR, "span.content--rich-text__text-1W"
                        )
                        for span in spans:
                            text = span.text.strip()
                            if text:
                                text_parts.append(text)
                    body_text = "\n".join(text_parts)
                else:
                    body_text = body_container.text.strip()
            else:
                body_text = "\n".join(
                    p.text.strip() for p in paragraphs if p.text.strip()
                )

            if not body_text:
                logger.warning("В статье нет текста: %s", story.url)
                return None

            full_text = f"{title}\n{body_text}"
            return ArticleContent(text=full_text, url=story.url)

        except Exception as e:
            logger.error("Ошибка при загрузке %s: %s", story.url, e)
            return None

    def fetch_all_articles(self, stories: list[Story]) -> list[ArticleContent]:
        driver = self.create_driver()
        results = []
        try:
            for story in stories:
                content = self.fetch_article_content_selenium(story, driver)
                if content:
                    results.append(content)
                time.sleep(1.5)  # задержка между запросами
        finally:
            driver.quit()
        return results

    # -------------------- Работа с GPT --------------------
    def _call_gpt_with_retry(
        self, text: str, max_retries: int = 3, delay: int = 2
    ) -> str | None:
        for attempt in range(max_retries):
            try:
                response = self.openai_client.responses.create(
                    prompt={"id": self.prompt_id},
                    input=text,
                )
                return response.output_text
            except Exception as e:
                logger.warning(
                    "Ошибка при вызове GPT (попытка %d/%d): %s",
                    attempt + 1,
                    max_retries,
                    e,
                )
                if attempt < max_retries - 1:
                    time.sleep(delay)
                else:
                    logger.error("Все попытки вызова GPT завершились ошибкой")
                    return None
        return None

    @staticmethod
    def convert_to_news_article(json_str: str, link: str = "") -> NewsArticle | None:
        try:
            data = json.loads(json_str)
            art = NewsArticle(**data)
            art.text = (
                art.text + f"<p></p><p></p><b>Источник:</b> <a href='{link}'>{link}</a>"
            )
            return art
        except (json.JSONDecodeError, ValidationError) as e:
            logger.error("Ошибка конвертации в NewsArticle: %s", e)
            logger.debug("Проблемный JSON: %s", json_str)
            return None

    def process_articles(self, articles: list[ArticleContent]) -> list[NewsArticle]:
        models: list[NewsArticle] = []
        for article in articles:
            logger.info("Обработка статьи: %s", article.url)
            gpt_response = self._call_gpt_with_retry(article.text)
            if not gpt_response:
                gpt_response = self._call_gpt_with_retry(article.text)
                if not gpt_response:
                    logger.warning(
                        "Не удалось получить ответ GPT для статьи %s", article.url
                    )
                    continue
            model = self.convert_to_news_article(gpt_response)
            if model:
                models.append(model)
            else:
                logger.warning(
                    "Не удалось преобразовать ответ GPT в NewsArticle для %s",
                    article.url,
                )
        return models

    # -------------------- Публикация --------------------
    @staticmethod
    def publish(articles: list[NewsArticle]) -> None:
        """
        Отправляет статьи на эндпоинт /api/dzen-v1/material в формате JSON.
        Использует API-ключ из переменной окружения DZEN_PUBLISH_API_KEY.
        """

        if not articles:
            logger.info("Нет статей для публикации.")
            return

        api_key = os.getenv("DZEN_PUBLISH_API_KEY")
        if not api_key:
            logger.error("Переменная окружения DZEN_PUBLISH_API_KEY не установлена")
            return

        url = os.getenv(
            "DZEN_PUBLISH_URL", "https://realnoevremya.ru/api/dzen-v1/material"
        )
        headers = {"X-Api-Key": api_key, "Content-Type": "application/json"}

        session = requests.Session()
        success_count = 0
        fail_count = 0

        for article in articles:
            payload = article.model_dump(exclude_none=True)

            for attempt in range(3):
                try:
                    resp = session.post(url, json=payload, headers=headers, timeout=10)
                    resp.raise_for_status()
                    logger.info("Статья успешно опубликована: %s", article.title)
                    success_count += 1
                    break
                except requests.exceptions.RequestException as e:
                    logger.warning(
                        "Ошибка публикации '%s' (попытка %d/3): %s",
                        article.title,
                        attempt + 1,
                        e,
                    )
                    if attempt == 2:
                        logger.error(
                            "Не удалось опубликовать статью: %s", article.title
                        )
                        fail_count += 1
                    else:
                        time.sleep(2**attempt)

            time.sleep(0.5)

        logger.info(
            "Публикация завершена. Успешно: %d, Ошибок: %d", success_count, fail_count
        )

    # -------------------- Главный рабочий процесс --------------------
    def run(self) -> None:
        logger.info("Запуск сбора новостей с Дзена")

        try:
            raw = self.fetch_news()
        except Exception as e:
            logger.error("Критическая ошибка при получении данных: %s", e)
            return

        if raw is None:
            logger.error("Не удалось получить данные. Завершение.")
            return

        parsed = self.parse_news(raw)
        if parsed is None:
            logger.error("Не удалось распарсить данные. Завершение.")
            return

        stories = parsed.data.stories
        logger.info("Всего новостей: %d", len(stories))

        minutes = 30
        now = datetime.now(timezone(timedelta(hours=3)))
        if now.hour == 9 and now.minute < 30:
            minutes = 180

        recent_stories = self.filter_recent_stories(stories, minutes=minutes)

        logger.info(
            f"Новостей за последние {minutes} минут: {len(recent_stories)}",
        )

        if not recent_stories:
            logger.info("Нет свежих новостей. Завершение.")
            return

        articles = self.fetch_all_articles(recent_stories)
        logger.info("Загружено %d статей из %d", len(articles), len(recent_stories))

        if not articles:
            logger.info("Нет загруженных статей. Завершение.")
            return

        models = self.process_articles(articles)
        logger.info("Создано %d моделей NewsArticle", len(models))

        if models:
            self.publish(models)
        else:
            logger.info("Нет моделей для публикации.")


# -------------------- Точка входа --------------------
def run_scraper():
    now = datetime.now(timezone(timedelta(hours=3)))  # UTC+3
    if now.weekday() >= 5:  # сб=5, вс=6 – пропускаем
        return
    start = dt_time(9, 0)
    end = dt_time(18, 11)
    if not (start <= now.time() < end):
        return
    scraper = DzenScraper()
    scraper.run()


def main():
    schedule.every(30).minutes.do(run_scraper)
    while True:
        schedule.run_pending()
        time.sleep(60)


if __name__ == "__main__":
    main()
