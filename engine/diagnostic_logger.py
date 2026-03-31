"""
diagnostic_logger.py — ORB 매칭 진단 데이터를 SQLite DB에 기록합니다.

DB 위치: {프로젝트 루트}/test/orb_diagnostic.db

테이블:
  frames     — 프레임별 요약 (YOLO 감지 여부, 최종 점수, 합격 여부, 전처리 모드)
  roi_scores — ROI별 상세 점수 (타겟 ID, ROI 인덱스, 개별 점수)

활용 예시 (Claude Code가 직접 쿼리):
  SELECT target_id, roi_idx, AVG(score), COUNT(*)
  FROM roi_scores GROUP BY target_id, roi_idx;

  SELECT preprocessing, AVG(best_score), ROUND(AVG(is_ok)*100,1) as pass_rate
  FROM frames GROUP BY preprocessing;
"""

import sqlite3
import os
import time

_ROOT   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_ROOT, "test", "orb_diagnostic.db")


class DiagnosticLogger:
    def __init__(self, db_path: str = DB_PATH):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self.conn    = sqlite3.connect(db_path, check_same_thread=False)
        self._create_tables()
        print(f"[DiagLogger] DB 준비 완료: {db_path}")

    # ──────────────────────────────────────────────────────
    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS frames (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                frame_idx     INTEGER,
                ts            REAL,
                preprocessing TEXT,        -- 'clahe' | 'raw'
                yolo_detected INTEGER,     -- 0/1
                yolo_w        INTEGER,     -- YOLO 크롭 원본 너비
                yolo_h        INTEGER,     -- YOLO 크롭 원본 높이
                best_target   TEXT,        -- 이긴 타겟 ID (예: '1')
                best_score    INTEGER,     -- 최고 ROI 점수
                roi_passed    INTEGER,     -- 합격 ROI 수
                roi_total     INTEGER,     -- 전체 ROI 수
                is_ok         INTEGER      -- 0/1
            );

            CREATE TABLE IF NOT EXISTS roi_scores (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                frame_id  INTEGER,
                target_id TEXT,     -- '1', '2', '3', '4'
                roi_idx   INTEGER,  -- 0-based ROI 인덱스
                x1 INTEGER, y1 INTEGER, x2 INTEGER, y2 INTEGER,  -- 640x360 기준 픽셀
                score     INTEGER,  -- 매칭 점수 (good_matches 수)
                passed    INTEGER,  -- ROI_MATCH_THRESHOLD 초과 여부
                FOREIGN KEY(frame_id) REFERENCES frames(id)
            );

            CREATE INDEX IF NOT EXISTS idx_frames_ok   ON frames(is_ok);
            CREATE INDEX IF NOT EXISTS idx_roi_target  ON roi_scores(target_id, roi_idx);
        """)
        self.conn.commit()

    # ──────────────────────────────────────────────────────
    def log(self, frame_idx: int, preprocessing: str,
            yolo_detected: bool, yolo_w: int, yolo_h: int,
            best_target: str, best_score: int,
            roi_passed: int, roi_total: int, is_ok: bool,
            roi_detail: list):
        """
        roi_detail: [(target_id, roi_idx, x1, y1, x2, y2, score, passed), ...]
        """
        try:
            cur = self.conn.cursor()
            cur.execute("""
                INSERT INTO frames
                  (frame_idx, ts, preprocessing, yolo_detected, yolo_w, yolo_h,
                   best_target, best_score, roi_passed, roi_total, is_ok)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (frame_idx, time.time(), preprocessing,
                  int(yolo_detected), yolo_w, yolo_h,
                  best_target, best_score, roi_passed, roi_total, int(is_ok)))

            fid = cur.lastrowid
            cur.executemany("""
                INSERT INTO roi_scores
                  (frame_id, target_id, roi_idx, x1, y1, x2, y2, score, passed)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, [(fid, tid, ri, x1, y1, x2, y2, s, int(p))
                  for (tid, ri, x1, y1, x2, y2, s, p) in roi_detail])

            self.conn.commit()
        except Exception as ex:
            print(f"[DiagLogger] 기록 실패: {ex}")

    # ──────────────────────────────────────────────────────
    def clear(self):
        """DB 초기화 (테이블 유지, 데이터만 삭제)"""
        self.conn.executescript("DELETE FROM roi_scores; DELETE FROM frames;")
        self.conn.commit()
        print("[DiagLogger] DB 데이터 초기화 완료")

    def row_counts(self) -> dict:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM frames")
        f = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM roi_scores")
        r = cur.fetchone()[0]
        return {"frames": f, "roi_scores": r}

    def close(self):
        self.conn.close()
