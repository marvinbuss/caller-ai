#Requires -Modules @{ ModuleName = 'Pester'; ModuleVersion = '5.0.0' }

BeforeAll {
    $script:Script = (Resolve-Path (Join-Path $PSScriptRoot '../Initialize-Identity.ps1')).Path
    $script:Fake = (Resolve-Path (Join-Path $PSScriptRoot 'FakeAz.ps1')).Path
    $script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../../../..')).Path
    $script:Mutations = 'group create|identity create|ad app create|ad app update|ad sp create|credential reset|--method post'

    function New-Case {
        $root = Join-Path ([System.IO.Path]::GetTempPath()) ('identity-test-' + [guid]::NewGuid().ToString('N'))
        $folder = Join-Path $root 'code/infra/environments/int'
        New-Item -ItemType Directory -Force -Path $folder | Out-Null
        $document = Get-Content -LiteralPath (Join-Path $script:RepositoryRoot 'code/infra/environments/int/int.tfvars.json') -Raw | ConvertFrom-Json -AsHashtable -Depth 30
        $state = @{
            account         = @{ id = $document.environment.subscription_id; tenantId = $document.environment.tenant_id; user = @{ name = 'admin@example.test'; type = 'user' } }
            directoryRoles  = @('Privileged Role Administrator')
            azureRoles      = @('Owner')
            groups          = @($document.app_environment.identity.user_group_id)
            resourceGroups  = @()
            identities      = @{}
            apps            = @()
            assignments     = @()
            failOn          = ''
            servicePrincipals = @{
                '00000003-0000-0000-c000-000000000000' = @{ id = 'graph-sp'; appId = '00000003-0000-0000-c000-000000000000'; appRoles = @(@{ id = 'read-role'; value = 'Application.Read.All' }) }
                ($document.environment.client_id)      = @{ id = 'deploy-sp'; appId = $document.environment.client_id }
            }
            containerApps   = @{ ($document.app_environment.container_apps.management.app_name) = @{ fqdn = 'management.example.azurecontainerapps.io' } }
        }
        $case = [pscustomobject]@{
            Root = $root; Document = $document; Tfvars = Join-Path $folder 'int.tfvars.json'
            State = Join-Path $root 'state.json'; Log = Join-Path $root 'az.log'
        }
        $state | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $case.State -Encoding utf8
        New-Item -ItemType File -Path $case.Log | Out-Null
        Save-Document $case
        return $case
    }

    function Save-Document($Case) { $Case.Document | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $Case.Tfvars -Encoding utf8 }
    function Get-App($Case, [string] $Name) { (Get-State $Case).apps | Where-Object { $_.displayName -eq $Name } | Select-Object -First 1 }
    function Get-State($Case) { Get-Content -LiteralPath $Case.State -Raw | ConvertFrom-Json -AsHashtable -Depth 30 }
    function Set-State($Case, [scriptblock] $Change) {
        $state = Get-State $Case
        & $Change $state
        $state | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $Case.State -Encoding utf8
    }
    function Get-Log($Case) { , @(Get-Content -LiteralPath $Case.Log) }
    function Get-Mutations($Case) { , @((Get-Content -LiteralPath $Case.Log) | Where-Object { $_ -match $script:Mutations }) }
    function Clear-Log($Case) { Clear-Content -LiteralPath $Case.Log }

    function Add-ManagementIdentity($Case) {
        Set-State $Case { param($s) $s.identities['id-ca-caller-int-management'] = @{ name = 'id-ca-caller-int-management'; principalId = 'management-principal'; clientId = 'management-client' } }
    }

    function Invoke-Identity($Case, [string[]] $Extra = @()) {
        $env:FAKE_AZ_STATE = $Case.State
        $env:FAKE_AZ_LOG = $Case.Log
        $arguments = @('-NoProfile', '-File', $script:Script, '-Environment', 'int', '-AzPath', $script:Fake, '-RepositoryRoot', $Case.Root) + $Extra
        $output = & pwsh @arguments 2>&1 | ForEach-Object { "$_" }
        return [pscustomobject]@{ ExitCode = $LASTEXITCODE; Output = ($output -join "`n") }
    }
}

