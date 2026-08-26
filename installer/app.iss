; Установщик «Автомонтаж видео» для Windows 10/11 (x64).
; Сборка:  build\dist\AutoVideoIA  -> installer\output\AutoVideoIA-Setup-<версия>.exe
; Требуется Inno Setup 6.3+ (из-за ArchitecturesAllowed=x64compatible).
;
; Ставим в LocalAppData, а не в Program Files: тогда не нужны права администратора
; и не возникает проблем с записью (первый запуск доустанавливает библиотеки
; в папку приложения). Так же ставится Ollama.

#define AppName "Автомонтаж видео"
#define AppVersion "1.0.0"
#define AppPublisher "doublesroses-ai"
#define AppURL "https://github.com/doublesroses-ai/auto-video-IA"
#define AppExeName "Автомонтаж видео.bat"
#define DistDir "..\build\dist\AutoVideoIA"

[Setup]
AppId={{8F3A1C22-6D4E-4B7A-9E15-2A7C5D9B0E31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
DefaultDirName={localappdata}\Programs\AutoVideoIA
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=output
OutputBaseFilename=AutoVideoIA-Setup-{#AppVersion}
; ultra64 роняет 32-битный компилятор на таком объёме
Compression=lzma2/max
SolidCompression=no
WizardStyle=modern
; Закрываем запущенное приложение и наблюдатель, иначе обновление упрётся в занятые файлы
CloseApplications=yes
RestartApplications=no
UninstallDisplayName={#AppName}

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать ярлык на рабочем столе"; GroupDescription: "Ярлыки:"
Name: "autostart"; Description: "Следить за папкой input автоматически при входе в Windows"; GroupDescription: "Автозапуск:"

[Files]
; Всё содержимое собранной папки, кроме установщика C++ runtime (он в [Run])
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "vendor\*,*.marker,test_demo.mp4,__pycache__\*,*.pyc"
Source: "{#DistDir}\vendor\vc_redist.x64.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{group}\Удалить {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; C++ runtime нужен библиотекам распознавания речи. На большинстве машин он уже
; стоит, поэтому запускаем только при отсутствии — иначе зря просим права
; администратора (сама программа ставится без них).
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/install /passive /norestart"; StatusMsg: "Устанавливаю компоненты Microsoft Visual C++..."; Check: NeedsVCRedist; Flags: waituntilterminated
; Первая настройка: докачивает модели и библиотеки видеокарты
Filename: "{app}\python\pythonw.exe"; Parameters: """{app}\first_run.py"""; WorkingDir: "{app}"; StatusMsg: "Запускаю первую настройку..."; Flags: nowait postinstall skipifsilent; Description: "Выполнить первую настройку (скачать модели)"
Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent unchecked; Description: "Запустить {#AppName}"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\lib"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\pipeline\__pycache__"
Type: files; Name: "{app}\first_run_done.marker"
Type: filesandordirs; Name: "{localappdata}\AutoVideoIA"

[Code]
// Автозапуск наблюдателя делаем задачей планировщика, а не ярлыком в автозагрузке:
// задача переживает спящий режим и не отключается случайно в «Приложениях при запуске».
const
  TaskName = 'AutoVideoIA Watcher';

// Библиотеки распознавания речи требуют msvcp140.dll (C++ runtime от Microsoft).
// Если он уже есть — не трогаем систему и не просим прав администратора.
function NeedsVCRedist(): Boolean;
begin
  Result := not FileExists(ExpandConstant('{sys}\msvcp140.dll'));
end;

procedure RegisterWatcherTask();
var
  ResultCode: Integer;
  Cmd: String;
begin
  Cmd := 'schtasks /Create /TN "' + TaskName + '" /TR "wscript.exe \"' +
         ExpandConstant('{app}\run_watcher_hidden.vbs') + '\"" /SC ONLOGON /F /RL LIMITED';
  Exec(ExpandConstant('{cmd}'), '/C ' + Cmd, '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure RemoveWatcherTask();
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/C schtasks /Delete /TN "' + TaskName + '" /F',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure StopRunningWatcher();
var
  ResultCode: Integer;
  Script: String;
begin
  // Наблюдатель держит файлы приложения — без этого обновление упадёт.
  // Закрываем ТОЛЬКО процессы из папки установки: чужие программы на Python
  // и другие окна пользователя трогать нельзя.
  Script := 'Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like ''' +
            ExpandConstant('{app}') + '*'' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }';
  Exec('powershell.exe', '-NoProfile -ExecutionPolicy Bypass -Command "' + Script + '"',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    StopRunningWatcher()
  else if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('autostart') then
      RegisterWatcherTask()
    else
      RemoveWatcherTask();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Keep: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    RemoveWatcherTask();
    StopRunningWatcher();
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    Keep := MsgBox('Удалить также скачанные модели распознавания речи?' + #13#10 +
                   'Это освободит около 2 ГБ в папке .cache\huggingface.' + #13#10#13#10 +
                   'Ваши видео в папке «Видео\AutoVideoIA» удалены НЕ будут.' + #13#10 +
                   'Программа Ollama, если вы её ставили, удаляется отдельно.',
                   mbConfirmation, MB_YESNO);
    if Keep = IDYES then
      DelTree(ExpandConstant('{%USERPROFILE}\.cache\huggingface'), True, True, True);
  end;
end;
