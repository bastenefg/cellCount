[CmdletBinding()]
param(
    [string]$PythonExe = '',
    [switch]$SkipInstall,
    [switch]$Zip,
    [string]$ReleaseFolder = ''
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if (-not $PythonExe) {
    $PythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
        & py -3.12 -m venv (Join-Path $projectRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python 3.12 environment.' }
    }
}
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
& $PythonExe -c 'import sys; assert sys.version_info[:2] == (3, 12), "Build with Python 3.12 (recorded version: 3.12.13)."'
if ($LASTEXITCODE -ne 0) { throw 'Python version check failed.' }
$appVersion = (& $PythonExe -c 'from desktop import __version__; print(__version__)').Trim()
if ($LASTEXITCODE -ne 0 -or $appVersion -notmatch '^\d+\.\d+\.\d+$') {
    throw 'Could not determine the desktop app version.'
}
if (-not $ReleaseFolder) { $ReleaseFolder = $appVersion }
if ($ReleaseFolder -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$' -or $ReleaseFolder -in '.', '..') {
    throw 'ReleaseFolder must be a simple folder name using letters, digits, dots, underscores or hyphens.'
}
$distDirectory = Join-Path (Join-Path $projectRoot 'dist') $ReleaseFolder
$appDirectory = Join-Path $distDirectory 'Live-Dead Cell Counter'
if (Test-Path -LiteralPath $appDirectory) {
    throw "Release already exists at '$appDirectory'. Choose a new -ReleaseFolder to keep that app intact."
}

if (-not $SkipInstall) {
    & $PythonExe -m pip install -r (Join-Path $projectRoot 'requirements-build.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& $PythonExe -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed.' }
& $PythonExe -m PyInstaller --noconfirm --clean --distpath $distDirectory --workpath (Join-Path (Join-Path $projectRoot 'build') $ReleaseFolder) (Join-Path $projectRoot 'CHO_Cell_Counter.spec')
if ($LASTEXITCODE -ne 0) { throw 'App build failed.' }

Copy-Item -LiteralPath (Join-Path $projectRoot 'APP_GUIDE.md') -Destination $appDirectory -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'build\third_party_notices\THIRD_PARTY_NOTICES.md') -Destination $appDirectory -Force
Write-Host "Portable app: $(Join-Path $appDirectory 'Live-Dead Cell Counter.exe')"

if ($Zip) {
    $zipPath = Join-Path $distDirectory "Live-Dead-Cell-Counter-$appVersion-Windows-x64.zip"
    Compress-Archive -LiteralPath $appDirectory -DestinationPath $zipPath -CompressionLevel Optimal -Force
    Write-Host "Share this ZIP: $zipPath"
    Get-FileHash -LiteralPath $zipPath -Algorithm SHA256
}
