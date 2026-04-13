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
import json
import os
import sys
import time
import concurrent.futures

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response

# ── 프로젝트 루트를 sys.path에 추가 (engine/ 임포트용) ─────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _ROOT)

# ── stdout 파이프 보호 ────────────────────────────────────────────────────────────
# 문제: ultralytics/Rich가 non-TTY(파이프) 환경을 감지해 sys.stdout을 교체하거나 닫음
#       → 부모 프로세스(GUI)가 파이프 EOF를 받아 "서버 죽음"으로 오인
# 해결: 진짜 파이프를 클로저로 캡처해두고, close()를 막는 래퍼를 sys.stdout에 장착.
#       YOLO 로드 후 sys.stdout이 바뀌어도 _restore_stdout()로 래퍼를 다시 복원함.
_pipe_stdout = sys.stdout   # 원본 파이프 참조 유지 (GC 방지 + 복원용)

class _ProtectedStdout:
    """ultralytics/Rich가 sys.stdout을 교체·닫아도 파이프를 살려두는 래퍼."""
    def __init__(self, wrapped):
        object.__setattr__(self, '_w', wrapped)
    def write(self, s):
        try:   return object.__getattribute__(self, '_w').write(s)
        except Exception: return 0
    def flush(self):
        try:   object.__getattribute__(self, '_w').flush()
        except Exception: pass
    def fileno(self):
        return object.__getattribute__(self, '_w').fileno()
    def isatty(self):    return False
    def readable(self):  return False
    def writable(self):  return True
    def close(self):     pass          # ← 닫기 완전 차단
    @property
    def closed(self):    return False
    @property
    def encoding(self):
        return getattr(object.__getattribute__(self, '_w'), 'encoding', 'utf-8')
    @property
    def errors(self):
        return getattr(object.__getattribute__(self, '_w'), 'errors', 'replace')
    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_w'), name)

sys.stdout = _ProtectedStdout(_pipe_stdout)

def _restore_stdout():
    """YOLO 로드 후 sys.stdout이 교체됐으면 보호 래퍼를 다시 복원."""
    if not isinstance(sys.stdout, _ProtectedStdout):
        sys.stdout = _ProtectedStdout(_pipe_stdout)

app = FastAPI(title="Canon Monitor API")

# ── 파이프라인 싱글톤 ────────────────────────────────────────────────────────────
_pipeline = None


