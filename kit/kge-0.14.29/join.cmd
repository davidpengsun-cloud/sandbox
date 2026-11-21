@echo off
cd /d "%~dp0"
copy /b images\kge-0.14.29.tar.part-aa + images\kge-0.14.29.tar.part-ab + images\kge-0.14.29.tar.part-ac + images\kge-0.14.29.tar.part-ad images\kge-0.14.29.tar >nul
copy /b images\kge-portal-0.14.29.tar.part-aa + images\kge-portal-0.14.29.tar.part-ab + images\kge-portal-0.14.29.tar.part-ac images\kge-portal-0.14.29.tar >nul
echo Joined. Compare these with the lines for them in SHA256SUMS:
certutil -hashfile images\kge-0.14.29.tar SHA256
certutil -hashfile images\kge-portal-0.14.29.tar SHA256
pause
