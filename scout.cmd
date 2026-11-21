@echo off
REM ProspectScout launcher - opens the local app in your browser.
setlocal
cd /d "%~dp0"
where py >nul 2>nul && (py -m prospectscout %* & goto :eof)
python -m prospectscout %*
endlocal
