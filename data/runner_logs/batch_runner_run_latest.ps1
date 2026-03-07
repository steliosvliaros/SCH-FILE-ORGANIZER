Set-Location 'C:\00_Developement\sch-file-organizer'
$env:PYTHONPATH = 'C:\00_Developement\sch-file-organizer;' + $env:PYTHONPATH
New-Item -ItemType Directory -Force -Path 'C:\00_Developement\sch-file-organizer\data\runner_logs' | Out-Null

Write-Host 'Stage 1: 01_policy_check -> 01_policy_check.ipynb'
Write-Host 'Already done - review if you want to rerun.'

Write-Host 'Stage 2: 02_inventory -> 02_inventory.ipynb'
Write-Host 'Already done - review if you want to rerun.'

Write-Host 'Stage 3: 03_rule_classification -> 03_rule_classification.ipynb'
Write-Host 'Already done - review if you want to rerun.'

Write-Host 'Stage 4: 04_extract_text -> 04_extract_text.ipynb'
Write-Host 'Already done - review if you want to rerun.'

Write-Host 'Stage 5: 05_review_outputs -> 05_review_outputs.ipynb'
Write-Host 'Already done - review if you want to rerun.'
Write-Host 'Review checkpoint before planning or any execution work.'
Read-Host 'Checkpoint reached. Press Enter to continue or Ctrl+C to stop'

Write-Host 'Stage 6: 06_planner -> 06_planner.ipynb'
Write-Host 'Already done - review if you want to rerun.'

Write-Host 'Stage 7: 07_execution_manifest -> 07_execution_manifest.ipynb'
Write-Host 'Already done - review if you want to rerun.'
Write-Host 'Manifest checkpoint before any apply action.'
Read-Host 'Checkpoint reached. Press Enter to continue or Ctrl+C to stop'
