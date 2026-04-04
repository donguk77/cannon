"""
train_yolo.py — YOLO 재학습 스크립트
- 실제 데이터셋: datasets/canon_monitor/canon_data.yaml 사용
- Python 3.13 PyTorch 미호환 시 명확한 오류 메시지 반환
"""
import os, sys, shutil, yaml, random

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# ─── 경로 상수 (실제 데이터셋 경로 고정) ─────────────────────────────────────
BEST_MODEL   = os.path.join(_ROOT, "models", "canon_fast_yolo", "weights", "best.pt")
# 세그멘테이션 기본 모델 — 로컬에 없으면 ultralytics가 자동 다운로드함
BASE_MODEL   = "yolov8n-seg.pt"
DATASET_YAML = os.path.join(_ROOT, "datasets", "canon_monitor", "canon_data.yaml")

# 보조 데이터 (dataset_target_and_1cycle/data 의 jpg+txt 쌍)
AUX_DATA_DIR  = os.path.join(_ROOT, "dataset_target_and_1cycle", "data")
# 메인 학습 이미지 폴더
TRAIN_IMG_DIR = os.path.join(_ROOT, "datasets", "canon_monitor", "images", "train")
TRAIN_LBL_DIR = os.path.join(_ROOT, "datasets", "canon_monitor", "labels", "train")
VAL_IMG_DIR   = os.path.join(_ROOT, "datasets", "canon_monitor", "images", "val")
VAL_LBL_DIR   = os.path.join(_ROOT, "datasets", "canon_monitor", "labels", "val")



def _check_pytorch() -> str:
    """
    PyTorch 임포트를 시도하고, 실패 시 오류 내용을 반환합니다.
    Python 3.13+ 에서는 PyTorch가 공식 미지원입니다.
    """
    try:
        import torch
        return ""
    except ImportError:
        return "PyTorch 미설치: pip install torch torchvision"
    except Exception as e:
        ver = sys.version_info
        if ver.major == 3 and ver.minor >= 13:
            return (
                f"[Python {ver.major}.{ver.minor} 미호환]\n"
                "PyTorch는 현재 Python 3.13 이상을 공식 지원하지 않습니다.\n"
                "해결 방법: Python 3.11 또는 3.12 버전으로 변경 후 재설치하세요.\n"
                f"상세 오류: {e}"
            )
        return f"PyTorch 로드 실패: {e}"


def _merge_aux_data(progress_cb=None):
    """
    dataset_target_and_1cycle/data 의 jpg+txt 쌍을
    datasets/canon_monitor/images+labels/train 으로 복사합니다.
    이미지는 이미 있으면 덮지 않지만, 라벨(.txt)은 항상 최신으로 덮어쓰기
    (세그멘테이션 포맷으로 업데이트된 라벨 즉시 반영)
    """
    if not os.path.isdir(AUX_DATA_DIR):
        return 0
    os.makedirs(TRAIN_IMG_DIR, exist_ok=True)
    os.makedirs(TRAIN_LBL_DIR, exist_ok=True)
    copied = 0
    files = [f for f in os.listdir(AUX_DATA_DIR) if f.lower().endswith((".jpg", ".png"))]
    for fname in files:
        base = os.path.splitext(fname)[0]
        src_img = os.path.join(AUX_DATA_DIR, fname)
        src_lbl = os.path.join(AUX_DATA_DIR, base + ".txt")
        dst_img = os.path.join(TRAIN_IMG_DIR, fname)
        dst_lbl = os.path.join(TRAIN_LBL_DIR, base + ".txt")
        if not os.path.exists(dst_img):
            shutil.copy2(src_img, dst_img)
            copied += 1
        # 라벨은 항상 덮어쓰기 (세그 포맷 업데이트 반영)
        if os.path.exists(src_lbl):
            shutil.copy2(src_lbl, dst_lbl)
    if progress_cb and copied:
        progress_cb(8, f"보조 데이터 {copied}개 병합 완료")
    return copied


