@echo off
rem claude-chat 一鍵檢查：Python 風格與語法（ruff）、前端語法（node --check）、自動測試（pytest）。
rem 任何一項失敗就停並回傳 1；全部通過印 ALL OK。改完程式跑這個，再重啟伺服器。
setlocal
cd /d "%~dp0"
set PY=.venv\Scripts\python.exe
echo [1/3] ruff check
%PY% -m ruff check . || goto :fail
echo [2/3] node --check
for %%f in (*.js static\js\*.js hooks\*.js) do (node --check "%%f" || goto :fail)
echo [3/3] pytest
%PY% -m pytest || goto :fail
echo.
echo ALL OK
exit /b 0
:fail
echo.
echo CHECK FAILED
exit /b 1
