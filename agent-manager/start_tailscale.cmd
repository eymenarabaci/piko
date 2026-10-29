@echo off
title Tailscale Baslatici
cd /d "%~dp0"
echo Tailscale servisi baslatiliyor (Yonetici yetkisi istenecek)...
powershell -NoProfile -Command "Start-Process cmd -ArgumentList '/c net start Tailscale & timeout /t 2 & \"C:\Program Files\Tailscale\tailscale-ipn.exe\"' -Verb RunAs"
echo Islem tamamlandi. Sag alttaki sistem tepsisinde (saatin yaninda) Tailscale simgesi belirecektir.
timeout /t 3
