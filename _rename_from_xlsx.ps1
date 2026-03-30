$ErrorActionPreference = 'Stop'
$xlsxPath = 'C:\Users\User\Downloads\proposed_file_naming_20260330.xlsx'
if (-not (Test-Path -LiteralPath $xlsxPath)) { throw "Spreadsheet not found: $xlsxPath" }

function Normalize-RelPath([string]$p) {
    if ([string]::IsNullOrWhiteSpace($p)) { return '' }
    return $p.Trim().Replace('/', '\').TrimStart('\')
}

$excel = $null
$workbook = $null
$sheet = $null
$used = $null
$rows = @()
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $workbook = $excel.Workbooks.Open($xlsxPath)
    $sheet = $workbook.Worksheets.Item(1)
    $used = $sheet.UsedRange

    $rowCount = $used.Rows.Count
    $colCount = $used.Columns.Count
    if ($rowCount -lt 2) { throw 'Spreadsheet has no data rows.' }

    $headers = @{}
    for ($c = 1; $c -le $colCount; $c++) {
        $name = [string]$used.Cells.Item(1, $c).Text
        if (-not [string]::IsNullOrWhiteSpace($name)) {
            $headers[$name.Trim()] = $c
        }
    }

    if (-not $headers.ContainsKey('source_relative_path')) { throw 'Missing column: source_relative_path' }
    if (-not $headers.ContainsKey('proposed_same_folder_path')) { throw 'Missing column: proposed_same_folder_path' }

    $srcCol = $headers['source_relative_path']
    $dstCol = $headers['proposed_same_folder_path']

    for ($r = 2; $r -le $rowCount; $r++) {
        $src = Normalize-RelPath([string]$used.Cells.Item($r, $srcCol).Text)
        $dst = Normalize-RelPath([string]$used.Cells.Item($r, $dstCol).Text)
        if ([string]::IsNullOrWhiteSpace($src) -or [string]::IsNullOrWhiteSpace($dst)) { continue }
        if ($src -eq $dst) { continue }
        $rows += [pscustomobject]@{ Row = $r; SourceRel = $src; TargetRel = $dst }
    }
}
finally {
    if ($workbook) { $workbook.Close($false) | Out-Null }
    if ($excel) { $excel.Quit() }
    foreach ($obj in @($used, $sheet, $workbook, $excel)) {
        if ($obj) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($obj) }
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}

if ($rows.Count -eq 0) { throw 'No rename rows found after filtering.' }

$candidateRoots = @(
    'C:\Users\User\Desktop',
    'C:\Users\User\Downloads',
    'C:\00_dev\SCH-FILE-ORGANIZER'
) | Where-Object { Test-Path -LiteralPath $_ }

if ($candidateRoots.Count -eq 0) { throw 'No candidate roots exist for path resolution.' }

$sample = $rows | Select-Object -First ([Math]::Min(200, $rows.Count))
$scores = foreach ($root in $candidateRoots) {
    $hits = 0
    foreach ($m in $sample) {
        $srcPath = Join-Path $root $m.SourceRel
        if (Test-Path -LiteralPath $srcPath -PathType Leaf) { $hits++ }
    }
    [pscustomobject]@{ Root = $root; Hits = $hits }
}

$best = $scores | Sort-Object Hits -Descending | Select-Object -First 1
if ($best.Hits -eq 0) {
    $scoreText = ($scores | ForEach-Object { "$($_.Root)=$($_.Hits)" }) -join ', '
    throw ("Could not resolve a root folder from source_relative_path. Scores: $scoreText")
}
$root = $best.Root
Write-Output ("Resolved root: $root")
Write-Output ('Root scores: ' + (($scores | ForEach-Object { "$($_.Root)=$($_.Hits)" }) -join ', '))

$ops = @()
$skipped = @()
foreach ($m in $rows) {
    $src = Join-Path $root $m.SourceRel
    $dst = Join-Path $root $m.TargetRel
    if (-not (Test-Path -LiteralPath $src -PathType Leaf)) {
        $skipped += [pscustomobject]@{ Row = $m.Row; Reason = 'source_missing'; Source = $src; Target = $dst }
        continue
    }
    $ops += [pscustomobject]@{ Row = $m.Row; Source = $src; Target = $dst }
}

if ($ops.Count -eq 0) { throw 'No valid source files found to rename.' }

$dupTargets = $ops | Group-Object Target | Where-Object { $_.Count -gt 1 }
if ($dupTargets) {
    $examples = ($dupTargets | Select-Object -First 10 | ForEach-Object { $_.Name }) -join '; '
    throw ("Duplicate targets in sheet: $examples")
}

$sourceSet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
foreach ($o in $ops) { [void]$sourceSet.Add($o.Source) }

$blocked = @()
foreach ($o in $ops) {
    if (Test-Path -LiteralPath $o.Target -PathType Leaf) {
        if (-not $sourceSet.Contains($o.Target)) {
            $blocked += $o
        }
    }
}
if ($blocked.Count -gt 0) {
    $examples = ($blocked | Select-Object -First 10 | ForEach-Object { $_.Target }) -join '; '
    throw ("Blocked by existing target file(s): $examples")
}

$staged = @()
foreach ($o in $ops) {
    $srcDir = Split-Path -Parent $o.Source
    $tmp = Join-Path $srcDir ('.rename_tmp_' + [guid]::NewGuid().ToString('N'))
    Move-Item -LiteralPath $o.Source -Destination $tmp
    $staged += [pscustomobject]@{ Row = $o.Row; Temp = $tmp; Target = $o.Target }
}

$renamed = 0
foreach ($s in $staged) {
    $targetDir = Split-Path -Parent $s.Target
    if (-not (Test-Path -LiteralPath $targetDir -PathType Container)) {
        New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    }
    Move-Item -LiteralPath $s.Temp -Destination $s.Target
    $renamed++
}

Write-Output ("Rows in sheet: $($rows.Count)")
Write-Output ("Valid ops: $($ops.Count)")
Write-Output ("Skipped missing source: $($skipped.Count)")
Write-Output ("Renamed: $renamed")
if ($skipped.Count -gt 0) {
    Write-Output 'Sample skipped rows:'
    $skipped | Select-Object -First 10 Row,Reason,Source,Target | Format-Table -AutoSize | Out-String | Write-Output
}
