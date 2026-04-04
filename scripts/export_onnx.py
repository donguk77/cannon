"""
export_onnx.py — best.pt → ONNX 변환 스크립트
ultralytics의 export() 기능을 사용하여 ONNX 파일을 생성합니다.

사용법:
  python scripts/export_onnx.py
  python scripts/export_onnx.py --half        # FP16 (GPU 전용)
  python scripts/export_onnx.py --int8        # INT8 양자화 (CPU 가속)
"""
import os, sys, argparse

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BEST_PT = os.path.join(_ROOT, "models", "canon_fast_yolo", "weights", "best.pt")

def main():
    parser = argparse.ArgumentParser(description="YOLO best.pt → ONNX 변환")
    parser.add_argument("--model", default=BEST_PT, help="변환할 .pt 파일 경로")
    parser.add_argument("--int8", action="store_true", help="INT8 양자화 적용")
    parser.add_argument("--half", action="store_true", help="FP16 적용 (GPU 전용)")
    parser.add_argument("--imgsz", type=int, default=640, help="입력 이미지 크기")
    args = parser.parse_args()

    if not os.path.exists(args.model):
        print(f"[export_onnx] 모델을 찾을 수 없습니다: {args.model}")
        sys.exit(1)

    from ultralytics import YOLO
    model = YOLO(args.model)

    # ONNX 변환 실행
    print(f"[export_onnx] 변환 시작: {args.model}")
    print(f"[export_onnx] INT8={args.int8}, FP16={args.half}, imgsz={args.imgsz}")

    export_args = {
        "format": "onnx",
        "imgsz": args.imgsz,
        "simplify": True,       # ONNX 그래프 최적화
        "opset": 17,            # ONNX opset 버전
    }

    if args.half:
        export_args["half"] = True
    if args.int8:
        export_args["int8"] = True

    result = model.export(**export_args)
    print(f"[export_onnx] ✅ 변환 완료: {result}")

    # 변환된 파일을 weights/ 폴더에 유지
    onnx_path = args.model.replace(".pt", ".onnx")
    if os.path.exists(onnx_path):
        size_mb = os.path.getsize(onnx_path) / (1024 * 1024)
        print(f"[export_onnx] 📦 ONNX 파일: {onnx_path} ({size_mb:.1f} MB)")
    else:
        print(f"[export_onnx] ⚠️ ONNX 파일 경로 확인: {result}")


if __name__ == "__main__":
    main()
