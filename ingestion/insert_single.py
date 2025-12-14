#insert_single.py

import mysql.connector
from dateutil import parser
import re
from sqlalchemy.orm import Session
from sqlalchemy import text
from config.settings import settings

# --- [설정] DB 접속 정보 (기존 insert_articles.py 참조) ---

DB_CONFIG = {
    'host': settings.db_host,
    'user': settings.db_user,
    'password': settings.db_password,  # 보안을 위해 환경변수 사용을 권장합니다.
    'database': settings.db_name,
    'port': settings.db_port
}

def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)

def clean_price(price_input):
    """가격 정보를 정수로 변환"""
    if not price_input: return 0
    if isinstance(price_input, int): return price_input
    try:
        return int(float(price_input))
    except ValueError:
        nums = re.sub(r'[^0-9]', '', str(price_input))
        return int(nums) if nums else 0

def parse_status(status_str):
    """상태값을 DB ENUM에 맞게 변환"""
    if not status_str: return "Active"
    s = status_str.lower()
    if "ongoing" in s: return "Active"
    elif "closed" in s or "soldout" in s: return "SoldOut"
    elif "reserved" in s: return "Reserved"
    return "Active"

def parse_time(time_input):
    """시간 문자열을 DB 포맷으로 변환"""
    if not time_input: return None
    try:
        # 이미 datetime 객체거나 정수형 타임스탬프인 경우 처리
        if isinstance(time_input, int):
             # 타임스탬프가 밀리초 단위일 경우 초 단위로 변경
            return parser.parse(str(time_input)).strftime('%Y-%m-%d %H:%M:%S')
        
        dt = parser.parse(str(time_input))
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except:
        return None

def insert_single_article(db: Session, article_data: dict) -> bool:
    p_id = article_data.get('id')
    p_title = article_data.get('title')
    
    if not p_id or not p_title:
        print("❌ [DB Error] 필수 데이터(ID, Title)가 누락되었습니다.")
        return False

    incoming_status = parse_status(article_data.get("status"))

    row = db.execute(
        text("SELECT status FROM listing WHERE id = :id"),
        {"id": p_id},
    ).mappings().fetchone()

    if row and row["status"] == "SoldOut":
        print("✅ 이미 SoldOut → 이후 변경(가격/본문/상태 포함) 무시하고 스킵")
        db.execute(text("UPDATE listing SET needs_processing = 0 WHERE id = :id"), {"id": p_id})
        return True

    p_price = clean_price(article_data.get('price'))
    p_thumb = article_data.get('thumbnail_url')
    p_link = article_data.get('post_url')
    p_status = incoming_status
    p_desc = article_data.get('description', '')
    p_created = parse_time(article_data.get('created_at'))
    p_boosted = parse_time(article_data.get('boosted_at'))
    p_region_id = article_data.get('region_id')

    # [수정됨] ON DUPLICATE KEY UPDATE 부분에 조건문 추가
    # 가격, 썸네일, 본문이 하나라도 다르면 needs_processing = 1, 아니면 기존 값 유지
    sql = text("""
        INSERT INTO listing 
        (id, title, price, thumbnail_url, post_url, status, description, created_at, boosted_at, region_id)
        VALUES (:id, :title, :price, :thumbnail_url, :post_url, :status, :description, :created_at, :boosted_at, :region_id)
        ON DUPLICATE KEY UPDATE
            needs_processing = IF(
                NOT (price <=> VALUES(price)) OR
                NOT (thumbnail_url <=> VALUES(thumbnail_url)) OR
                NOT (description <=> VALUES(description)),
                1, 
                needs_processing
            ),
            title = VALUES(title),
            price = VALUES(price),
            status = VALUES(status),
            thumbnail_url = VALUES(thumbnail_url),
            post_url = VALUES(post_url),
            description = VALUES(description),
            created_at = VALUES(created_at),
            boosted_at = VALUES(boosted_at),
            region_id = VALUES(region_id)
        """)
    try:
        db.execute(sql, {
            "id": p_id,
            "title": p_title,
            "price": p_price,
            "thumbnail_url": p_thumb,
            "post_url": p_link,
            "status": p_status,
            "description": p_desc,
            "created_at": p_created,
            "boosted_at": p_boosted,
            "region_id": p_region_id,
        })
        print(f"✅ [DB Success] 게시글 저장 준비 완료 (ID: {p_id})")  # commit은 caller에서
        return True
    except Exception as e:
        print(f"❌ [DB Fail] 저장 실패: {e}")
        #rollback은 caller가 하거나 여기서 해도 됨. 보수적으로 여기서 해줌.
        db.rollback()
        return False
    """
    conn.commit()
    print(f"✅ [DB Success] 게시글 저장 완료 (ID: {p_id})")
    return True
    """
