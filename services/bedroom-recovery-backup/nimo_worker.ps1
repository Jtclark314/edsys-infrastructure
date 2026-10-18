$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    if ($env:COMPUTERNAME -ine 'NIMO-LAPTOP' -or $env:USERNAME -ine 'jtcla') { throw 'identity' }
    $root = Join-Path $env:LOCALAPPDATA 'EdSys\NimoVoice'
    $cursor = Get-Item -LiteralPath $root
    while ($cursor) {
        if ($cursor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'reparse' }
        $cursor = $cursor.Parent
    }
    $names = @('camera-view.js','start-assist.js','stop-assist.js','lg-key.js','lg-remote.json','settings.json','NimoVoice.exe','NimoVoice.cs','Microsoft.Web.WebView2.Core.dll','Microsoft.Web.WebView2.WinForms.dll','WebView2Loader.dll')
    Add-Type -AssemblyName System.IO.Compression
    $memory = New-Object IO.MemoryStream
    $archive = New-Object IO.Compression.ZipArchive($memory,[IO.Compression.ZipArchiveMode]::Create,$true)
    $files = @{}
    $hasher = [Security.Cryptography.SHA256]::Create()
    foreach ($name in $names) {
        $path = Join-Path $root $name
        $item = Get-Item -LiteralPath $path
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $item.Length -gt 8388608) { throw 'file' }
        $stamp = $item.LastWriteTimeUtc.Ticks
        $fileStream = [IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        try {
            if ($fileStream.Length -gt 8388608) { throw 'file_size' }
            $bounded = New-Object IO.MemoryStream
            $buffer = New-Object byte[] 65536
            while (($read = $fileStream.Read($buffer,0,$buffer.Length)) -gt 0) {
                if (($bounded.Length + $read) -gt 8388608) { throw 'file_grew' }
                $bounded.Write($buffer,0,$read)
            }
            $raw = $bounded.ToArray();$bounded.Dispose()
        } finally { $fileStream.Dispose() }
        $after = Get-Item -LiteralPath $path
        if ($raw.Length -ne $item.Length -or $stamp -ne $after.LastWriteTimeUtc.Ticks) { throw 'changed' }
        $hash = ([BitConverter]::ToString($hasher.ComputeHash($raw))).Replace('-','').ToLowerInvariant()
        $entry = $archive.CreateEntry($name)
        $stream = $entry.Open(); $stream.Write($raw,0,$raw.Length); $stream.Dispose()
        $files[$name] = @{bytes=$raw.Length;sha256=$hash}
    }
    $manifest = @{version=1;kind='nimo';source='NIMO-LAPTOP';user='jtcla';created_at=[DateTime]::UtcNow.ToString('o');files=$files;browser_profile_included=$false;media_included=$false;owned_file_count=11}
    $raw = [Text.Encoding]::UTF8.GetBytes(($manifest | ConvertTo-Json -Depth 6 -Compress))
    $entry=$archive.CreateEntry('manifest.json');$stream=$entry.Open();$stream.Write($raw,0,$raw.Length);$stream.Dispose()
    $archive.Dispose()
    [Console]::Out.Write([Convert]::ToBase64String($memory.ToArray()))
    $memory.Dispose();$hasher.Dispose()
} catch {
    [Console]::Error.WriteLine('nimo_capture_failed')
    exit 1
}
