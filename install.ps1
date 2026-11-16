#requires -Version 5.1
param([switch]$NoLaunch, [string]$InstallDir)

# Public first-install bootstrap. Existing installations use the app updater.
function Install-QchipTec {
    param([switch]$NoLaunch, [string]$InstallDir)
    $ErrorActionPreference = 'Stop'
    if ([Environment]::OSVersion.Platform -ne 'Win32NT' -or
        -not [Environment]::Is64BitOperatingSystem) {
        throw 'Qchip TEC requires 64-bit Windows.'
    }

    $chosen = if ([string]::IsNullOrWhiteSpace($InstallDir)) { Join-Path (Get-Location).Path 'Qchip-TEC' } else { $InstallDir }
    $target = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($chosen)
    $target = [IO.Path]::GetFullPath($target)
    $parent = [IO.Path]::GetDirectoryName($target)
    if (Test-Path -LiteralPath $target) {
        Write-Host "Qchip TEC install folder already exists: $target"
        Write-Host 'Use Check for updates inside the app. No files were changed.'
        return
    }

    # Fail locally before spending time downloading a package to an unwritable path.
    $probe = Join-Path $parent ('.qchip-write-' + [Guid]::NewGuid().ToString('N'))
    try {
        $null = New-Item -ItemType Directory -Path $parent -Force
        [IO.File]::WriteAllText($probe, '')
    } catch { throw "Installation path is not writable: $target. Choose another -InstallDir. $($_.Exception.Message)" }
    finally { if (Test-Path -LiteralPath $probe -PathType Leaf) { Remove-Item -LiteralPath $probe -Force } }

    $repository = 'youchang0908/qchip-tec'
    $headers = @{ 'User-Agent' = 'QchipTEC-Installer'; Accept = 'application/vnd.github+json' }
    $release = Invoke-RestMethod -Uri "https://api.github.com/repos/$repository/releases/latest" -Headers $headers
    if ($release.draft -or $release.prerelease -or
        $release.tag_name -cnotmatch '^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$') {
        throw 'No stable Qchip TEC release is available.'
    }
    $version = $release.tag_name.Substring(1)
    $tag = [Uri]::EscapeDataString($release.tag_name)
    $archiveName = 'Qchip-TEC-Windows-x64.zip'
    $allowed = @('Qchip TEC.exe', 'version.json', 'LICENSE.txt', 'THIRD_PARTY_NOTICES.txt')

    function Get-ReleaseAssetUrl([string]$Name) {
        $assets = @($release.assets | Where-Object { $_.name -ceq $Name -and $_.state -eq 'uploaded' })
        $expected = "https://github.com/$repository/releases/download/$tag/$([Uri]::EscapeDataString($Name))"
        if ($assets.Count -ne 1 -or $assets[0].browser_download_url -cne $expected) {
            throw 'The release package is not ready. Please try again later.'
        }
        return $expected
    }

    $manifestUrl = Get-ReleaseAssetUrl 'update.json'
    $archiveUrl = Get-ReleaseAssetUrl $archiveName
    $null = New-Item -ItemType Directory -Path $parent -Force
    $work = Join-Path $parent ('.qchip-install-' + [Guid]::NewGuid().ToString('N'))
    $null = New-Item -ItemType Directory -Path $work
    try {
        Write-Host "Downloading Qchip TEC $version..."
        $manifestPath = Join-Path $work 'update.json'
        Invoke-WebRequest -UseBasicParsing -Uri $manifestUrl -Headers $headers -OutFile $manifestPath
        if ((Get-Item -LiteralPath $manifestPath).Length -gt 131072) { throw 'Update manifest is too large.' }
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($manifest.schema -ne 1 -or $manifest.product -cne 'QchipTEC' -or
            $manifest.platform -cne 'windows-x64' -or $manifest.version -cne $version -or
            $manifest.archive.name -cne $archiveName -or
            $manifest.archive.bytes -le 0 -or $manifest.archive.bytes -gt 188743680 -or
            $manifest.archive.sha256 -cnotmatch '^[a-f0-9]{64}$') {
            throw 'Unexpected update manifest.'
        }
        $records = @($manifest.files)
        if ($records.Count -ne 4 -or
            @(Compare-Object -ReferenceObject $allowed -DifferenceObject @($records.path) -CaseSensitive).Count -ne 0) {
            throw 'Unexpected program file list.'
        }
        foreach ($file in $records) {
            if ($file.bytes -le 0 -or $file.bytes -gt 125829120 -or $file.sha256 -cnotmatch '^[a-f0-9]{64}$') {
                throw 'Invalid program checksum or size.'
            }
        }

        $archivePath = Join-Path $work $archiveName
        Invoke-WebRequest -UseBasicParsing -Uri $archiveUrl -Headers $headers -OutFile $archivePath
        if ((Get-Item -LiteralPath $archivePath).Length -ne $manifest.archive.bytes -or
            (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $manifest.archive.sha256) {
            throw 'Download checksum failed. Nothing was installed.'
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip = [IO.Compression.ZipFile]::OpenRead($archivePath)
        try {
            if ($zip.Entries.Count -ne 4) { throw 'Unexpected ZIP contents.' }
            foreach ($entry in $zip.Entries) {
                $record = @($records | Where-Object { $_.path -ceq $entry.FullName })
                if ($record.Count -ne 1 -or $entry.Length -ne $record[0].bytes) {
                    throw 'Unexpected ZIP entry or size.'
                }
            }
            if (@(Compare-Object -ReferenceObject $allowed -DifferenceObject @($zip.Entries.FullName) -CaseSensitive).Count -ne 0) {
                throw 'Unexpected ZIP file paths.'
            }
        } finally { $zip.Dispose() }

        $program = Join-Path $work 'program'
        [IO.Compression.ZipFile]::ExtractToDirectory($archivePath, $program)
        foreach ($file in $records) {
            if ((Get-FileHash -LiteralPath (Join-Path $program $file.path) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $file.sha256) {
                throw 'Program file checksum failed. Nothing was installed.'
            }
        }
        $metadata = Get-Content -LiteralPath (Join-Path $program 'version.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($metadata.product -cne 'QchipTEC' -or $metadata.version -cne $version) {
            throw 'Program version does not match the release.'
        }
        # Same-volume rename; never overwrite an existing installation or data.
        [IO.Directory]::Move($program, $target)
    } finally {
        if (Test-Path -LiteralPath $work) {
            $workFull = [IO.Path]::GetFullPath($work)
            $parentFull = [IO.Path]::GetFullPath($parent).TrimEnd('\')
            if ([IO.Path]::GetDirectoryName($workFull) -ine $parentFull -or
                [IO.Path]::GetFileName($workFull) -cnotmatch '^\.qchip-install-[a-f0-9]{32}$') {
                throw 'Unexpected installer cleanup path; temporary files were preserved.'
            }
            Remove-Item -LiteralPath $workFull -Recurse -Force
        }
    }

    $exe = Join-Path $target 'Qchip TEC.exe'
    try {
        $menu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
        $null = New-Item -ItemType Directory -Path $menu -Force
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut((Join-Path $menu 'Qchip TEC.lnk'))
        $shortcut.TargetPath = $exe
        $shortcut.WorkingDirectory = $target
        $shortcut.IconLocation = "$exe,0"
        $shortcut.Save()
    } catch { Write-Warning 'Installed successfully, but the Start menu shortcut could not be created.' }

    Write-Host "Qchip TEC $version installed: $target"
    Write-Host 'Device communication drivers and WebView2 Runtime are required.'
    if (-not $NoLaunch) { Start-Process -FilePath $exe -WorkingDirectory $target }
}

Install-QchipTec -NoLaunch:$NoLaunch -InstallDir $InstallDir