def _convert_bbox_to_seg(progress_cb=None):
    """
    train/val 라벨 폴더를 순회하여
    직사각형(5값: class cx cy w h) 형식의 라벨을
    세그멘테이션 폴리곤(9값: class x1 y1 x2 y2 x3 y3 x4 y4) 형식으로 자동 변환합니다.
    이미 폴리곤 형식(6값 이상)인 라인은 건드리지 않습니다.
    """
    converted = 0
    for lbl_dir in [TRAIN_LBL_DIR, VAL_LBL_DIR]:
        if not os.path.isdir(lbl_dir):
            continue
        for fname in os.listdir(lbl_dir):
            if not fname.endswith(".txt"):
                continue
            fpath = os.path.join(lbl_dir, fname)
            lines = []
            needs_convert = False
            with open(fpath, "r") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) == 5:
                        # 직사각형 → 4꼭짓점 폴리곤으로 변환
                        cls_id = parts[0]
                        cx, cy, w, h = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
                        x1, y1 = cx - w/2, cy - h/2   # 좌상
                        x2, y2 = cx + w/2, cy - h/2   # 우상
                        x3, y3 = cx + w/2, cy + h/2   # 우하
                        x4, y4 = cx - w/2, cy + h/2   # 좌하
                        lines.append(f"{cls_id} {x1:.6f} {y1:.6f} {x2:.6f} {y2:.6f} {x3:.6f} {y3:.6f} {x4:.6f} {y4:.6f}")
                        needs_convert = True
                    else:
                        lines.append(line.strip())
            if needs_convert:
                with open(fpath, "w") as f:
                    f.write("\n".join(lines) + "\n")
                converted += 1
    if converted > 0:
        msg = f"Bbox→Seg 라벨 자동 변환: {converted}개 파일"
        print(f"[train_yolo] {msg}")
        if progress_cb:
            progress_cb(11, msg)
    return converted


def _split_train_val(val_ratio: float = 0.2, progress_cb=None) -> tuple:
    """
    train + val 폴더의 전체 이미지를 통합한 뒤,
    random.seed(42)로 완전 무작위 셔플 후 8:2로 재분배합니다.
    - Val이 Train의 부분집합이 되는 데이터 코드 증복(Data Leakage)을 완전 차단
    - 라벨(.txt) 파일도 이미지와 함께 이동
    """
    for d in [TRAIN_IMG_DIR, TRAIN_LBL_DIR, VAL_IMG_DIR, VAL_LBL_DIR]:
        os.makedirs(d, exist_ok=True)

    # 전체 이미지 파일명 수집 (train + val 통합, 중복 제거)
    all_imgs = set()
    for img_dir in [TRAIN_IMG_DIR, VAL_IMG_DIR]:
        for f in os.listdir(img_dir):
            if f.lower().endswith((".jpg", ".png")):
                all_imgs.add(f)

    all_imgs = sorted(all_imgs)  # 정렬 후 셔플 -> 재현성 보장
    random.seed(42)
    random.shuffle(all_imgs)

    n_val = max(1, int(len(all_imgs) * val_ratio))
    val_set = set(all_imgs[:n_val])
    trn_set = set(all_imgs[n_val:])

    def _safe_copy(src, dst):
        """src와 dst가 다른 파일일 때만 복사"""
        if os.path.exists(src):
            if not os.path.exists(dst) or not os.path.samefile(src, dst):
                shutil.copy2(src, dst)

    # ─ Val 폴더 정리: train_set에 속해야 하는 파일은 train으로 이동 ─
    for fname in list(os.listdir(VAL_IMG_DIR)):
        if not fname.lower().endswith((".jpg", ".png")):
            continue
        base = os.path.splitext(fname)[0]
        if fname in trn_set:
            _safe_copy(os.path.join(VAL_IMG_DIR, fname),  os.path.join(TRAIN_IMG_DIR, fname))
            _safe_copy(os.path.join(VAL_LBL_DIR, base + ".txt"), os.path.join(TRAIN_LBL_DIR, base + ".txt"))
            try: os.remove(os.path.join(VAL_IMG_DIR, fname))
            except: pass
            try: os.remove(os.path.join(VAL_LBL_DIR, base + ".txt"))
            except: pass

    # ─ Train 폴더 정리: val_set에 속해야 하는 파일은 val로 이동 ─
    for fname in list(os.listdir(TRAIN_IMG_DIR)):
        if not fname.lower().endswith((".jpg", ".png")):
            continue
        base = os.path.splitext(fname)[0]
        if fname in val_set:
            _safe_copy(os.path.join(TRAIN_IMG_DIR, fname), os.path.join(VAL_IMG_DIR, fname))
            _safe_copy(os.path.join(TRAIN_LBL_DIR, base + ".txt"), os.path.join(VAL_LBL_DIR, base + ".txt"))
            try: os.remove(os.path.join(TRAIN_IMG_DIR, fname))
            except: pass
            try: os.remove(os.path.join(TRAIN_LBL_DIR, base + ".txt"))
            except: pass

    n_trn_final = len([f for f in os.listdir(TRAIN_IMG_DIR) if f.lower().endswith((".jpg", ".png"))])
    n_val_final = len([f for f in os.listdir(VAL_IMG_DIR)   if f.lower().endswith((".jpg", ".png"))])

    # ─ YOLO 캐시 무효화 (이전 잘못된 split 상태를 기억하는 .cache 강제 삭제) ─
    for cache_f in ["train.cache", "val.cache"]:
        try: os.remove(os.path.join(VAL_LBL_DIR, cache_f))
        except: pass
        try: os.remove(os.path.join(TRAIN_LBL_DIR, cache_f))
        except: pass
        # labels 루트 폴더에도 생길 수 있으므로 삭제
        try: os.remove(os.path.join(os.path.dirname(TRAIN_LBL_DIR), cache_f))
        except: pass

    msg = (f"Train/Val 재분리 완료 -- "
           f"Train: {n_trn_final}장 / Val: {n_val_final}장 (중복 없음, 캐시 초기화)")
    print(f"[train_yolo] {msg}")
    if progress_cb:
        progress_cb(10, msg)
    return n_trn_final, n_val_final