AfterAll {
    Remove-Item Env:FAKE_AZ_STATE, Env:FAKE_AZ_LOG -ErrorAction SilentlyContinue
}

Describe 'Initialize-Identity registration phase' {
    BeforeEach { $script:Case = New-Case }
    AfterEach { Remove-Item -LiteralPath $script:Case.Root -Recurse -Force -ErrorAction SilentlyContinue }

    It 'previews with -WhatIf, changes nothing and exits 0' {
        $result = Invoke-Identity $script:Case @('-WhatIf')
        $result.ExitCode | Should -Be 0
        $result.Output | Should -Match 'Would create the app registration'
        (Get-Mutations $script:Case).Count | Should -Be 0
        Test-Path (Join-Path $script:Case.Root 'code/.local/identity/int/management-signin-secret') | Should -BeFalse
    }

    It 'creates every object and prints the values for the variable file' {
        $result = Invoke-Identity $script:Case
        $result.ExitCode | Should -Be 0
        $state = Get-State $script:Case
        $state.resourceGroups | Should -Contain 'rg-caller-int-identity'
        $state.identities.Keys | Should -Contain 'id-caller-int-runtime'
        $state.apps.Count | Should -Be 2
        $management = Get-App $script:Case 'caller-int-management'
        $runtimeApp = Get-App $script:Case 'caller-int-runtime'
        @($management.appRoles | ForEach-Object { $_.value }) | Should -Be @('Configuration.Manager', 'Configuration.Tester', 'Runtime.Caller')
        @($runtimeApp.appRoles | ForEach-Object { $_.value }) | Should -Be @('Management.Caller')
        $state.servicePrincipals.Keys | Should -Contain $management.appId
        $state.servicePrincipals.Keys | Should -Contain $runtimeApp.appId
        $result.Output | Should -Match "management_app_client_id = $($management.appId)"
        $result.Output | Should -Match "runtime_app_client_id\s+= $($runtimeApp.appId)"
        $result.Output | Should -Match 'runtime_identity_name\s+= id-caller-int-runtime'
    }

    It 'makes a second run a no-op' {
        (Invoke-Identity $script:Case).ExitCode | Should -Be 0
        Clear-Log $script:Case
        $second = Invoke-Identity $script:Case
        $second.ExitCode | Should -Be 0
        (Get-Mutations $script:Case).Count | Should -Be 0
        $second.Output | Should -Match 'Finished: 0 change'
    }

    It 'reads the role names from the variable file' {
        $script:Case.Document.app_environment.identity.roles = @{ editor = 'Org.Editor'; tester = 'Org.Tester'; runtime = 'Org.Runtime'; management = 'Org.Management' }
        Save-Document $script:Case
        (Invoke-Identity $script:Case).ExitCode | Should -Be 0
        @((Get-App $script:Case 'caller-int-management').appRoles | ForEach-Object { $_.value }) | Should -Be @('Org.Editor', 'Org.Tester', 'Org.Runtime')
        @((Get-App $script:Case 'caller-int-runtime').appRoles | ForEach-Object { $_.value }) | Should -Be @('Org.Management')
    }

    It 'assigns the runtime, tester, Graph and group roles' {
        (Invoke-Identity $script:Case).ExitCode | Should -Be 0
        $state = Get-State $script:Case
        $app = Get-App $script:Case 'caller-int-management'
        $sp = $state.servicePrincipals[$app.appId]
        $role = @{}
        foreach ($item in $app.appRoles) { $role[$item.value] = $item.id }
        $runtime = $state.identities['id-caller-int-runtime']
        $groupId = $script:Case.Document.app_environment.identity.user_group_id
        $onApp = @($state.assignments | Where-Object { $_.resourceId -eq $sp.id })
        ($onApp | Where-Object { $_.principalId -eq $runtime.principalId -and $_.appRoleId -eq $role['Runtime.Caller'] }) | Should -Not -BeNullOrEmpty
        ($onApp | Where-Object { $_.principalId -eq 'deploy-sp' -and $_.appRoleId -eq $role['Configuration.Tester'] }) | Should -Not -BeNullOrEmpty
        ($onApp | Where-Object { $_.principalId -eq $groupId -and $_.appRoleId -eq $role['Configuration.Editor'] }) | Should -BeNullOrEmpty
        ($onApp | Where-Object { $_.principalId -eq $groupId -and $_.appRoleId -eq $role['Configuration.Manager'] }) | Should -Not -BeNullOrEmpty
        ($onApp | Where-Object { $_.principalId -eq $groupId -and $_.appRoleId -eq $role['Configuration.Tester'] }) | Should -Not -BeNullOrEmpty
        ($state.assignments | Where-Object { $_.resourceId -eq 'graph-sp' -and $_.principalId -eq 'deploy-sp' -and $_.appRoleId -eq 'read-role' }) | Should -Not -BeNullOrEmpty
    }

    It 'writes the client secret only to the ignored file and never prints it' {
        $result = Invoke-Identity $script:Case
        $file = Join-Path $script:Case.Root 'code/.local/identity/int/management-signin-secret'
        Test-Path $file | Should -BeTrue
        (Get-Content -LiteralPath $file -Raw) | Should -Be 'synthetic-secret-value-0001'
        $result.Output | Should -Not -Match 'synthetic-secret-value-0001'
        (Get-Log $script:Case) -join "`n" | Should -Not -Match 'synthetic-secret-value-0001'
    }

    It 'creates no other secret, certificate or key and limits the validity to 90 days' {
        (Invoke-Identity $script:Case).ExitCode | Should -Be 0
        $log = Get-Log $script:Case
        @($log -match 'credential reset').Count | Should -Be 1
        $log -match 'keyvault|--create-cert|certificate|--years|ssh|keypair' | Should -BeNullOrEmpty
        $reset = ($log -match 'credential reset')[0]
        $end = [datetime]::Parse(([regex]::Match($reset, '--end-date (\d{4}-\d{2}-\d{2})')).Groups[1].Value)
        ($end - (Get-Date)).TotalDays | Should -BeLessOrEqual 90
        ($end - (Get-Date)).TotalDays | Should -BeGreaterThan 1
    }

    It 'does not create a second secret while a valid one exists' {
        (Invoke-Identity $script:Case).ExitCode | Should -Be 0
        Clear-Log $script:Case
        (Invoke-Identity $script:Case).ExitCode | Should -Be 0
        @((Get-Content -LiteralPath $script:Case.Log) | Where-Object { $_ -match 'credential reset' }).Count | Should -Be 0
    }

    It 'stops before any change when <Name>' -ForEach @(
        @{ Name = 'there is no sign-in'; Change = { param($s) $s.account = $null }; Message = 'no Azure CLI sign-in' }
        @{ Name = 'the tenant differs'; Change = { param($s) $s.account.tenantId = 'other' }; Message = 'tenant differs' }
        @{ Name = 'the subscription differs'; Change = { param($s) $s.account.id = 'other' }; Message = 'subscription differs' }
        @{ Name = 'the directory role is missing'; Change = { param($s) $s.directoryRoles = @('Reader') }; Message = 'Privileged Role Administrator or Global Administrator' }
        @{ Name = 'the Azure rights are missing'; Change = { param($s) $s.azureRoles = @('Reader') }; Message = 'Azure rights on the subscription' }
    ) {
        Set-State $script:Case $Change
        $result = Invoke-Identity $script:Case
        $result.ExitCode | Should -Be 1
        $result.Output | Should -Match ([regex]::Escape($Message))
        (Get-Mutations $script:Case).Count | Should -Be 0
    }

    It 'exits 2 for invalid input: <Name>' -ForEach @(
        @{ Name = 'missing user group'; Change = { param($d) $d.app_environment.identity.Remove('user_group_id') } }
        @{ Name = 'user group not a GUID'; Change = { param($d) $d.app_environment.identity.user_group_id = 'group' } }
        @{ Name = 'duplicate role names'; Change = { param($d) $d.app_environment.identity.roles.tester = $d.app_environment.identity.roles.editor } }
        @{ Name = 'missing role names'; Change = { param($d) $d.app_environment.identity.Remove('roles') } }
    ) {
        & $Change $script:Case.Document
        Save-Document $script:Case
        $result = Invoke-Identity $script:Case
        $result.ExitCode | Should -Be 2
        (Get-Mutations $script:Case).Count | Should -Be 0
    }

    It 'exits 2 when the file, the environment name or the group does not exist' {
        Remove-Item -LiteralPath $script:Case.Tfvars
        (Invoke-Identity $script:Case).ExitCode | Should -Be 2
        Save-Document $script:Case
        (Invoke-Identity $script:Case @('-Phase', 'wrong')).ExitCode | Should -Be 2
        Set-State $script:Case { param($s) $s.groups = @() }
        $missing = Invoke-Identity $script:Case
        $missing.ExitCode | Should -Be 2
        $missing.Output | Should -Match 'user_group_id'
    }

    It 'exits 3 and names the failed step' {
        Set-State $script:Case { param($s) $s.failOn = 'app-create' }
        $result = Invoke-Identity $script:Case
        $result.ExitCode | Should -Be 3
        $result.Output | Should -Match 'Step failed: runtime app registration'
    }
}

