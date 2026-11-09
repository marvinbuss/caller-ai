#Requires -Version 7.0
<#
.SYNOPSIS
Creates the directory-side objects of an environment with the Azure CLI (FR-040 to FR-043, FR-052, FR-053).

.DESCRIPTION
Run once per environment by a directory administrator, with -WhatIf first. Phase "registration" creates,
idempotently: the resource group and the runtime's user-assigned identity, the management sign-in
registration with its service principal and the application roles named in the variable file, the role
assignment of the runtime identity, the tester role and the read-only Graph permission Application.Read.All
of the deployment identity, the editor and tester roles of the user group, and a client secret that is valid
for at most 90 days. Phase "redirect" registers the management app's callback address after the first app
environment deployment.

The only secret is the client secret. It is written to code/.local/identity/<env>/management-signin-secret
(ignored by git, owner-only access) and is never printed or logged. No other secret, certificate or key is
created.

Exit codes: 0 success or preview, 1 preflight failure, 2 invalid input, 3 an Azure CLI step failed.

.EXAMPLE
pwsh code/infra/scripts/identity/Initialize-Identity.ps1 -Environment int -WhatIf
pwsh code/infra/scripts/identity/Initialize-Identity.ps1 -Environment int
pwsh code/infra/scripts/identity/Initialize-Identity.ps1 -Environment int -Phase redirect
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string] $Environment,

    [string] $Phase = 'registration',

    # Test seam: the Azure CLI executable or script to call.
    [string] $AzPath = 'az',

    [string] $RepositoryRoot = (Join-Path $PSScriptRoot '../../../..')
)

$ErrorActionPreference = 'Stop'
$script:Preview = [bool] $WhatIfPreference
$script:GraphAppId = '00000003-0000-0000-c000-000000000000'
$script:CallbackPath = '/auth/callback'
$script:SecretValidityDays = 89
$script:Changes = 0

function Stop-Script {
    param([int] $Code, [string] $Message)
    [Console]::Error.WriteLine($Message)
    exit $Code
}

function Invoke-Az {
    # Returns parsed JSON (or the raw text for -Raw). Error text is never echoed: it could hold a secret.
    param([string[]] $Arguments, [switch] $Raw, [switch] $AllowFailure)
    $output = & $AzPath @Arguments --only-show-errors 2>$null
    if ($LASTEXITCODE -ne 0) {
        if ($AllowFailure) { return $null }
        throw 'az_failed'
    }
    $text = ($output -join "`n").Trim()
    if ($Raw) { return $text }
    if ([string]::IsNullOrWhiteSpace($text)) { return $null }
    return $text | ConvertFrom-Json -Depth 20
}

function Invoke-Step {
    param([string] $Name, [scriptblock] $Action)
    try {
        & $Action
    }
    catch {
        Stop-Script 3 "Step failed: $Name. Fix the cause and run the script again; completed steps are not repeated."
    }
}

function Write-Change {
    param([string] $Text)
    $script:Changes++
    if ($script:Preview) { Write-Host "Would $Text" } else { Write-Host "Done: $Text" }
}

function Test-Guid {
    param($Value)
    $parsed = [guid]::Empty
    return ($Value -is [string]) -and [guid]::TryParse($Value, [ref] $parsed)
}

