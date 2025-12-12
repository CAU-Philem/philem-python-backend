import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

def mobile_to_desktop(mobile_url: str) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    }

    # 1) redirect 따라가서 최종 URL 확인
    r = requests.get(mobile_url, headers=headers, allow_redirects=True, timeout=10)

    final_url = str(r.url)
    if "/kr/buy-sell/" in final_url:
        return final_url

    # 2) redirect가 안되면 HTML에서 canonical/og:url 파싱
    soup = BeautifulSoup(r.text, "html.parser")

    canonical = soup.select_one('link[rel="canonical"]')
    if canonical and canonical.get("href"):
        href = canonical["href"].strip()
        return urljoin(final_url, href)

    og = soup.select_one('meta[property="og:url"]')
    if og and og.get("content"):
        return og["content"].strip()

    raise ValueError("Could not resolve desktop url from mobile url")

def normalize_daangn_url(url: str) -> str:
    if "daangn.com/articles/" in url:
        return mobile_to_desktop(url)
    return url  # 이미 desktop이거나 다른 형태면 그대로 (필요시 더 정규화)