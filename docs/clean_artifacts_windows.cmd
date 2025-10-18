@echo off
setlocal enabledelayedexpansion

rem clean_artifacts_windows.cmd
rem ----------------------------
rem Limpia artefactos (BD, logs, JSON y __pycache__) en Windows
rem sin requerir PowerShell ni cambiar ExecutionPolicy.
rem Uso:  docs\clean_artifacts_windows.cmd

pushd "%~dp0.." >nul 2>&1
echo [ clean ] Repo root: %CD%

rem --- Artefactos del servidor ---
if exist "server\omi.db" (
  del /q /f "server\omi.db" && echo [ clean ] Eliminado server\omi.db
) else (
  echo [ clean ] No existe server\omi.db (ok)
)
if exist "server\logs" (
  rmdir /s /q "server\logs" && echo [ clean ] Eliminado server\logs
) else (
  echo [ clean ] No existe server\logs (ok)
)

rem --- Artefactos del cliente / agente ---
if exist "client\logs" (
  rmdir /s /q "client\logs" && echo [ clean ] Eliminado client\logs
) else (
  echo [ clean ] No existe client\logs (ok)
)

for %%F in (
  "client\agent_pi\data\structure.json"
  "client\agent_pi\data\server.json"
) do (
  if exist %%~F (
    del /q /f %%~F && echo [ clean ] Eliminado %%~F
  ) else (
    echo [ clean ] No existe %%~F (ok)
  )
)

rem --- Artefactos del servicio MIDI ---
for %%F in (
  "client\servicios\MIDI\OMIMIDI_map.json"
  "client\servicios\MIDI\OMIMIDI_state.json"
  "client\servicios\MIDI\OMIMIDI_last_event.json"
  "client\servicios\MIDI\OMIMIDI_learn_request.json"
  "client\servicios\MIDI\OMIMIDI_restart.flag"
  "client\servicios\MIDI\OMIMIDI_webui.pid"
) do (
  if exist %%~F (
    del /q /f %%~F && echo [ clean ] Eliminado %%~F
  ) else (
    echo [ clean ] No existe %%~F (ok)
  )
)

rem --- Eliminar directorios __pycache__ ---
echo [ clean ] Eliminando directorios __pycache__
for /f "delims=" %%D in ('dir /ad /b /s __pycache__ 2^>NUL') do (
  rmdir /s /q "%%D" && echo [ clean ] Eliminado %%D
)

echo [ clean ] Limpieza completada.
popd >nul 2>&1
endlocal