function Read-Inputs {
    if ($Environment -notin @('int', 'dev', 'tst', 'prd')) { Stop-Script 2 'Invalid input: environment must be int, dev, tst or prd.' }
    if ($Phase -notin @('registration', 'redirect')) { Stop-Script 2 'Invalid input: phase must be registration or redirect.' }
    $path = Join-Path $RepositoryRoot "code/infra/environments/$Environment/$Environment.tfvars.json"
    if (-not (Test-Path -LiteralPath $path)) { Stop-Script 2 "Invalid input: $path does not exist." }
    try { $document = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json -Depth 20 }
    catch { Stop-Script 2 "Invalid input: $path is not valid JSON." }
    try {
        $identity = $document.app_environment.identity
        $inputs = [ordered]@{
            Subscription  = $document.environment.subscription_id
            Tenant        = $document.environment.tenant_id
            DeploymentApp = $document.environment.client_id
            IdentityGroup = $identity.resource_group_name
            RuntimeName   = $identity.runtime_identity_name
            Location      = $document.environment.location
            Editor        = $identity.roles.editor
            Tester        = $identity.roles.tester
            Runtime       = $identity.roles.runtime
            Management    = $identity.roles.management
            UserGroup     = $identity.user_group_id
            ManagementApp = $document.app_environment.container_apps.management.app_name
            AppsGroup     = $document.app_environment.resource_group_name
        }
    }
    catch { Stop-Script 2 'Invalid input: the identity or environment settings are missing.' }
    foreach ($name in 'Subscription', 'Tenant', 'DeploymentApp', 'UserGroup') {
        if (-not (Test-Guid $inputs[$name])) { Stop-Script 2 "Invalid input: $name must be a GUID (app_environment.identity.user_group_id is required)." }
    }
    foreach ($name in 'IdentityGroup', 'RuntimeName', 'Location', 'Editor', 'Tester', 'Runtime', 'Management', 'ManagementApp', 'AppsGroup') {
        if ([string]::IsNullOrWhiteSpace($inputs[$name]) -or $inputs[$name] -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,89}$') {
            Stop-Script 2 "Invalid input: $name is missing or malformed."
        }
    }
    if (@($inputs.Editor, $inputs.Tester, $inputs.Runtime, $inputs.Management | Select-Object -Unique).Count -ne 4) {
        Stop-Script 2 'Invalid input: the four role names must differ.'
    }
    return $inputs
}

function Test-Preflight {
    param($Inputs)
    $account = Invoke-Az -Arguments @('account', 'show', '--output', 'json') -AllowFailure
    if (-not $account) { Stop-Script 1 'Preflight failed: no Azure CLI sign-in. Run az login with the directory administrator account.' }
    if ($account.tenantId -ne $Inputs.Tenant) { Stop-Script 1 'Preflight failed: the signed-in tenant differs from environment.tenant_id.' }
    if ($account.id -ne $Inputs.Subscription) { Stop-Script 1 'Preflight failed: the selected subscription differs from environment.subscription_id.' }
    if ($account.user.type -ne 'user') { Stop-Script 1 'Preflight failed: sign in as a person; a service principal cannot be used for directory administration.' }
    $me = Invoke-Az -Arguments @('ad', 'signed-in-user', 'show', '--output', 'json') -AllowFailure
    if (-not $me) {
        # The cached account can outlive the Graph token, so a stale sign-in passes the check above.
        Stop-Script 1 ("Preflight failed: the signed-in user cannot be read from the directory. The Azure CLI sign-in has usually " +
            "expired; run 'az logout' and 'az login --tenant $($Inputs.Tenant)', select subscription $($Inputs.Subscription), then run the script again.")
    }

    $roles = Invoke-Az -Arguments @('rest', '--method', 'get', '--url',
        'https://graph.microsoft.com/v1.0/me/transitiveMemberOf/microsoft.graph.directoryRole?$select=displayName', '--output', 'json') -AllowFailure
    $held = @($roles.value | ForEach-Object { $_.displayName })
    $needed = if ($Phase -eq 'registration') {
        @('Privileged Role Administrator', 'Global Administrator')
    } else {
        @('Application Administrator', 'Cloud Application Administrator', 'Privileged Role Administrator', 'Global Administrator')
    }
    if (-not ($held | Where-Object { $_ -in $needed })) {
        $label = if ($Phase -eq 'registration') {
            'the directory role Privileged Role Administrator or Global Administrator (needed to consent to the Graph application permission)'
        } else { 'a directory role that can edit the app registration (for example Application Administrator)' }
        Stop-Script 1 "Preflight failed: missing $label."
    }

    $assignments = Invoke-Az -Arguments @('role', 'assignment', 'list', '--assignee', $me.id, '--include-inherited', '--include-groups',
        '--scope', "/subscriptions/$($Inputs.Subscription)", '--output', 'json') -AllowFailure
    $azure = @($assignments | ForEach-Object { $_.roleDefinitionName })
    $enough = ('Owner' -in $azure) -or (('Contributor' -in $azure) -and ('User Access Administrator' -in $azure))
    if (-not $enough -and $Phase -eq 'registration') {
        Stop-Script 1 'Preflight failed: missing Azure rights on the subscription (Owner, or Contributor with User Access Administrator).'
    }
    $group = Invoke-Az -Arguments @('ad', 'group', 'show', '--group', $Inputs.UserGroup, '--output', 'json') -AllowFailure
    if (-not $group) { Stop-Script 2 'Invalid input: identity.user_group_id does not name an existing group.' }
    $deployment = Invoke-Az -Arguments @('ad', 'sp', 'show', '--id', $Inputs.DeploymentApp, '--output', 'json') -AllowFailure
    if (-not $deployment) { Stop-Script 2 'Invalid input: environment.client_id does not name an existing service principal.' }
    return [pscustomobject]@{ Group = $group; Deployment = $deployment }
}

