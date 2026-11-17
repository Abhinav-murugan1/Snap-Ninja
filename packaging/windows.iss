#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#include "generated.iss"

[Setup]
AppId={{2351AACD-18AE-431D-BC91-A753E2DAB731}
AppName=Snap Ninja
AppVersion={#AppVersion}
AppPublisher=Abhinav Murugan
AppPublisherURL=https://github.com/Abhinav-murugan1/snap-ninja
AppSupportURL=https://github.com/Abhinav-murugan1/snap-ninja/issues
DefaultDirName={localappdata}\Programs\Snap Ninja
DefaultGroupName=Snap Ninja
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
SetupIconFile=..\static\snap-ninja-v1.ico
UninstallDisplayIcon={app}\Snap Ninja.exe
LicenseFile=..\LICENSE
OutputDir=..\dist\installer
OutputBaseFilename=SnapNinja-Setup-{#AppVersion}-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
SetupMutex=SnapNinjaSetup

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\Snap Ninja\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "vendor\MicrosoftEdgeWebview2Setup.exe"; Flags: dontcopy
Source: "install_media.ps1"; Flags: dontcopy
Source: "{tmp}\snap-ninja-media\*"; DestDir: "{app}\dependencies\ffmpeg"; Flags: external ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Snap Ninja"; Filename: "{app}\Snap Ninja.exe"; AppUserModelID: "SnapNinja.Desktop"
Name: "{autodesktop}\Snap Ninja"; Filename: "{app}\Snap Ninja.exe"; Tasks: desktopicon; AppUserModelID: "SnapNinja.Desktop"

[Run]
Filename: "{app}\Snap Ninja.exe"; Description: "Launch Snap Ninja"; Flags: nowait postinstall skipifsilent

[Code]
var DownloadPage: TDownloadWizardPage;
    MediaReady: Boolean;

function WebViewInstalled: Boolean;
var Version: String;
begin
  Result := (RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
  if not Result then
    Result := (RegQueryStringValue(HKLM32, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
  if not Result then
    Result := (RegQueryStringValue(HKLM64, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0'));
end;

procedure InitializeWizard;
begin
  DownloadPage := CreateDownloadPage('Setting up Snap Ninja',
    'Downloading video tools. Keep your internet connection active.', nil);
  MediaReady := False;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer;
    Arguments: String;
begin
  Result := '';
  if not WebViewInstalled then begin
    WizardForm.StatusLabel.Caption := 'Installing Microsoft WebView2...';
    ExtractTemporaryFile('MicrosoftEdgeWebview2Setup.exe');
    if not Exec(ExpandConstant('{tmp}\MicrosoftEdgeWebview2Setup.exe'), '/silent /install', '', SW_HIDE, ewWaitUntilTerminated, Code) then begin
      Result := 'Could not start Microsoft WebView2 setup. Retry installation.';
      Exit;
    end;
    if not WebViewInstalled then begin
      Result := 'Microsoft WebView2 setup did not finish (code ' + IntToStr(Code) + '). Check your connection and retry.';
      Exit;
    end;
  end;
  if MediaReady then Exit;
  DownloadPage.Clear;
  DownloadPage.Add('{#MediaURL}', 'snap-ninja-ffmpeg.zip', '{#MediaSHA256}');
  DownloadPage.Show;
  try
    try
      DownloadPage.Download;
    except
      Result := 'Video tools could not be downloaded or verified. Check your connection and retry. ' + GetExceptionMessage;
      Exit;
    end;
  finally
    DownloadPage.Hide;
  end;
  ExtractTemporaryFile('install_media.ps1');
  Arguments := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{tmp}\install_media.ps1') + '" -Archive "' +
    ExpandConstant('{tmp}\snap-ninja-ffmpeg.zip') + '" -Destination "' +
    ExpandConstant('{tmp}\snap-ninja-media') + '"';
  WizardForm.StatusLabel.Caption := 'Preparing video tools...';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Arguments, '', SW_HIDE, ewWaitUntilTerminated, Code) then begin
    Result := 'Could not prepare the video tools. Retry installation.';
    Exit;
  end;
  if Code <> 0 then begin
    Result := 'Video tools could not be extracted. Check free disk space and retry installation.';
    Exit;
  end;
  MediaReady := True;
end;
