# SPD Report Generator

Windows에서 SPD·Touchstone·선택 DCR 입력으로 Excel 전원 무결성 보고서를 만듭니다. 채널·사이트 연결, 그룹별 그래프, 활성 CAP 수량과 배치도를 지원하며 모든 입력은 로컬에서 처리합니다.

[Windows x64 릴리스](https://github.com/yunhyok/report-generator/releases/latest)에서 설치 파일(`*-Setup-x64.exe`)을 내려받아 실행하세요. 설치에는 관리자 권한이 필요하며, 기본 경로는 `C:\Program Files\SPD Report Generator`입니다. 설치 화면에서 다른 드라이브나 폴더를 선택할 수 있습니다. Python 설치는 필요하지 않습니다. 설치 후 시작 메뉴의 **SPD Report Generator**를 엽니다.

프로그램에서 **F1**을 누르거나 **Help → 사용 설명서**를 선택하면 내장 뷰어에서 사용 순서·계산식·지원 범위를 확인할 수 있습니다. 시작 메뉴의 **SPD Report Generator → Help**에서도 같은 설명서를 엽니다. [한국어 HTML 설명서 원본](src/report_generator/docs/help.html)

**2 채널·그룹 → Excel 양식 저장…**으로 사용 중인 채널·사이트를 내보내고, 첫 번째 시트의 `priority`·`group`을 입력한 뒤 **Excel 설정 불러오기…**로 반영할 수 있습니다. 두 설정 중 하나만 입력해도 되며 빈 셀·생략한 열은 기존 값을 유지합니다. 순위는 채널 전체, 그룹은 채널·사이트별로 적용됩니다. 불러온 설정은 프로젝트로 저장하세요. 자세한 절차와 예제는 설명서의 **Excel에서 순위·그룹 한꺼번에 입력하기**를 참고하세요.

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