function Get-RoleAssignments {
    param([string] $ResourceId)
    $result = Invoke-Az -Arguments @('rest', '--method', 'get', '--url',
        "https://graph.microsoft.com/v1.0/servicePrincipals/$ResourceId/appRoleAssignedTo", '--output', 'json')
    return @($result.value)
}

function Set-RoleAssignment {
    # Idempotent: reads first and writes only when the assignment is missing.
    param([string] $ResourceId, [string] $PrincipalId, [string] $RoleId, [string] $Description)
    if ($ResourceId -and $PrincipalId -and $RoleId) {
        $existing = Get-RoleAssignments -ResourceId $ResourceId
        if ($existing | Where-Object { $_.principalId -eq $PrincipalId -and $_.appRoleId -eq $RoleId }) {
            Write-Host "Unchanged: $Description"
            return
        }
    }
    Write-Change "assign $Description"
    if ($script:Preview) { return }
    $body = @{ principalId = $PrincipalId; resourceId = $ResourceId; appRoleId = $RoleId } | ConvertTo-Json -Compress
    Invoke-Az -Arguments @('rest', '--method', 'post', '--url',
        "https://graph.microsoft.com/v1.0/servicePrincipals/$ResourceId/appRoleAssignedTo",
        '--headers', 'Content-Type=application/json', '--body', $body, '--output', 'json') | Out-Null
}

function Set-OwnerOnlyAccess {
    param([string] $Path)
    if ($IsWindows) {
        & icacls $Path /inheritance:r /grant:r "$($env:USERNAME):(R,W)" | Out-Null
    } else {
        [System.IO.File]::SetUnixFileMode($Path, [System.IO.UnixFileMode]::UserRead -bor [System.IO.UnixFileMode]::UserWrite)
    }
}

function Find-Registration {
    param([string] $DisplayName)
    $found = Invoke-Az -Arguments @('ad', 'app', 'list', '--display-name', $DisplayName, '--output', 'json')
    return @($found | Where-Object { $_.displayName -eq $DisplayName }) | Select-Object -First 1
}

function Set-AccessTokenVersion {
    # Both registrations issue version 2 access tokens, which the apps verify (issuer and `ver` claim).
    param($App, [string] $Name)
    if (-not $App) { return }
    $current = Invoke-Az -Arguments @('ad', 'app', 'show', '--id', $App.appId, '--output', 'json')
    if ($current.api -and $current.api.requestedAccessTokenVersion -eq 2) { Write-Host "Unchanged: access token version of $Name"; return }
    Write-Change "set the access token version 2 of $Name"
    if (-not $script:Preview) {
        Invoke-Az -Arguments @('ad', 'app', 'update', '--id', $App.appId, '--set', 'api.requestedAccessTokenVersion=2') -Raw | Out-Null
    }
}

