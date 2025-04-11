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
import re
from xml.etree import ElementTree

class AdaptiveDeadLinkChecker:
    def __init__(self, start_url, max_workers=None):
        # 自动计算线程池大小
        if max_workers is None:
            cpu_count = os.cpu_count() or 1  # 处理None的情况
            max_workers = min(32, cpu_count * 4 + 4)  # 标准动态算法
        self.max_workers = max_workers
        
        # 初始化核心参数
        self.start_url = self.normalize_url(start_url)
        self.base_domain = urlparse(self.start_url).netloc.split(':')[0]
        
        # 线程安全数据结构
        self.visited = set()
        self.all_links = set()
        self.dead_links = []
        self.lock = threading.Lock()
        self.task_queue = Queue()
        self.task_queue.put(self.start_url)
        
        # 请求配置
        self.headers = {
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        }

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
        
        # XML处理（Sitemap/RSS）
        if 'xml' in content_type:
            try:
                root = ElementTree.fromstring(content)
                for elem in root.iter():
                    if any(tag in elem.tag for tag in ['loc', 'url']):
                        if elem.text:
                            links.add(elem.text.strip())
            except Exception:
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
        
        # 链接标准化
        processed = set()
        for link in links:
            try:
                absolute = urljoin(base_url, unquote(link))
                normalized = self.normalize_url(absolute)
                if self.is_internal_link(normalized):
                    processed.add(normalized)
            except Exception as e:
                print(f"链接处理异常: {link} - {e}")
        
        return processed

    def worker(self):
        """自适应工作线程"""
        while True:
            try:
                current_url = self.task_queue.get(timeout=30)
                
                # 状态检查
                with self.lock:
                    if current_url in self.visited:
                        self.task_queue.task_done()
                        continue
                    self.visited.add(current_url)
                
                print(f"检测中: {unquote(current_url)}")
                
                try:
                    # 请求处理
                    encoded_url = self.encode_special_chars(current_url)
                    response = requests.get(
                        encoded_url,
                        headers=self.headers,
                        timeout=15,
                        allow_redirects=True,
                        stream=True
                    )
                    
                    # 处理重定向
                    final_url = self.normalize_url(response.url)
                    if final_url != current_url:
                        with self.lock:
                            if final_url not in self.visited:
                                self.task_queue.put(final_url)
                    
                    # 死链检测
                    if response.status_code >= 400:
                        with self.lock:
                            self.dead_links.append(current_url)
                        print(f"发现死链 [{response.status_code}]: {unquote(current_url)}")
                        continue
                    
                    # 内容解析
                    content_type = response.headers.get('Content-Type', '')
                    links = self.extract_links(
                        response.content if 'xml' in content_type else response.text,
                        final_url,
                        content_type.split(';')[0]
                    )
                    
                    # 队列更新
                    with self.lock:
                        new_links = links - self.all_links
                        self.all_links.update(new_links)
                        for link in new_links:
                            self.task_queue.put(link)
                
                except requests.RequestException as e:
                    with self.lock:
                        self.dead_links.append(current_url)
                    print(f"请求失败: {unquote(current_url)} - {e}")
                
                time.sleep(0.3)  # 流量控制
            
            except Exception as e:
                if 'empty' not in str(e):
                    print(f"线程异常: {e}")
                break

    def dynamic_scaling(self):
        """动态负载均衡（示例实现）"""
        # 可根据队列长度动态调整线程数
        # 此处为示例，实际生产环境需要更复杂逻辑
        qsize = self.task_queue.qsize()
        if qsize > 100 and self.max_workers < 32:
            self.max_workers = min(32, self.max_workers + 2)
        elif qsize < 20 and self.max_workers > 2:
            self.max_workers = max(2, self.max_workers - 1)

    def run(self):
        """启动检测系统"""
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            print(f"启动线程池，工作线程数: {self.max_workers}")
            
            # 初始任务分配
            futures = [executor.submit(self.worker) for _ in range(self.max_workers)]
            
            # 动态负载监控
            while not self.task_queue.empty():
                self.dynamic_scaling()  # 动态调整
                time.sleep(5)
            
            # 等待任务完成
            while any(not f.done() for f in futures):
                time.sleep(1)
        
        return self.dead_links

if __name__ == "__main__":
    # 自动配置示例
    checker = AdaptiveDeadLinkChecker("https://blog.jackeylea.com")
    results = checker.run()
    
    print("\n检测完成，发现死链：")
    for idx, link in enumerate(results, 1):
        print(f"{idx}. {unquote(link)}")