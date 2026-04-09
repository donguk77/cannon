"""
connect_phone/server/app.py
===========================
핸드폰 앱과 연결하는 FastAPI WebSocket 서버.

실행:
    cd connect_phone/server
    uvicorn app:app --host 0.0.0.0 --port 8765

외부 접속 (Cloudflare Tunnel):
    cloudflared tunnel --url http://localhost:8765
    → 터미널에 출력되는 https://xxxx.trycloudflare.com 을 앱 설정에 입력

프로토콜:
    Client → Server : binary  (JPEG 바이트)
    Server → Client : text    (JSON 결과)

결과 JSON:
    {
        "status":      "pass" | "fail",
        "target_id":   "1"~"4" | null,
        "score":       int,       ← ROI 모드: 최고 ROI 스코어 / 전체이미지: good_matches 수
        "roi_passed":  int,
        "roi_total":   int,
        "corners":     [[x,y]×4] | null,   ← 0~1 정규화 비율
        "processing_ms": float
    }
"""
import asyncio
import base64
import json
import os
import sys
import time
import concurrent.futures

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

# ── 프로젝트 루트를 sys.path에 추가 (engine/ 임포트용) ─────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

app = FastAPI(title="Canon Monitor API")

# ── 파이프라인 싱글톤 ────────────────────────────────────────────────────────────
_pipeline = None


