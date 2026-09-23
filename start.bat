@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (set "PYTHON_CMD=py -3") else (set "PYTHON_CMD=python")
%PYTHON_CMD% --version >nul 2>&1 || goto no_python
if not exist ".venv\Scripts\python.exe" (
    %PYTHON_CMD% -m venv .venv || goto failed
)
".venv\Scripts\python.exe" -c "import fastapi, uvicorn, playwright" >nul 2>&1 || ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto failed
".venv\Scripts\python.exe" launch.py
if errorlevel 1 goto failed
exit /b 0

:no_python
echo 未找到 Python 3。请先安装 Python 3.11 或更新版本，并勾选 Add python.exe to PATH。
pause
exit /b 1

:failed
echo 启动失败。请检查上方错误信息及 README.md 的故障排查部分。
pause
exit /b 1
