#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Prepare Windows to run Cammy Control Center (C3) in WSL2 (docs/WINDOWS.md, step 1).

.DESCRIPTION
  1. Checks Windows 11 22H2 or later (mirrored networking and WSLg).
  2. Installs or updates WSL and the Ubuntu distribution.
  3. Turns on mirrored networking in %USERPROFILE%\.wslconfig, so WSL shares the PC's
     home-network address and C3 can find the camera.
  4. Adds a logon task that keeps the Ubuntu distribution running, so C3 keeps Cammy
     connected without an open terminal. With -LinuxDiskModel it first attaches the
     Linux project disk (dual boot only).
  It changes nothing inside Ubuntu: run tools/windows/setup-c3-wsl.sh there next.
  Status: v1.0-alpha1, not yet run on a Windows PC.

.PARAMETER Distro
  WSL distribution to use. Default Ubuntu-24.04.

.PARAMETER LinuxDiskModel
  Dual boot only: part of the model name of a whole disk holding an ext4 partition with
  the CammyOS checkout (for example "NM790"). It is attached with `wsl --mount` at every
  logon. Leave empty when the checkout lives inside WSL.

.PARAMETER Partition
  Partition number on that disk. Default 1.

.PARAMETER NoLogonTask
  Skip the logon task.

.EXAMPLE
  Set-ExecutionPolicy -Scope Process Bypass; .\Setup-CammyWSL.ps1

.EXAMPLE
  .\Setup-CammyWSL.ps1 -LinuxDiskModel NM790
#>
[CmdletBinding()]
param(
    [string]$Distro = 'Ubuntu-24.04',
    [string]$LinuxDiskModel = '',
    [int]$Partition = 1,
    [switch]$NoLogonTask
)
$ErrorActionPreference = 'Stop'
function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Ok($text) { Write-Host "  ok    $text" -ForegroundColor Green }
function Todo($text) { Write-Host "  TODO  $text" -ForegroundColor Yellow }

Step 'Windows version'
$build = [int](Get-CimInstance Win32_OperatingSystem).BuildNumber
if ($build -ge 22621) { Ok "build $build" }
else { Todo "build $build: Windows 11 22H2 (22621) or later is needed for mirrored networking and the CCC window. Update Windows first."; exit 1 }

Step 'WSL'
$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
& wsl.exe --status *> $null
if (-not $wsl -or $LASTEXITCODE -ne 0) {
    Write-Host '  installing WSL (no distribution yet)'
    & wsl.exe --install --no-distribution
    Todo 'WSL was installed. Restart Windows, then run this script again.'
    exit 1
}
& wsl.exe --update
Ok 'WSL is installed and up to date'

Step "Distribution $Distro"
# wsl -l -q prints UTF-16; strip the NULs before comparing.
$installed = (& wsl.exe --list --quiet) -replace "`0", '' | Where-Object { $_.Trim() -ne '' } | ForEach-Object { $_.Trim() }
if ($installed -contains $Distro) { Ok 'installed' }
else {
    Write-Host "  installing $Distro: it opens a window that asks for a new Linux user name and password"
    & wsl.exe --install -d $Distro
    Todo "Finish creating the Linux user in the $Distro window, then run this script again."
    exit 1
}

Step 'Mirrored networking (%USERPROFILE%\.wslconfig)'
$config = Join-Path $env:USERPROFILE '.wslconfig'
$lines = if (Test-Path $config) { [System.Collections.Generic.List[string]](Get-Content $config) } else { [System.Collections.Generic.List[string]]::new() }
$section = $lines.FindIndex([Predicate[string]] { param($l) $l.Trim() -eq '[wsl2]' })
if ($section -lt 0) { $lines.Add('[wsl2]'); $section = $lines.Count - 1 }
$end = $lines.Count
for ($i = $section + 1; $i -lt $lines.Count; $i++) { if ($lines[$i].Trim().StartsWith('[')) { $end = $i; break } }
$key = -1
for ($i = $section + 1; $i -lt $end; $i++) { if ($lines[$i] -match '^\s*networkingMode\s*=') { $key = $i } }
if ($key -ge 0) {
    if ($lines[$key] -match '=\s*mirrored\s*$') { Ok 'already mirrored'; $changed = $false }
    else { $lines[$key] = 'networkingMode=mirrored'; $changed = $true }
} else { $lines.Insert($section + 1, 'networkingMode=mirrored'); $changed = $true }
if ($changed) {
    if (Test-Path $config) { Copy-Item $config "$config.bak" -Force }
    Set-Content -Path $config -Value $lines -Encoding ascii
    & wsl.exe --shutdown
    Ok "set networkingMode=mirrored (previous file kept as .wslconfig.bak); WSL restarted"
}

$mountLine = ''
if ($LinuxDiskModel) {
    Step "Linux project disk ($LinuxDiskModel)"
    $disk = @(Get-Disk | Where-Object { $_.FriendlyName -like "*$LinuxDiskModel*" })
    if ($disk.Count -ne 1) { Todo "found $($disk.Count) disks matching '$LinuxDiskModel'; give a more specific -LinuxDiskModel"; exit 1 }
    $n = $disk[0].Number
    if (@(Get-Partition -DiskNumber $n -ErrorAction SilentlyContinue | Where-Object DriveLetter).Count -gt 0) {
        Todo "disk $n has a Windows drive letter; it is not the Linux-only disk"; exit 1
    }
    $mountLine = "wsl.exe --mount \\.\PHYSICALDRIVE$n --partition $Partition --type ext4 2>`$null"
    Ok "disk $n ($($disk[0].FriendlyName)): appears in WSL as /mnt/wsl/PHYSICALDRIVE${n}p$Partition"
    Write-Host "  In Ubuntu, run setup-c3-wsl.sh with: --lab-from /mnt/wsl/PHYSICALDRIVE${n}p$Partition --venv .venv-wsl"
}

if (-not $NoLogonTask) {
    Step 'Logon task "CammyOS WSL"'
    $dir = Join-Path $env:LOCALAPPDATA 'CammyOS'
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $starter = Join-Path $dir 'start-wsl.ps1'
    # Keeps one process alive in the distribution so systemd (and C3) keep running.
    Set-Content -Path $starter -Encoding ascii -Value @"
# Written by Setup-CammyWSL.ps1. Attaches the Linux disk (dual boot) and keeps $Distro running.
$mountLine
wsl.exe -d $Distro --exec /bin/sh -c 'exec sleep infinity'
"@
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$starter`""
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
    $level = if ($LinuxDiskModel) { 'Highest' } else { 'Limited' }   # wsl --mount needs administrator rights
    $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel $level
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName 'CammyOS WSL' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Start-ScheduledTask -TaskName 'CammyOS WSL'
    Ok "registered and started ($starter)"
}

Step 'Next'
Write-Host "  Open $Distro from the Start menu and follow docs/WINDOWS.md, step 2."
