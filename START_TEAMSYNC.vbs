Option Explicit
Dim shell, fso, root, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "cmd.exe /k """ & root & "\START_TEAMSYNC.cmd"""
shell.Run cmd, 1, False
