@echo off
echo Running dependency compilation...
uv pip compile requirements.in -o requirements.txt
if %errorlevel% neq 0 (
	echo Compilation failed. Fix errors and try again.
	exit /b %errorlevel%
)
echo Compilation successful. Syncing dependencies...
uv pip sync requirements.txt
if %errorlevel% neq 0 (
	echo Sync failed.
	exit /b %errorlevel%
)
echo Dependencies successfully compiled and synced.