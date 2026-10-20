<#
.SYNOPSIS
  Build release\MediaSaver.exe (one-file, windowed).
.DESCRIPTION
  Creates / refreshes .venv, installs requirements, runs the unit tests, makes build\icon.ico, runs PyInstaller,
  copies the result to release\MediaSaver.exe and writes release\SHA256SUMS. Details: docs\BUILD.md.
.PARAMETER Shortcut
  Also create an "Media Saver" shortcut on the desktop pointing to the built exe.
.PARAMETER SkipTests
  Do not run the unit tests (default: tests must pass before the exe is built).
.PARAMETER SkipBuild
  Only prepare .venv (dev setup); no tests, no icon, no exe.
.PARAMETER PythonVersion
  Python version passed to the `py` launcher when .venv has to be created (default 3.14).
#>
[CmdletBinding()]
param(
    [switch]$Shortcut,
    [switch]$SkipTests,
    [switch]$SkipBuild,
    [string]$PythonVersion = "3.14"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

function Invoke-Checked {
    param([string]$What, [scriptblock]$Command)
    # Windows PowerShell 5.1 turns stderr text of native tools (pip, PyInstaller log to stderr) into errors when
    # output is redirected; only the exit code decides here.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Command } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "$What failed with exit code $LASTEXITCODE" }
}

# 1. virtual environment ---------------------------------------------------------------------------------------------
if (-not (Test-Path $VenvPython)) {
    Write-Host "Creating .venv with Python $PythonVersion ..."
    Invoke-Checked "venv creation" { py "-$PythonVersion" -m venv (Join-Path $Root ".venv") }
}
Write-Host "Installing requirements ..."
Invoke-Checked "pip upgrade" { & $VenvPython -m pip install --quiet --upgrade pip }
Invoke-Checked "pip install" { & $VenvPython -m pip install --quiet -r requirements.txt -r requirements-dev.txt }
& $VenvPython --version
& $VenvPython -m pip list --format=freeze | Select-String -Pattern "^(pywebview|pythonnet|pyinstaller|pillow)=="

if ($SkipBuild) { Write-Host "Environment ready (-SkipBuild)."; return }

# 2. tests -----------------------------------------------------------------------------------------------------------
if (-not $SkipTests) {
    $Scratch = Join-Path ([IO.Path]::GetTempPath()) ("media_saver_tests_" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $Scratch | Out-Null
    $env:MEDIA_SAVER_HOME = $Scratch
    try {
        Write-Host "Running unit tests ..."
        Invoke-Checked "unit tests" { & $VenvPython -m unittest discover -s tests }
    }
    finally {
        Remove-Item Env:\MEDIA_SAVER_HOME -ErrorAction SilentlyContinue
        Remove-Item -Recurse -Force $Scratch -ErrorAction SilentlyContinue
    }
}

# 3. icon ------------------------------------------------------------------------------------------------------------
Invoke-Checked "icon" { & $VenvPython build\make_icon.py }

# 4. PyInstaller -----------------------------------------------------------------------------------------------------
Write-Host "Running PyInstaller (one-file) ..."
Invoke-Checked "PyInstaller" {
    & $VenvPython -m PyInstaller --noconfirm --clean `
        --distpath (Join-Path $Root "build\dist") --workpath (Join-Path $Root "build\work") `
        (Join-Path $Root "build\MediaSaver.spec")
}

# 5. release copy ----------------------------------------------------------------------------------------------------
$Built = Join-Path $Root "build\dist\MediaSaver.exe"
if (-not (Test-Path $Built)) { throw "PyInstaller did not produce $Built" }
$ReleaseDir = Join-Path $Root "release"
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
$Release = Join-Path $ReleaseDir "MediaSaver.exe"
Copy-Item -Force $Built $Release
$SizeMb = [math]::Round((Get-Item $Release).Length / 1MB, 1)
Write-Host "Built $Release ($SizeMb MB)"

# 5b. checksum file: the self-updater verifies the downloaded exe against the "SHA256SUMS" release asset ------------------
$Hash = (Get-FileHash -Algorithm SHA256 -Path $Release).Hash.ToLowerInvariant()
$SumsLine = "$Hash  MediaSaver.exe"
[IO.File]::WriteAllText((Join-Path $ReleaseDir "SHA256SUMS"), "$SumsLine`n", (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Wrote $(Join-Path $ReleaseDir 'SHA256SUMS')"
Write-Host $SumsLine

# 6. optional desktop shortcut ---------------------------------------------------------------------------------------
if ($Shortcut) {
    $Desktop = [Environment]::GetFolderPath("Desktop")
    $Link = Join-Path $Desktop "Media Saver.lnk"
    $Shell = New-Object -ComObject WScript.Shell
    $Lnk = $Shell.CreateShortcut($Link)
    $Lnk.TargetPath = $Release
    $Lnk.WorkingDirectory = $ReleaseDir
    $Lnk.IconLocation = "$Release,0"
    $Lnk.Description = "Media Saver"
    $Lnk.Save()
    Write-Host "Shortcut created: $Link"
}
