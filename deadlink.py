import requests
from urllib.parse import (
    urljoin, urlparse, urlsplit, 
    urlunsplit, unquote, quote, urlunparse
)
from bs4 import BeautifulSoup
import time

def normalize_url(url):
    """标准化URL：移除锚点并解码UTF8字符"""
    parsed = urlsplit(url)
    # 移除锚点部分并解码字符
    cleaned = urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.query,
        ''  # 移除片段标识
    ))
    return unquote(cleaned)

def encode_special_chars(url):
    """编码特殊字符为URL安全格式"""
    parsed = urlparse(url)
    # 分段编码路径部分
    encoded_path = '/'.join(
        [quote(p, safe='') for p in parsed.path.split('/')]
    )
    # 保留查询参数中的=和&符号
    encoded_query = quote(parsed.query, safe='=&')
    
    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        encoded_path,
        parsed.params,
        encoded_query,
        parsed.fragment
    ))

def is_internal_link(base_url, link):
    """判断是否为同一域名下的内部链接"""
    base_domain = urlparse(base_url).netloc
    link_domain = urlparse(link).netloc
    return link_domain == base_domain

def find_dead_links(start_url):
    visited = set()          # 已访问的URL集合
    all_links = set()        # 所有发现的内链（已标准化）
    dead_links = []          # 检测到的死链列表
    stack = [start_url]      # DFS栈结构
    
    # 请求头模拟浏览器访问
    headers = {'User-Agent': 'Mozilla/5.0'}

    while stack:
        current_url = stack.pop()
        if current_url in visited:
            continue
        visited.add(current_url)
        print(f"正在处理: {unquote(current_url)}")  # 显示解码后的URL

        try:
            # 编码特殊字符确保有效请求
            encoded_url = encode_special_chars(current_url)
            
            # 发送HTTP请求
            response = requests.get(
                encoded_url, 
                headers=headers, 
                timeout=10,
                allow_redirects=True
            )
            
            # 检测响应状态码
            if not response.ok:
                dead_links.append(current_url)
                print(f"发现死链: {unquote(current_url)}")
                continue

            # 解析页面内容并提取链接
            soup = BeautifulSoup(response.text, 'html.parser')
            for link in soup.find_all('a', href=True):
                href = link['href']
                
                # 处理相对路径并解码中文
                decoded_href = unquote(href)
                absolute_url = urljoin(encoded_url, decoded_href)
                cleaned_url = normalize_url(absolute_url)
                
                # 仅处理同一域名下的链接
                if is_internal_link(start_url, cleaned_url):
                    if cleaned_url not in all_links:
                        all_links.add(cleaned_url)
                        stack.append(cleaned_url)

        except Exception as e:
            print(f"请求失败: {unquote(current_url)} - 错误: {e}")
            dead_links.append(current_url)
        
        time.sleep(0.5)  # 请求间隔防止封禁

    return dead_links

if __name__ == "__main__":
    # 使用示例（替换为你的目标网站）
    target_url = "https://blog.jackeylea.com"
    invalid_links = find_dead_links(target_url)
    
    print("\n最终发现的死链列表:")
    for idx, link in enumerate(invalid_links, 1):
        # 显示解码后的中文URL
        decoded_link = unquote(link)
        print(f"{idx}. {decoded_link}")