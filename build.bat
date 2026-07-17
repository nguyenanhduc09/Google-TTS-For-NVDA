@echo off
setlocal enabledelayedexpansion
set "EXIT_CODE=0"

echo ============================================
echo   Google TTS For NVDA - Add-on Builder
echo ============================================
echo.

cd /d "%~dp0"

:: --------------- Read version from manifest.ini ---------------
set "VERSION="
for /f "tokens=1,* delims==" %%A in ('findstr /b "version" googleTtsForNvda\manifest.ini') do (
    set "VERSION=%%B"
)
:: Trim leading/trailing spaces
for /f "tokens=*" %%V in ("!VERSION!") do set "VERSION=%%V"

if "!VERSION!"=="" (
    echo [ERROR] Could not read version from manifest.ini.
    set "EXIT_CODE=1"
    goto cleanup_and_exit
)
echo Version: !VERSION!
echo.

:: --------------- Clean build artifacts ---------------
echo [1/8] Cleaning build artifacts...
if exist "__pycache__" (
    rmdir /s /q "__pycache__" 2>nul
    echo       Removed __pycache__
)
for /d /r "googleTtsForNvda" %%D in (__pycache__) do (
    if exist "%%D" (
        rmdir /s /q "%%D" 2>nul
        echo       Removed %%D
    )
)
if exist "googleTtsForNvda\googleTtsForNvda.nvda-addon" (
    del /f /q "googleTtsForNvda\googleTtsForNvda.nvda-addon" 2>nul
    echo       Removed stale .nvda-addon from source tree.
)
echo       Done.
echo.

:: --------------- Merge conflict marker check ---------------
echo [2/8] Checking for unresolved merge conflict markers...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$patterns = '*.py','*.js','*.html','*.ini','*.json','*.bat','*.md','*.po','*.pot'; $files = @(Get-ChildItem -Path 'googleTtsForNvda' -Recurse -File -Include $patterns); $files += Get-Item 'build.bat','AGENTS.md','readme.md','TRANSLATING.md','build_i18n.py'; $matches = $files | Select-String -Pattern '^(<<<<<<<|=======|>>>>>>>)'; if ($matches) { $matches | ForEach-Object { Write-Host ('      [ERROR] {0}:{1}: {2}' -f $_.Path, $_.LineNumber, $_.Line.Trim()) }; exit 1 }"
if errorlevel 1 (
    echo [ERROR] Unresolved merge conflict markers found.
    set "EXIT_CODE=1"
    goto cleanup_and_exit
)
echo       Passed.
echo.

:: --------------- Build translations ---------------
echo [3/8] Building translations...
python build_i18n.py --all-languages
if errorlevel 1 (
    echo [ERROR] Translation build failed.
    set "EXIT_CODE=1"
    goto cleanup_and_exit
)
echo       Passed.
echo.

:: --------------- Python syntax check ---------------
echo [4/8] Checking Python syntax...
python -m compileall -q googleTtsForNvda
if errorlevel 1 (
    echo [ERROR] Python syntax check failed.
    set "EXIT_CODE=1"
    goto cleanup_and_exit
)
echo       Passed.
echo.

:: --------------- JavaScript syntax check ---------------
echo [5/8] Checking JavaScript syntax...
node --check googleTtsForNvda\synthDrivers\googleTtsForNvda\web\bridgeHarness.js
if errorlevel 1 (
    echo [ERROR] JavaScript syntax check failed.
    set "EXIT_CODE=1"
    goto cleanup_and_exit
)
echo       Passed.
echo.

:: --------------- Verify no .zvoice in source ---------------
echo [6/8] Verifying no .zvoice files in source tree...
set "FOUND_ZVOICE=0"
for /r "googleTtsForNvda" %%F in (*.zvoice) do (
    echo       [ERROR] Found .zvoice file: %%F
    set "FOUND_ZVOICE=1"
)
if "!FOUND_ZVOICE!"=="1" (
    echo [ERROR] Voice data files must not be in the source tree.
    set "EXIT_CODE=1"
    goto cleanup_and_exit
)
echo       Clean - no .zvoice files found.
echo.

:: --------------- Clean __pycache__ created by compileall ---------------
echo [7/8] Cleaning __pycache__ created by syntax check...
if exist "__pycache__" rmdir /s /q "__pycache__" 2>nul
for /d /r "googleTtsForNvda" %%D in (__pycache__) do (
    if exist "%%D" rmdir /s /q "%%D" 2>nul
)
echo       Done.
echo.

:: --------------- Package the add-on ---------------
set "OUTPUT=dist\googleTtsForNvda-!VERSION!.nvda-addon"
echo [8/8] Packaging add-on to %OUTPUT% ...

if not exist "dist" mkdir dist

:: Remove old build with same version if present
if exist "!OUTPUT!" del /f /q "!OUTPUT!"

:: Use PowerShell to create a temporary ZIP archive first, as Compress-Archive requires .zip extension
set "TEMP_ZIP=dist\temp_build.zip"
if exist "!TEMP_ZIP!" del /f /q "!TEMP_ZIP!"

powershell -NoProfile -Command "Compress-Archive -Path 'googleTtsForNvda\*' -DestinationPath '!TEMP_ZIP!' -Force"
if errorlevel 1 (
    echo [ERROR] Packaging failed.
    set "EXIT_CODE=1"
    goto cleanup_and_exit
)

:: Move/Rename the temporary zip to the final destination
move /y "!TEMP_ZIP!" "!OUTPUT!" >nul
if errorlevel 1 (
    echo [ERROR] Failed to rename package to .nvda-addon.
    set "EXIT_CODE=1"
    goto cleanup_and_exit
)

:: Show file size
for %%A in ("!OUTPUT!") do (
    set "SIZE=%%~zA"
)
echo       Created: !OUTPUT!
echo       Size:    !SIZE! bytes
echo.

echo ============================================
echo   Build complete: !OUTPUT!
echo ============================================
exit /b 0

:cleanup_and_exit
echo.
echo Cleaning __pycache__ before exit...
if exist "__pycache__" rmdir /s /q "__pycache__" 2>nul
for /d /r "googleTtsForNvda" %%D in (__pycache__) do (
    if exist "%%D" rmdir /s /q "%%D" 2>nul
)
exit /b !EXIT_CODE!
