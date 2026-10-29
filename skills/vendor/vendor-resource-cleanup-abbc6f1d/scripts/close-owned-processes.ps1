param(
 [Parameter(Mandatory)][string]$ManifestPath,
 [switch]$Apply,
 [string]$LogPath
)
$ErrorActionPreference='Stop'
$manifest=Get-Content -LiteralPath $ManifestPath -Raw|ConvertFrom-Json
if([string]::IsNullOrWhiteSpace($manifest.owner_task)){throw 'Missing owner_task'}
$all=@(Get-CimInstance Win32_Process -ErrorAction Stop)
$services=@(Get-CimInstance Win32_Service -ErrorAction Stop)
$serviceIDs=@($services|Where-Object ProcessId -gt 0|ForEach-Object{[int]$_.ProcessId})
$map=@{};foreach($process in $all){$map[[int]$process.ProcessId]=$process}
$ancestors=@{};$currentId=$PID
for($i=0;$i-lt 40;$i++){
 if($ancestors.ContainsKey($currentId)){break}
 $ancestors[$currentId]=$true
 if(-not $map.ContainsKey($currentId)){break}
 $currentId=[int]$map[$currentId].ParentProcessId
}
$protected=@('ChatGPT.exe','codex.exe','easyeda.exe','explorer.exe','services.exe','svchost.exe','lsass.exe','System','Secure System','MsMpEng.exe','avp.exe','remoting_host.exe','sqlservr.exe','GHelper.exe')
$results=@()
foreach($entry in @($manifest.resources)){
 $status=$null;$name=$null
 try{
  $targetId=[int]$entry.pid
  if($entry.state-ne 'completed'-or $entry.disposable-ne $true-or $entry.shared-ne $false){$status='SkipNotDisposable'}
  elseif($targetId-le 4-or $ancestors.ContainsKey($targetId)-or $targetId-in $serviceIDs){$status='SkipProtected'}
  else{
   $process=Get-CimInstance Win32_Process -Filter "ProcessId=$targetId" -ErrorAction Stop
   if(-not $process){$status='AlreadyExited'}
   else{
    $name=$process.Name
    if($name-in $protected){$status='SkipProtected'}
    elseif([string]::IsNullOrWhiteSpace($entry.created_utc)-or [string]::IsNullOrWhiteSpace($entry.executable_path)-or $null-eq $entry.parent_pid){$status='SkipMissingIdentity'}
    else{
     $expected=if($entry.created_utc -is [DateTime]){$entry.created_utc.ToUniversalTime()}else{[DateTimeOffset]::Parse([string]$entry.created_utc,[Globalization.CultureInfo]::InvariantCulture).UtcDateTime}
     $actual=$process.CreationDate.ToUniversalTime()
     if($actual-ne $expected-or -not [string]::Equals($process.ExecutablePath,$entry.executable_path,[StringComparison]::OrdinalIgnoreCase)-or [int]$process.ParentProcessId-ne [int]$entry.parent_pid){$status='SkipIdentityMismatch'}
     elseif(-not $Apply){$status='WouldStop'}
     else{
      $handle=Get-Process -Id $targetId -ErrorAction Stop
      if([math]::Abs(($handle.StartTime.ToUniversalTime()-$expected).TotalMilliseconds)-gt 1){$status='SkipIdentityMismatch'}
      else{
       Stop-Process -InputObject $handle -ErrorAction Stop
       $exited=$handle.WaitForExit(3000)
       $status=if($exited){'Stopped'}else{'StopPending'}
      }
     }
    }
   }
  }
 }catch{$status='Failed: '+$_.Exception.Message}
 $results += [pscustomobject]@{PID=$entry.pid;Name=$name;Status=$status;OwnerTask=$manifest.owner_task}
}
if($LogPath){ConvertTo-Json -InputObject @($results) -Depth 4|Set-Content -LiteralPath $LogPath -Encoding utf8}
return $results


