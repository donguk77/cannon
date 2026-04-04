import sqlite3
import os
from datetime import datetime

# [버그 수정] 이 파일(__file__)이 db/ 폴더 안에 있으므로,
# 항상 프로젝트 루트 기준의 절대 경로를 자동으로 계산합니다.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB_PATH = os.path.join(_THIS_DIR, "canon.db")

class DBManager:
    """
    [아키텍처 문서 반영: 경량 로컬 데이터베이스 제어기 (SQLite)]
    거대한 DB 서버(MySQL 등)를 공장에 설치할 수 없는 단점을 보완하기 위해
    파일 하나('canon.db')만으로 구동되는 가성비 추적 시스템의 핵심입니다.
    
    기능: 영상 파일(무거움)을 DB에 우겨넣지 않고, '경로'만 가볍게 저장하여 시스템 저하 현상을 영구 차단.
    """
    def __init__(self, db_path=None):
        # db_path를 명시하지 않으면 이 파일 옆(db/canon.db)에 자동으로 생성됩니다.
        self.db_path = db_path if db_path else _DEFAULT_DB_PATH
        
        # 파일이 들어갈 db 폴더가 혹시 없다면 즉시 생성
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        # 클래스를 켜자마자 기본 뼈대(테이블)가 잘 있는지 스캔
        self.init_db()

    def get_connection(self):
        """파이썬 엔진(AI 처리)과 GUI 시스템(사람 보는 창)이 동시에 DB를 열어도 뻗지 않도록 설정"""
        return sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)

    def init_db(self):
        """아키텍처 문서 8번 항목 '데이터베이스 스키마 설계'의 3대 테이블 생성"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # 1. 일일 검출 이력: GUI 역사 탭(History Tab)에 표출될 가장 중요한 모니터링 원천 데이터
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS detection_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                screen_id TEXT,
                confidence REAL,
                fps TEXT,
                method TEXT,
                status TEXT
            )
        ''')

        # 2. 에러 큐(Queue): AI가 낮에 못 맞히고 토스한 "애매한" 파일들의 리스트 (Siamese/LLM 처리 대기열)
        # ※ 아키텍처 핵심 논리: 그림 파일을 넣지 않고 경로명(image_path) 텍스트 파일만 넣어 0kb 속도 방어
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS error_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                image_path TEXT NOT NULL,
                reason TEXT,
                reviewed TEXT DEFAULT 'N',
                label TEXT
            )
        ''')

        # 3. 모델 버전 관리: "Train" 버튼 눌렀을 때, 언제(updated_at) 똑똑해졌고 과거 정확도는 얼마(mAP)였는지 증명
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS model_version (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                mAP REAL,
                file_path TEXT
            )
        ''')

        conn.commit()
        conn.close()

    def insert_detection_log(self, screen_id, confidence, fps, method, status):
        """AI 백엔드가 카메라 프레임을 처리할 때마다 실시간으로 이 쿼리를 호출해 로그를 남김"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute('''
            INSERT INTO detection_log (timestamp, screen_id, confidence, fps, method, status)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (timestamp, screen_id, confidence, fps, method, status))
        
        conn.commit()
        conn.close()

    def get_recent_logs(self, limit=100):
        """GUI의 3번째 탭(History)이 이 함수를 호출해 화면에 100건을 표로 짜라락 뿌려줌"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # 최근에 일어난 사건(에러)을 가장 위(내림차순, DESC)로 끌어올림
        cursor.execute('''
            SELECT timestamp, screen_id, confidence, fps, method, status
            FROM detection_log
            ORDER BY id DESC
            LIMIT ?
        ''', (limit,))
        
        rows = cursor.fetchall()
        conn.close()
        return rows
