import os, time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import create_engine, text
from sshtunnel import SSHTunnelForwarder

from config.settings import settings

from core.listing_processor import process_listing
from core.openai_analyzer import OpenAIAnalyzer
import signal

# 워커 종료 플래그
STOP = False

def handle_stop(signum, frame):
    global STOP
    STOP = True
    print(f"종료 시그널 수신 ({signum}) → 안전 종료 준비")

signal.signal(signal.SIGTERM, handle_stop)  # docker / k8s stop
signal.signal(signal.SIGINT, handle_stop)   # ctrl + c


# =========================================================
# 🔧 0. 로컬 환경 설정 (환경변수 X, 직접 입력)
# =========================================================

# .env로 이동, config/settings.py 에서 관리

# =========================================================
# 1. OpenAI 키 설정
# =========================================================

# core/openai_analyzer.py 로 이동

# =========================================================
# 2. JSON 파싱 유틸
# =========================================================

# core/openai_analyzer.py 로 이동

# =========================================================
# 3. 브랜드/역할 설정
# =========================================================

# core/listing_processor_context.py 으로 이동

# =========================================================
# 4. 브랜드 정규화
# =========================================================


# core/model_utils.py 로 이동


# =========================================================
# 5. 모델명 정규화 normalize_model_name()
# =========================================================


# core/model_utils.py 로 이동


# =========================================================
# 6. itemModel 캐시 + 생성
# =========================================================

# get_or_create_model_full_spec() 을 core/listing_processor_context.py 로 이동

# core/openai_analyzer.py로 이동


# =========================================================
# 7. 메인 실행부
# =========================================================

# =========================================================
# 0-1. 배치 크기 설정
# =========================================================
BATCH_SIZE = 30  # 원하면 50~100 정도로 올려도 됨


# =========================================================
# 0-2. needs_processing 플래그 관리용 헬퍼
# =========================================================

# mark_listing_done() 을 core/listing_processor_context.py 로 이동

def create_db_engine():
    use_ssh = os.getenv("USE_SSH_TUNNEL", "false").lower() == "true"

    if use_ssh:
        print("USE_SSH_TUNNEL=true → SSH 터널 사용")

        server = SSHTunnelForwarder(
            (settings.ssh_host, 22),
            ssh_username=settings.ssh_user,
            ssh_pkey=settings.ssh_key_path,
            remote_bind_address=(settings.db_host, 3306),
        )
        server.start()

        engine = create_engine(
            f"mysql+pymysql://{settings.db_user}:{settings.db_password}"
            f"@127.0.0.1:{server.local_bind_port}/{settings.db_name}",
            pool_pre_ping=True,
        )
        return engine, server

    else:
        print("USE_SSH_TUNNEL=false → Direct DB 연결")
        engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        return engine, None


def main():
    #print("서버 연결 중...")

    engine, ssh_server = create_db_engine()
    MAX_WORKERS = int(os.getenv("OPENAI_WORKERS", "3"))
    SLEEP_SECONDS = int(os.getenv("WORKER_SLEEP_SECONDS", "30"))

    try:
        while not STOP:
            with engine.connect() as conn:
                print("📥 미분석 데이터 조회 중...")

                rows = conn.execute(text("""
                    SELECT seq, id AS original_id, title, description, price, post_url
                    FROM listing
                    WHERE needs_processing = 1
                    ORDER BY seq
                    LIMIT :limit
                """), {"limit": BATCH_SIZE}).mappings().all()

            if not rows:
                print("대기: 미분석 데이터 없음")
                time.sleep(SLEEP_SECONDS)
                continue

            print(f"⚡ {len(rows)}건 병렬 분석 시작!")

            def process_row(row):
                try:
                    local = OpenAIAnalyzer(
                        settings.openai_api_keys,
                        settings.openai_model
                    )

                    res = local.analyze_camera_data_openai(
                        row["title"],
                        row["description"],
                        row["price"],
                    )
                    return {"row": row, "res": res}
                except Exception as e:
                    print(f"❌ row 처리 오류 (seq={row['seq']}): {e}")
                    return {"row": row, "res": None}

            results = []
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as exe:
                futures = [exe.submit(process_row, r) for r in rows]
                for f in as_completed(futures):
                    results.append(f.result())

            # 2단계: DB 작업은 메인 쓰레드에서 순차 처리
            with engine.begin() as conn:  # 트랜잭션 시작
                for data in results:
                    row = data["row"]
                    result = data["res"]
                    process_listing(conn, row, result)

            print(f"배치 처리 완료!: {len(rows)}건")
    finally: 
        if ssh_server:
            ssh_server.stop()
            print("🔒 SSH 터널 종료")


if __name__ == "__main__":
    main()
