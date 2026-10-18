@echo off
rem Fetch the latest news and open the page locally (http://localhost:8000)
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python scripts\build_news.py
start "" http://localhost:8000/
python scripts\preview_server.py