class Pipeline:
    """기존 engine/ 모듈을 그대로 사용하는 처리 파이프라인."""

    RESIZE_W = 640   # ORB 처리용 고정 크기
    RESIZE_H = 360

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
            orb_compare_threshold=int(cfg.get("orb_compare_threshold", cfg.get("match_threshold", 25))),
        )

        self.ROI_MATCH_THRESHOLD  = int(cfg.get("roi_match_threshold", 7))
        self.final_pass_threshold = int(cfg.get("final_pass_threshold", cfg.get("MATCH_THRESHOLD", 60)))
        # 폰 카메라는 각도·조명 변화가 크므로 기본값을 0.3으로 낮춤
        yolo_conf = float(cfg.get("yolo_conf", 0.3))

        # YOLO 탐지기 로드 (stdout 보호 래퍼 장착 후 진행)
        # ultralytics/Rich가 sys.stdout을 교체해도 _ProtectedStdout이 파이프를 지킴.
        # 로드 완료 후 _restore_stdout()로 sys.stdout을 래퍼로 다시 복원함.
        self.detector = None
        try:
            from engine.detector import BezelDetector
            active_file = os.path.join(_ROOT, "data", "active_model.json")
            model_path  = None
            if os.path.isfile(active_file):
                try:
                    with open(active_file, encoding="utf-8") as af:
                        model_path = os.path.join(_ROOT, json.load(af).get("path", ""))
                except Exception:
                    pass
            if not model_path or not os.path.isfile(model_path):
                seg_best  = os.path.join(_ROOT, "models", "canon_fast_yolo", "weights", "best.pt")
                det_model = os.path.join(_ROOT, "yolov8n.pt")
                model_path = seg_best if os.path.isfile(seg_best) else (
                             det_model if os.path.isfile(det_model) else None)
            if model_path:
                self.detector = BezelDetector(model_path=model_path, conf_threshold=yolo_conf)
                _restore_stdout()   # ultralytics가 바꾼 sys.stdout을 파이프 래퍼로 복원
                print(f"[Pipeline] YOLO 로드: {os.path.basename(model_path)} conf={yolo_conf}")
            else:
                _restore_stdout()
                print("[Pipeline] YOLO 모델 없음 — ORB 단독 모드")
        except Exception as ex:
            _restore_stdout()
            print(f"[Pipeline] YOLO 로드 실패 ({ex}) — ORB 단독 모드")

        # 타겟 로드
        # [버그 수정] detector=None 으로 전달 — 이전에는 detector=self.detector를 넘겨
        # 서버 시작 시 모든 타겟 이미지에 YOLO 추론을 돌렸음.
        # 서버 프로세스에서 최초 YOLO 추론 시 CUDA/메모리 오류로 프로세스가 종료되는 문제.
        # YOLO는 라이브 프레임 처리(process 메서드) 시에만 사용하면 충분함.
        target_dir  = os.path.join(_ROOT, "data", "targets")
        roi_cfg     = os.path.join(_ROOT, "data", "roi_config.json")
        mask_cfg    = os.path.join(_ROOT, "data", "mask_config.json")
        self.targets = self.matcher.load_targets_from_dir(
            target_dir, roi_cfg,
            detector=None,           # 시작 시 YOLO 추론 금지 → crash 방지
            mask_config_path=mask_cfg,
        )

        # 멀티 타겟 병렬 처리용 스레드풀
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

        self._frame_count = 0   # 로그 throttle용

        print(f"[Pipeline] 초기화 완료 — 타겟 {len(self.targets)}개  "
              f"final_pass_thr={self.final_pass_threshold}  roi_thr={self.ROI_MATCH_THRESHOLD}")

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

        # ① YOLO 크롭 (ORB 처리용 고정 640×360)
        analysis = cv2.resize(frame, (self.RESIZE_W, self.RESIZE_H))
        raw_corners = None
        yolo_hit = False
        t_yolo_ms = 0.0
        if self.detector:
            try:
                # 원본 프레임이 너무 크면 YOLO 내부 텐서 변환이 수백ms 걸림
                # (예: 2944×5233 → ultralytics letterbox 전처리 과부하)
                # imgsz=640 기준 최대 1280px로 사전 축소
                YOLO_MAX = 1280
                yolo_frame = frame
                yolo_scale = 1.0
                if max(fh, fw) > YOLO_MAX:
                    yolo_scale = YOLO_MAX / max(fh, fw)
                    yolo_frame = cv2.resize(
                        frame,
                        (int(fw * yolo_scale), int(fh * yolo_scale)),
                        interpolation=cv2.INTER_AREA,
                    )

                t_y0 = time.perf_counter()
                cropped, _ = self.detector.detect_and_crop(yolo_frame)
                t_yolo_ms = (time.perf_counter() - t_y0) * 1000

                if cropped is not None and cropped.size > 0:
                    analysis = cv2.resize(cropped, (self.RESIZE_W, self.RESIZE_H))
                    raw_corners = self.detector.last_corners
                    # 축소된 프레임 기준 코너 → 원본 프레임 좌표로 복원
                    if raw_corners is not None and yolo_scale != 1.0:
                        raw_corners = raw_corners / yolo_scale
                    yolo_hit = True
                else:
                    print(f"[Pipeline] YOLO 미감지 ({fw}×{fh}) → 전체 프레임 ORB")
            except Exception as e:
                print(f"[Pipeline] YOLO 예외 ({fw}×{fh}): {e}")

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
                # YOLO 성공(yolo_hit) → 크롭 이미지 → orb_compare_threshold (정상 합격 기준)
                # YOLO 실패(not yolo_hit) → 원본 전체 → final_pass_threshold (최후 수단)
                thr = self.matcher.orb_compare_threshold if yolo_hit else self.final_pass_threshold
                s, p = self.matcher.compare_descriptors(
                    full_des_cache, t_data.get("full"),
                    threshold=thr)
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

        processing_ms = (time.perf_counter() - t_start) * 1000
        orb_ms = processing_ms - t_yolo_ms

        # 30프레임마다 1번만 로그 출력
        self._frame_count += 1
        if self._frame_count % 30 == 0:
            print(f"[Pipeline] {'HIT' if yolo_hit else 'miss'}  "
                  f"yolo={t_yolo_ms:.0f}ms  orb={orb_ms:.0f}ms  total={processing_ms:.0f}ms  "
                  f"score={best_score}  ok={best_ok}  ({fw}×{fh})  (#{self._frame_count})")
        return {
            "status":        "pass" if best_ok else "fail",
            "target_id":     best_tid if best_ok else None,
            "score":         best_score,
            "roi_passed":    best_passed,
            "roi_total":     best_total,
            "corners":       corners,
            "processing_ms": round(processing_ms, 1),
            "yolo_hit":      yolo_hit,
        }


# ── 연결 클라이언트 관리 ──────────────────────────────────────────────────────────
_clients: dict[str, WebSocket] = {}

# cid → {last_frame, last_result, fps, connected_at}
_client_cache: dict[str, dict] = {}

