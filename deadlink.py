import requests
import os
import threading
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import (
    urljoin, urlparse, urlsplit,
    urlunsplit, unquote, quote, urlunparse
)
from bs4 import BeautifulSoup
import time
import logging
from xml.etree import ElementTree

# 配置日志记录
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class AccurateLinkChecker:
    def __init__(self, start_url, max_workers=None):
        # 自动计算线程数
        cpu_count = os.cpu_count() or 1
        self.max_workers = min(32, cpu_count * 4 + 4) if max_workers is None else max_workers
        
        # 初始化参数
        self.start_url = self.normalize_url(start_url)
        self.base_domain = urlparse(self.start_url).netloc.split(':')[0]
        
        # 状态管理
        self.visited = set()
        self.all_links = set()
        self.dead_links = []  # 存储元组 (url, status_code, error)
        self.lock = threading.Lock()
        self.task_queue = Queue()
        self.task_queue.put(self.start_url)
        
        # 请求配置
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': self.start_url
        })
        self.timeout = 15
        self.retries = 2  # 重试次数

    @staticmethod
    def normalize_url(url):
        """标准化URL处理"""
        parsed = urlsplit(url)
        cleaned = urlunsplit((
            parsed.scheme,
            parsed.netloc,
            parsed.path.rstrip('/'),
            parsed.query,
            ''  # 移除片段
        ))
        return unquote(cleaned).lower()

    def encode_special_chars(self, url):
        """安全编码URL"""
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

    def is_internal_link(self, url):
        """内部链接检测"""
        link_domain = urlparse(url).netloc.split(':')[0]
        return link_domain == self.base_domain

    def extract_links(self, content, base_url, content_type):
        """多格式链接提取"""
        links = set()
        
        # XML处理
        if 'xml' in content_type:
            try:
                root = ElementTree.fromstring(content)
                for elem in root.iter():
                    if any(tag in elem.tag for tag in ['loc', 'url']):
                        if elem.text:
                            links.add(elem.text.strip())
            except Exception as e:
                logging.warning(f"XML解析失败: {str(e)}")
                soup = BeautifulSoup(content, 'xml')
                for tag in soup.find_all(['loc', 'url', 'link', 'guid']):
                    if tag.text:
                        links.add(tag.text.strip())
                    if tag.has_attr('href'):
                        links.add(tag['href'].strip())
        
        # HTML处理
        else:
            soup = BeautifulSoup(content, 'html.parser')
            for tag in soup.find_all('a', href=True):
                links.add(tag['href'])
        
        # 标准化处理
        processed = set()
        for link in links:
            try:
                absolute = urljoin(base_url, unquote(link))
                normalized = self.normalize_url(absolute)
                if self.is_internal_link(normalized):
                    processed.add(normalized)
            except Exception as e:
                logging.error(f"链接处理异常: {link} - {str(e)}")
        
        return processed

    def handle_request(self, url):
        """带重试机制的请求处理"""
        encoded_url = self.encode_special_chars(url)
        
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(
                    encoded_url,
                    timeout=self.timeout,
                    allow_redirects=True,
                    stream=True
                )
                
                # 处理重定向链
                if response.history:
                    logging.info(f"重定向路径: {url} -> {response.url}")
                
                return response, None
                
            except requests.exceptions.SSLError as e:
                logging.warning(f"SSL错误 [{url}]: {str(e)}")
                return None, ('SSL Error', str(e))
            except requests.exceptions.Timeout as e:
                if attempt == self.retries:
                    logging.warning(f"请求超时 [{url}]")
                    return None, ('Timeout', str(e))
                time.sleep(1)
            except requests.exceptions.TooManyRedirects as e:
                logging.warning(f"重定向过多 [{url}]")
                return None, ('TooManyRedirects', str(e))
            except requests.exceptions.RequestException as e:
                logging.error(f"请求异常 [{url}]: {str(e)}")
                return None, (type(e).__name__, str(e))
        
        return None, ('MaxRetriesExceeded', '')

    def worker(self):
        """工作线程逻辑"""
        while True:
            try:
                current_url = self.task_queue.get(timeout=30)
                
                with self.lock:
                    if current_url in self.visited:
                        self.task_queue.task_done()
                        continue
                    self.visited.add(current_url)
                
                logging.info(f"检测中: {unquote(current_url)}")
                
                # 发送请求
                response, error = self.handle_request(current_url)
                
                if error is not None:
                    with self.lock:
                        self.dead_links.append((
                            current_url,
                            f"{error[0]} - {error[1]}"
                        ))
                    continue
                
                # 处理有效响应
                final_url = self.normalize_url(response.url)
                if final_url != current_url:
                    with self.lock:
                        if final_url not in self.visited:
                            self.task_queue.put(final_url)
                
                # 状态码检测
                if response.status_code >= 400:
                    with self.lock:
                        self.dead_links.append((
                            current_url,
                            f"HTTP {response.status_code}"
                        ))
                    logging.warning(f"发现异常状态码 [{response.status_code}]: {unquote(current_url)}")
                    continue
                
                # 内容处理
                content_type = response.headers.get('Content-Type', '')
                try:
                    content = response.content if 'xml' in content_type else response.text
                    links = self.extract_links(
                        content,
                        final_url,
                        content_type.split(';')[0]
                    )
                except UnicodeDecodeError:
                    logging.warning(f"内容解码失败: {unquote(current_url)}")
                    links = set()
                
                # 队列更新
                with self.lock:
                    new_links = links - self.all_links
                    self.all_links.update(new_links)
                    for link in new_links:
                        self.task_queue.put(link)
                
                time.sleep(0.5)  # 流量控制
                
            except Exception as e:
                if 'empty' not in str(e):
                    logging.error(f"线程异常: {str(e)}")
                break

    def run(self):
        """运行检测器"""
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            logging.info(f"启动检测器，线程数: {self.max_workers}")
            
            # 提交初始任务
            futures = [executor.submit(self.worker) for _ in range(self.max_workers)]
            
            # 等待任务完成
            while not self.task_queue.empty():
                time.sleep(1)
            
            executor.shutdown(wait=True)
        
        return self.dead_links

if __name__ == "__main__":
    checker = AccurateLinkChecker("https://blog.jackeylea.com")
    results = checker.run()
    
    print("\n检测结果:")
    print(f"{'序号':<5} | {'URL':<60} | {'错误类型'}")
    print("-" * 90)
    for idx, (url, error) in enumerate(results, 1):
        print(f"{idx:<5} | {unquote(url):<60} | {error}")