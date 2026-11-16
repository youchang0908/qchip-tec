#requires -Version 5.1
param([switch]$Worker, [string]$FixtureRoot)
$ErrorActionPreference = 'Stop'

if (-not $Worker) {
    # Run in a child with private environment variables, never the user's profile.
    $tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    $fixture = Join-Path $tempBase ('qchip-installer-test-' + [Guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $fixture
    try {
        $info = New-Object Diagnostics.ProcessStartInfo
        $info.FileName = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $info.Arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Worker -FixtureRoot "' + $fixture + '"'
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardOutput = $true
        $info.RedirectStandardError = $true
        $info.EnvironmentVariables['LOCALAPPDATA'] = Join-Path $fixture 'initial-local'
        $info.EnvironmentVariables['APPDATA'] = Join-Path $fixture 'initial-roaming'
        $process = [Diagnostics.Process]::Start($info)
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(45000)) {
            $process.Kill()
            $process.WaitForExit()
            throw 'Isolated installer test timed out.'
        }
        Write-Output $stdout.Result
        if ($process.ExitCode -ne 0) { throw $stderr.Result }
    } finally {
        $full = [IO.Path]::GetFullPath($fixture)
        if ([IO.Path]::GetDirectoryName($full) -ine $tempBase -or
            [IO.Path]::GetFileName($full) -cnotmatch '^qchip-installer-test-[a-f0-9]{32}$') {
            throw 'Refusing unexpected test cleanup path.'
        }
        Remove-Item -LiteralPath $full -Recurse -Force
    }
    return
}

function Assert-Check([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}
$root = [IO.Path]::GetFullPath($FixtureRoot)
Assert-Check ($env:LOCALAPPDATA -ceq (Join-Path $root 'initial-local')) 'Child LOCALAPPDATA was not isolated.'
Assert-Check ($env:APPDATA -ceq (Join-Path $root 'initial-roaming')) 'Child APPDATA was not isolated.'
$installer = Join-Path (Split-Path $PSScriptRoot -Parent) 'install.ps1'
$global:QchipInstallerTestRequests = 0
$global:QchipInstallerTestLaunches = 0
$global:QchipInstallerTestCorrupt = $false

function Start-Process { $global:QchipInstallerTestLaunches++; throw 'No EXE may be launched by installer tests.' }
function Invoke-RestMethod {
    param([string]$Uri, $Headers)
    $global:QchipInstallerTestRequests++
    Assert-Check ($Uri -ceq 'https://api.github.com/repos/youchang0908/qchip-tec/releases/latest') 'Unexpected API request.'
    return $global:QchipInstallerTestFixtureRelease
}
function Invoke-WebRequest {
    param([switch]$UseBasicParsing, [string]$Uri, $Headers, [string]$OutFile)
    $global:QchipInstallerTestRequests++
    $prefix = $root.TrimEnd('\') + '\'
    Assert-Check ([IO.Path]::GetFullPath($OutFile).StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) 'Download escaped isolated profile.'
    if ($Uri -ceq 'https://github.com/youchang0908/qchip-tec/releases/download/v1.0.1/update.json') {
        Copy-Item -LiteralPath $global:QchipInstallerTestManifestPath -Destination $OutFile
    } elseif ($Uri -ceq 'https://github.com/youchang0908/qchip-tec/releases/download/v1.0.1/Qchip-TEC-Windows-x64.zip') {
        Copy-Item -LiteralPath $global:QchipInstallerTestArchivePath -Destination $OutFile
        if ($global:QchipInstallerTestCorrupt) {
            $bytes = [IO.File]::ReadAllBytes($OutFile)
            $bytes[$bytes.Length - 1] = $bytes[$bytes.Length - 1] -bxor 1
            [IO.File]::WriteAllBytes($OutFile, $bytes)
        }
    } else { throw 'Unexpected download request; real network is forbidden.' }
}

function Set-TestProfile([string]$Case) {
    $env:LOCALAPPDATA = Join-Path $root ($Case + '\local')
    $env:APPDATA = Join-Path $root ($Case + '\roaming')
    $null = New-Item -ItemType Directory -Path $env:LOCALAPPDATA, $env:APPDATA -Force
    Set-Location -LiteralPath (Join-Path $root $Case)
    $global:QchipInstallerTestRequests = 0
}

$payload = Join-Path $root 'payload'
$null = New-Item -ItemType Directory -Path $payload
[IO.File]::WriteAllText((Join-Path $payload 'Qchip TEC.exe'), 'FAKE TEST BYTES - NEVER EXECUTE')
[IO.File]::WriteAllText((Join-Path $payload 'version.json'), '{"product":"QchipTEC","version":"1.0.1"}')
[IO.File]::WriteAllText((Join-Path $payload 'LICENSE.txt'), 'Fixture license')
[IO.File]::WriteAllText((Join-Path $payload 'THIRD_PARTY_NOTICES.txt'), 'Fixture notices')
$records = @(Get-ChildItem -LiteralPath $payload -File | ForEach-Object {
    @{ path = $_.Name; bytes = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
})
Add-Type -AssemblyName System.IO.Compression.FileSystem
$global:QchipInstallerTestArchivePath = Join-Path $root 'fixture.zip'
[IO.Compression.ZipFile]::CreateFromDirectory($payload, $global:QchipInstallerTestArchivePath)
$archive = Get-Item -LiteralPath $global:QchipInstallerTestArchivePath
$manifest = @{
    schema = 1; product = 'QchipTEC'; platform = 'windows-x64'; version = '1.0.1'
    archive = @{ name = 'Qchip-TEC-Windows-x64.zip'; bytes = $archive.Length; sha256 = (Get-FileHash -LiteralPath $archive.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    files = $records
}
$global:QchipInstallerTestManifestPath = Join-Path $root 'fixture-update.json'
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $global:QchipInstallerTestManifestPath -Encoding UTF8
$global:QchipInstallerTestFixtureRelease = [pscustomobject]@{
    draft = $false; prerelease = $false; tag_name = 'v1.0.1'
    assets = @('update.json', 'Qchip-TEC-Windows-x64.zip') | ForEach-Object {
        [pscustomobject]@{ name = $_; state = 'uploaded'; browser_download_url = ('https://github.com/youchang0908/qchip-tec/releases/download/v1.0.1/' + $_) }
    }
}

# 1. Real hashing/extraction and COM shortcut creation, with fake download bytes.
Set-TestProfile 'success'
& $installer -NoLaunch
$target = Join-Path (Get-Location).Path 'Qchip-TEC'
Assert-Check (@(Get-ChildItem -LiteralPath $target -File).Count -eq 4) 'Expected exactly four installed files.'
foreach ($record in $records) {
    Assert-Check ((Get-FileHash -LiteralPath (Join-Path $target $record.path) -Algorithm SHA256).Hash.ToLowerInvariant() -ceq $record.sha256) 'Installed file checksum mismatch.'
}
$link = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Qchip TEC.lnk'
Assert-Check (Test-Path -LiteralPath $link) 'Isolated Start menu shortcut missing.'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
Assert-Check ($shortcut.TargetPath -ieq (Join-Path $target 'Qchip TEC.exe')) 'Shortcut target incorrect.'
Assert-Check ($shortcut.WorkingDirectory -ieq $target) 'Shortcut working directory incorrect.'
Assert-Check ($global:QchipInstallerTestRequests -eq 3) 'Unexpected success network mock calls.'
Write-Host 'PASS 1: isolated install, checksums and shortcut; no launch'

# 2. Same-length corrupted ZIP must fail before installation and clean staging.
Set-TestProfile 'bad-hash'
$global:QchipInstallerTestCorrupt = $true
$failure = $null
try { & $installer -NoLaunch } catch { $failure = $_.Exception.Message }
Assert-Check ($failure -like '*Download checksum failed*') 'Corrupted archive was not rejected by hash.'
Assert-Check (-not (Test-Path -LiteralPath (Join-Path (Get-Location).Path 'Qchip-TEC'))) 'Hash failure left an installation.'
Assert-Check (@(Get-ChildItem -LiteralPath (Get-Location).Path -Force -Filter '.qchip-install-*').Count -eq 0) 'Hash failure left staging.'
Assert-Check (-not (Test-Path -LiteralPath (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Qchip TEC.lnk'))) 'Hash failure left shortcut.'
Write-Host 'PASS 2: corrupted ZIP rejected and no installation left'

# 3. Existing installation is untouched and performs no network calls.
Set-TestProfile 'existing'
$target = Join-Path (Get-Location).Path 'Qchip-TEC'
$null = New-Item -ItemType Directory -Path $target
$sentinel = Join-Path $target 'sentinel.txt'
[IO.File]::WriteAllText($sentinel, 'PRESERVE EXISTING USER FILE')
$before = (Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash
& $installer -NoLaunch
Assert-Check ($global:QchipInstallerTestRequests -eq 0) 'Existing install made a network request.'
Assert-Check ((Get-FileHash -LiteralPath $sentinel -Algorithm SHA256).Hash -ceq $before) 'Existing sentinel changed.'
Assert-Check (@(Get-ChildItem -LiteralPath $target -Force).Count -eq 1) 'Existing install was modified.'
Assert-Check ($global:QchipInstallerTestLaunches -eq 0) 'An executable was launched.'
Write-Host 'PASS 3: existing installation unchanged; no network'
# 4. An explicit install root is exact, with no extra Qchip-TEC nesting.
Set-TestProfile 'custom'
$global:QchipInstallerTestCorrupt = $false
$customRoot = Join-Path $root 'custom\nested\chosen-root'
& $installer -NoLaunch -InstallDir $customRoot
Assert-Check (Test-Path -LiteralPath (Join-Path $customRoot 'Qchip TEC.exe')) 'Explicit install root not used.'
Assert-Check (-not (Test-Path -LiteralPath (Join-Path $customRoot 'Qchip-TEC'))) 'Explicit root gained an extra folder.'
$customLink = $shell.CreateShortcut((Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Qchip TEC.lnk'))
Assert-Check ($customLink.TargetPath -ieq (Join-Path $customRoot 'Qchip TEC.exe')) 'Custom shortcut target incorrect.'
Write-Host 'PASS 4: explicit root and shortcut are exact'

# 5. An invalid/unwritable parent fails before any network request.
Set-TestProfile 'unwritable'
$blockedParent = Join-Path (Get-Location).Path 'not-a-directory'
[IO.File]::WriteAllText($blockedParent, 'KEEP THIS FILE')
$failure = $null
try { & $installer -NoLaunch -InstallDir (Join-Path $blockedParent 'Qchip-TEC') } catch { $failure = $_.Exception.Message }
Assert-Check ($failure -like '*Installation path is not writable*') 'Unwritable path lacks an actionable error.'
Assert-Check ($global:QchipInstallerTestRequests -eq 0) 'Unwritable path attempted network.'
Assert-Check ([IO.File]::ReadAllText($blockedParent) -ceq 'KEEP THIS FILE') 'Existing parent file changed.'
Write-Host 'PASS 5: invalid/unwritable parent rejected before network'
@{ checks_passed = 5; powershell = $PSVersionTable.PSVersion.ToString(); profiles_isolated = $true; real_network_requests = 0; exe_launches = 0 } | ConvertTo-Json -Compress
