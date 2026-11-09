# Stateful fake of the Azure CLI for the identity script tests. State: $env:FAKE_AZ_STATE (JSON), log: $env:FAKE_AZ_LOG.
$statePath = $env:FAKE_AZ_STATE
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json -AsHashtable -Depth 30
$arguments = @($args)
Add-Content -LiteralPath $env:FAKE_AZ_LOG -Value ($arguments -join ' ') -Encoding utf8

function Save-State { $state | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $statePath -Encoding utf8 }
function Out-Json { param($Value) ConvertTo-Json -InputObject $Value -Depth 30 -Compress }
function Get-Option { param([string] $Name) $i = [array]::IndexOf($arguments, $Name); if ($i -ge 0 -and $i + 1 -lt $arguments.Count) { return $arguments[$i + 1] } }
function Fail { exit 1 }
function New-Id { [guid]::NewGuid().ToString() }

$verb = ($arguments | Where-Object { $_ -notlike '-*' } | Select-Object -First 4) -join ' '
$command = "$($arguments[0]) $($arguments[1]) $($arguments[2])".Trim()

switch -Regex ($command) {
    '^account show' {
        if (-not $state.account) { Fail }
        Out-Json $state.account; exit 0
    }
    '^ad signed-in-user' { if ($state.directoryReadable -eq $false) { Fail }; Out-Json @{ id = 'user-object-id' }; exit 0 }
    '^role assignment list' { Out-Json @($state.azureRoles | ForEach-Object { @{ roleDefinitionName = $_ } }); exit 0 }
    '^ad group show' {
        $id = Get-Option '--group'
        if ($state.groups -contains $id) { Out-Json @{ id = $id } ; exit 0 } else { Fail }
    }
    '^group show' { if ($state.resourceGroups -contains (Get-Option '--name')) { Out-Json @{ name = (Get-Option '--name') }; exit 0 } else { Fail } }
    '^group create' {
        $state.resourceGroups += (Get-Option '--name'); Save-State; Out-Json @{ name = (Get-Option '--name') }; exit 0
    }
    '^identity show' {
        $found = $state.identities[(Get-Option '--name')]
        if ($found) { Out-Json $found; exit 0 } else { Fail }
    }
    '^identity create' {
        $name = Get-Option '--name'
        $state.identities[$name] = @{ name = $name; principalId = New-Id; clientId = New-Id }
        Save-State; Out-Json $state.identities[$name]; exit 0
    }
    '^ad app list' {
        $name = Get-Option '--display-name'
        Out-Json @($state.apps | Where-Object { $_.displayName -eq $name }); exit 0
    }
    '^ad app create' {
        if ($state.failOn -eq 'app-create') { Fail }
        $app = @{ id = New-Id; appId = New-Id; displayName = (Get-Option '--display-name'); appRoles = @(); identifierUris = @(); api = @{ requestedAccessTokenVersion = $null }; web = @{ redirectUris = @() }; credentials = @() }
        $state.apps += $app; Save-State; Out-Json $app; exit 0
    }
    '^ad app show' {
        $app = $state.apps | Where-Object { $_.appId -eq (Get-Option '--id') } | Select-Object -First 1
        if ($app) { Out-Json $app; exit 0 } else { Fail }
    }
    '^ad app update' {
        $app = $state.apps | Where-Object { $_.appId -eq (Get-Option '--id') } | Select-Object -First 1
        if (-not $app) { Fail }
        $roles = Get-Option '--app-roles'
        if ($roles) { $app.appRoles = @(Get-Content -LiteralPath $roles.TrimStart('@') -Raw | ConvertFrom-Json -AsHashtable -Depth 20) }
        # The real CLI rejects generic --set on app registrations ("Couldn't find 'api' in ''").
        if (Get-Option '--set') { Fail }
        $uris = Get-Option '--identifier-uris'
        if ($uris) { $app.identifierUris = @($uris) }
        $index = [array]::IndexOf($arguments, '--web-redirect-uris')
        if ($index -ge 0) { $app.web = @{ redirectUris = @($arguments[($index + 1)..($arguments.Count - 1)] | Where-Object { $_ -notlike '-*' }) } }
        Save-State; exit 0
    }
    '^ad app credential' {
        $app = $state.apps | Where-Object { $_.appId -eq (Get-Option '--id') } | Select-Object -First 1
        if ($arguments[3] -eq 'list') { Out-Json @($app.credentials | ForEach-Object { $_ }); exit 0 }
        $end = Get-Option '--end-date'
        $app.credentials += @{ displayName = (Get-Option '--display-name'); endDateTime = "${end}T00:00:00Z" }
        Save-State; Out-Json @{ appId = $app.appId; password = 'synthetic-secret-value-0001'; tenant = $state.account.tenantId }; exit 0  # pragma: allowlist secret
    }
    '^ad sp show' {
        $id = Get-Option '--id'
        $found = $state.servicePrincipals[$id]
        if ($found) { Out-Json $found; exit 0 } else { Fail }
    }
    '^ad sp create' {
        $id = Get-Option '--id'
        $state.servicePrincipals[$id] = @{ id = New-Id; appId = $id; appRoles = @() }
        Save-State; Out-Json $state.servicePrincipals[$id]; exit 0
    }
    '^rest' {
        $method = (Get-Option '--method')
        $url = (Get-Option '--url')
        if ($url -like '*transitiveMemberOf*') { Out-Json @{ value = @($state.directoryRoles | ForEach-Object { @{ displayName = $_ } }) }; exit 0 }
        if ($method -eq 'patch' -and $url -match '/v1\.0/applications/([^/]+)$') {
            $app = $state.apps | Where-Object { $_.id -eq $Matches[1] } | Select-Object -First 1
            if (-not $app) { Fail }
            $body = (Get-Option '--body') | ConvertFrom-Json -AsHashtable -Depth 20
            if ($body.api) { $app.api = $body.api }
            Save-State; exit 0
        }
        if ($url -match '/servicePrincipals/([^/]+)/appRoleAssignedTo') {
            $resource = $Matches[1]
            if ($method -eq 'get') {
                Out-Json @{ value = @($state.assignments | Where-Object { $_.resourceId -eq $resource }) }; exit 0
            }
            $body = (Get-Option '--body') | ConvertFrom-Json -AsHashtable
            $state.assignments += @{ principalId = $body.principalId; resourceId = $resource; appRoleId = $body.appRoleId }
            Save-State; Out-Json @{ id = New-Id }; exit 0
        }
        Fail
    }
    '^containerapp show' {
        $found = $state.containerApps[(Get-Option '--name')]
        if (-not $found) { Fail }
        Write-Output $found.fqdn; exit 0
    }
    default { Fail }
}
