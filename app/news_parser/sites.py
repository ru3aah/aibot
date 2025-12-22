from abc import ABC

import requests
from bs4 import BeautifulSoup


class SiteParser(ABC):
    def __init__(self, url: str, article_path: str = ''):
        self.base_url = url
        self.article_path = article_path

    def parse(self) :
        raise NotImplementedError

class HabrParser(SiteParser):
    def __init__(self):
        super().__init__('https://habr.com/', 'ru/articles/')

    def parse(self):
        response = requests.get(self.base_url+self.article_path,
                                headers={'User-Agent':
                                        'Mozilla/5.0 ('
                                                       'Macintosh; Intel Mac '
                                                       'OS X 10_15_7) '
                                                       'AppleWebKit/537.36 ('
                                                       'KHTML, like Gecko) '
                                                       'Chrome/143.0.0.0 '
                                                       'Safari/537.36'
                                         }
                                )
        soup = BeautifulSoup(response.text, 'html.parser')
        articles = soup.find('div',
                             class_='tm-articles-list').find_all('article')
        for article in articles:
            title = article.find('h2').a.span.text
            url = article.get('id')
            print(title)
            print(self.base_url + self.article_path + url)


HabrParser().parse()