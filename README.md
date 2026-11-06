# SPD Report Generator

Windows에서 SPD·Touchstone·선택 DCR 입력으로 Excel 전원 무결성 보고서를 만듭니다. 채널·사이트 연결, 그룹별 그래프, 활성 CAP 수량과 배치도를 지원하며 모든 입력은 로컬에서 처리합니다.

[Windows x64 릴리스](https://github.com/yunhyok/report-generator/releases/latest)에서 설치 파일(`*-Setup-x64.exe`)을 내려받아 실행하세요. 설치에는 관리자 권한이 필요하며, 기본 경로는 `C:\Program Files\SPD Report Generator`입니다. 설치 화면에서 다른 드라이브나 폴더를 선택할 수 있습니다. Python 설치는 필요하지 않습니다. 설치 후 시작 메뉴의 **SPD Report Generator**를 엽니다.

프로그램에서 **F1**을 누르거나 **Help → 사용 설명서**를 선택하면 내장 뷰어에서 사용 순서·계산식·지원 범위를 확인할 수 있습니다. 시작 메뉴의 **SPD Report Generator → Help**에서도 같은 설명서를 엽니다. [한국어 HTML 설명서 원본](src/report_generator/docs/help.html)

**2 채널·그룹 → Excel 양식 저장…**으로 사용 중인 원래 채널명·사이트를 내보냅니다. 첫 번째 시트의 `priority`(순위·구분명), `group`(상세 그래프 묶음), `net`(보고서 표시명) 중 필요한 값만 입력할 것. 선택 열은 하나 이상 남기면 되며, 행·열 순서는 자유입니다. 1행 제목으로 열을 찾으므로 원래 채널명·사이트는 유지할 것. 저장한 `.xlsx` 하나를 **2 채널·그룹** 화면에 끌어다 놓거나 **Excel 설정 불러오기…**로 열 수 있습니다. 빈 셀·생략한 열·파일에 없는 행은 기존 값 유지. 순위는 채널 전체에, 그룹·표시명은 채널·사이트별 적용. 다음 작업에서도 쓰려면 프로젝트를 저장할 것.

`net`에 입력한 이름은 PI 요약·상세 표, CAP 수량 표, DCR 표의 **Channel** 칸에 표시합니다. 이름을 바꾼 셀의 메모에서 원래 채널명을 확인할 수 있습니다. CAP 집계와 배치도는 실제 SPD Net을 사용합니다. 그룹의 모든 표시명이 같으면 그래프와 상세 시트의 제목으로 쓰고, 서로 다르면 그룹 제목을 유지하고 곡선별 이름을 범례에 표시합니다. 기존 표시명을 지우려면 화면에서 **보고서 표시명** 칸을 비우세요.

**3 CAP·DCR → DCR → Excel 표 가져오기…**에서 측정 시트를 선택하면 사이트·채널 이름으로 값을 연결합니다. IRDrop 표는 `Name`을 채널, `SITE`를 사이트, 마지막 `DCR_IRDrop[mΩ]` 열을 측정값으로 읽습니다. 행 정렬은 자유입니다. 머리글까지 복사해 붙여넣거나 기존 매핑 없이 DCR 보고서만 만들 수도 있습니다. 숫자 한 열만 붙여넣을 때는 대상 채널과 입력 순서를 먼저 지정하세요. 확인을 누르면 기존 DCR 표를 교체합니다.

채널 순위를 켜면 DCR 표의 행, 개별 채널의 상세 그래프와 Excel 상세 시트가 숫자 순위가 낮은 채널부터 표시됩니다. 그룹을 비워 두면 채널별로, 같은 그룹명을 지정하면 한 그래프로 묶어 표시합니다.

Top·Bottom CAP 배치도는 모든 CAP가 실장된 것으로 가정한 부품 위치도입니다. 선택한 Net의 CAP 자리를 용량별 색으로 표시하며, 실제 상태에 따른 수량은 **CAP_수량** 시트에서 확인합니다.

상세 그래프는 원본이 포함하는 **1kHz ~ 2GHz** 구간을 표시합니다. 원본 주파수 점을 유지하고, 간격이 넓으면 표시용으로 보간해 주파수가 10배 증가할 때마다 최소 30간격을 확보합니다. PI 요약 시트 2행에는 실제 표시 범위가 나옵니다.

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