class Pipeline:
    """기존 engine/ 모듈을 그대로 사용하는 처리 파이프라인."""

    RESIZE_W = 640   # ORB 처리용 고정 크기
    RESIZE_H = 360
    DISPLAY_MAX = 640  # 디스플레이용 최대 해상도 (종횡비 유지)

    def __init__(self):
        from engine.preprocessor import ImagePreprocessor
        from engine.matcher import ScreenMatcher

        # 설정 파일 로드
        cfg_path = os.path.join(_ROOT, "data", "params_config.json")
        cfg = {}
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)

        tile = int(cfg.get("clahe_tile_grid", 8))
        self.preprocessor = ImagePreprocessor(
            clahe_clip_limit=float(cfg.get("clahe_clip_limit", 2.0)),
            clahe_tile_grid=(tile, tile),
            blur_ksize=int(cfg.get("blur_ksize", 0)),
            gamma=float(cfg.get("gamma", 1.0)),
            sharpen_amount=float(cfg.get("sharpen_amount", 1.0)),
        )

        self.matcher = ScreenMatcher(
            orb_nfeatures=int(cfg.get("nfeatures", 700)),
            lowe_ratio=float(cfg.get("lowe_ratio", 0.75)),
            match_threshold=int(cfg.get("match_threshold", 25)),
        )

        self.ROI_MATCH_THRESHOLD = int(cfg.get("roi_match_threshold", 7))
        self.MATCH_THRESHOLD     = int(cfg.get("MATCH_THRESHOLD", 60))

        # YOLO 탐지기 (없으면 ORB 단독 모드)
        self.detector = None
        try:
            from engine.detector import BezelDetector
            model_path = os.path.join(_ROOT, "models", "canon_fast_yolo", "weights", "best.pt")
            if os.path.isfile(model_path):
                self.detector = BezelDetector(model_path=model_path)
                print(f"[Pipeline] YOLO 로드: {os.path.basename(model_path)}")
            else:
                print("[Pipeline] best.pt 없음 → ORB 단독 모드")
        except Exception as e:
            print(f"[Pipeline] YOLO 초기화 실패 → ORB 단독 모드: {e}")

        # 타겟 로드
        target_dir  = os.path.join(_ROOT, "data", "targets")
        roi_cfg     = os.path.join(_ROOT, "data", "roi_config.json")
        mask_cfg    = os.path.join(_ROOT, "data", "mask_config.json")
        self.targets = self.matcher.load_targets_from_dir(
            target_dir, roi_cfg,
            detector=self.detector,
            mask_config_path=mask_cfg,
        )

        # 멀티 타겟 병렬 처리용 스레드풀
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

        print(f"[Pipeline] 초기화 완료 — 타겟 {len(self.targets)}개  "
              f"MATCH_THR={self.MATCH_THRESHOLD}  ROI_THR={self.ROI_MATCH_THRESHOLD}")

    def process(self, frame_bytes: bytes) -> dict:
        """
        JPEG 바이트 → 처리 결과 dict.
        tab_monitor.py 의 매칭 로직과 동일하게 구현.
        """
        t_start = time.perf_counter()

        # 디코드
        buf   = np.frombuffer(frame_bytes, dtype=np.uint8)
        frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if frame is None:
            return {"status": "error", "message": "프레임 디코드 실패"}

        fh, fw = frame.shape[:2]

        # 디스플레이용: 종횡비 유지하며 축소
        scale_d = self.DISPLAY_MAX / max(fw, fh)
        dw, dh  = max(1, int(fw * scale_d)), max(1, int(fh * scale_d))
        display_frame = cv2.resize(frame, (dw, dh))

        # ① YOLO 크롭 (ORB 처리용 고정 640×360)
        analysis = cv2.resize(frame, (self.RESIZE_W, self.RESIZE_H))
        raw_corners = None
        if self.detector:
            try:
                cropped, _ = self.detector.detect_and_crop(frame)
                if cropped is not None and cropped.size > 0:
                    analysis = cv2.resize(cropped, (self.RESIZE_W, self.RESIZE_H))
                    raw_corners = self.detector.last_corners  # (4,2) float32 원본좌표
            except Exception:
                pass

        # ② 전처리
        orb_ready = self.preprocessor.preprocess_for_orb(analysis)
        union_masks = getattr(self.matcher, "union_masks", [])
        if union_masks:
            orb_ready = self.preprocessor.apply_masks(orb_ready, union_masks)

        # ③ ROI별 특징점 미리 추출 (중복 좌표 재사용)
        live_roi_features: dict = {}
        full_des_cache = None
        for _, t_data in self.targets.items():
            if t_data.get("n_rois", 0) == 0:
                if full_des_cache is None:
                    _, full_des_cache = self.matcher.get_features(orb_ready)
            else:
                for (_, rx1, ry1, rx2, ry2) in t_data["rois"]:
                    key = (rx1, ry1, rx2, ry2)
                    if key not in live_roi_features:
                        crop = orb_ready[ry1:ry2, rx1:rx2]
                        _, q_des = self.matcher.get_features(crop) if crop.size > 0 else (None, None)
                        live_roi_features[key] = q_des

        # ④ 타겟 비교 (tab_monitor.py 와 동일 로직)
        def _compare(target_item):
            tid, t_data = target_item
            rois   = t_data.get("rois", [])
            n_rois = t_data.get("n_rois", 0)

            if n_rois == 0:
                s, p = self.matcher.compare_descriptors(full_des_cache, t_data.get("full"))
                return tid, 1, s, (1 if p else 0), p

            passed = 0
            max_s  = 0
            for (t_des, rx1, ry1, rx2, ry2) in rois:
                q_des = live_roi_features.get((rx1, ry1, rx2, ry2))
                s, p  = self.matcher.compare_descriptors(
                    q_des, t_des, threshold=self.ROI_MATCH_THRESHOLD)
                if s > max_s:
                    max_s = s
                if p:
                    passed += 1

            required = n_rois if n_rois <= 2 else n_rois - 1
            tok = passed >= required
            return tid, n_rois, max_s, passed, tok

        best_score = 0;  best_passed = 0;  best_total = 0
        best_ok    = False;  best_tid = None

        futures = [self._executor.submit(_compare, item) for item in self.targets.items()]
        for fut in concurrent.futures.as_completed(futures):
            tid, n_tot, cur_max_s, passed, tok = fut.result()
            if tok or cur_max_s > best_score:
                best_score  = cur_max_s
                best_passed = passed
                best_total  = n_tot
                best_ok     = tok
                best_tid    = tid

        # ⑤ 코너 정규화 (원본 프레임 비율로 변환)
        corners = None
        if raw_corners is not None:
            corners = [[float(x / fw), float(y / fh)] for x, y in raw_corners]

        # ⑥ 원본 종횡비 디스플레이 프레임에 어노테이션
        color_bgr = (83, 200, 0) if best_ok else (68, 23, 255)   # BGR: 초록/빨강

        # YOLO 폴리곤 (원본 좌표 → 디스플레이 좌표)
        if raw_corners is not None:
            sd = np.array([scale_d, scale_d])
            scaled_d = (raw_corners * sd).astype(np.int32)
            cv2.polylines(display_frame, [scaled_d.reshape(-1, 1, 2)], True, color_bgr, 3, cv2.LINE_AA)
            for pt in scaled_d:
                cv2.circle(display_frame, tuple(pt), 7, color_bgr, -1)

        # PASS / FAIL 텍스트
        font_scale = max(0.6, dh / 480)
        label = f"PASS  Target {best_tid}" if best_ok else "FAIL"
        cv2.putText(display_frame, label, (14, int(dh * 0.07)),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale * 1.2, (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(display_frame, label, (14, int(dh * 0.07)),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale * 1.2, color_bgr, 2, cv2.LINE_AA)
        if best_total > 0:
            sub = f"ROI {best_passed}/{best_total}  score {best_score}"
            cv2.putText(display_frame, sub, (14, int(dh * 0.12)),
                        cv2.FONT_HERSHEY_SIMPLEX, font_scale * 0.65, (220, 220, 220), 1, cv2.LINE_AA)

        # JPEG 인코딩 → base64
        _, enc = cv2.imencode('.jpg', display_frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
        frame_b64 = base64.b64encode(enc).decode('ascii')

        processing_ms = (time.perf_counter() - t_start) * 1000
        return {
            "status":        "pass" if best_ok else "fail",
            "target_id":     best_tid if best_ok else None,
            "score":         best_score,
            "roi_passed":    best_passed,
            "roi_total":     best_total,
            "corners":       corners,
            "processing_ms": round(processing_ms, 1),
            "frame":         frame_b64,
        }


# ── 연결 클라이언트 관리 ──────────────────────────────────────────────────────────
_clients: dict[str, WebSocket] = {}


@app.on_event("startup")
async def _startup():
    global _pipeline
    print("[Server] 파이프라인 초기화 중 (최초 1회, 수십 초 소요)...")
    loop = asyncio.get_event_loop()
    _pipeline = await loop.run_in_executor(None, Pipeline)
    print(f"[Server] 준비 완료 — ws://0.0.0.0:8765/ws")


@app.get("/")
async def root():
    """서버 상태 확인용 엔드포인트."""
    return JSONResponse({
        "service":          "Canon Monitor API",
        "connected_clients": len(_clients),
        "targets":           list(_pipeline.targets.keys()) if _pipeline else [],
        "ready":             _pipeline is not None,
    })


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    cid = str(id(ws))
    _clients[cid] = ws
    print(f"[WS] 연결: {cid}  (총 {len(_clients)}대)")

    # 클라이언트별 최신 프레임 큐 (maxsize=2 → 밀리면 오래된 프레임 버림)
    q: asyncio.Queue = asyncio.Queue(maxsize=2)

    async def worker():
        loop = asyncio.get_event_loop()
        while True:
            frame_bytes = await q.get()
            if frame_bytes is None:
                break
            try:
                result = await loop.run_in_executor(None, _pipeline.process, frame_bytes)
                await ws.send_text(json.dumps(result))
            except Exception as e:
                print(f"[WS] 처리 오류 ({cid}): {e}")
                break

    worker_task = asyncio.create_task(worker())

    try:
        while True:
            frame_bytes = await ws.receive_bytes()
            if q.full():
                try:
                    q.get_nowait()   # 오래된 프레임 버리기
                except asyncio.QueueEmpty:
                    pass
            await q.put(frame_bytes)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS] 예외 ({cid}): {e}")
    finally:
        await q.put(None)     # worker 종료
        await worker_task
        _clients.pop(cid, None)
        print(f"[WS] 해제: {cid}  (총 {len(_clients)}대)")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
