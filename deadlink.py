import requests
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import threading
import difflib

class LinkChecker:
    def __init__(self, start_url, max_workers=4):
        self.base_url = self.normalize_url(start_url)
        self.base_domain = urlparse(start_url).netloc
        self.homepage_features = self.get_homepage_features()
        self.lock = threading.Lock()
        self.visited = set()
        self.dead_links = []
        self.task_queue = deque([self.base_url])
        self.max_workers = max_workers
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept-Language': 'en-US,en;q=0.9'
        })

    def normalize_url(self, url):
        """标准化URL格式"""
        parsed = urlparse(url)
        return parsed._replace(
            path=parsed.path.rstrip('/'),
            query='',
            fragment=''
        ).geturl().lower()

    def get_homepage_features(self):
        """获取首页特征用于相似度比对"""
        try:
            resp = self.session.get(self.base_url, timeout=10)
            return self.extract_features(resp.text)
        except Exception as e:
            print(f"首页特征获取失败: {str(e)}")
            return []

    def extract_features(self, text):
        """提取页面特征"""
        clean_text = text.replace('\n', ' ').strip()[:2000]
        return [
            len(clean_text),          # 文本长度
            clean_text.count(' '),    # 空格数量
            clean_text.count('<div'), # 结构特征
            clean_text.count('href=') # 链接数量
        ]

    def is_similar_to_homepage(self, content):
        """改进的相似度检测算法"""
        if not self.homepage_features:
            return False
            
        current_features = self.extract_features(content)
        similarity = difflib.SequenceMatcher(
            None, 
            str(self.homepage_features),
            str(current_features)
        ).ratio()
        return similarity > 0.8

    def check_redirect_chain(self, response):
        """分析重定向链有效性"""
        # 直接访问错误
        if response.status_code >= 400:
            return True
            
        # 重定向到首页且内容相似
        if response.url == self.base_url:
            return self.is_similar_to_homepage(response.text)
            
        # 检查重定向历史
        for resp in response.history:
            if resp.status_code >= 400:
                return True
                
        return False

    def process_link(self, url):
        """处理单个链接"""
        try:
            print(f"\n[处理] {url}")
            
            # 发送请求（禁用自动重定向以手动跟踪）
            resp = self.session.get(url, allow_redirects=True, timeout=15)
            
            # 检测异常链接
            if self.check_redirect_chain(resp):
                with self.lock:
                    self.dead_links.append({
                        'url': url,
                        'status': resp.status_code,
                        'final_url': resp.url,
                        'history': [r.status_code for r in resp.history]
                    })
                    print(f"!! 发现异常链接: {url}")
            
            # 提取页面链接
            soup = BeautifulSoup(resp.text, 'lxml')
            for link in soup.find_all('a', href=True):
                absolute_url = urljoin(url, link['href'])
                parsed_url = urlparse(absolute_url)
                
                # 域名校验和标准化
                if parsed_url.netloc == self.base_domain:
                    normalized = self.normalize_url(absolute_url)
                    
                    with self.lock:
                        if normalized not in self.visited:
                            self.visited.add(normalized)
                            self.task_queue.append(normalized)
                            print(f"发现新链接: {normalized}")

        except requests.exceptions.RequestException as e:
            print(f"请求错误: {url} - {str(e)}")
            with self.lock:
                self.dead_links.append({
                    'url': url,
                    'error': str(e)
                })
        except Exception as e:
            print(f"处理异常: {url} - {str(e)}")

    def run(self):
        """启动检测任务"""
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while True:
                batch = []
                
                # 获取任务批次
                with self.lock:
                    if not self.task_queue:
                        if threading.active_count() <= 1:
                            break
                        continue
                        
                    for _ in range(min(10, len(self.task_queue))):
                        batch.append(self.task_queue.popleft())
                
                # 提交任务
                futures = [executor.submit(self.process_link, url) for url in batch]
                
                # 等待完成
                for future in futures:
                    future.result()

        return self.dead_links

if __name__ == "__main__":
    # 使用示例
    checker = LinkChecker("https://blog.jackeylea.com")
    results = checker.run()
    
    print("\n检测结果：")
    for item in results:
        if 'error' in item:
            print(f"[错误] {item['url']} - {item['error']}")
        else:
            print(f"[{item['status']}] {item['url']}")
            print(f"    最终地址: {item['final_url']}")
            print(f"    重定向链: {item['history']}")