def _fix_yaml_path():
    """
    canon_data.yaml 의 path를 '.'(상대 경로)로 고정합니다.
    절대 경로에 한글이 포함되면 PyYAML 파서가 실패하므로
    ultralytics가 yaml 파일 위치를 기준으로 경로를 잡는 상대 경로 방식을 사용합니다.
    """
    if not os.path.exists(DATASET_YAML):
        _create_yaml()
        return
    try:
        with open(DATASET_YAML, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"[train_yolo] yaml 파싱 실패 ({e}), 재생성합니다.")
        _create_yaml()
        return
    # path를 '.'으로 설정 (yaml 파일 위치가 기준이 되므로 한글 경로 우회 가능)
    if data.get("path") != ".":
        data["path"] = "."
        with open(DATASET_YAML, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        print("[train_yolo] yaml path → '.' 으로 수정 완료")


def _create_yaml():
    """canon_data.yaml이 없거나 깨진 경우 기본값으로 새로 생성"""
    os.makedirs(os.path.dirname(DATASET_YAML), exist_ok=True)
    content = "names:\n  0: canon_monitor\npath: .\ntrain: images/train\nval: images/val\n"
    with open(DATASET_YAML, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[train_yolo] canon_data.yaml 새로 생성")


def run_yolo_training(epochs: int = 30, imgsz: int = 640, progress_cb=None, mode: str = 'resume') -> dict:
    """
    YOLO 재학습 주 진입점.
    """
    # ① Python/PyTorch 호환성 사전 체크
    err = _check_pytorch()
    if err:
        if progress_cb: progress_cb(-1, err)
        return {"error": err, "map50": 0.0, "model_path": ""}

    try:
        from ultralytics import YOLO
    except Exception as e:
        msg = f"ultralytics load failed: {e}"
        if progress_cb: progress_cb(-1, msg)
        return {"error": msg, "map50": 0.0, "model_path": ""}

    # ② 보조 데이터 병합 (라벨은 항상 최신으로 덮어쓰기)
    if progress_cb: progress_cb(6, "Merging aux data...")
    _merge_aux_data(progress_cb)

    # ②-b 직사각형 라벨을 세그 폴리곤으로 자동 변환
    _convert_bbox_to_seg(progress_cb)

    # ②-c Train/Val 완전 분리 (데이터 중복 차단)
    if progress_cb: progress_cb(9, "Train/Val 데이터 무작위 분리 중 (8:2)...")
    n_trn, n_val = _split_train_val(val_ratio=0.2, progress_cb=progress_cb)
    if n_trn < 1:
        msg = "Train 이미지가 없습니다! datasets/canon_monitor/images/train 폴더를 확인하세요."
        if progress_cb: progress_cb(-1, msg)
        return {"error": msg, "map50": 0.0, "model_path": ""}

    # ③ 모델 선택 — mode 파라미터에 따라 결정
    if mode == 'scratch':
        # 처음부터: 모델명만 전달하면 ultralytics가 자동 다운로드
        model_path = BASE_MODEL
    else:
        # 이어서: best.pt가 있으면 사용, 없으면 기본 세그 모델
        model_path = BEST_MODEL if os.path.exists(BEST_MODEL) else BASE_MODEL
    # 이어서 모드에서 best.pt를 지정했는데 파일이 없는 경우만 에러
    if mode == 'resume' and model_path == BEST_MODEL and not os.path.exists(BEST_MODEL):
        if progress_cb: progress_cb(12, "best.pt 없음 → 기본 세그 모델로 처음부터 학습합니다.")
        model_path = BASE_MODEL

    mode_label = '처음부터 (yolov8n-seg.pt)' if mode == 'scratch' else '이어서 (best.pt)'
    if progress_cb: progress_cb(12, f"모드: {mode_label} | 모델: {os.path.basename(model_path)}")

    # ④ yaml 파일을 완전히 ASCII-safe 내용으로 재작성
    #    (한글 경로 파싱 문제를 근본 차단)
    #    ultralytics는 yaml 파일이 있는 폴더 상대경로를 지원하지 않으므로
    #    작업 디렉토리를 datasets/canon_monitor 로 변경 후 상대 경로 사용
    yaml_dir = os.path.join(_ROOT, "datasets", "canon_monitor")
    yaml_file = os.path.join(yaml_dir, "canon_data.yaml")
    yaml_content = (
        "nc: 1\n"
        "names:\n"
        "  0: canon_monitor\n"
        "path: .\n"
        "train: images/train\n"
        "val: images/val\n"
    )
    os.makedirs(yaml_dir, exist_ok=True)
    with open(yaml_file, "w", encoding="ascii") as f:
        f.write(yaml_content)
    print(f"[train_yolo] yaml rewritten (ascii-safe)")

    # ⑤ 학습 실행 — cwd를 yaml 폴더로 변경하면 상대경로 'images/train' 이 정확히 작동
    if progress_cb: progress_cb(15, f"YOLO training start ({epochs} epochs)...")
    prev_cwd = os.getcwd()
    try:
        os.chdir(yaml_dir)
        model = YOLO(model_path)
        save_dir = os.path.join(_ROOT, "models")
        os.makedirs(save_dir, exist_ok=True)

        # ─── Epoch 진행률 콜백 ──────────────────────────────────────────────
        def _on_epoch_end(trainer):
            """매 epoch 완료 시 지표를 [PCT%] 형식으로 출력 (GUI subprocess 파서 연동)"""
            cur   = trainer.epoch + 1
            total = trainer.epochs
            pct   = 15 + int((cur / total) * 80)   # 15%~95% 사이 분배
            m     = trainer.metrics
            # 세그멘테이션 MASK(M) 지표 우선 추출, 없으면 BBOX(B)
            p     = m.get("metrics/precision(M)", m.get("metrics/precision(B)", 0.0))
            r     = m.get("metrics/recall(M)",    m.get("metrics/recall(B)",    0.0))
            mp50  = m.get("metrics/mAP50(M)",     m.get("metrics/mAP50(B)",     0.0))
            mp595 = m.get("metrics/mAP50-95(M)",  m.get("metrics/mAP50-95(B)",  0.0))
            seg_l = m.get("train/seg_loss",       m.get("train/box_loss",       0.0))
            msg = (f"Epoch {cur}/{total} | "
                   f"Prec={p:.3f} Rec={r:.3f} "
                   f"mAP50={mp50:.3f} mAP50-95={mp595:.3f} "
                   f"SegLoss={seg_l:.4f}")
            print(f"[{pct}%] {msg}", flush=True)
            # 별도 METRIC 줄도 출력 (GUI가 최종 파싱용으로 사용)
            # GUI의 호환성을 위해 SegLoss를 box_loss 키로 내려줌
            print(f"[METRIC] epoch={cur} total={total} "
                  f"P={p:.4f} R={r:.4f} "
                  f"mAP50={mp50:.4f} mAP50-95={mp595:.4f} "
                  f"box_loss={seg_l:.4f}", flush=True)

        model.add_callback("on_train_epoch_end", _on_epoch_end)
        # ────────────────────────────────────────────────────────────────────

        results = model.train(
            task     = "segment",   # 세그멘테이션 다각형 학습 강제 선언
            data     = "canon_data.yaml",
            epochs   = epochs,
            imgsz    = imgsz,
            batch    = 8,
            project  = save_dir,
            name     = "canon_fast_yolo",
            exist_ok = True,
            verbose  = False,   # ultralytics 기본 로그 off (콜백으로 대체)
            patience = 0,   # 조기 종료(Early Stopping) 비활성화 (무조건 지정한 에폭까지 돌게 함)
        )
    finally:
        os.chdir(prev_cwd)

    trained_best = os.path.join(_ROOT, "models", "canon_fast_yolo", "weights", "best.pt")
    # 최종 지표 전부 추출 (MASK 지표 우선)
    metrics = {}
    try:
        rd = results.results_dict
        metrics = {
            "map50":     float(rd.get("metrics/mAP50(M)",     rd.get("metrics/mAP50(B)", 0.0))),
            "map50_95":  float(rd.get("metrics/mAP50-95(M)",  rd.get("metrics/mAP50-95(B)", 0.0))),
            "precision": float(rd.get("metrics/precision(M)", rd.get("metrics/precision(B)", 0.0))),
            "recall":    float(rd.get("metrics/recall(M)",    rd.get("metrics/recall(B)", 0.0))),
            "box_loss":  float(rd.get("train/seg_loss",       rd.get("train/box_loss", 0.0))),
        }
    except Exception:
        pass

    map50 = metrics.get("map50", 0.0)

    # ⑥ ONNX 자동 변환 — 추론 속도 2배 향상
    try:
        if os.path.exists(trained_best):
            if progress_cb: progress_cb(98, "ONNX 변환 중...")
            onnx_model = YOLO(trained_best)
            onnx_path = onnx_model.export(format="onnx", imgsz=640, simplify=True, opset=17)
            if progress_cb: progress_cb(99, f"ONNX 변환 완료: {os.path.basename(str(onnx_path))}")
    except Exception as ex:
        print(f"[train_yolo] ONNX 변환 실패 (무시): {ex}")

    if progress_cb: progress_cb(100, f"Training done! mAP50={map50:.3f}")
    return {"model_path": trained_best, "map50": map50, "metrics": metrics, "error": ""}






if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--mode", type=str, default="resume",
                        choices=["scratch", "resume"],
                        help="scratch=처음부터, resume=이어서(기본)")
    args = parser.parse_args()

    def _cb(pct, msg):
        # GUI subprocess 파서 형식: "[PCT%] 메시지"
        print(f"[{pct}%] {msg}", flush=True)

    result = run_yolo_training(epochs=args.epochs, mode=args.mode, progress_cb=_cb)
    if result.get("error"):
        print(f"[-1%] 오류: {result['error']}", flush=True)
        sys.exit(1)
    else:
        print(f"[100%] 완료! mAP50={result.get('map50', 0):.3f}", flush=True)
        sys.exit(0)

