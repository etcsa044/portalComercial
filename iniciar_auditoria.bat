@echo off
echo ========================================================
echo   Iniciando Sistema de Auditoria de Ventas y Retencion  
echo ========================================================
echo.
echo Abriendo el servidor local...
echo La aplicacion se abrira en tu navegador automaticamente.
echo (Manten esta ventana negra abierta mientras uses la app)
echo.

cd /d "%~dp0"
uv run streamlit run app.py

pause
