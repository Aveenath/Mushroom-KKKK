@echo off
echo ========================================================
echo         STARTING MUSHROOM FARM OS
echo ========================================================
echo.

:: Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to PATH. 
    echo Please install Python 3.9 or newer from python.org and check "Add to PATH".
    pause
    exit
)

echo [1/2] Checking and installing required packages... (This may take a minute)
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [ERROR] Failed to install requirements. Please check your internet connection.
    pause
    exit
)

echo [2/2] Starting the Live Dashboard...
echo.
echo ========================================================
echo The application will now open in your web browser.
echo Do NOT close this black window while using the app!
echo ========================================================
echo.

:: Run the Streamlit application
streamlit run app.py

pause
