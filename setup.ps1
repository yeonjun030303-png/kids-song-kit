# 키즈 노래 키트 - 원클릭 설정 (PowerShell)
# 실행: 이 폴더에서 PowerShell을 열고
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Quiet($cmd) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { Invoke-Expression "$cmd 2>&1" | Out-Null; return $LASTEXITCODE } finally { $ErrorActionPreference = $old }
}
function Run($cmd) {
    Invoke-Expression $cmd
    if ($LASTEXITCODE -ne 0) { throw "실패: $cmd" }
}

Write-Host "`n[1/6] git / GitHub CLI 확인" -ForegroundColor Cyan
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    winget install --id GitHub.cli -e --silent --accept-package-agreements --accept-source-agreements
    Refresh-Path
}
if (-not (Get-Command git -ErrorAction SilentlyContinue) -or -not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "git 또는 gh 설치가 끝나지 않았습니다. PowerShell을 닫았다가 다시 열고 같은 명령을 다시 실행하세요."
}

Write-Host "`n[2/6] GitHub 로그인 (브라우저가 열리면 승인)" -ForegroundColor Cyan
if ((Quiet "gh auth status") -ne 0) { Run "gh auth login -h github.com -p https -w" }

Write-Host "`n[3/6] 저장소 만들고 올리기 (비공개)" -ForegroundColor Cyan
if (-not (Test-Path ".git")) { Run "git init -b main" }
git add -A
Quiet 'git -c user.name="kids-kit" -c user.email="kids-kit@users.noreply.github.com" commit -m "init"' | Out-Null
$repo = "kids-song-kit"
if ((Quiet "gh repo view $repo") -ne 0) {
    Run "gh repo create $repo --private --source . --remote origin --push"
} else {
    Write-Host "이미 저장소가 있어 push만 합니다."
    git push -u origin main
}

Write-Host "`n[4/6] 비밀값 3개 입력 (화면에 안 보이게 저장됨)" -ForegroundColor Cyan
$gem = Read-Host "Gemini API 키"
$mail = Read-Host "Gmail 주소 (받을 주소 겸 보내는 주소)"
$pw = (Read-Host "Gmail 앱 비밀번호 (16자리)") -replace "\s", ""
$gem  | gh secret set GEMINI_API_KEY
$mail | gh secret set GMAIL_ADDRESS
$pw   | gh secret set GMAIL_APP_PASSWORD
if ($LASTEXITCODE -ne 0) { throw "비밀값 저장 실패" }

Write-Host "`n[5/6] 테스트 실행 시작 (예시 가사 + 짧은 곡)" -ForegroundColor Cyan
Run "gh workflow run episode-kit.yml -f test=true"
Start-Sleep -Seconds 8

Write-Host "`n[6/6] 진행 상황 보기" -ForegroundColor Cyan
Write-Host "첫 실행은 모델 다운로드와 CPU 생성 때문에 오래 걸릴 수 있어요. (창을 닫아도 계속 돌아가고, 끝나면 메일이 옵니다)"
gh run list --workflow episode-kit.yml --limit 1
Write-Host "`n브라우저로 보려면: gh run watch"
Write-Host "정기 실행: 매주 월/목 새벽 4시(KST)에 자동으로 돌아갑니다." -ForegroundColor Green
