# SPD Report Generator

Windows에서 SPD·Touchstone·선택 DCR 입력으로 Excel 전원 무결성 보고서를 만듭니다. 채널·사이트 연결, 그룹별 그래프, 활성 CAP 수량과 배치도를 지원하며 모든 입력은 로컬에서 처리합니다.

[Windows x64 릴리스](https://github.com/yunhyok/report-generator/releases/latest)에서 설치 파일(`*-Setup-x64.exe`)을 내려받아 실행하세요. 설치에는 관리자 권한이 필요하며, 기본 경로는 `C:\Program Files\SPD Report Generator`입니다. 설치 화면에서 다른 드라이브나 폴더를 선택할 수 있습니다. Python 설치는 필요하지 않습니다. 설치 후 시작 메뉴의 **SPD Report Generator**를 엽니다.

프로그램에서 **F1**을 누르거나 **Help → 사용 설명서**를 선택하면 내장 뷰어에서 사용 순서·계산식·지원 범위를 확인할 수 있습니다. 시작 메뉴의 **SPD Report Generator → Help**에서도 같은 설명서를 엽니다. [한국어 HTML 설명서 원본](src/report_generator/docs/help.html)

**2 채널·그룹 → Excel 양식 저장…**으로 사용 중인 원래 채널명·사이트를 내보냅니다. 첫 번째 시트의 `priority`(순위·구분명), `group`(상세 그래프 묶음), `net`(보고서 표시명) 중 필요한 값만 입력할 것. 선택 열은 하나 이상 남기면 되며, 행·열 순서는 자유입니다. 1행 제목으로 열을 찾으므로 원래 채널명·사이트는 유지할 것. 저장한 `.xlsx` 하나를 **2 채널·그룹** 화면에 끌어다 놓거나 **Excel 설정 불러오기…**로 열 수 있습니다. 빈 셀·생략한 열·파일에 없는 행은 기존 값 유지. 순위는 채널 전체에, 그룹·표시명은 채널·사이트별 적용. 다음 작업에서도 쓰려면 프로젝트를 저장할 것.

`net`은 보고서에 보일 이름만 변경합니다. **SPD Net (실제 연결)**, CAP 연결·집계, 배치도와 수치 표의 원래 식별자는 유지합니다. 같은 상세 그룹의 모든 대상 행에 동일한 표시명을 입력하면 그래프·Excel 상세 시트 제목과 탭에 그 이름을 사용합니다. 표시명이 서로 다르거나 일부만 있으면 원래 그룹 제목을 유지하고 범례에 표시명·원래 채널·사이트를 표시합니다. 중복 탭 이름에는 번호를 붙여 구분합니다. 표시명은 화면의 **보고서 표시명** 열에서도 직접 입력·삭제 가능. Excel의 빈칸으로는 기존 표시명을 지우지 않습니다.

개발 환경은 Python 3.12 이상입니다. PowerShell에서:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install -e . pytest
.\.venv\Scripts\python -m report_generator.app
.\.venv\Scripts\python -m pytest
```

설치 파일을 빌드하려면 Python 3.12.10 x64 환경에 `requirements-build.txt`와 Inno Setup 6 또는 7을 설치한 뒤 `python scripts/build_release.py`를 실행합니다. 필요하면 `ISCC` 환경 변수로 컴파일러 경로를 지정하세요. 빌드는 실행 파일의 자체 검사를 통과한 후 설치 EXE와 SHA256 파일을 `dist/`에 저장합니다.

화면 입력 없이 검증하려면 `python scripts/validate_reports.py --validate-project example.report.json --output checked.xlsx --reference reference.xlsx`를 실행하세요. 설치 EXE도 같은 인자를 지원합니다. `--reference`는 선택 사항이며 결과 옆에 HTML·JSON 검증 기록을 저장합니다. 자세한 내용은 HTML 설명서의 **화면 없이 생성·검증**에 있습니다.

실제 설계·모델·측정 입력과 생성 보고서는 저장소에 포함하지 않습니다. 테스트는 가상 데이터를 사용합니다. 코드와 설명서는 [MIT License](LICENSE)를 따르며, 배포물에는 사용 라이브러리의 라이선스 고지도 동봉됩니다.
