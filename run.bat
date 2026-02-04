@echo off
cd /d "%~dp0"
python main.py 2>nul || py -3.12 main.py
