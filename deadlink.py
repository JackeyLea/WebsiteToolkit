import requests
from urllib.parse import (
    urljoin, urlparse, urlsplit, 
    urlunsplit, unquote, quote, urlunparse
)
from bs4 import BeautifulSoup
import time
import re
from xml.etree import ElementTree

def normalize_url(url):
    """标准化URL：移除锚点并解码UTF8字符"""
    parsed = urlsplit(url)
    cleaned = urlunsplit((
        parsed.scheme,
        parsed.netloc,
        parsed.path.rstrip('/'),  # 统一去除末尾斜杠
        parsed.query,
        ''  # 移除片段
    ))
    return unquote(cleaned).lower()  # 统一小写处理

def encode_special_chars(url):
    """编码特殊字符为URL安全格式"""
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

def is_internal_link(base_url, link):
    """判断是否为同一域名下的内部链接"""
    base_domain = urlparse(base_url).netloc.split(':')[0]
    link_domain = urlparse(link).netloc.split(':')[0]
    return link_domain == base_domain

def extract_xml_links(content, base_url):
    """从XML内容中提取链接（支持多种格式）"""
    links = set()
    
    try:
        # 尝试解析为sitemap格式
        root = ElementTree.fromstring(content)
        for elem in root.iter():
            if 'loc' in elem.tag:
                links.add(elem.text.strip())
            elif 'link' in elem.tag and 'href' in elem.attrib:
                links.add(elem.attrib['href'].strip())
                
    except ElementTree.ParseError:
        # 尝试解析为RSS格式
        soup = BeautifulSoup(content, 'xml')
        for link in soup.find_all(['loc', 'link', 'guid', 'url']):
            if link.text:
                links.add(link.text.strip())
            if 'href' in link.attrs:
                links.add(link.attrs['href'].strip())
    
    # 转换相对链接为绝对链接
    processed = set()
    for link in links:
        if re.match(r'^https?://', link, re.I):
            abs_url = link
        else:
            abs_url = urljoin(base_url, link)
        processed.add(normalize_url(abs_url))
    
    return processed

def find_dead_links(start_url):
    visited = set()          # 已访问的URL集合
    all_links = set()        # 所有发现的内链（已标准化）
    dead_links = []          # 检测到的死链列表
    stack = [normalize_url(start_url)]  # 初始化处理队列
    
    headers = {
        'User-Agent': 'Mozilla/5.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
    }

    while stack:
        current_url = stack.pop()
        if current_url in visited:
            continue
        visited.add(current_url)
        print(f"正在检测: {unquote(current_url)}")

        try:
            encoded_url = encode_special_chars(current_url)
            response = requests.get(
                encoded_url, 
                headers=headers, 
                timeout=10,
                allow_redirects=True,
                stream=True  # 流式传输提高大文件处理效率
            )
            response.raise_for_status()
            
            # 记录最终请求URL（处理重定向后）
            final_url = normalize_url(response.url)
            if final_url != current_url:
                visited.add(final_url)

            # 状态码检测
            if not response.ok:
                dead_links.append(current_url)
                print(f"发现死链: {unquote(current_url)}")
                continue

            # 内容类型检测
            content_type = response.headers.get('Content-Type', '').split(';')[0]
            is_xml = content_type in ['application/xml', 'text/xml', 'application/rss+xml']
            
            # 解析内容提取链接
            new_links = set()
            if is_xml:
                xml_links = extract_xml_links(response.content, final_url)
                new_links.update(xml_links)
            else:
                soup = BeautifulSoup(response.text, 'html.parser')
                for link in soup.find_all('a', href=True):
                    href = unquote(link['href'])
                    absolute = urljoin(final_url, href)
                    normalized = normalize_url(absolute)
                    new_links.add(normalized)

            # 过滤并添加新链接
            for link in new_links:
                if is_internal_link(start_url, link):
                    if link not in all_links:
                        all_links.add(link)
                        stack.append(link)

        except requests.RequestException as e:
            print(f"请求失败: {unquote(current_url)} - {str(e)}")
            dead_links.append(current_url)
        except Exception as e:
            print(f"处理异常: {unquote(current_url)} - {str(e)}")
        
        time.sleep(0.5)

    return dead_links

if __name__ == "__main__":
    target_url = "https://blog.jackeylea.com"
    invalid_links = find_dead_links(target_url)
    
    print("\n检测完成，发现以下死链：")
    for idx, link in enumerate(invalid_links, 1):
        print(f"{idx}. {unquote(link)}")