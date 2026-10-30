# SPD Report Generator

Windows에서 SPD·Touchstone·선택 DCR 입력으로 Excel 전원 무결성 보고서를 만듭니다. 채널·사이트 연결, 그룹별 그래프, 활성 CAP 수량과 배치도를 지원하며 모든 입력은 로컬에서 처리합니다.

[v0.1.0 Windows x64 포터블 다운로드](https://github.com/yunhyok/report-generator/releases/tag/v0.1.0)에서 ZIP 전체를 풀고 `SPD Report Generator.exe`를 실행하세요. `_internal` 폴더도 함께 보관해야 합니다.

사용 순서·계산식·지원 범위는 [한국어 HTML 설명서](src/report_generator/docs/help.html)에 있습니다. GitHub에서는 HTML이 웹페이지로 표시되지 않으므로 파일을 내려받아 브라우저로 열거나, 프로그램의 **Help → 사용 설명서 / F1**을 사용하세요.

개발 환경은 Python 3.12 이상입니다. PowerShell에서:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e . pytest
.\.venv\Scripts\python -m report_generator.app
.\.venv\Scripts\python -m pytest
```

실제 설계·모델·측정 입력과 생성 보고서는 저장소에 포함하지 않습니다. 테스트는 가상 데이터를 사용합니다. 코드와 설명서는 [MIT License](LICENSE)를 따르며, 배포물에는 사용 라이브러리의 라이선스 고지도 동봉됩니다.
