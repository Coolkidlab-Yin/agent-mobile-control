@echo off
rem claude-chat 一鍵檢查：Python 風格與語法（ruff）、前端語法（node --check）、自動測試（pytest）。
rem 任何一項失敗就停並回傳 1；全部通過印 ALL OK。改完程式跑這個，再重啟伺服器。
rem   check.cmd      → 幾秒：風格、語法、單元測試（跳過要起瀏覽器的 ui 組）
rem   check.cmd ui   → 再加手機介面測試：headless Chromium 模擬 iPhone 實際點右滑、看圖、檔案檢視、問題卡（約 20 秒）
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
set PYTEST_ARGS=-m "not ui"
if /i "%~1"=="ui" set PYTEST_ARGS=
echo [1/3] ruff check
%PY% -m ruff check . || goto :fail
echo [2/3] node --check
for %%f in (*.js static\js\*.js hooks\*.js) do (node --check "%%f" || goto :fail)
echo [3/3] pytest %PYTEST_ARGS%
%PY% -m pytest %PYTEST_ARGS% || goto :fail
echo.
echo ALL OK
exit /b 0
:fail
echo.
echo CHECK FAILED
exit /b 1