# ── YOLO 전용 실행기 (max_workers=1) ─────────────────────────────────────────────
# 문제: 폰 N대가 동시에 process()를 호출하면 N개의 YOLO 추론이 동시에 CPU를 점유
#       → CPU 경합으로 1대 기준 ~80ms가 N대에서 ~280ms로 느려짐
# 해결: 전용 단일 스레드 풀로 YOLO 추론을 직렬화
#       asyncio 레벨 큐(maxsize=2)가 오래된 프레임을 버려 latency 누적 방지
_yolo_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="yolo")
# max_workers=1 이유: PyTorch는 YOLO 추론 1회에 전체 CPU 코어를 모두 사용함
# 여러 워커를 열면 코어가 쪼개져 1회 추론 시간이 늘어남 → 총 처리량은 동일하거나 악화
# 진짜 속도 개선이 필요하면: ① GPU 사용 ② yolo_imgsz=320으로 줄이기


@app.on_event("startup")
async def _startup():
    global _pipeline
    import traceback
    print("[Server] 파이프라인 초기화 중 (최초 1회, 수십 초 소요)...")
    loop = asyncio.get_event_loop()
    try:
        _pipeline = await loop.run_in_executor(None, Pipeline)
        print(f"[Server] 준비 완료 — ws://0.0.0.0:8765/ws")
    except Exception:
        # 초기화 실패 시 전체 traceback 출력 후 서버는 계속 실행 (연결은 가능하지만 처리 불가)
        print("[Server] !! 파이프라인 초기화 실패 — 오류 내용:")
        traceback.print_exc()
        print("[Server] 서버는 실행 중이지만 이미지 처리가 불가능합니다. 로그를 확인하세요.")


@app.get("/")
async def root():
    """서버 상태 확인용 엔드포인트."""
    return JSONResponse({
        "service":          "Canon Monitor API",
        "connected_clients": len(_clients),
        "targets":           list(_pipeline.targets.keys()) if _pipeline else [],
        "ready":             _pipeline is not None,
    })


@app.get("/clients")
async def get_clients():
    """연결된 클라이언트 목록 반환 (GUI 관제 탭 폴링용)."""
    result = []
    for cid, info in list(_client_cache.items()):
        last_result = info.get("last_result") or {}
        result.append({
            "cid":          cid,
            "fps":          info.get("fps", 0.0),
            "status":       last_result.get("status", "unknown"),
            "target_id":    last_result.get("target_id"),
            "score":        last_result.get("score", 0),
            "connected_at": info.get("connected_at", 0.0),
        })
    return JSONResponse(result)


@app.get("/frame/{cid}")
async def get_frame(cid: str):
    """해당 클라이언트의 최신 JPEG 프레임 반환 (GUI 관제 탭 폴링용)."""
    info = _client_cache.get(cid)
    if info is None or info.get("last_frame") is None:
        return Response(status_code=404)
    return Response(content=info["last_frame"], media_type="image/jpeg")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    cid = str(id(ws))
    _clients[cid] = ws
    _client_cache[cid] = {
        "last_frame":   None,
        "last_result":  {},
        "fps":          0.0,
        "connected_at": time.time(),
    }
    print(f"[WS] 연결: {cid}  (총 {len(_clients)}대)")

    # 클라이언트별 최신 프레임 큐 (maxsize=2 → 밀리면 오래된 프레임 버림)
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    _frame_times: list[float] = []  # FPS 계산용 (최근 2초 윈도우)

    async def worker():
        loop = asyncio.get_event_loop()
        while True:
            frame_bytes = await q.get()
            if frame_bytes is None:
                break
            try:
                result = await loop.run_in_executor(_yolo_pool, _pipeline.process, frame_bytes)
                await ws.send_text(json.dumps(result))
                # ── 캐시 업데이트 ──────────────────────────────────────────
                now = time.time()
                _frame_times.append(now)
                cutoff = now - 2.0
                while _frame_times and _frame_times[0] < cutoff:
                    _frame_times.pop(0)
                fps = len(_frame_times) / 2.0
                _client_cache[cid].update({
                    "last_frame":  frame_bytes,
                    "last_result": result,
                    "fps":         round(fps, 1),
                })
            except Exception as e:
                # [버그 수정] break → continue: 예외 발생 시 Worker 죽지 않고 다음 프레임 계속 처리
                # break이면 WebSocket 연결은 살아있는데 서버가 아무것도 안 보내 → UI 멈춤처럼 보임
                print(f"[WS] 처리 오류 ({cid}): {e}")
                continue

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
        _client_cache.pop(cid, None)
        print(f"[WS] 해제: {cid}  (총 {len(_clients)}대)")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8765, log_level="info")
