; Inno Setup Script for Hybrid Edge Print Agent
; Assumes edge_print_agent.exe is built and placed in dist/ folder,
; and SumatraPDF.exe is downloaded and placed in the same folder as this script.

[Setup]
AppId={{D37E5528-7A1A-4C2E-8EBA-7128E61BEF7A}}
AppName=Hybrid Edge Print Agent
AppVersion=1.0.0
AppPublisher=PrintAgent
DefaultDirName={autopf}\PrintAgent
DefaultGroupName=Hybrid Edge Print Agent
DisableProgramGroupPage=yes
OutputBaseFilename=PrintAgentSetup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Require administrative privileges to install services
PrivilegesRequired=admin

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\edge_print_agent.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "SumatraPDF.exe"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}"; Permissions: users-modify
Name: "{commonappdata}\PrintAgent"; Permissions: users-modify

[Run]
; Install the service using SCM registration
Filename: "{app}\edge_print_agent.exe"; Parameters: "install --startup=auto"; Flags: runhidden waituntilterminated
; Start the service
Filename: "{app}\edge_print_agent.exe"; Parameters: "start"; Flags: runhidden waituntilterminated

[UninstallRun]
; Stop the service before removing files
Filename: "{app}\edge_print_agent.exe"; Parameters: "stop"; Flags: runhidden waituntilterminated
; Remove the service registration
Filename: "{app}\edge_print_agent.exe"; Parameters: "remove"; Flags: runhidden waituntilterminated

[UninstallDelete]
Type: filesandordirs; Name: "{commonappdata}\PrintAgent"
Type: filesandordirs; Name: "{app}"

[Code]
var
  ApiPage: TInputQueryWizardPage;
  StoragePage: TInputOptionWizardPage;

function EscapeJsonString(const S: String): String;
var
  I: Integer;
begin
  Result := '';
  for I := 1 to Length(S) do
  begin
    if S[I] = '\' then
      Result := Result + '\\'
    else if S[I] = '"' then
      Result := Result + '\"'
    else
      Result := Result + S[I];
  end;
end;

procedure InitializeWizard;
begin
  // Create custom Wizard Page for API settings
  ApiPage := CreateInputQueryPage(wpSelectDir,
    'Cloud API Connection Details', 
    'Provide connection details for the central cloud.',
    'Please enter the Cloud API URL, your installation token, and the agent name.');
  
  ApiPage.Add('Cloud API URL:', False);
  ApiPage.Add('Installation Token:', False);
  ApiPage.Add('Agent Name:', False);

  // Set default values
  ApiPage.Values[0] := 'http://localhost:5000';
  ApiPage.Values[1] := '';
  ApiPage.Values[2] := 'PythonEdgeAgent';

  // Create custom Wizard Page for storage options
  StoragePage := CreateInputOptionPage(ApiPage.ID,
    'Credential Storage Selection', 
    'Choose where the agent credentials should be stored.',
    'Selecting the correct storage is important for service stability. SQLite is recommended for background services running under NT AUTHORITY\SYSTEM.',
    True, False);

  StoragePage.Add('SQLite Database (Recommended for Windows Service / SYSTEM account)');
  StoragePage.Add('Keyring (Windows Credential Manager)');

  // Default to SQLite
  StoragePage.SelectedValue := 0;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ApiUrl, InstallToken, AgentName, StoragePref: String;
  ConfigJson: String;
begin
  if CurStep = ssPostInstall then
  begin
    ApiUrl := ApiPage.Values[0];
    InstallToken := ApiPage.Values[1];
    AgentName := ApiPage.Values[2];
    
    if StoragePage.SelectedValue = 0 then
      StoragePref := 'sqlite'
    else
      StoragePref := 'keyring';

    // Generate config.json content
    ConfigJson := '{' + #13#10 +
      '  "CLOUD_API_URL": "' + EscapeJsonString(ApiUrl) + '",' + #13#10 +
      '  "INSTALL_TOKEN": "' + EscapeJsonString(InstallToken) + '",' + #13#10 +
      '  "AGENT_NAME": "' + EscapeJsonString(AgentName) + '",' + #13#10 +
      '  "CREDENTIAL_STORAGE": "' + EscapeJsonString(StoragePref) + '"' + #13#10 +
      '}';

    // Save configuration file to the app folder
    SaveStringToFile(ExpandConstant('{app}\config.json'), ConfigJson, False);
  end;
end;
