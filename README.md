# SPD Report Generator

Windows에서 SPD·Touchstone·선택 DCR 입력으로 Excel 전원 무결성 보고서를 만듭니다. 채널·사이트 연결, 그룹별 그래프, 활성 CAP 수량과 배치도를 지원하며 모든 입력은 로컬에서 처리합니다.

[Windows x64 설치 프로그램 다운로드](https://github.com/yunhyok/report-generator/releases/latest)에서 `SPD-Report-Generator-v0.1.1-Setup-x64.exe`를 실행하세요. 관리자 권한이나 Python 설치 없이 사용할 수 있습니다. 설치 후 시작 메뉴의 **SPD Report Generator**를 엽니다.

사용 순서·계산식·지원 범위는 [한국어 HTML 설명서](src/report_generator/docs/help.html)에 있습니다. GitHub에서는 HTML이 웹페이지로 표시되지 않으므로 파일을 내려받아 브라우저로 열거나, 프로그램의 **Help → 사용 설명서 / F1**을 사용하세요.

개발 환경은 Python 3.12 이상입니다. PowerShell에서:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e . pytest
.\.venv\Scripts\python -m report_generator.app
.\.venv\Scripts\python -m pytest
```

설치 파일을 빌드하려면 Python 3.12.10 x64 환경에 `requirements-build.txt`와 Inno Setup 6 또는 7을 설치한 뒤 `python scripts/build_release.py`를 실행합니다. 필요하면 `ISCC` 환경 변수로 컴파일러 경로를 지정하세요. 빌드는 실행 파일의 자체 검사를 통과한 후 설치 EXE와 SHA256 파일을 `dist/`에 저장합니다.

실제 설계·모델·측정 입력과 생성 보고서는 저장소에 포함하지 않습니다. 테스트는 가상 데이터를 사용합니다. 코드와 설명서는 [MIT License](LICENSE)를 따르며, 배포물에는 사용 라이브러리의 라이선스 고지도 동봉됩니다.