function Initialize-RuntimeRegistration {
    # The runtime's own registration is the audience of the management app's workload token (FR-052).
    param($Inputs)
    $appName = "caller-$Environment-runtime"
    $script:runtimeApp = Find-Registration -DisplayName $appName
    if ($script:runtimeApp) { Write-Host "Unchanged: app registration $appName" }
    else {
        Write-Change "create the app registration $appName"
        if (-not $script:Preview) {
            $script:runtimeApp = Invoke-Az -Arguments @('ad', 'app', 'create', '--display-name', $appName,
                '--sign-in-audience', 'AzureADMyOrg', '--output', 'json')
        }
    }
    $app = $script:runtimeApp
    if ($app -and -not ($app.identifierUris -contains "api://$($app.appId)")) {
        Write-Change "set the application ID URI of $appName"
        if (-not $script:Preview) {
            Invoke-Az -Arguments @('ad', 'app', 'update', '--id', $app.appId, '--identifier-uris', "api://$($app.appId)") -Raw | Out-Null
        }
    }
    Set-AccessTokenVersion -App $app -Name $appName
    $sp = $null
    if ($app) { $sp = Invoke-Az -Arguments @('ad', 'sp', 'show', '--id', $app.appId, '--output', 'json') -AllowFailure }
    if (-not $sp) {
        Write-Change "create the service principal of $appName"
        if (-not $script:Preview) { $sp = Invoke-Az -Arguments @('ad', 'sp', 'create', '--id', $app.appId, '--output', 'json') }
    }
    $has = $false
    if ($app) {
        $fresh = Invoke-Az -Arguments @('ad', 'app', 'show', '--id', $app.appId, '--output', 'json')
        $has = [bool] (@($fresh.appRoles) | Where-Object { $_.value -eq $Inputs.Management })
        $merged = @($fresh.appRoles)
    }
    if ($has) { Write-Host "Unchanged: application role $($Inputs.Management)"; return }
    Write-Change "define the application role $($Inputs.Management) on $appName"
    if ($script:Preview) { return }
    $merged += [ordered]@{
        allowedMemberTypes = @('Application'); description = 'Calls from the management app to the runtime'
        displayName = $Inputs.Management; id = [guid]::NewGuid().ToString(); isEnabled = $true; value = $Inputs.Management
    }
    $file = Join-Path ([System.IO.Path]::GetTempPath()) ("caller-roles-" + [guid]::NewGuid().ToString('N') + '.json')
    try {
        ConvertTo-Json -InputObject $merged -Depth 10 | Set-Content -LiteralPath $file -Encoding utf8NoBOM
        Invoke-Az -Arguments @('ad', 'app', 'update', '--id', $app.appId, '--app-roles', "@$file") -Raw | Out-Null
    }
    finally { Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue }
}