Describe 'Initialize-Identity redirect phase' {
    BeforeEach { $script:Case = New-Case }
    AfterEach { Remove-Item -LiteralPath $script:Case.Root -Recurse -Force -ErrorAction SilentlyContinue }

    It 'needs the registration phase first' {
        $result = Invoke-Identity $script:Case @('-Phase', 'redirect')
        $result.ExitCode | Should -Be 1
        $result.Output | Should -Match 'registration phase first'
    }

    It 'reads the address with az containerapp show and registers the callback address' {
        (Invoke-Identity $script:Case).ExitCode | Should -Be 0
        Add-ManagementIdentity $script:Case
        Clear-Log $script:Case
        $result = Invoke-Identity $script:Case @('-Phase', 'redirect')
        $result.ExitCode | Should -Be 0
        (Get-Log $script:Case) -match 'containerapp show --name ca-caller-int-management' | Should -Not -BeNullOrEmpty
        (Get-App $script:Case 'caller-int-management').web.redirectUris | Should -Contain 'https://management.example.azurecontainerapps.io/auth/callback'
    }

    It 'previews without changing the registration and is a no-op the second time' {
        (Invoke-Identity $script:Case).ExitCode | Should -Be 0
        Add-ManagementIdentity $script:Case
        Clear-Log $script:Case
        (Invoke-Identity $script:Case @('-Phase', 'redirect', '-WhatIf')).ExitCode | Should -Be 0
        (Get-App $script:Case 'caller-int-management').web.redirectUris | Should -BeNullOrEmpty
        (Invoke-Identity $script:Case @('-Phase', 'redirect')).ExitCode | Should -Be 0
        Clear-Log $script:Case
        (Invoke-Identity $script:Case @('-Phase', 'redirect')).ExitCode | Should -Be 0
        (Get-Mutations $script:Case).Count | Should -Be 0
    }

    It 'assigns the management role of the runtime registration to the management identity' {
        (Invoke-Identity $script:Case).ExitCode | Should -Be 0
        Add-ManagementIdentity $script:Case
        (Invoke-Identity $script:Case @('-Phase', 'redirect')).ExitCode | Should -Be 0
        $state = Get-State $script:Case
        $runtimeApp = Get-App $script:Case 'caller-int-runtime'
        $sp = $state.servicePrincipals[$runtimeApp.appId]
        ($state.assignments | Where-Object { $_.resourceId -eq $sp.id -and $_.principalId -eq 'management-principal' -and $_.appRoleId -eq $runtimeApp.appRoles[0].id }) | Should -Not -BeNullOrEmpty
    }

    It 'stops when the management identity does not exist yet' {
        (Invoke-Identity $script:Case).ExitCode | Should -Be 0
        $result = Invoke-Identity $script:Case @('-Phase', 'redirect')
        $result.ExitCode | Should -Be 1
        $result.Output | Should -Match 'Deploy the app environment first'
    }

    It 'names the failed step when the app has no address' {
        (Invoke-Identity $script:Case).ExitCode | Should -Be 0
        Add-ManagementIdentity $script:Case
        Set-State $script:Case { param($s) $s.containerApps = @{} }
        $result = Invoke-Identity $script:Case @('-Phase', 'redirect')
        $result.ExitCode | Should -Be 3
        $result.Output | Should -Match 'Step failed: management app address'
    }
}
