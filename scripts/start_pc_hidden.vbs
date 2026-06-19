' Launch JARVIS PC Server with NO visible window (true background mode).
' The autostart task runs this; it starts start_pc.bat hidden. All output still
' goes to jarvis_pc_server.log, so nothing is lost despite the hidden window.
Dim sh, fso, dir
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
' 0 = hidden window, False = don't wait for it to finish
sh.Run "cmd /c """ & dir & "\start_pc.bat""", 0, False