function Invoke-Registration {
    param($Inputs, $Found)
    $appName = "caller-$Environment-management"
    $secretName = "caller-$Environment-signin"  # pragma: allowlist secret

    Invoke-Step 'resource group for the identity objects' {
        $existing = Invoke-Az -Arguments @('group', 'show', '--name', $Inputs.IdentityGroup, '--subscription', $Inputs.Subscription, '--output', 'json') -AllowFailure
        if ($existing) { Write-Host "Unchanged: resource group $($Inputs.IdentityGroup)"; return }
        Write-Change "create resource group $($Inputs.IdentityGroup)"
        if (-not $script:Preview) {
            Invoke-Az -Arguments @('group', 'create', '--name', $Inputs.IdentityGroup, '--location', $Inputs.Location,
                '--subscription', $Inputs.Subscription, '--output', 'json') | Out-Null
        }
    }

    $runtime = $null
    Invoke-Step 'runtime app registration' { Initialize-RuntimeRegistration -Inputs $Inputs }

    Invoke-Step 'runtime user-assigned identity' {
        $script:runtimeIdentity = Invoke-Az -Arguments @('identity', 'show', '--name', $Inputs.RuntimeName,
            '--resource-group', $Inputs.IdentityGroup, '--subscription', $Inputs.Subscription, '--output', 'json') -AllowFailure
        if ($script:runtimeIdentity) { Write-Host "Unchanged: runtime identity $($Inputs.RuntimeName)"; return }
        Write-Change "create the user-assigned identity $($Inputs.RuntimeName)"
        if (-not $script:Preview) {
            $script:runtimeIdentity = Invoke-Az -Arguments @('identity', 'create', '--name', $Inputs.RuntimeName,
                '--resource-group', $Inputs.IdentityGroup, '--location', $Inputs.Location,
                '--subscription', $Inputs.Subscription, '--output', 'json')
        }
    }
    $runtime = $script:runtimeIdentity

    $app = $null
    Invoke-Step 'management app registration' {
        $script:app = Find-Registration -DisplayName $appName
        if ($script:app) { Write-Host "Unchanged: app registration $appName"; return }
        Write-Change "create the app registration $appName"
        if (-not $script:Preview) {
            $script:app = Invoke-Az -Arguments @('ad', 'app', 'create', '--display-name', $appName,
                '--sign-in-audience', 'AzureADMyOrg', '--output', 'json')
        }
    }
    $app = $script:app

    if ($app -and -not ($app.identifierUris -contains "api://$($app.appId)")) {
        Invoke-Step 'application ID URI' {
            Write-Change "set the application ID URI of $appName"
            if (-not $script:Preview) {
                Invoke-Az -Arguments @('ad', 'app', 'update', '--id', $app.appId, '--identifier-uris', "api://$($app.appId)") -Raw | Out-Null
            }
        }
    }

    Invoke-Step 'management access token version' { Set-AccessTokenVersion -App $app -Name $appName }

    $sp = $null
    Invoke-Step 'management service principal' {
        if ($app) { $script:sp = Invoke-Az -Arguments @('ad', 'sp', 'show', '--id', $app.appId, '--output', 'json') -AllowFailure }
        else { $script:sp = $null }
        if ($script:sp) { Write-Host 'Unchanged: service principal'; return }
        Write-Change "create the service principal of $appName"
        if (-not $script:Preview) {
            $script:sp = Invoke-Az -Arguments @('ad', 'sp', 'create', '--id', $app.appId, '--output', 'json')
        }
    }
    $sp = $script:sp

    $roleIds = @{}
    Invoke-Step 'application roles' {
        $current = @()
        if ($app) {
            $fresh = Invoke-Az -Arguments @('ad', 'app', 'show', '--id', $app.appId, '--output', 'json')
            $current = @($fresh.appRoles)
        }
        $definitions = @(
            @{ Value = $Inputs.Editor; Types = @('User'); Text = 'Edit and publish agent configurations' },
            @{ Value = $Inputs.Tester; Types = @('User', 'Application'); Text = 'Use the test console' },
            @{ Value = $Inputs.Runtime; Types = @('Application'); Text = 'Runtime calls to the management app' }
        )
        $merged = @($current)
        $added = @()
        foreach ($definition in $definitions) {
            $existing = $current | Where-Object { $_.value -eq $definition.Value } | Select-Object -First 1
            if ($existing) { $roleIds[$definition.Value] = $existing.id; continue }
            $id = [guid]::NewGuid().ToString()
            $roleIds[$definition.Value] = $id
            $added += $definition.Value
            $merged += [ordered]@{
                allowedMemberTypes = $definition.Types; description = $definition.Text; displayName = $definition.Value
                id = $id; isEnabled = $true; value = $definition.Value
            }
        }
        if ($added.Count -eq 0) { Write-Host 'Unchanged: application roles'; return }
        Write-Change "define the application roles $($added -join ', ')"
        if ($script:Preview) { return }
        $file = Join-Path ([System.IO.Path]::GetTempPath()) ("caller-roles-" + [guid]::NewGuid().ToString('N') + '.json')
        try {
            ConvertTo-Json -InputObject $merged -Depth 10 | Set-Content -LiteralPath $file -Encoding utf8NoBOM
            Invoke-Az -Arguments @('ad', 'app', 'update', '--id', $app.appId, '--app-roles', "@$file") -Raw | Out-Null
        }
        finally { Remove-Item -LiteralPath $file -Force -ErrorAction SilentlyContinue }
    }

    Invoke-Step 'runtime role assignment' {
        $principal = if ($runtime) { $runtime.principalId } else { $null }
        Set-RoleAssignment -ResourceId $sp.id -PrincipalId $principal -RoleId $roleIds[$Inputs.Runtime] `
            -Description "the $($Inputs.Runtime) role to the runtime identity"
    }
    Invoke-Step 'tester role of the deployment identity' {
        Set-RoleAssignment -ResourceId $sp.id -PrincipalId $Preflight.Deployment.id -RoleId $roleIds[$Inputs.Tester] `
            -Description "the $($Inputs.Tester) role to the deployment identity"
    }
    Invoke-Step 'Graph permission of the deployment identity' {
        $graph = Invoke-Az -Arguments @('ad', 'sp', 'show', '--id', $script:GraphAppId, '--output', 'json')
        $read = $graph.appRoles | Where-Object { $_.value -eq 'Application.Read.All' } | Select-Object -First 1
        Set-RoleAssignment -ResourceId $graph.id -PrincipalId $Preflight.Deployment.id -RoleId $read.id `
            -Description 'the read-only Graph permission Application.Read.All to the deployment identity'
    }
    Invoke-Step 'user group role assignments' {
        foreach ($role in @($Inputs.Editor, $Inputs.Tester)) {
            Set-RoleAssignment -ResourceId $sp.id -PrincipalId $Preflight.Group.id -RoleId $roleIds[$role] `
                -Description "the $role role to the user group"
        }
    }

    Invoke-Step 'client secret' {
        $valid = $false
        if ($app) {
            $credentials = @(Invoke-Az -Arguments @('ad', 'app', 'credential', 'list', '--id', $app.appId, '--output', 'json'))
            $valid = [bool] ($credentials | Where-Object {
                    $_.displayName -eq $secretName -and ([datetime] $_.endDateTime) -gt (Get-Date).AddDays(14)
                })
        }
        if ($valid) { Write-Host 'Unchanged: a valid client secret exists; it is not created again.'; return }
        Write-Change 'create a client secret that is valid for at most 90 days'
        if ($script:Preview) { return }
        $end = (Get-Date).ToUniversalTime().AddDays($script:SecretValidityDays).ToString('yyyy-MM-dd')
        $created = Invoke-Az -Arguments @('ad', 'app', 'credential', 'reset', '--id', $app.appId, '--append',
            '--display-name', $secretName, '--end-date', $end, '--output', 'json')
        $directory = Join-Path $RepositoryRoot "code/.local/identity/$Environment"
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
        $target = Join-Path $directory 'management-signin-secret'
        Set-Content -LiteralPath $target -Value $created.password -NoNewline -Encoding utf8NoBOM
        Set-OwnerOnlyAccess -Path $target
        $created = $null
        Write-Host "The client secret was written to $target. Store it once as MANAGEMENT_SIGNIN_SECRET in the protected GitHub environments, then delete the file."
    }

    $clientId = if ($app) { $app.appId } else { '<created on apply>' }
    Write-Host ''
    Write-Host 'Values for app_environment.identity in the variable file:'
    Write-Host "  tenant_id                = $($Inputs.Tenant)"
    Write-Host "  management_app_client_id = $clientId"
    $runtimeClient = if ($script:runtimeApp) { $script:runtimeApp.appId } else { '<created on apply>' }
    Write-Host "  runtime_app_client_id    = $runtimeClient"
    Write-Host "  runtime_identity_name    = $($Inputs.RuntimeName)"
    Write-Host "  roles.editor             = $($Inputs.Editor)"
    Write-Host "  roles.tester             = $($Inputs.Tester)"
    Write-Host "  roles.runtime            = $($Inputs.Runtime)"
    Write-Host "  roles.management         = $($Inputs.Management)"
}

