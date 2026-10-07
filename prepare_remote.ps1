param([int]$HttpsPort = 8443)
$ErrorActionPreference = 'Stop'
if ($HttpsPort -ne 8443) { throw 'この初版の準備対象は8443です。' }
$status = tailscale status --json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $status.BackendState -ne 'Running') {
    throw 'Tailscaleを起動し、利用者本人のアカウントでログインしてください。'
}
$serve = tailscale serve status --json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Serveの既存設定を確認できません。' }
if ($serve.TCP.PSObject.Properties.Name -contains "$HttpsPort") {
    throw '8443に既存設定があります。他のサービスを上書きせず、別ポートの設計を確認してください。'
}
$user = $status.User.PSObject.Properties["$($status.Self.UserID)"].Value.LoginName
$dns = $status.Self.DNSName.TrimEnd('.')
if (-not $user -or -not $dns.EndsWith('.ts.net')) { throw '現在の端末のDNS名と利用者を確認できません。' }
$privateDir = Join-Path $PSScriptRoot 'private'
New-Item -ItemType Directory -Force -Path $privateDir | Out-Null
$config = Join-Path $privateDir 'remote.json'
if (Test-Path -LiteralPath $config) { throw '既存のprivate/remote.jsonを保持します。設定を確認してください。' }
@{ origin="https://${dns}:$HttpsPort"; allowed_user=$user } | ConvertTo-Json | Set-Content -LiteralPath $config -Encoding utf8
Write-Output 'private/remote.jsonを作成しました。現在ログインしている本人だけを許可します。接続は有効化していません。'
Write-Output '1. python app.py --remote-config private/remote.json'
Write-Output '2. tailscale serve --bg --https=8443 http://127.0.0.1:8876'
Write-Output '3. private/remote.jsonのoriginを、同じ本人の別PCから開いて確認してください。'
Write-Output '停止は tailscale serve --https=8443 off 。既存Serveを一括resetしないでください。'
