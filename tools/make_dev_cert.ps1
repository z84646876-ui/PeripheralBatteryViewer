<#
.SYNOPSIS
生成本机自签名代码签名证书（用于开发/内测签名与 signtool 流程验证）。
注意：Windows SmartScreen 信任新签名可执行文件仍需要受信任 CA 颁发的商业 OV/EV 代码签名证书；
本脚本生成的自签名证书仅在将其 .cer 导入本机“受信任的根证书颁发机构”后受本机信任。
#>
param(
    [string]$Subject = "CN=PeripheralBatteryViewer Dev",
    [string]$OutDir = "$PSScriptRoot\..\.build_tmp",
    [string]$Password = "pbv-dev-cert"
)

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$cert = New-SelfSignedCertificate `
    -Type CodeSigningCert `
    -Subject $Subject `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -NotAfter (Get-Date).AddYears(3)

$pfxPath = Join-Path $OutDir "pbv_dev_codesign.pfx"
$cerPath = Join-Path $OutDir "pbv_dev_codesign.cer"
$secPwd = ConvertTo-SecureString -String $Password -Force -AsPlainText

Export-PfxCertificate -Cert $cert -FilePath $pfxPath -Password $secPwd | Out-Null
Export-Certificate -Cert $cert -FilePath $cerPath | Out-Null

Write-Host "Created PFX: $pfxPath"
Write-Host "Created CER: $cerPath"
Write-Host "To sign during build_exe.bat:"
Write-Host "  set PBV_SIGN_CERT=$pfxPath"
Write-Host "  set PBV_SIGN_PASS=$Password"
