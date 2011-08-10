@echo off
if exist C:\Python26\python.exe (
    C:\Python26\python.exe "%~dp0\clcache.py" %*
) else (
    python.exe "%~dp0\clcache.py" %*
)
