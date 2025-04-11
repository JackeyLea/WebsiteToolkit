import requests
from urllib.parse import (
    urljoin, urlparse, urlsplit, 
    urlunsplit, unquote, quote, urlunparse
)
from bs4 import BeautifulSoup
import time
import re
from xml.etree import ElementTree
import threading
from queue import Queue
from concurrent.futures import ThreadPoolExecutor

class DeadLinkChecker:
    def __init__(self, start_url, max_workers=5):
        self.start_url = self.normalize_url(start_url)
        self.max_workers = max_workers
        self.lock = threading.Lock()
        
        # 共享状态
        self.visited = set()
        self.all_links = set()
        self.dead_links = []
        
        # 任务队列
        self.task_queue = Queue()
        self.task_queue.put(self.start_url)
        
        # 域名限制
        self.base_domain = urlparse(self.start_url).netloc.split(':')[0]
        
        # 请求配置
        self.headers = {
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }

    @staticmethod
    def normalize_url(url):
        """标准化URL并转为小写"""
        parsed = urlsplit(url)
        cleaned = urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip('/'),
            parsed.query,
            ''
        ))
        return unquote(cleaned).lower()

    def encode_special_chars(self, url):
        """编码特殊字符"""
        parsed = urlparse(url)
        encoded_path = '/'.join(
            [quote(p, safe='') for p in parsed.path.split('/')]
        )
        encoded_query = quote(parsed.query, safe='=&')
        return urlunparse((
            parsed.scheme,
            parsed.netloc,
            encoded_path,
            parsed.params,
            encoded_query,
            parsed.fragment
        ))

    def is_internal_link(self, link):
        """判断是否为内部链接"""
        link_domain = urlparse(link).netloc.split(':')[0]
        return link_domain == self.base_domain

    def extract_links(self, content, base_url, content_type):
        """从内容中提取链接"""
        links = set()
        
        if content_type in ['application/xml', 'text/xml', 'application/rss+xml']:
            try:
                root = ElementTree.fromstring(content)
                for elem in root.iter():
                    if 'loc' in elem.tag and elem.text:
                        links.add(elem.text.strip())
                    elif 'link' in elem.tag and 'href' in elem.attrib:
                        links.add(elem.attrib['href'].strip())
            except ElementTree.ParseError:
                soup = BeautifulSoup(content, 'xml')
                for tag in soup.find_all(['loc', 'link', 'guid', 'url']):
                    if tag.text:
                        links.add(tag.text.strip())
                    if 'href' in tag.attrs:
                        links.add(tag.attrs['href'].strip())
        else:
            soup = BeautifulSoup(content, 'html.parser')
            for link in soup.find_all('a', href=True):
                links.add(link['href'])

        # 转换为绝对URL并标准化
        normalized_links = set()
        for link in links:
            try:
                decoded = unquote(link)
                absolute = urljoin(base_url, decoded)
                normalized = self.normalize_url(absolute)
                if self.is_internal_link(normalized):
                    normalized_links.add(normalized)
            except Exception as e:
                print(f"链接处理失败: {link} - {str(e)}")
        
        return normalized_links

    def process_url(self):
        """工作线程处理函数"""
        while True:
            try:
                current_url = self.task_queue.get(timeout=30)
                
                # 检查是否已处理
                with self.lock:
                    if current_url in self.visited:
                        self.task_queue.task_done()
                        continue
                    self.visited.add(current_url)
                
                print(f"正在检测: {unquote(current_url)}")
                
                try:
                    encoded_url = self.encode_special_chars(current_url)
                    response = requests.get(
                        encoded_url,
                        headers=self.headers,
                        timeout=15,
                        allow_redirects=True,
                        stream=True
                    )
                    response.raise_for_status()
                    
                    # 处理重定向后的最终URL
                    final_url = self.normalize_url(response.url)
                    if final_url != current_url:
                        with self.lock:
                            if final_url not in self.visited:
                                self.task_queue.put(final_url)
                    
                    # 提取内容中的链接
                    content_type = response.headers.get('Content-Type', '').split(';')[0]
                    links = self.extract_links(
                        response.content if 'xml' in content_type else response.text,
                        final_url,
                        content_type
                    )
                    
                    # 添加新链接到队列
                    with self.lock:
                        new_links = links - self.all_links
                        self.all_links.update(new_links)
                        for link in new_links:
                            self.task_queue.put(link)
                
                except requests.RequestException as e:
                    with self.lock:
                        self.dead_links.append(current_url)
                    print(f"发现死链: {unquote(current_url)} - {str(e)}")
                
                except Exception as e:
                    print(f"处理异常: {unquote(current_url)} - {str(e)}")
                
                time.sleep(0.3)  # 控制请求速率
                
            except Exception as e:
                print(f"队列获取异常: {str(e)}")
                break

    def run(self):
        """启动检测任务"""
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            for _ in range(self.max_workers):
                executor.submit(self.process_url)
            
            # 等待所有任务完成
            while not self.task_queue.empty():
                time.sleep(1)
            
            executor.shutdown(wait=True)
        
        return self.dead_links

if __name__ == "__main__":
    checker = DeadLinkChecker(
        start_url="https://blog.jackeylea.com",
        max_workers=8
    )
    invalid_links = checker.run()
    
    print("\n检测完成，发现以下死链：")
    for idx, link in enumerate(invalid_links, 1):
        print(f"{idx}. {unquote(link)}")