function Invoke-Redirect {
    param($Inputs)
    $appName = "caller-$Environment-management"
    $app = Find-Registration -DisplayName $appName
    if (-not $app) { Stop-Script 1 'Preflight failed: the app registration does not exist. Run the registration phase first.' }
    $fqdn = $null
    Invoke-Step 'management app address' {
        $script:fqdn = Invoke-Az -Arguments @('containerapp', 'show', '--name', $Inputs.ManagementApp, '--resource-group', $Inputs.AppsGroup,
            '--subscription', $Inputs.Subscription, '--query', 'properties.configuration.ingress.fqdn', '--output', 'tsv') -Raw
        if ([string]::IsNullOrWhiteSpace($script:fqdn)) { throw 'no_address' }
    }
    $fqdn = $script:fqdn
    $uri = "https://$fqdn$script:CallbackPath"
    Invoke-Step 'redirect address' {
        $fresh = Invoke-Az -Arguments @('ad', 'app', 'show', '--id', $app.appId, '--output', 'json')
        $uris = @($fresh.web.redirectUris)
        if ($uris -contains $uri) { Write-Host "Unchanged: redirect address $uri"; return }
        Write-Change "register the redirect address $uri"
        if (-not $script:Preview) {
            $all = @($uris + $uri | Where-Object { $_ })
            Invoke-Az -Arguments (@('ad', 'app', 'update', '--id', $app.appId, '--web-redirect-uris') + $all) -Raw | Out-Null
        }
    }
    Write-Host "Redirect address: $uri"

    Invoke-Step 'management role of the management identity' {
        $runtimeApp = Find-Registration -DisplayName "caller-$Environment-runtime"
        if (-not $runtimeApp) { Stop-Script 1 'Preflight failed: the runtime app registration does not exist. Run the registration phase first.' }
        $identity = Invoke-Az -Arguments @('identity', 'show', '--name', "id-$($Inputs.ManagementApp)", '--resource-group', $Inputs.AppsGroup,
            '--subscription', $Inputs.Subscription, '--output', 'json') -AllowFailure
        if (-not $identity) { Stop-Script 1 'Preflight failed: the management identity does not exist yet. Deploy the app environment first.' }
        $runtimeSp = Invoke-Az -Arguments @('ad', 'sp', 'show', '--id', $runtimeApp.appId, '--output', 'json')
        $fresh = Invoke-Az -Arguments @('ad', 'app', 'show', '--id', $runtimeApp.appId, '--output', 'json')
        $role = @($fresh.appRoles) | Where-Object { $_.value -eq $Inputs.Management } | Select-Object -First 1
        Set-RoleAssignment -ResourceId $runtimeSp.id -PrincipalId $identity.principalId -RoleId $role.id `
            -Description "the $($Inputs.Management) role to the management identity"
    }
}

$inputs = Read-Inputs
$Preflight = Test-Preflight -Inputs $inputs
$mode = if ($script:Preview) { 'preview (nothing is changed)' } else { 'apply' }
Write-Host "Environment $Environment, phase $Phase, $mode."

if ($Phase -eq 'registration') { Invoke-Registration -Inputs $inputs } else { Invoke-Redirect -Inputs $inputs }

if ($script:Preview) { Write-Host "Preview finished: $($script:Changes) change(s) would be made." }
else { Write-Host "Finished: $($script:Changes) change(s) made." }
exit 0
