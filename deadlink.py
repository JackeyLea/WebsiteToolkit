# !/bin/python

import requests
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit
from bs4 import BeautifulSoup
import time

def normalize_url(url):
    """移除URL中的锚点部分"""
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ''))

def is_internal_link(base_url, link):
    """判断是否为同一域名下的内部链接"""
    base_domain = urlparse(base_url).netloc
    link_domain = urlparse(link).netloc
    return link_domain == base_domain

def find_dead_links(start_url):
    visited = set()          # 已访问的URL集合
    all_links = set()        # 所有发现的内链（已去重）
    dead_links = []          # 检测到的死链列表
    stack = [start_url]      # DFS栈结构
    
    # 初始化起始URL（标准化后加入集合）
    normalized_start = normalize_url(start_url)
    all_links.add(normalized_start)
    
    # 请求头模拟浏览器访问
    headers = {'User-Agent': 'Mozilla/5.0'}

    while stack:
        current_url = stack.pop()
        if current_url in visited:
            continue
        visited.add(current_url)
        print(f"Processing: {current_url}")

        try:
            # 发送HTTP请求
            response = requests.get(current_url, headers=headers, timeout=10)
            
            # 检测响应状态码
            if not response.ok:
                dead_links.append(current_url)
                print(f"发现死链: {current_url}")
                continue

            # 解析页面内容并提取链接
            soup = BeautifulSoup(response.text, 'html.parser')
            for link in soup.find_all('a', href=True):
                href = link['href']
                absolute_url = urljoin(current_url, href)
                cleaned_url = normalize_url(absolute_url)
                
                # 仅处理同一域名下的链接
                if is_internal_link(start_url, cleaned_url):
                    if cleaned_url not in all_links:
                        all_links.add(cleaned_url)
                        stack.append(cleaned_url)  # 新链接压入栈顶（DFS）

        except Exception as e:
            print(f"请求失败: {current_url} - 错误: {e}")
            dead_links.append(current_url)
        
        time.sleep(0.5)  # 请求间隔防止封禁

    return dead_links

if __name__ == "__main__":
    # 使用示例（替换为你的目标网站）
    target_url = "https://blog.jackeylea.com"
    invalid_links = find_dead_links(target_url)
    
    print("\n最终发现的死链列表:")
    for idx, link in enumerate(invalid_links, 1):
        print(f"{idx}. {link}")