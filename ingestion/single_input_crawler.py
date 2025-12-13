import json
import time
import re
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# 분리한 DB 모듈 임포트
from ingestion import insert_single
from core.locations import SEOUL_LOCATIONS_DATA

# --- [설정] 지역 ID 매핑 데이터 (insert_articles.py에서 가져옴) ---

def create_driver():
    """기존 capstone_crawl.py와 동일한 드라이버 설정"""
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--lang=ko-KR")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': '''
            Object.defineProperty(navigator, 'webdriver', {
              get: () => undefined
            })
        '''
    })
    return driver

def find_region_id(region_name):
    """
    크롤링된 지역명(예: '역삼1동')으로 ID를 찾습니다.
    정확히 일치하지 않으면 포함된 단어로 검색합니다.
    """
    if not region_name: return None

    # 1. 정확히 일치하는 경우
    if region_name in SEOUL_LOCATIONS_DATA:
        return SEOUL_LOCATIONS_DATA[region_name][0]
    
    # 2. 키워드 매칭 (예: '역삼동' -> '역삼1동' ID 반환 등)
    for key, ids in SEOUL_LOCATIONS_DATA.items():
        if region_name in key or key in region_name:
            return ids[0]
            
    return None # 매핑 실패시

def extract_single_article_data(html, target_id):
    """
    HTML 내의 script 태그들을 뒤져서 target_id를 가진 article 객체를 찾습니다.
    """
    soup = BeautifulSoup(html, 'html.parser')
    scripts = soup.find_all('script')
    
    for script in scripts:
        txt = (script.string or script.text or "").strip()
        if not txt: continue
        
        # JSON 형태인지 대략 확인
        if '{' in txt and '}' in txt:
            try:
                # 스크립트 태그 내에 바로 JSON이 있는 경우도 있고, 변수 할당식일 수도 있음
                # 가장 확실한 방법: id가 포함된 JSON 블록 찾기
                start_idx = txt.find('{')
                end_idx = txt.rfind('}')
                json_candidate = txt[start_idx:end_idx+1]
                
                data = json.loads(json_candidate)
                
                # 재귀적으로 JSON 트리를 탐색하여 해당 ID를 가진 article 객체 찾기
                found_article = find_article_in_json(data, target_id)
                if found_article:
                    return found_article
            except:
                continue
    return None

def find_article_in_json(data, target_id):
    """JSON 객체(딕셔너리/리스트) 내부를 순회하며 ID가 일치하는 게시글 객체 리턴"""
    if isinstance(data, dict):
        # 현재 딕셔너리가 우리가 찾는 article인지 확인
        # (id가 일치하고 title이 있어야 함)
        curr_id = str(data.get('id', ''))
        curr_article_id = str(data.get('articleId', ''))
        
        if (curr_id == str(target_id) or curr_article_id == str(target_id)) and 'title' in data:
            return data
        
        for k, v in data.items():
            res = find_article_in_json(v, target_id)
            if res: return res
            
    elif isinstance(data, list):
        for item in data:
            res = find_article_in_json(item, target_id)
            if res: return res
            
    return None

def process_single_url(target_url: str) -> int:
    # 1. URL에서 Article ID 추출
    # 예: https://www.daangn.com/articles/12345678 -> 12345678
    path = urlparse(target_url).path
    match = re.search(r'/articles/(\d+)', path)
    if not match:
        raise ValueError("❌ 유효한 당근마켓 게시글 URL이 아닙니다.")

    article_id = match.group(1)
    print(f"🔎 Article ID 추출: {article_id}")

    # 2. Selenium으로 페이지 로드
    driver = create_driver()
    try:
        print(f"🌐 페이지 접속 중: {target_url}")
        driver.get(target_url)
        time.sleep(2) # 페이지 로딩 대기
        
        html = driver.page_source
        
        # 3. 데이터 추출
        raw_data = extract_single_article_data(html, article_id)
        
        if not raw_data:
            raise RuntimeError("❌ 페이지에서 게시글 정보를 찾을 수 없습니다. (품절/삭제 또는 HTML 구조 변경)")
            # 봇 차단 가능성도 있으므로 간단한 디버깅용 파일 저장
            # with open("debug_fail.html", "w", encoding="utf-8") as f: f.write(html)
            
        # 4. DB 스키마에 맞게 데이터 매핑
        # 당근 상세페이지 JSON 구조는 리스트와 다를 수 있으므로 안전하게 get 사용
        region_dict = raw_data.get('region', {})
        region_name = region_dict.get('name') if isinstance(region_dict, dict) else None
        
        # 지역 ID 찾기
        region_id = find_region_id(region_name)
        
        # 사진 URL 처리 (단일 혹은 리스트)
        photos = raw_data.get('photos', [])
        thumbnail_url = photos[0].get('url') if photos and isinstance(photos, list) else raw_data.get('coverImage')

        mapped_data = {
            'id': raw_data.get('id') or raw_data.get('articleId'),
            'title': raw_data.get('title'),
            'price': raw_data.get('price'),
            'thumbnail_url': thumbnail_url,
            'post_url': target_url, # 입력받은 URL 사용
            'status': raw_data.get('status'),
            'description': raw_data.get('description'),
            'created_at': raw_data.get('articleCreatedAt') or raw_data.get('createdAt'),
            'boosted_at': raw_data.get('boostedAt'), # 상세페이지엔 없을 수 있음
            'region_id': region_id
        }

        # 5. DB 저장 모듈 호출
        insert_single.insert_single_article(mapped_data)

        return int(mapped_data["id"])
    except Exception as e:
        print(f"❌ 처리 중 치명적 오류 발생: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    # 사용자 입력 받기
    input_url = input("🔗 당근마켓 게시글 URL을 입력하세요: ").strip()
    if input_url:
        process_single_url(input_url)