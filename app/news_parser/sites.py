from abc import ABC
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import logging

class Article:
    def __init__(self, source, type, title, summary, published_at, url):
        self.source = source
        self.type = type
        self.title = title
        self.summary = summary
        self.published_at = published_at
        self.url = url

class SiteParser(ABC):
    def __init__(self, url: str, article_path: str = ''):
        self.base_url = url
        self.article_path = article_path
    
    def parse(self):
        raise NotImplementedError
    
    def normalize_url(self, url: str):
        raise NotImplementedError

class HabrParser(SiteParser):
    USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) '
                  'Chrome/143.0.0.0 Safari/537.36')
    
    def __init__(self):
        super().__init__('https://habr.com/', 'ru/articles/')
        self.source = 'habr'
        self.type = 'site'
    
    def normalize_url(self, url: str):
        return self.base_url + self.article_path + url
    
    def parse(self):
        try:
            response = requests.get(
                self.base_url + self.article_path,
                headers={'User-Agent': self.USER_AGENT},
                timeout=10
            )
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            articles_container = soup.find('div', class_='tm-articles-list')
            
            if not articles_container:
                logging.warning("Articles container not found")
                return []
                
            articles = articles_container.find_all('article')
            res = []
            
            for article in articles:
                try:
                    # Safe element extraction
                    h2_tag = article.find('h2')
                    if not h2_tag or not h2_tag.a or not h2_tag.a.span:
                        continue
                        
                    title = h2_tag.a.span.text
                    article_id = article.get('id')
                    if not article_id:
                        continue
                        
                    url = self.normalize_url(article_id)
                    
                    time_tag = article.find('time')
                    if not time_tag:
                        continue
                        
                    dt = datetime.fromisoformat(time_tag.get('datetime'))
                    
                    body_div = article.find('div', class_='article-formatted-body')
                    summary = body_div.text if body_div else ""
                    
                    res.append(Article(
                        source=self.source,
                        type=self.type,
                        title=title,
                        summary=summary,
                        published_at=dt,
                        url=url
                    ))
                    
                except Exception as e:
                    logging.warning(f"Error parsing article: {e}")
                    continue
                    
            return res
            
        except requests.RequestException as e:
            logging.error(f"Network error: {e}")
            return []
        except Exception as e:
            logging.error(f"Parsing error: {e}")
            return []

if __name__ == "__main__":
    parser = HabrParser()
    articles = parser.parse()
    for article in articles:
        print(f"Title: {article.title}")
        print(f"URL: {article.url}")
        print(f"Published at: {article.published_at}")
        print(f"Summary: {article.summary}")
        print(f"Source: {article.source}")
        print("---")