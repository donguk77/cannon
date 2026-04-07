"""벤치마크: PyTorch vs ONNX 추론 속도 비교"""
import time, os, sys, cv2, numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(_ROOT, "bench_result.txt")

img_dir = os.path.join(_ROOT, "data", "yolo", "images", "train")
imgs = [f for f in os.listdir(img_dir) if f.endswith(".jpg")][:1]
buf = np.fromfile(os.path.join(img_dir, imgs[0]), dtype=np.uint8)
frame = cv2.imdecode(buf, cv2.IMREAD_COLOR)

lines = [f"Image: {frame.shape}"]

from ultralytics import YOLO

# PyTorch
pt_path = os.path.join(_ROOT, "models", "canon_fast_yolo", "weights", "best.pt")
m1 = YOLO(pt_path)
for _ in range(3): m1(frame, verbose=False)
t1 = []
for _ in range(10):
    s = time.perf_counter(); m1(frame, verbose=False); t1.append((time.perf_counter()-s)*1000)
a1 = sum(t1)/len(t1)
lines.append(f"PyTorch: {a1:.1f}ms (avg 10)")

# ONNX
ox_path = os.path.join(_ROOT, "models", "canon_fast_yolo", "weights", "best.onnx")
if os.path.exists(ox_path):
    m2 = YOLO(ox_path, task="segment")
    for _ in range(3): m2(frame, verbose=False)
    t2 = []
    for _ in range(10):
        s = time.perf_counter(); m2(frame, verbose=False); t2.append((time.perf_counter()-s)*1000)
    a2 = sum(t2)/len(t2)
    lines.append(f"ONNX:    {a2:.1f}ms (avg 10)")
    lines.append(f"Speedup: {a1/a2:.2f}x ({a1-a2:.1f}ms saved)")
else:
    lines.append("ONNX: best.onnx not found")

with open(OUT, "w") as f:
    f.write("\n".join(lines))
print("\n".join(lines))
