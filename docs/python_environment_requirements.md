# Canon AI Vision v5.0 파이썬 환경 요구사항

본 문서는 `Canon AI Vision v5.0` 프로젝트의 원활한 실행과 유지보수를 위한 파이썬 환경 세팅 가이드를 제공합니다.

---

## ⚠️ 핵심 주의 사항: 파이썬 버전 (Python 3.13 미호환)
현재 시점(2026.03) 기준으로 딥러닝 핵심 라이브러리인 **PyTorch가 Python 3.13을 공식 지원하지 않습니다**. 
Python 3.13에서 YOLO 학습 또는 추론을 시도할 경우, 다음과 같은 치명적 오류가 발생합니다.

> **오류 메시지:** `[WinError 1114] DLL 초기화 루틴을 실행할 수 없습니다.` (c10.dll / torch 관련 모듈 로드 실패)

**[해결책 및 권장 사항]**
*   **권장 파이썬 버전**: **Python 3.11.x** (가장 안정적인 호환성 제공) 또는 Python 3.12.x
*   본 시스템은 시스템 기본 파이썬이 3.13 이더라도 딥러닝 연산에서 충돌을 피하기 위해, `py -3.11` 명령어를 통해 **Python 3.11 환경을 강제 격리**하여 실행하도록 설계되어 있습니다.

---

## 📦 종속성 패키지 명세 (Requirements)
명시된 패키지들은 모두 Python 3.11 환경에 설치되어야 합니다.

| 라이브러리 | 권장 버전 | 용도 및 설명 | 설치 명령어 (참고용) |
| :--- | :--- | :--- | :--- |
| **PyTorch** | `2.11.0+cpu` | AI 텐서 연산 베이스 엔진 | `pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu` |
| **Ultralytics** | `8.4.30 이상` | YOLOv8/YOLO11 등 딥러닝 모델 전/후처리 및 시스템 인터페이스 | `pip install ultralytics` |
| **OpenCV** | `4.13.0 이상` | (`opencv-python`) 캠 인식, 이미지 자르기, 색상 변환, ORB 연산 | `pip install opencv-python` |
| **Optuna** | `4.8.0 이상` | MLOps 야간 학습 파이프라인의 **베이시안 파라미터 최적화(Auto-Tuning)** | `pip install optuna` |
| **PyQt5** | `5.15+` | 사용자 화면(대시보드) UI 프레임워크 | `pip install PyQt5` |
| **기본 필수** | - | `numpy`, `pyyaml`, `requests` (제미나이 API 통신용) | `pip install numpy pyyaml requests` |

---

## 🚀 올바른 실행 방법

본 프로젝트의 루트 디렉터리에 위치한 실행 배치 파일(`1.시스템_시작하기.bat`)은 내부적으로 다음과 같이 처리합니다.

```bat
:: Python 3.11을 최우선으로 사용하여 프로그램 구동
py -3.11 gui\main_window.py
```
명령 프롬프트 환경에서 직접 실행 시에도 `python ...` 이 아니라 `py -3.11 ...` 형태로 사용할 것을 권장합니다.

---

## 🛠️ 새 환경 구성 가이드 (백지 상태에서 시작할 경우)

만약 새로운 PC나 노트북에 이 프로젝트를 옮겨간다면, 시스템 전역 설치 대신 **가상환경(venv)** 구성을 강력히 권장합니다. 충돌을 방지하고 깔끔한 관리가 가능합니다.

1.  Python 웹사이트(python.org)에서 **Python 3.11.9 (Windows 64-bit)** 버전을 다운로드하여 설치합니다.
2.  터미널(cmd 또는 PowerShell)을 열고 프로젝트 폴더로 위치를 이동합니다.
3.  아래의 명령어를 한 줄씩 복사/붙여넣기하여 `canon_env`라는 이름의 가상환경을 만들고 필수 패키지를 설치합니다.

```bat
# 1. 가상환경 생성 (canon_env 폴더가 생김)
py -3.11 -m venv canon_env

# 2. pip 최신화
.\canon_env\Scripts\python.exe -m pip install --upgrade pip

# 3. PyTorch (CPU 버전) 가볍게 설치
.\canon_env\Scripts\pip.exe install torch torchvision --index-url https://download.pytorch.org/whl/cpu

# 4. 비전 및 UI 프레임워크 한 번에 설치
.\canon_env\Scripts\pip.exe install ultralytics opencv-python PyQt5 optuna requests numpy pyyaml
```

설치가 끝났으면, 이 후 프로그램 실행 시 `py -3.11` 대신 `.\canon_env\Scripts\python.exe`를 사용하여 스크립트를 실행해 주면 됩니다. (예: `.\canon_env\Scripts\python.exe gui\main_window.py`)

의존성 설치가 완료되면 배치파일(`1.시스템_시작하기.bat`)을 통해 바로 시스템을 사용할 수 있습니다.
