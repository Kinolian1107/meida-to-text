' Hidden launcher: start Ubuntu-24.04 and keep it alive after Windows logon.
' Installed to %LOCALAPPDATA%\media2text\wsl-autostart.vbs by register-wsl-autostart.ps1.
Option Explicit
Dim sh, cmd
Set sh = CreateObject("WScript.Shell")
cmd = "C:\Windows\System32\wsl.exe -d Ubuntu-24.04 -u root --shell-type login --exec /home/kino/git/media2text/scripts/wsl-autostart.sh"
' Wait so Task Scheduler can restart WSL if this process dies.
sh.Run cmd, 0, True
