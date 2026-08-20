#Requires -RunAsAdministrator

param(
    [string]$RemoteAddress = "192.168.10.0/24"
)

$ErrorActionPreference = "Stop"
$vmCreatorId = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}"
$rules = [ordered]@{
    "WSL-media2text-frontend" = @{
        DisplayName = "WSL media2text frontend"
        Port = 10001
    }
    "WSL-hermes-web" = @{
        DisplayName = "WSL Hermes Daily Research web"
        Port = 10003
    }
    "WSL-hermes-postgres" = @{
        DisplayName = "WSL Hermes Daily Research PostgreSQL"
        Port = 15433
    }
    "WSL-ollama" = @{
        DisplayName = "WSL Ollama"
        Port = 11434
    }
    "WSL-lazybun-postgres" = @{
        DisplayName = "WSL LazyBun PostgreSQL"
        Port = 15434
    }
    "WSL-lazybun-redis" = @{
        DisplayName = "WSL LazyBun Redis"
        Port = 16379
    }
    "WSL-claude-code-bridge" = @{
        DisplayName = "WSL Claude Code bridge"
        Port = 18793
    }
    "WSL-cursor-cli-bridge" = @{
        DisplayName = "WSL Cursor CLI bridge"
        Port = 18790
    }
}

$legacyRuleNames = @(
    "WSL-media2text-5173",
    "WSL media2text 5173",
    "media2text frontend",
    "Hermes Daily Research LAN 8080",
    "Hermes PostgreSQL 5433"
)

foreach ($legacyName in $legacyRuleNames) {
    Remove-NetFirewallHyperVRule -Name $legacyName -ErrorAction SilentlyContinue
    Get-NetFirewallRule -DisplayName $legacyName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule
}

foreach ($legacyPort in @(5173, 8080, 5433)) {
    netsh interface portproxy delete v4tov4 `
        listenaddress=0.0.0.0 listenport=$legacyPort | Out-Null
}

foreach ($name in $rules.Keys) {
    Remove-NetFirewallHyperVRule -Name $name -ErrorAction SilentlyContinue
    $rule = $rules[$name]
    New-NetFirewallHyperVRule `
        -Name $name `
        -DisplayName $rule.DisplayName `
        -Direction Inbound `
        -VMCreatorId $vmCreatorId `
        -Protocol TCP `
        -LocalPorts $rule.Port `
        -RemoteAddresses $RemoteAddress `
        -Profiles Any `
        -Action Allow `
        -Enabled True | Out-Null
}

Write-Host "Configured mirrored-network firewall rules for $RemoteAddress"
$rules.GetEnumerator() | ForEach-Object {
    Write-Host ("  {0}: TCP {1}" -f $_.Value.DisplayName, $_.Value.Port)
}
