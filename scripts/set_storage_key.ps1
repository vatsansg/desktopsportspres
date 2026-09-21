<#
.SYNOPSIS
  Saves the Azure Storage Account key into this project's git-ignored .env - WITHOUT ever showing
  it on screen, in shell history, or in a chat.

.DESCRIPTION
  Run this yourself in a normal PowerShell window (it prompts for the key with masked input):

      cd C:\vatsan\techprojects\INFRA\ledassetmanagement\Applications\desktop
      .\scripts\set_storage_key.ps1

  It writes STORAGE_ACCOUNT_NAME / STORAGE_ACCOUNT_KEY / STORAGE_CONTAINER to .\.env (development only;
  an installed build never reads a .env - on a venue machine the key is entered in Settings).
  It then restricts the file to your Windows account and confirms git will ignore it.
#>
param(
    [string]$Account = "sasportspresentation",
    [string]$Container = "2026"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"

$secure = Read-Host -AsSecureString "Storage account access key (input is hidden)"
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }

$plain = $plain.Trim()
if ($plain -notmatch '^[A-Za-z0-9+/]{40,120}={0,2}$') {
    throw "That does not look like a storage account key (about 88 letters, digits, + / and =). Nothing was written."
}

# Confirm git will ignore the file BEFORE the key is written to it.
Push-Location $root
try {
    git check-ignore -q ".env"
    if ($LASTEXITCODE -ne 0) { throw ".env is NOT git-ignored in this repository. Refusing to write a key into it." }
} finally { Pop-Location }

@(
    "# Development only - never commit. Written by scripts\set_storage_key.ps1",
    "STORAGE_ACCOUNT_NAME=$Account",
    "STORAGE_ACCOUNT_KEY=$plain",
    "STORAGE_CONTAINER=$Container"
) | Set-Content -Path $envFile -Encoding UTF8
$plain = $null

# Only your own Windows account may read it.
icacls $envFile /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null

Write-Host ""
Write-Host "Saved to $envFile (git-ignored, readable only by $($env:USERNAME))."
Write-Host "The key was not displayed. Tell Claude 'the key is in .env' and it will run the live checks."
