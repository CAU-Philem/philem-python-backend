import time
import re
from datetime import datetime
from urllib.parse import urlparse
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from dateutil import parser as date_parser
from sqlalchemy.orm import Session


# 분리한 DB 모듈 임포트
from ingestion import insert_single
from core.locations import SEOUL_LOCATIONS_DATA


def create_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--lang=ko-KR")
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    return driver

def find_region_id(region_name):
    if not region_name: return None
    clean_name = region_name.strip()
    if clean_name in SEOUL_LOCATIONS_DATA:
        return SEOUL_LOCATIONS_DATA[clean_name][0]
    for key, ids in SEOUL_LOCATIONS_DATA.items():
        if clean_name in key or key in clean_name:
            return ids[0]
    return None

def extract_url_info(url):
    try:
        parsed = urlparse(url)
        return parsed.path
    except:
        return None

def clean_price(price_text):
    if not price_text: return 0
    if "나눔" in price_text or "없음" in price_text: return 0
    nums = re.sub(r'[^0-9]', '', price_text)
    return int(nums) if nums else 0

def scrape_data_from_dom(driver, target_url):
    try:
        wait = WebDriverWait(driver, 5)
        
        # 1. [시간] <time> 태그
        created_at = None
        boosted_at = None
        try:
            time_elem = wait.until(EC.presence_of_element_located((By.XPATH, "//time[@datetime]")))
            datetime_str = time_elem.get_attribute("datetime")
            time_text = time_elem.text.strip() 

            if datetime_str:
                dt_obj = date_parser.parse(datetime_str)
                formatted_time = dt_obj.strftime('%Y-%m-%d %H:%M:%S')

                if "끌올" in time_text:
                    boosted_at = formatted_time
                    created_at = formatted_time 
                else:
                    created_at = formatted_time
                    boosted_at = None
        except:
            pass

        # 2. [제목] 및 [상태] (여기가 핵심 수정 파트!)
        title = ""
        status = "Active"
        
        try:
            # 제목 태그(h1)를 먼저 찾습니다.
            h1_elem = driver.find_element(By.TAG_NAME, "h1")
            title = h1_elem.text.strip()
            
            # [핵심] 제목(h1)의 바로 위 부모 컨테이너를 찾습니다.
            # 상태 뱃지(span)는 제목과 같은 부모 아래에 형제(sibling)로 존재하기 때문입니다.
            parent_elem = h1_elem.find_element(By.XPATH, "..")
            
            # 부모 컨테이너 안의 텍스트만 가져와서 검사합니다.
            # 이렇게 하면 하단 '추천 상품' 영역은 검사하지 않게 됩니다.
            header_text = parent_elem.text 
            
            if "판매완료" in header_text or "거래완료" in header_text:
                status = "SoldOut"
            elif "예약중" in header_text:
                status = "Reserved"
                
        except:
            # h1을 못 찾은 경우
            pass

        # 3. [가격] h3 태그
        try:
            h3s = driver.find_elements(By.TAG_NAME, "h3")
            price = 0
            for h3 in h3s:
                txt = h3.text.strip()
                if txt and (re.search(r'\d', txt) or "나눔" in txt):
                    price = clean_price(txt)
                    break
        except:
            price = 0

        # 4. [본문] p 태그
        try:
            ps = driver.find_elements(By.TAG_NAME, "p")
            description = ""
            max_len = 0
            for p in ps:
                txt = p.text.strip()
                if len(txt) > max_len and len(txt) > 5 and "매물 지도" not in txt:
                    max_len = len(txt)
                    description = txt
        except:
            description = ""

        # 5. [지역] <a> 태그 href에 '?in=' 포함
        region_name = None
        try:
            region_elem = driver.find_element(By.CSS_SELECTOR, "a[href*='?in=']")
            region_name = region_elem.text.strip()
            print(f"📍 지역명 추출 성공: {region_name}")
        except:
            try:
                region_elem = driver.find_element(By.CSS_SELECTOR, "a[href*='&in=']")
                region_name = region_elem.text.strip()
            except:
                pass

        # 6. [썸네일] src에 '/origin/article/' 포함
        thumbnail_url = None
        try:
            imgs = driver.find_elements(By.TAG_NAME, "img")
            for img in imgs:
                src = img.get_attribute("src")
                if src and "/origin/article/" in src:
                    thumbnail_url = src
                    print(f"🖼️ 썸네일 발견: {thumbnail_url}")
                    break
        except:
            pass

        return {
            "title": title,
            "price": price,
            "description": description,
            "thumbnail_url": thumbnail_url,
            "status": status,
            "region_name": region_name,
            "created_at": created_at, 
            "boosted_at": boosted_at
        }

    except Exception as e:
        print(f"❌ DOM 파싱 에러: {e}")
        return None

def process_single_url(db: Session, target_url: str) -> str:
    db_id = extract_url_info(target_url)
    if not db_id:
        print("❌ 유효한 URL이 아닙니다.")
        return

    print(f"🔎 [분석 시작] (Scoped Status Check)")
    print(f"   - Target ID: {db_id}")

    driver = create_driver()
    try:
        driver.get(target_url)
        time.sleep(2.0)
        
        scraped_data = scrape_data_from_dom(driver, target_url)
        
        if not scraped_data:
            print("❌ 데이터를 찾지 못했습니다.")
            return

        region_id = find_region_id(scraped_data['region_name'])
        
        mapped_data = {
            'id': db_id,
            'title': scraped_data['title'],
            'price': scraped_data['price'],
            'thumbnail_url': scraped_data['thumbnail_url'],
            'post_url': target_url, 
            'status': scraped_data['status'],
            'description': scraped_data['description'],
            'created_at': scraped_data['created_at'],
            'boosted_at': scraped_data['boosted_at'], 
            'region_id': region_id 
        }

        # ✅ db 세션 넘겨서 같은 트랜잭션으로 insert
        ok = insert_single.insert_single_article(db, mapped_data)
        if not ok:
            raise RuntimeError("❌ 저장 실패")

        # ✅ commit은 여기서 (insert_single 내부에서 commit 제거했을 때)
        db.commit()

        print("✅ 저장 완료!")
        print(f"   - 상태: {mapped_data['status']}")
        print(f"   - 지역: {scraped_data['region_name']} -> {region_id}")

        return db_id

    except Exception as e:
        # ✅ 실패 시 rollback (같은 세션이니까)
        db.rollback()
        print(f"❌ 에러: {e}")
        raise
    finally:
        driver.quit()

if __name__ == "__main__":
    input_url = input("🔗 당근마켓 URL 입력: ").strip()
    if input_url:
        process_single_url(input_url)